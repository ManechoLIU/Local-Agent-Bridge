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
    collections::{HashMap, VecDeque},
    io::Write,
    process::{Command, Stdio},
    sync::Arc,
    time::{Duration, Instant},
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

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize, Hash)]
pub struct WindowIdentity {
    pub window_id: String,
    pub pid: i32,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize, Hash)]
pub struct ElementIdentity {
    pub element_id: String,
    pub window_id: String,
    pub pid: i32,
}

pub trait ComputerProvider: Send + Sync + 'static {
    fn permissions(&self) -> Result<Value, String>;
    fn list_apps(&self) -> Result<Vec<AppInfo>, String>;
    fn list_windows(&self) -> Result<Value, String>;
    fn active_window(&self) -> Result<Value, String>;
    fn activate_app(&self, pid: i32, bundle_id: &str) -> Result<Value, String>;
    fn screenshot(
        &self,
        window: &WindowIdentity,
        max_width: u32,
        max_height: u32,
    ) -> Result<Value, String> {
        let _ = (window, max_width, max_height);
        Err("screenshot is not implemented by provider".into())
    }
    fn find(
        &self,
        window: &WindowIdentity,
        query: &str,
        max_results: u32,
    ) -> Result<Value, String> {
        let _ = (window, query, max_results);
        Err("find is not implemented by provider".into())
    }
    fn click(&self, element: &ElementIdentity) -> Result<Value, String> {
        let _ = element;
        Err("click is not implemented by provider".into())
    }
    fn type_text(&self, window: &WindowIdentity, text: &str) -> Result<Value, String> {
        let _ = (window, text);
        Err("type is not implemented by provider".into())
    }
    fn key(
        &self,
        window: &WindowIdentity,
        key: &str,
        modifiers: &[String],
    ) -> Result<Value, String> {
        let _ = (window, key, modifiers);
        Err("key is not implemented by provider".into())
    }
    fn scroll(&self, window: &WindowIdentity, delta_x: i32, delta_y: i32) -> Result<Value, String> {
        let _ = (window, delta_x, delta_y);
        Err("scroll is not implemented by provider".into())
    }
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

    fn helper_stdin(&self, args: &[&str], input: &[u8]) -> Result<Value, String> {
        #[cfg(target_os = "macos")]
        {
            let mut child = Command::new(env!("COMPUTER_RUNTIME_MACOS_HELPER"))
                .args(args)
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .spawn()
                .map_err(|e| format!("macOS helper launch failed: {e}"))?;
            child
                .stdin
                .take()
                .ok_or("macOS helper stdin unavailable")?
                .write_all(input)
                .map_err(|e| format!("macOS helper stdin failed: {e}"))?;
            let output = child
                .wait_with_output()
                .map_err(|e| format!("macOS helper wait failed: {e}"))?;
            if !output.status.success() {
                return Err(format!(
                    "macOS helper failed: {}",
                    String::from_utf8_lossy(&output.stderr)
                ));
            }
            let value: Value = serde_json::from_slice(&output.stdout)
                .map_err(|e| format!("macOS helper returned invalid JSON: {e}"))?;
            if value.get("ok").and_then(Value::as_bool) != Some(true) {
                return Err(value
                    .get("error")
                    .and_then(Value::as_str)
                    .unwrap_or("macOS helper failed")
                    .into());
            }
            Ok(value.get("result").cloned().unwrap_or(Value::Null))
        }
        #[cfg(not(target_os = "macos"))]
        {
            let _ = (args, input);
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
    fn screenshot(&self, w: &WindowIdentity, mw: u32, mh: u32) -> Result<Value, String> {
        self.helper(&[
            "screenshot",
            &w.window_id,
            &w.pid.to_string(),
            &mw.to_string(),
            &mh.to_string(),
        ])
    }
    fn find(&self, w: &WindowIdentity, q: &str, max: u32) -> Result<Value, String> {
        self.helper_stdin(
            &["find", &w.window_id, &w.pid.to_string(), &max.to_string()],
            q.as_bytes(),
        )
    }
    fn click(&self, e: &ElementIdentity) -> Result<Value, String> {
        self.helper(&["click", &e.element_id, &e.window_id, &e.pid.to_string()])
    }
    fn type_text(&self, w: &WindowIdentity, text: &str) -> Result<Value, String> {
        self.helper_stdin(&["type", &w.window_id, &w.pid.to_string()], text.as_bytes())
    }
    fn key(&self, w: &WindowIdentity, key: &str, modifiers: &[String]) -> Result<Value, String> {
        self.helper(&[
            "key",
            &w.window_id,
            &w.pid.to_string(),
            key,
            &modifiers.join(","),
        ])
    }
    fn scroll(&self, w: &WindowIdentity, dx: i32, dy: i32) -> Result<Value, String> {
        self.helper(&[
            "scroll",
            &w.window_id,
            &w.pid.to_string(),
            &dx.to_string(),
            &dy.to_string(),
        ])
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
    observations: VecDeque<Observation>,
}

#[derive(Clone, Debug)]
struct Observation {
    id: String,
    created: Instant,
    apps: Vec<AppIdentity>,
    windows: Vec<WindowIdentity>,
    elements: Vec<ElementIdentity>,
}

struct RuntimeInner<P: ComputerProvider> {
    backend_key_sha256: [u8; 32],
    provider: P,
    sessions: Mutex<HashMap<String, ComputerSession>>,
    observation_ttl: Duration,
    max_observations: usize,
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
                observation_ttl: Duration::from_secs(30),
                max_observations: 32,
            }),
        })
    }

    pub fn new_with_observation_limits(
        backend_key: &[u8],
        provider: P,
        ttl: Duration,
        max: usize,
    ) -> Result<Self, String> {
        if ttl.is_zero() || max == 0 {
            return Err("observation limits must be non-zero".into());
        }
        let runtime = Self::new(backend_key, provider)?;
        let mut inner = Arc::try_unwrap(runtime.inner).map_err(|_| "unexpected shared runtime")?;
        inner.observation_ttl = ttl;
        inner.max_observations = max;
        Ok(Self {
            inner: Arc::new(inner),
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
                    observations: VecDeque::new(),
                },
            );
            Ok(json!({"session_id":session_id,"generation":1}))
        }
        "computer_session_close" => {
            let session_id = string_arg(arguments, "session_id")?;
            let generation = generation_arg(arguments)?;
            let mut sessions = runtime.inner.sessions.lock().await;
            ensure_session_owner(&sessions, session_id, generation, binding)?;
            sessions.remove(session_id);
            Ok(json!({"closed":true,"session_id":session_id}))
        }
        "computer_permissions" => {
            let session_id = string_arg(arguments, "session_id")?;
            let generation = generation_arg(arguments)?;
            ensure_session(&runtime.inner.sessions, session_id, generation, binding).await?;
            runtime
                .inner
                .provider
                .permissions()
                .map_err(ToolError::Provider)
        }
        "computer_list_apps" => {
            let session_id = string_arg(arguments, "session_id")?;
            let generation = generation_arg(arguments)?;
            ensure_session(&runtime.inner.sessions, session_id, generation, binding).await?;
            let apps = runtime
                .inner
                .provider
                .list_apps()
                .map_err(ToolError::Provider)?;
            let observation_id = format!("obs_{}", Uuid::new_v4());
            let identities: Vec<_> = apps
                .iter()
                .map(|app| AppIdentity {
                    pid: app.pid,
                    bundle_id: app.bundle_id.clone(),
                })
                .collect();
            store_observation(
                runtime,
                session_id,
                generation,
                binding,
                Observation {
                    id: observation_id.clone(),
                    created: Instant::now(),
                    apps: identities,
                    windows: vec![],
                    elements: vec![],
                },
            )
            .await?;
            Ok(json!({"session_id":session_id,"observation_id":observation_id,"apps":apps}))
        }
        "computer_list_windows" => {
            let session_id = string_arg(arguments, "session_id")?;
            let generation = generation_arg(arguments)?;
            ensure_session(&runtime.inner.sessions, session_id, generation, binding).await?;
            let windows = runtime
                .inner
                .provider
                .list_windows()
                .map_err(ToolError::Provider)?;
            let identities = window_identities(&windows)?;
            let observation_id = format!("obs_{}", Uuid::new_v4());
            store_observation(
                runtime,
                session_id,
                generation,
                binding,
                Observation {
                    id: observation_id.clone(),
                    created: Instant::now(),
                    apps: vec![],
                    windows: identities,
                    elements: vec![],
                },
            )
            .await?;
            Ok(json!({"session_id":session_id,"observation_id":observation_id,"windows":windows}))
        }
        "computer_active_window" => {
            let session_id = string_arg(arguments, "session_id")?;
            let generation = generation_arg(arguments)?;
            ensure_session(&runtime.inner.sessions, session_id, generation, binding).await?;
            let window = runtime
                .inner
                .provider
                .active_window()
                .map_err(ToolError::Provider)?;
            let identities = if window.is_null() {
                vec![]
            } else {
                window_identities(&json!([window.clone()]))?
            };
            let observation_id = format!("obs_{}", Uuid::new_v4());
            store_observation(
                runtime,
                session_id,
                generation,
                binding,
                Observation {
                    id: observation_id.clone(),
                    created: Instant::now(),
                    apps: vec![],
                    windows: identities,
                    elements: vec![],
                },
            )
            .await?;
            Ok(json!({"session_id":session_id,"observation_id":observation_id,"window":window}))
        }
        "computer_activate_app" => {
            let session_id = string_arg(arguments, "session_id")?;
            let generation = generation_arg(arguments)?;
            let observation_id = string_arg(arguments, "observation_id")?;
            let pid = i32_arg(arguments, "pid")?;
            let bundle_id = string_arg(arguments, "bundle_id")?;
            let sessions = runtime.inner.sessions.lock().await;
            let session = ensure_session_owner(&sessions, session_id, generation, binding)?;
            let observed =
                fresh_observation(session, observation_id, runtime.inner.observation_ttl)?;
            if !observed.apps.contains(&AppIdentity {
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
        "computer_screenshot"
        | "computer_find"
        | "computer_type"
        | "computer_key"
        | "computer_scroll" => {
            let session_id = string_arg(arguments, "session_id")?;
            let generation = generation_arg(arguments)?;
            let observation_id = string_arg(arguments, "observation_id")?;
            let window = WindowIdentity {
                window_id: string_arg(arguments, "window_id")?.into(),
                pid: i32_arg(arguments, "pid")?,
            };
            {
                let sessions = runtime.inner.sessions.lock().await;
                let session = ensure_session_owner(&sessions, session_id, generation, binding)?;
                let observed =
                    fresh_observation(session, observation_id, runtime.inner.observation_ttl)?;
                if !observed.windows.contains(&window) {
                    return Err(ToolError::Forbidden(
                        "target window was not present in the referenced session observation"
                            .into(),
                    ));
                }
            }
            match name {
                "computer_screenshot" => {
                    require_provider_permission(runtime, "screen_recording")?;
                    runtime
                        .inner
                        .provider
                        .screenshot(
                            &window,
                            bounded_u32(arguments, "max_width", 1, 4096)?,
                            bounded_u32(arguments, "max_height", 1, 4096)?,
                        )
                        .map_err(ToolError::Provider)
                }
                "computer_find" => {
                    require_provider_permission(runtime, "accessibility")?;
                    let query = bounded_text(arguments, "query", 256)?;
                    let found = runtime
                        .inner
                        .provider
                        .find(
                            &window,
                            query,
                            bounded_u32(arguments, "max_results", 1, 100)?,
                        )
                        .map_err(ToolError::Provider)?;
                    let elements = element_identities(&found, &window)?;
                    let new_id = format!("obs_{}", Uuid::new_v4());
                    store_observation(
                        runtime,
                        session_id,
                        generation,
                        binding,
                        Observation {
                            id: new_id.clone(),
                            created: Instant::now(),
                            apps: vec![],
                            windows: vec![window],
                            elements,
                        },
                    )
                    .await?;
                    Ok(json!({"session_id":session_id,"observation_id":new_id,"elements":found}))
                }
                "computer_type" => {
                    require_provider_permission(runtime, "accessibility")?;
                    runtime
                        .inner
                        .provider
                        .type_text(&window, bounded_text(arguments, "text", 4096)?)
                        .map_err(ToolError::Provider)
                }
                "computer_key" => {
                    require_provider_permission(runtime, "accessibility")?;
                    let key = allowed_key(arguments)?;
                    let mods = allowed_modifiers(arguments)?;
                    runtime
                        .inner
                        .provider
                        .key(&window, key, &mods)
                        .map_err(ToolError::Provider)
                }
                _ => {
                    require_provider_permission(runtime, "accessibility")?;
                    runtime
                        .inner
                        .provider
                        .scroll(
                            &window,
                            bounded_i32(arguments, "delta_x", -2000, 2000)?,
                            bounded_i32(arguments, "delta_y", -2000, 2000)?,
                        )
                        .map_err(ToolError::Provider)
                }
            }
        }
        "computer_click" => {
            let session_id = string_arg(arguments, "session_id")?;
            let generation = generation_arg(arguments)?;
            let observation_id = string_arg(arguments, "observation_id")?;
            let element = ElementIdentity {
                element_id: string_arg(arguments, "element_id")?.into(),
                window_id: string_arg(arguments, "window_id")?.into(),
                pid: i32_arg(arguments, "pid")?,
            };
            {
                let sessions = runtime.inner.sessions.lock().await;
                let session = ensure_session_owner(&sessions, session_id, generation, binding)?;
                let observed =
                    fresh_observation(session, observation_id, runtime.inner.observation_ttl)?;
                if !observed.elements.contains(&element) {
                    return Err(ToolError::Forbidden(
                        "target element was not present in the referenced session observation"
                            .into(),
                    ));
                }
            }
            require_provider_permission(runtime, "accessibility")?;
            runtime
                .inner
                .provider
                .click(&element)
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
    generation: u64,
    binding: &RequestBinding,
) -> Result<(), ToolError> {
    let sessions = sessions.lock().await;
    ensure_session_owner(&sessions, session_id, generation, binding).map(|_| ())
}

fn ensure_session_owner<'a>(
    sessions: &'a HashMap<String, ComputerSession>,
    session_id: &str,
    generation: u64,
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
    if session.generation != generation {
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

fn generation_arg(arguments: &Value) -> Result<u64, ToolError> {
    arguments
        .get("expected_session_generation")
        .and_then(Value::as_u64)
        .ok_or_else(|| {
            ToolError::BadRequest("missing argument: expected_session_generation".into())
        })
}

fn bounded_text<'a>(arguments: &'a Value, name: &str, max: usize) -> Result<&'a str, ToolError> {
    let value = string_arg(arguments, name)?;
    if value.chars().count() > max {
        return Err(ToolError::BadRequest(format!(
            "argument exceeds {max} characters: {name}"
        )));
    }
    Ok(value)
}
fn bounded_u32(arguments: &Value, name: &str, min: u32, max: u32) -> Result<u32, ToolError> {
    let n = arguments
        .get(name)
        .and_then(Value::as_u64)
        .ok_or_else(|| ToolError::BadRequest(format!("missing argument: {name}")))?;
    let n =
        u32::try_from(n).map_err(|_| ToolError::BadRequest(format!("invalid argument: {name}")))?;
    if !(min..=max).contains(&n) {
        return Err(ToolError::BadRequest(format!(
            "argument out of bounds: {name}"
        )));
    }
    Ok(n)
}
fn bounded_i32(arguments: &Value, name: &str, min: i32, max: i32) -> Result<i32, ToolError> {
    let n = i32_arg(arguments, name)?;
    if !(min..=max).contains(&n) {
        return Err(ToolError::BadRequest(format!(
            "argument out of bounds: {name}"
        )));
    }
    Ok(n)
}
fn allowed_key(arguments: &Value) -> Result<&str, ToolError> {
    let k = string_arg(arguments, "key")?;
    const KEYS: &[&str] = &[
        "enter",
        "tab",
        "escape",
        "space",
        "backspace",
        "delete",
        "up",
        "down",
        "left",
        "right",
        "home",
        "end",
        "pageup",
        "pagedown",
        "a",
        "c",
        "v",
        "x",
        "z",
    ];
    if !KEYS.contains(&k) {
        return Err(ToolError::BadRequest("unsupported key".into()));
    }
    Ok(k)
}
fn allowed_modifiers(arguments: &Value) -> Result<Vec<String>, ToolError> {
    let values = arguments
        .get("modifiers")
        .and_then(Value::as_array)
        .ok_or_else(|| ToolError::BadRequest("missing argument: modifiers".into()))?;
    if values.len() > 4 {
        return Err(ToolError::BadRequest("too many modifiers".into()));
    }
    let mut out = vec![];
    for v in values {
        let s = v
            .as_str()
            .ok_or_else(|| ToolError::BadRequest("invalid modifier".into()))?;
        if !["command", "control", "option", "shift"].contains(&s) || out.iter().any(|x| x == s) {
            return Err(ToolError::BadRequest(
                "unsupported or duplicate modifier".into(),
            ));
        }
        out.push(s.into());
    }
    Ok(out)
}

fn window_identities(value: &Value) -> Result<Vec<WindowIdentity>, ToolError> {
    value
        .as_array()
        .ok_or_else(|| ToolError::Provider("window inventory is not an array".into()))?
        .iter()
        .map(|v| {
            Ok(WindowIdentity {
                window_id: v
                    .get("window_id")
                    .and_then(Value::as_str)
                    .ok_or_else(|| ToolError::Provider("window is missing window_id".into()))?
                    .into(),
                pid: i32::try_from(
                    v.get("pid")
                        .and_then(Value::as_i64)
                        .ok_or_else(|| ToolError::Provider("window is missing pid".into()))?,
                )
                .map_err(|_| ToolError::Provider("invalid window pid".into()))?,
            })
        })
        .collect()
}
fn element_identities(
    value: &Value,
    window: &WindowIdentity,
) -> Result<Vec<ElementIdentity>, ToolError> {
    value
        .as_array()
        .ok_or_else(|| ToolError::Provider("find result is not an array".into()))?
        .iter()
        .map(|v| {
            let e = ElementIdentity {
                element_id: v
                    .get("element_id")
                    .and_then(Value::as_str)
                    .ok_or_else(|| ToolError::Provider("element is missing element_id".into()))?
                    .into(),
                window_id: v
                    .get("window_id")
                    .and_then(Value::as_str)
                    .ok_or_else(|| ToolError::Provider("element is missing window_id".into()))?
                    .into(),
                pid: i32::try_from(
                    v.get("pid")
                        .and_then(Value::as_i64)
                        .ok_or_else(|| ToolError::Provider("element is missing pid".into()))?,
                )
                .map_err(|_| ToolError::Provider("invalid element pid".into()))?,
            };
            if e.window_id != window.window_id || e.pid != window.pid {
                return Err(ToolError::Provider(
                    "provider returned element outside target window".into(),
                ));
            }
            Ok(e)
        })
        .collect()
}
fn fresh_observation<'a>(
    session: &'a ComputerSession,
    id: &str,
    ttl: Duration,
) -> Result<&'a Observation, ToolError> {
    let o = session
        .observations
        .iter()
        .find(|o| o.id == id)
        .ok_or_else(|| {
            ToolError::Forbidden("observation does not belong to this Computer session".into())
        })?;
    if o.created.elapsed() > ttl {
        return Err(ToolError::Forbidden("Computer observation expired".into()));
    }
    Ok(o)
}
async fn store_observation<P: ComputerProvider>(
    runtime: &ComputerRuntime<P>,
    sid: &str,
    generation: u64,
    binding: &RequestBinding,
    observation: Observation,
) -> Result<(), ToolError> {
    let mut sessions = runtime.inner.sessions.lock().await;
    let session = ensure_session_owner(&sessions, sid, generation, binding)?;
    let mut updated = session.clone();
    while updated.observations.len() >= runtime.inner.max_observations {
        updated.observations.pop_front();
    }
    updated.observations.push_back(observation);
    sessions.insert(sid.into(), updated);
    Ok(())
}

fn require_provider_permission<P: ComputerProvider>(
    runtime: &ComputerRuntime<P>,
    name: &str,
) -> Result<(), ToolError> {
    let permissions = runtime
        .inner
        .provider
        .permissions()
        .map_err(ToolError::Provider)?;
    if permissions.get(name).and_then(Value::as_bool) != Some(true) {
        return Err(ToolError::Forbidden(format!(
            "Computer Runtime permission unavailable: {name}"
        )));
    }
    Ok(())
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
                "required":["session_id","expected_session_generation","observation_id","pid","bundle_id"],
                "properties":{
                    "session_id":{"type":"string"},
                    "expected_session_generation":{"type":"integer","minimum":1},
                    "observation_id":{"type":"string"},
                    "pid":{"type":"integer"},
                    "bundle_id":{"type":"string"}
                }
            }),
        ),
        tool_spec(
            "computer_screenshot",
            window_schema(
                json!({"max_width":{"type":"integer","minimum":1,"maximum":4096},"max_height":{"type":"integer","minimum":1,"maximum":4096}}),
                &["max_width", "max_height"],
            ),
        ),
        tool_spec(
            "computer_find",
            window_schema(
                json!({"query":{"type":"string","maxLength":256},"max_results":{"type":"integer","minimum":1,"maximum":100}}),
                &["query", "max_results"],
            ),
        ),
        tool_spec(
            "computer_click",
            json!({"type":"object","required":["session_id","expected_session_generation","observation_id","element_id","window_id","pid"],"properties":{"session_id":{"type":"string"},"expected_session_generation":{"type":"integer"},"observation_id":{"type":"string"},"element_id":{"type":"string"},"window_id":{"type":"string"},"pid":{"type":"integer"}}}),
        ),
        tool_spec(
            "computer_type",
            window_schema(
                json!({"text":{"type":"string","maxLength":4096}}),
                &["text"],
            ),
        ),
        tool_spec(
            "computer_key",
            window_schema(
                json!({"key":{"type":"string"},"modifiers":{"type":"array","maxItems":4,"items":{"type":"string"}}}),
                &["key", "modifiers"],
            ),
        ),
        tool_spec(
            "computer_scroll",
            window_schema(
                json!({"delta_x":{"type":"integer","minimum":-2000,"maximum":2000},"delta_y":{"type":"integer","minimum":-2000,"maximum":2000}}),
                &["delta_x", "delta_y"],
            ),
        ),
    ]
}

fn session_schema() -> Value {
    json!({
        "type":"object",
        "required":["session_id","expected_session_generation"],
        "properties":{"session_id":{"type":"string"},"expected_session_generation":{"type":"integer","minimum":1}}
    })
}

fn window_schema(extra: Value, extra_required: &[&str]) -> Value {
    let mut properties = json!({"session_id":{"type":"string"},"expected_session_generation":{"type":"integer","minimum":1},"observation_id":{"type":"string"},"window_id":{"type":"string"},"pid":{"type":"integer"}});
    properties
        .as_object_mut()
        .unwrap()
        .extend(extra.as_object().unwrap().clone());
    let mut required = vec![
        "session_id",
        "expected_session_generation",
        "observation_id",
        "window_id",
        "pid",
    ];
    required.extend_from_slice(extra_required);
    json!({"type":"object","required":required,"properties":properties})
}

fn tool_spec(name: &str, input_schema: Value) -> Value {
    json!({"name":name,"description":format!("Computer Runtime v1 tool: {name}"),"inputSchema":input_schema})
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
