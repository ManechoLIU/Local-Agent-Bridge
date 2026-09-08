use axum::{
    Router,
    body::{Body, Bytes},
    extract::State,
    http::{HeaderMap, Response, StatusCode, header},
    routing::post,
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{HashMap, HashSet},
    process::Command,
    sync::Arc,
};
use subtle::ConstantTimeEq;
use tokio::sync::Mutex;
use uuid::Uuid;

const BACKEND_KEY_HEADER: &str = "x-lab-backend-key";
const SIGNED_PROVENANCE: &str = "local_ingress_hmac_v1";

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize, Hash)]
pub struct AppIdentity {
    pub pid: i32,
    pub bundle_id: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AppInfo {
    pub pid: i32,
    pub bundle_id: String,
    pub display_name: String,
    pub is_active: bool,
    pub is_hidden: bool,
    pub is_terminated: bool,
}

pub trait ComputerProvider: Send + Sync + 'static {
    fn permissions(&self) -> Result<Value, String>;
    fn list_apps(&self) -> Result<Vec<AppInfo>, String>;
    fn list_windows(&self) -> Result<Value, String>;
    fn active_window(&self) -> Result<Value, String>;
    fn activate_app(&self, pid: i32, bundle_id: &str) -> Result<Value, String>;
}

#[derive(Clone, Debug, Default)]
pub struct NativeMacProvider;

impl NativeMacProvider {
    fn helper(&self, args: &[&str]) -> Result<Value, String> {
        #[cfg(target_os = "macos")]
        {
            let output = Command::new(env!("COMPUTER_RUNTIME_MACOS_HELPER"))
                .args(args)
                .output()
                .map_err(|error| format!("macOS helper launch failed: {error}"))?;
            if !output.status.success() {
                return Err(format!(
                    "macOS helper failed: {}",
                    String::from_utf8_lossy(&output.stderr)
                ));
            }
            let value: Value = serde_json::from_slice(&output.stdout)
                .map_err(|error| format!("macOS helper returned invalid JSON: {error}"))?;
            if value.get("ok").and_then(Value::as_bool) != Some(true) {
                return Err(value
                    .get("error")
                    .and_then(Value::as_str)
                    .unwrap_or("macOS helper failed")
                    .to_string());
            }
            Ok(value.get("result").cloned().unwrap_or(Value::Null))
        }
        #[cfg(not(target_os = "macos"))]
        {
            let _ = args;
            Err("native Computer Runtime provider is not implemented on this OS".into())
        }
    }
}

impl ComputerProvider for NativeMacProvider {
    fn permissions(&self) -> Result<Value, String> {
        self.helper(&["permissions"])
    }

    fn list_apps(&self) -> Result<Vec<AppInfo>, String> {
        let value = self.helper(&["list_apps"])?;
        serde_json::from_value(value).map_err(|error| format!("invalid app inventory: {error}"))
    }

    fn list_windows(&self) -> Result<Value, String> {
        self.helper(&["list_windows"])
    }

    fn active_window(&self) -> Result<Value, String> {
        self.helper(&["active_window"])
    }

    fn activate_app(&self, pid: i32, bundle_id: &str) -> Result<Value, String> {
        self.helper(&["activate_app", &pid.to_string(), bundle_id])
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct RequestBinding {
    principal_id: String,
    execution_session_id: String,
    capability_id: String,
    workspace_id: String,
}

#[derive(Clone, Debug)]
struct ComputerSession {
    binding: RequestBinding,
    generation: u64,
    observations: HashMap<String, HashSet<AppIdentity>>,
}

struct RuntimeInner<P: ComputerProvider> {
    backend_key_sha256: [u8; 32],
    provider: P,
    sessions: Mutex<HashMap<String, ComputerSession>>,
}

pub struct ComputerRuntime<P: ComputerProvider> {
    inner: Arc<RuntimeInner<P>>,
}

impl<P: ComputerProvider> Clone for ComputerRuntime<P> {
    fn clone(&self) -> Self {
        Self {
            inner: Arc::clone(&self.inner),
        }
    }
}

impl<P: ComputerProvider> ComputerRuntime<P> {
    pub fn new(backend_key: &[u8], provider: P) -> Result<Self, String> {
        if backend_key.len() < 32 {
            return Err("Computer Runtime backend key must be at least 32 bytes".into());
        }
        let mut backend_key_sha256 = [0_u8; 32];
        backend_key_sha256.copy_from_slice(&Sha256::digest(backend_key));
        Ok(Self {
            inner: Arc::new(RuntimeInner {
                backend_key_sha256,
                provider,
                sessions: Mutex::new(HashMap::new()),
            }),
        })
    }

    pub fn router(self) -> Router {
        Router::new()
            .route("/mcp", post(handle_mcp::<P>))
            .with_state(self)
    }
}

async fn handle_mcp<P: ComputerProvider>(
    State(runtime): State<ComputerRuntime<P>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response<Body> {
    if !valid_backend_key(&headers, &runtime.inner.backend_key_sha256) {
        return text_response(
            StatusCode::UNAUTHORIZED,
            "Computer Runtime backend authentication failed",
        );
    }

    let parsed: Value = match serde_json::from_slice(&body) {
        Ok(value) => value,
        Err(_) => return text_response(StatusCode::BAD_REQUEST, "invalid JSON-RPC body"),
    };
    let method = match parsed.get("method").and_then(Value::as_str) {
        Some(value) => value,
        None => return text_response(StatusCode::BAD_REQUEST, "JSON-RPC method is required"),
    };
    let id = parsed.get("id").cloned().unwrap_or(Value::Null);

    match method {
        "server/discover" | "initialize" => json_response(
            StatusCode::OK,
            json!({
                "jsonrpc": "2.0",
                "id": id,
                "result": {
                    "serverInfo": {"name": "computer-runtime", "version": "0.1.0"},
                    "capabilities": {"tools": {}}
                }
            }),
        ),
        "tools/list" => json_response(
            StatusCode::OK,
            json!({"jsonrpc":"2.0","id":id,"result":{"tools":tool_specs()}}),
        ),
        "tools/call" => {
            let binding = match signed_bridge_binding(&headers, &body) {
                Ok(value) => value,
                Err(error) => return text_response(StatusCode::FORBIDDEN, &error),
            };
            let params = parsed.get("params").cloned().unwrap_or_else(|| json!({}));
            let name = match params.get("name").and_then(Value::as_str) {
                Some(value) => value,
                None => {
                    return text_response(
                        StatusCode::BAD_REQUEST,
                        "tools/call requires params.name",
                    );
                }
            };
            let arguments = params
                .get("arguments")
                .cloned()
                .unwrap_or_else(|| json!({}));
            match call_tool(&runtime, &binding, name, &arguments).await {
                Ok(value) => tool_result(id, value),
                Err(ToolError::Forbidden(error)) => text_response(StatusCode::FORBIDDEN, &error),
                Err(ToolError::BadRequest(error)) => text_response(StatusCode::BAD_REQUEST, &error),
                Err(ToolError::Provider(error)) => text_response(StatusCode::BAD_GATEWAY, &error),
            }
        }
        method if method.starts_with("notifications/") => Response::builder()
            .status(StatusCode::NO_CONTENT)
            .body(Body::empty())
            .expect("static response"),
        _ => text_response(
            StatusCode::FORBIDDEN,
            "unsupported Computer Runtime MCP method",
        ),
    }
}

#[derive(Debug)]
enum ToolError {
    Forbidden(String),
    BadRequest(String),
    Provider(String),
}

async fn call_tool<P: ComputerProvider>(
    runtime: &ComputerRuntime<P>,
    binding: &RequestBinding,
    name: &str,
    arguments: &Value,
) -> Result<Value, ToolError> {
    match name {
        "computer_session_open" => {
            let session_id = format!("cs_{}", Uuid::new_v4());
            runtime.inner.sessions.lock().await.insert(
                session_id.clone(),
                ComputerSession {
                    binding: binding.clone(),
                    generation: 1,
                    observations: HashMap::new(),
                },
            );
            Ok(json!({"session_id":session_id,"generation":1}))
        }
        "computer_session_close" => {
            let session_id = string_arg(arguments, "session_id")?;
            let mut sessions = runtime.inner.sessions.lock().await;
            ensure_session_owner(&sessions, session_id, binding)?;
            sessions.remove(session_id);
            Ok(json!({"closed":true,"session_id":session_id}))
        }
        "computer_permissions" => {
            let session_id = string_arg(arguments, "session_id")?;
            ensure_session(&runtime.inner.sessions, session_id, binding).await?;
            runtime
                .inner
                .provider
                .permissions()
                .map_err(ToolError::Provider)
        }
        "computer_list_apps" => {
            let session_id = string_arg(arguments, "session_id")?;
            ensure_session(&runtime.inner.sessions, session_id, binding).await?;
            let apps = runtime
                .inner
                .provider
                .list_apps()
                .map_err(ToolError::Provider)?;
            let observation_id = format!("obs_{}", Uuid::new_v4());
            let identities = apps
                .iter()
                .map(|app| AppIdentity {
                    pid: app.pid,
                    bundle_id: app.bundle_id.clone(),
                })
                .collect();
            runtime
                .inner
                .sessions
                .lock()
                .await
                .get_mut(session_id)
                .expect("validated session")
                .observations
                .insert(observation_id.clone(), identities);
            Ok(json!({"session_id":session_id,"observation_id":observation_id,"apps":apps}))
        }
        "computer_list_windows" => {
            let session_id = string_arg(arguments, "session_id")?;
            ensure_session(&runtime.inner.sessions, session_id, binding).await?;
            let windows = runtime
                .inner
                .provider
                .list_windows()
                .map_err(ToolError::Provider)?;
            Ok(json!({"session_id":session_id,"windows":windows}))
        }
        "computer_active_window" => {
            let session_id = string_arg(arguments, "session_id")?;
            ensure_session(&runtime.inner.sessions, session_id, binding).await?;
            let window = runtime
                .inner
                .provider
                .active_window()
                .map_err(ToolError::Provider)?;
            Ok(json!({"session_id":session_id,"window":window}))
        }
        "computer_activate_app" => {
            let session_id = string_arg(arguments, "session_id")?;
            let observation_id = string_arg(arguments, "observation_id")?;
            let pid = i32_arg(arguments, "pid")?;
            let bundle_id = string_arg(arguments, "bundle_id")?;
            let sessions = runtime.inner.sessions.lock().await;
            let session = ensure_session_owner(&sessions, session_id, binding)?;
            let observed = session.observations.get(observation_id).ok_or_else(|| {
                ToolError::Forbidden("observation does not belong to this Computer session".into())
            })?;
            if !observed.contains(&AppIdentity {
                pid,
                bundle_id: bundle_id.to_string(),
            }) {
                return Err(ToolError::Forbidden(
                    "target app was not present in the referenced session observation".into(),
                ));
            }
            drop(sessions);
            runtime
                .inner
                .provider
                .activate_app(pid, bundle_id)
                .map_err(ToolError::Provider)
        }
        _ => Err(ToolError::Forbidden(format!(
            "unknown Computer Runtime tool: {name}"
        ))),
    }
}

async fn ensure_session(
    sessions: &Mutex<HashMap<String, ComputerSession>>,
    session_id: &str,
    binding: &RequestBinding,
) -> Result<(), ToolError> {
    let sessions = sessions.lock().await;
    ensure_session_owner(&sessions, session_id, binding).map(|_| ())
}

fn ensure_session_owner<'a>(
    sessions: &'a HashMap<String, ComputerSession>,
    session_id: &str,
    binding: &RequestBinding,
) -> Result<&'a ComputerSession, ToolError> {
    let session = sessions
        .get(session_id)
        .ok_or_else(|| ToolError::Forbidden("unknown or closed Computer session".into()))?;
    if &session.binding != binding {
        return Err(ToolError::Forbidden(
            "Computer session ownership does not match signed Bridge execution context".into(),
        ));
    }
    if session.generation != 1 {
        return Err(ToolError::Forbidden(
            "stale Computer session generation".into(),
        ));
    }
    Ok(session)
}

fn string_arg<'a>(arguments: &'a Value, name: &str) -> Result<&'a str, ToolError> {
    arguments
        .get(name)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| ToolError::BadRequest(format!("missing argument: {name}")))
}

fn i32_arg(arguments: &Value, name: &str) -> Result<i32, ToolError> {
    let raw = arguments
        .get(name)
        .and_then(Value::as_i64)
        .ok_or_else(|| ToolError::BadRequest(format!("missing argument: {name}")))?;
    i32::try_from(raw).map_err(|_| ToolError::BadRequest(format!("invalid argument: {name}")))
}

fn signed_bridge_binding(headers: &HeaderMap, body: &[u8]) -> Result<RequestBinding, String> {
    let required = |name: &str| {
        headers
            .get(name)
            .and_then(|value| value.to_str().ok())
            .filter(|value| !value.trim().is_empty())
            .map(str::to_string)
            .ok_or_else(|| format!("missing signed Bridge header: {name}"))
    };
    if required("x-lab-ingress-provenance")? != SIGNED_PROVENANCE {
        return Err("Computer Runtime requires signed Local Tool Bridge provenance".into());
    }
    let expected_body = required("x-lab-body-sha256")?;
    let actual_body = hex_sha256(body);
    if expected_body != actual_body {
        return Err("signed Bridge body hash does not match Computer Runtime request".into());
    }
    let _request_id = required("x-lab-request-id")?;
    let _nonce = required("x-lab-request-nonce")?;
    let _mac = required("x-lab-request-mac")?;
    Ok(RequestBinding {
        principal_id: required("x-lab-principal-id")?,
        execution_session_id: required("x-lab-execution-session-id")?,
        capability_id: required("x-lab-capability-id")?,
        workspace_id: required("x-lab-workspace-id")?,
    })
}

fn valid_backend_key(headers: &HeaderMap, expected: &[u8; 32]) -> bool {
    let Some(value) = headers
        .get(BACKEND_KEY_HEADER)
        .and_then(|value| value.to_str().ok())
    else {
        return false;
    };
    let digest = Sha256::digest(value.as_bytes());
    digest.as_slice().ct_eq(expected).into()
}

fn hex_sha256(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn tool_result(id: Value, value: Value) -> Response<Body> {
    json_response(
        StatusCode::OK,
        json!({
            "jsonrpc":"2.0",
            "id":id,
            "result":{
                "content":[{"type":"text","text":value.to_string()}],
                "structuredContent":value
            }
        }),
    )
}

fn tool_specs() -> Vec<Value> {
    vec![
        tool_spec(
            "computer_session_open",
            json!({"type":"object","properties":{}}),
        ),
        tool_spec("computer_session_close", session_schema()),
        tool_spec("computer_permissions", session_schema()),
        tool_spec("computer_list_apps", session_schema()),
        tool_spec("computer_list_windows", session_schema()),
        tool_spec("computer_active_window", session_schema()),
        tool_spec(
            "computer_activate_app",
            json!({
                "type":"object",
                "required":["session_id","observation_id","pid","bundle_id"],
                "properties":{
                    "session_id":{"type":"string"},
                    "observation_id":{"type":"string"},
                    "pid":{"type":"integer"},
                    "bundle_id":{"type":"string"}
                }
            }),
        ),
    ]
}

fn session_schema() -> Value {
    json!({
        "type":"object",
        "required":["session_id"],
        "properties":{"session_id":{"type":"string"}}
    })
}

fn tool_spec(name: &str, input_schema: Value) -> Value {
    json!({"name":name,"description":format!("Computer Runtime v0 tool: {name}"),"inputSchema":input_schema})
}

fn json_response(status: StatusCode, value: Value) -> Response<Body> {
    Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(value.to_string()))
        .expect("static response")
}

fn text_response(status: StatusCode, message: &str) -> Response<Body> {
    Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, "text/plain; charset=utf-8")
        .body(Body::from(message.to_string()))
        .expect("static response")
}
