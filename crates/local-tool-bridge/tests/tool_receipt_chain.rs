use axum::{
    Router,
    body::{Body, Bytes},
    extract::State,
    http::{HeaderMap, Request, StatusCode},
    routing::post,
};
use bridge_security_core::{
    Capability, CapabilityAuthority, CapabilityGrant, ExecutionContext, RequestProof,
};
use local_tool_bridge::{
    BridgeIngress, ProductionReleaseFence, ToolPolicy, ToolReceiptJournal, ToolReceiptState,
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::HashMap,
    io::{BufRead, BufReader, Read, Write},
    net::TcpListener,
    os::unix::{
        fs::{PermissionsExt, symlink},
        net::UnixListener,
    },
    path::{Path, PathBuf},
    sync::{
        Arc, Mutex as StdMutex,
        atomic::{AtomicUsize, Ordering},
    },
    thread,
    time::Duration,
};
use tokio::sync::Mutex;
use tower::ServiceExt;

const BACKEND_KEY: &str = "receipt-backend-0123456789abcdef0123456789abcdef";

#[derive(Clone, Default)]
struct BackendCapture(Arc<Mutex<Vec<(HeaderMap, Bytes)>>>);

async fn backend(
    State(capture): State<BackendCapture>,
    headers: HeaderMap,
    body: Bytes,
) -> (StatusCode, axum::Json<Value>) {
    capture.0.lock().await.push((headers, body.clone()));
    let parsed: Value = serde_json::from_slice(&body).unwrap();
    (
        StatusCode::OK,
        axum::Json(json!({
            "jsonrpc": "2.0",
            "id": parsed["id"],
            "result": {"content": [{"type":"text", "text":"done"}]}
        })),
    )
}

async fn wrong_id_backend(
    State(capture): State<BackendCapture>,
    headers: HeaderMap,
    body: Bytes,
) -> (StatusCode, axum::Json<Value>) {
    capture.0.lock().await.push((headers, body));
    (
        StatusCode::OK,
        axum::Json(json!({"jsonrpc":"2.0","id":"caller-controlled","result":{}})),
    )
}

async fn backend_server(capture: BackendCapture, wrong_id: bool) -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let app = if wrong_id {
        Router::new()
            .route("/mcp", post(wrong_id_backend))
            .with_state(capture)
    } else {
        Router::new()
            .route("/mcp", post(backend))
            .with_state(capture)
    };
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    format!("http://127.0.0.1:{}/mcp", addr.port())
}

#[derive(Clone, Copy)]
enum BackendProfile {
    MissingResultAndError,
    ResultAndError,
    WrongVersion,
    ScalarError,
    DuplicateResult,
    UnexpectedMember,
    LegalNumbers,
    LegalError,
}

async fn profile_backend(
    State((capture, profile)): State<(BackendCapture, BackendProfile)>,
    headers: HeaderMap,
    body: Bytes,
) -> axum::response::Response {
    capture.0.lock().await.push((headers, body.clone()));
    let parsed: Value = serde_json::from_slice(&body).unwrap();
    let id = serde_json::to_string(&parsed["id"]).unwrap();
    let raw = match profile {
        BackendProfile::MissingResultAndError => format!(r#"{{"jsonrpc":"2.0","id":{id}}}"#),
        BackendProfile::ResultAndError => format!(
            r#"{{"jsonrpc":"2.0","id":{id},"result":{{}},"error":{{"code":-1,"message":"bad"}}}}"#
        ),
        BackendProfile::WrongVersion => {
            format!(r#"{{"jsonrpc":"1.0","id":{id},"result":{{}}}}"#)
        }
        BackendProfile::ScalarError => {
            format!(r#"{{"jsonrpc":"2.0","id":{id},"error":"bad"}}"#)
        }
        BackendProfile::DuplicateResult => format!(
            r#"{{"jsonrpc":"2.0","id":{id},"result":{{"first":true}},"result":{{"second":true}}}}"#
        ),
        BackendProfile::UnexpectedMember => {
            format!(r#"{{"jsonrpc":"2.0","id":{id},"result":{{}},"unexpected":true}}"#)
        }
        BackendProfile::LegalNumbers => {
            format!(r#"{{"jsonrpc":"2.0","id":{id},"result":{{"fraction":1.25,"exponent":1e10}}}}"#)
        }
        BackendProfile::LegalError => format!(
            r#"{{"jsonrpc":"2.0","id":{id},"error":{{"code":-32000,"message":"backend error","data":{{"fraction":1.25}}}}}}"#
        ),
    };
    axum::response::Response::builder()
        .status(StatusCode::OK)
        .header("content-type", "application/json")
        .body(Body::from(raw))
        .unwrap()
}

async fn profile_backend_server(capture: BackendCapture, profile: BackendProfile) -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let app = Router::new()
        .route("/mcp", post(profile_backend))
        .with_state((capture, profile));
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    format!("http://127.0.0.1:{}/mcp", addr.port())
}

#[derive(Clone, Default)]
struct GatedBackend {
    capture: BackendCapture,
    started: Arc<tokio::sync::Notify>,
    release: Arc<tokio::sync::Notify>,
}

async fn gated_backend(
    State(state): State<GatedBackend>,
    headers: HeaderMap,
    body: Bytes,
) -> (StatusCode, axum::Json<Value>) {
    state.capture.0.lock().await.push((headers, body.clone()));
    state.started.notify_one();
    state.release.notified().await;
    let parsed: Value = serde_json::from_slice(&body).unwrap();
    (
        StatusCode::OK,
        axum::Json(json!({"jsonrpc":"2.0","id":parsed["id"],"result":{"ok":true}})),
    )
}

async fn gated_backend_server(state: GatedBackend) -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let app = Router::new()
        .route("/mcp", post(gated_backend))
        .with_state(state);
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    format!("http://127.0.0.1:{}/mcp", addr.port())
}

fn disconnect_backend_server(calls: Arc<AtomicUsize>) -> String {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut bytes = [0_u8; 4096];
        let _ = stream.read(&mut bytes);
        calls.fetch_add(1, Ordering::SeqCst);
    });
    format!("http://127.0.0.1:{}/mcp", address.port())
}

fn hanging_backend_server(calls: Arc<AtomicUsize>) -> String {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut bytes = [0_u8; 4096];
        let _ = stream.read(&mut bytes);
        calls.fetch_add(1, Ordering::SeqCst);
        thread::sleep(Duration::from_secs(4));
    });
    format!("http://127.0.0.1:{}/mcp", address.port())
}

fn fence() -> ProductionReleaseFence {
    ProductionReleaseFence {
        contract_version: "lab-production-connector-v1".into(),
        release_revision: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa".into(),
        endpoint_generation: 7,
        workspace: "/Users/echoman/Documents/ChatGPT/Local-Agent-Bridge".into(),
        tunnel_profile_name: "local-agent-bridge-production".into(),
    }
}

fn bridge(
    backend_url: &str,
    state_dir: &Path,
    host_socket: &Path,
    runtime_socket: Option<&Path>,
) -> BridgeIngress {
    let context = ExecutionContext {
        principal_id: "bridge-principal".into(),
        execution_session_id: "bridge-session".into(),
        capability_id: "bridge-capability".into(),
        workspace_id: "local-agent-bridge-production".into(),
    };
    let grant = CapabilityGrant {
        principal_id: context.principal_id.clone(),
        execution_session_id: context.execution_session_id.clone(),
        capability_id: context.capability_id.clone(),
        workspace_id: context.workspace_id.clone(),
        capabilities: vec![Capability::Shell],
    };
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as u64;
    let mut authority = CapabilityAuthority::new_for_test([41; 32]);
    let issued = authority.issue(grant, now - 1000, 60_000, 1).unwrap();
    authority.save_private(state_dir).unwrap();
    BridgeIngress::new_trusted_unix_socket(
        authority,
        issued,
        context,
        backend_url,
        BACKEND_KEY,
        ToolPolicy::catdesk_default(),
        state_dir,
    )
    .unwrap()
    .with_tool_receipt_chain(host_socket, runtime_socket, fence())
    .unwrap()
}

fn restarted_bridge(
    backend_url: &str,
    state_dir: &Path,
    host_socket: &Path,
    runtime_socket: Option<&Path>,
) -> BridgeIngress {
    let authority = CapabilityAuthority::load_or_create_private(state_dir).unwrap();
    let lease = authority.lease("bridge-capability").unwrap();
    let context = ExecutionContext {
        principal_id: lease.grant.principal_id.clone(),
        execution_session_id: lease.grant.execution_session_id.clone(),
        capability_id: lease.grant.capability_id.clone(),
        workspace_id: lease.grant.workspace_id.clone(),
    };
    let issued = authority.issued_capability("bridge-capability").unwrap();
    BridgeIngress::new_trusted_unix_socket(
        authority,
        issued,
        context,
        backend_url,
        BACKEND_KEY,
        ToolPolicy::catdesk_default(),
        state_dir,
    )
    .unwrap()
    .with_tool_receipt_chain(host_socket, runtime_socket, fence())
    .unwrap()
}

fn legacy_bridge(backend_url: &str, state_dir: &Path) -> BridgeIngress {
    let context = ExecutionContext {
        principal_id: "bridge-principal".into(),
        execution_session_id: "bridge-session".into(),
        capability_id: "bridge-capability".into(),
        workspace_id: "local-agent-bridge-production".into(),
    };
    let grant = CapabilityGrant {
        principal_id: context.principal_id.clone(),
        execution_session_id: context.execution_session_id.clone(),
        capability_id: context.capability_id.clone(),
        workspace_id: context.workspace_id.clone(),
        capabilities: vec![Capability::Shell],
    };
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as u64;
    let mut authority = CapabilityAuthority::new_for_test([41; 32]);
    let issued = authority.issue(grant, now - 1000, 60_000, 1).unwrap();
    authority.save_private(state_dir).unwrap();
    BridgeIngress::new_trusted_unix_socket(
        authority,
        issued,
        context,
        backend_url,
        BACKEND_KEY,
        ToolPolicy::catdesk_default(),
        state_dir,
    )
    .unwrap()
}

fn spawn_ndjson_server<F>(path: PathBuf, calls: usize, handler: F) -> thread::JoinHandle<()>
where
    F: Fn(Value) -> Result<Value, String> + Send + Sync + 'static,
{
    let handler = Arc::new(handler);
    thread::spawn(move || {
        let listener = UnixListener::bind(&path).unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600)).unwrap();
        for _ in 0..calls {
            let (mut stream, _) = listener.accept().unwrap();
            let mut line = String::new();
            BufReader::new(stream.try_clone().unwrap())
                .read_line(&mut line)
                .unwrap();
            let request: Value = serde_json::from_str(line.trim_end()).unwrap();
            let request_id = request["request_id"].clone();
            let response = match handler(request) {
                Ok(result) => json!({"request_id":request_id,"ok":true,"result":result}),
                Err(error) => json!({"request_id":request_id,"ok":false,"error":error}),
            };
            writeln!(stream, "{}", response).unwrap();
        }
    })
}

fn host_handler(log: Arc<StdMutex<Vec<Value>>>) -> impl Fn(Value) -> Result<Value, String> {
    move |request| {
        log.lock().unwrap().push(request.clone());
        let binding_keys = [
            "request_digest_profile",
            "bridge_call_id",
            "bridge_challenge_nonce",
            "transport_binding_id",
            "workspace",
            "production_release_revision",
            "production_endpoint_generation",
            "bridge_principal_id",
            "bridge_execution_session_id",
            "bridge_capability_id",
            "bridge_workspace_id",
            "tool_name",
            "outer_request_sha256",
            "forwarded_request_sha256",
            "normalized_request_sha256",
        ];
        let mut binding = serde_json::Map::new();
        for key in binding_keys {
            binding.insert(key.into(), request[key].clone());
        }
        binding.insert(
            "conversation_id".into(),
            json!("01a08c16-d75f-7342-8cc1-273f30a72325"),
        );
        binding.insert("browser_target_id".into(), json!("target-machine"));
        binding.insert("top_frame_id".into(), json!("frame-machine"));
        binding.insert("loader_id".into(), json!("loader-machine"));
        binding.insert("generation_anchor_sha256".into(), json!("1".repeat(64)));
        binding.insert("secure_origin".into(), json!("https://chatgpt.com"));
        binding.insert("extension_binding_sha256".into(), json!("2".repeat(64)));
        binding.insert("extension_instance_id_sha256".into(), json!("3".repeat(64)));
        binding.insert("extension_loaded_at_ms".into(), json!(100));
        match request["op"].as_str().unwrap() {
            "tool_receipt_issue" => {
                binding.insert("schema_version".into(), json!(1));
                binding.insert(
                    "provenance".into(),
                    json!("lab_host_invocation_capability_v1"),
                );
                binding.insert(
                    "capability_id".into(),
                    json!(format!(
                        "hic_{}",
                        request["bridge_call_id"].as_str().unwrap()
                    )),
                );
                binding.insert("issued_at_unix_ms".into(), json!(100));
                binding.insert("expires_at_unix_ms".into(), json!(9_999_999_999_999u64));
                binding.insert("host_mac_sha256".into(), json!("4".repeat(64)));
                Ok(json!({"capability":Value::Object(binding)}))
            }
            "tool_receipt_consume" => {
                let mut pre = binding;
                pre.insert("schema_version".into(), json!(1));
                pre.insert("provenance".into(), json!("lab_host_tool_pre_receipt_v1"));
                pre.insert(
                    "pre_receipt_id".into(),
                    json!(format!(
                        "hpr_{}",
                        request["bridge_call_id"].as_str().unwrap()
                    )),
                );
                pre.insert(
                    "capability_id".into(),
                    request["capability"]["capability_id"].clone(),
                );
                pre.insert(
                    "host_tool_execution_id".into(),
                    request["host_tool_execution_id"].clone(),
                );
                pre.insert(
                    "retry_outer_request_sha256".into(),
                    request["retry_outer_request_sha256"].clone(),
                );
                pre.insert("pre_nonce".into(), json!("pre-nonce"));
                pre.insert("issued_at_unix_ms".into(), json!(101));
                pre.insert("host_epoch_binding_sha256".into(), json!("5".repeat(64)));
                pre.insert("host_mac_sha256".into(), json!("6".repeat(64)));
                Ok(json!({"pre_receipt":Value::Object(pre)}))
            }
            "tool_receipt_terminal" => {
                binding.insert("schema_version".into(), json!(1));
                binding.insert(
                    "provenance".into(),
                    json!("lab_host_tool_terminal_receipt_v1"),
                );
                binding.insert(
                    "terminal_receipt_id".into(),
                    json!(format!(
                        "htr_{}",
                        request["bridge_call_id"].as_str().unwrap()
                    )),
                );
                binding.insert(
                    "pre_receipt_id".into(),
                    request["pre_receipt"]["pre_receipt_id"].clone(),
                );
                binding.insert(
                    "capability_id".into(),
                    request["pre_receipt"]["capability_id"].clone(),
                );
                binding.insert(
                    "host_tool_execution_id".into(),
                    request["pre_receipt"]["host_tool_execution_id"].clone(),
                );
                binding.insert(
                    "retry_outer_request_sha256".into(),
                    request["retry_outer_request_sha256"].clone(),
                );
                binding.insert(
                    "pre_receipt_sha256".into(),
                    json!(canonical_sha256(&request["pre_receipt"])),
                );
                binding.insert("backend_route".into(), request["backend_route"].clone());
                binding.insert("http_status".into(), request["http_status"].clone());
                binding.insert(
                    "response_body_sha256".into(),
                    request["response_body_sha256"].clone(),
                );
                binding.insert(
                    "terminal_classification".into(),
                    request["terminal_classification"].clone(),
                );
                binding.insert("issued_at_unix_ms".into(), json!(102));
                binding.insert(
                    "host_epoch_binding_sha256".into(),
                    request["pre_receipt"]["host_epoch_binding_sha256"].clone(),
                );
                binding.insert("host_mac_sha256".into(), json!("7".repeat(64)));
                Ok(json!({"terminal_receipt":Value::Object(binding)}))
            }
            other => Err(format!("unexpected Host op {other}")),
        }
    }
}

fn canonical_sha256(value: &Value) -> String {
    fn encode(value: &Value, output: &mut String) {
        match value {
            Value::Null => output.push_str("null"),
            Value::Bool(value) => output.push_str(if *value { "true" } else { "false" }),
            Value::Number(value) => output.push_str(&value.to_string()),
            Value::String(value) => output.push_str(&serde_json::to_string(value).unwrap()),
            Value::Array(values) => {
                output.push('[');
                for (index, value) in values.iter().enumerate() {
                    if index > 0 {
                        output.push(',');
                    }
                    encode(value, output);
                }
                output.push(']');
            }
            Value::Object(values) => {
                output.push('{');
                let mut keys = values.keys().collect::<Vec<_>>();
                keys.sort();
                for (index, key) in keys.into_iter().enumerate() {
                    if index > 0 {
                        output.push(',');
                    }
                    output.push_str(&serde_json::to_string(key).unwrap());
                    output.push(':');
                    encode(&values[key], output);
                }
                output.push('}');
            }
        }
    }
    let mut encoded = String::new();
    encode(value, &mut encoded);
    format!("{:x}", Sha256::digest(encoded.as_bytes()))
}

fn runtime_handler(
    log: Arc<StdMutex<Vec<Value>>>,
    reject_pre: bool,
    reject_terminal: bool,
) -> impl Fn(Value) -> Result<Value, String> {
    move |request| {
        log.lock().unwrap().push(request.clone());
        match request["op"].as_str().unwrap() {
            "pre" if reject_pre => Ok(json!({
                "protocol":"runtime_host_tool_hook_v1","decision":"REJECT",
                "bridge_call_id":request["bridge_call_id"],
                "host_tool_execution_id":request["host_tool_execution_id"]
            })),
            "pre" => Ok(json!({
                "protocol":"runtime_host_tool_hook_v1","decision":"ALLOW",
                "bridge_call_id":request["bridge_call_id"],
                "host_tool_execution_id":request["host_tool_execution_id"]
            })),
            "terminal" if reject_terminal => Ok(json!({
                "protocol":"runtime_host_tool_hook_v1","state":"REJECTED",
                "bridge_call_id":request["bridge_call_id"],
                "host_tool_execution_id":request["host_tool_execution_id"]
            })),
            "terminal" => Ok(json!({
                "protocol":"runtime_host_tool_hook_v1","state":"CLOSED",
                "bridge_call_id":request["bridge_call_id"],
                "host_tool_execution_id":request["host_tool_execution_id"]
            })),
            other => Err(format!("unexpected Runtime op {other}")),
        }
    }
}

fn call(body: String) -> Request<Body> {
    Request::builder()
        .method("POST")
        .uri("/mcp")
        .header("content-type", "application/json")
        .header("mcp-method", "tools/call")
        .header("mcp-name", "run_command")
        .header("x-request-id", "caller-controlled-proof-id")
        .header("mcp-session-id", "caller-controlled-session")
        .body(Body::from(body))
        .unwrap()
}

async fn response_json(response: axum::response::Response) -> Value {
    let bytes = axum::body::to_bytes(response.into_body(), usize::MAX)
        .await
        .unwrap();
    serde_json::from_slice(&bytes).unwrap_or_else(|error| {
        panic!(
            "response is not JSON ({error}): {}",
            String::from_utf8_lossy(&bytes)
        )
    })
}

fn challenge_body(id: &str, command: &str) -> String {
    json!({"jsonrpc":"2.0","id":id,"method":"tools/call","params":{
        "name":"run_command","arguments":{"command":command,"cwd":"/tmp","timeout":30}
    }})
    .to_string()
}

fn challenge_body_with_timeout(id: &str, command: &str, timeout: u64) -> String {
    json!({"jsonrpc":"2.0","id":id,"method":"tools/call","params":{
        "name":"run_command","arguments":{"command":command,"cwd":"/tmp","timeout":timeout}
    }})
    .to_string()
}

fn retry_body(id: &str, command: &str, capability: Value) -> String {
    json!({"jsonrpc":"2.0","id":id,"method":"tools/call","params":{
        "name":"run_command","arguments":{"command":command,"cwd":"/tmp","timeout":30,
        "host_invocation_capability":capability}
    }})
    .to_string()
}

fn retry_body_with_timeout(id: &str, command: &str, timeout: u64, capability: Value) -> String {
    json!({"jsonrpc":"2.0","id":id,"method":"tools/call","params":{
        "name":"run_command","arguments":{"command":command,"cwd":"/tmp","timeout":timeout,
        "host_invocation_capability":capability}
    }})
    .to_string()
}

#[tokio::test]
async fn protected_run_command_requires_then_consumes_host_capability_exactly_once() {
    let root = tempfile::tempdir().unwrap();
    let host_path = root.path().join("host.sock");
    let runtime_path = root.path().join("runtime.sock");
    let host_log = Arc::new(StdMutex::new(Vec::new()));
    let runtime_log = Arc::new(StdMutex::new(Vec::new()));
    let _host_thread = spawn_ndjson_server(host_path.clone(), 3, host_handler(host_log.clone()));
    let _runtime_thread = spawn_ndjson_server(
        runtime_path.clone(),
        2,
        runtime_handler(runtime_log.clone(), false, false),
    );
    while !host_path.exists() || !runtime_path.exists() {
        tokio::task::yield_now().await;
    }
    let backend_capture = BackendCapture::default();
    let backend_url = backend_server(backend_capture.clone(), false).await;
    let app = bridge(&backend_url, root.path(), &host_path, Some(&runtime_path)).router();

    let first = app
        .clone()
        .oneshot(call(challenge_body("caller-1", "printf ok")))
        .await
        .unwrap();
    assert_eq!(first.status(), StatusCode::OK);
    let challenge = response_json(first).await;
    assert_eq!(challenge["id"], "caller-1");
    assert_eq!(
        challenge["error"]["data"]["code"],
        "HOST_INVOCATION_CAPABILITY_REQUIRED"
    );
    assert_eq!(backend_capture.0.lock().await.len(), 0);
    let capability = challenge["error"]["data"]["host_invocation_capability"].clone();

    let retry_raw = retry_body("caller-2", "printf ok", capability);
    let second = tokio::time::timeout(
        Duration::from_secs(10),
        app.oneshot(call(retry_raw.clone())),
    )
    .await
    .expect("protected retry timed out")
    .unwrap();
    assert_eq!(second.status(), StatusCode::OK);
    let result = response_json(second).await;
    assert_eq!(result["id"], "caller-2");
    assert_eq!(backend_capture.0.lock().await.len(), 1);

    let (headers, forwarded) = backend_capture.0.lock().await[0].clone();
    let forwarded_json: Value = serde_json::from_slice(&forwarded).unwrap();
    let bridge_call_id = forwarded_json["id"].as_str().unwrap();
    assert!(bridge_call_id.starts_with("bc_"));
    assert!(
        forwarded_json["params"]["arguments"]
            .get("host_invocation_capability")
            .is_none()
    );
    assert_eq!(
        String::from_utf8(forwarded.to_vec()).unwrap(),
        format!(
            r#"{{"id":"{bridge_call_id}","jsonrpc":"2.0","method":"tools/call","params":{{"arguments":{{"command":"printf ok","cwd":"/tmp","timeout":30}},"name":"run_command"}}}}"#
        )
    );
    let host_calls = host_log.lock().unwrap().clone();
    assert_eq!(
        headers["x-lab-bridge-execution-id"],
        host_calls[1]["host_tool_execution_id"].as_str().unwrap()
    );
    assert_eq!(
        headers["x-lab-body-sha256"],
        host_calls[0]["forwarded_request_sha256"].as_str().unwrap()
    );
    assert_ne!(
        host_calls[0]["outer_request_sha256"],
        host_calls[1]["retry_outer_request_sha256"]
    );
    assert_eq!(
        host_calls[0]["forwarded_request_sha256"],
        format!("{:x}", Sha256::digest(&forwarded))
    );
    let proof_headers = [
        "x-lab-capability-id",
        "x-lab-principal-id",
        "x-lab-execution-session-id",
        "x-lab-workspace-id",
        "x-lab-bridge-execution-id",
        "x-lab-request-id",
        "x-lab-request-nonce",
        "x-lab-body-sha256",
        "x-lab-ingress-provenance",
        "x-lab-request-mac",
    ]
    .into_iter()
    .map(|name| {
        (
            name.to_string(),
            headers[name].to_str().unwrap().to_string(),
        )
    })
    .collect::<HashMap<_, _>>();
    let proof = RequestProof::from_headers(&proof_headers, "tools/call", "run_command").unwrap();
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as u64;
    let mut exact_verifier = CapabilityAuthority::load_or_create_private(root.path()).unwrap();
    exact_verifier
        .verify_request(
            &proof,
            &forwarded,
            "tools/call",
            "run_command",
            Capability::Shell,
            now,
        )
        .unwrap();
    let mut retry_verifier = CapabilityAuthority::load_or_create_private(root.path()).unwrap();
    assert!(
        retry_verifier
            .verify_request(
                &proof,
                retry_raw.as_bytes(),
                "tools/call",
                "run_command",
                Capability::Shell,
                now,
            )
            .is_err()
    );
    let mut mutated_forwarded = forwarded.to_vec();
    mutated_forwarded.push(b' ');
    let mut mutation_verifier = CapabilityAuthority::load_or_create_private(root.path()).unwrap();
    assert!(
        mutation_verifier
            .verify_request(
                &proof,
                &mutated_forwarded,
                "tools/call",
                "run_command",
                Capability::Shell,
                now,
            )
            .is_err()
    );
    assert_eq!(
        runtime_log
            .lock()
            .unwrap()
            .iter()
            .map(|v| v["op"].as_str().unwrap())
            .collect::<Vec<_>>(),
        ["pre", "terminal"]
    );
    assert_eq!(
        host_calls
            .iter()
            .map(|v| v["op"].as_str().unwrap())
            .collect::<Vec<_>>(),
        [
            "tool_receipt_issue",
            "tool_receipt_consume",
            "tool_receipt_terminal"
        ]
    );

    let journal = ToolReceiptJournal::load(root.path().join("tool-receipt-journal.json")).unwrap();
    assert_eq!(
        journal.state(bridge_call_id),
        Some(ToolReceiptState::Terminal)
    );
}

#[tokio::test]
async fn changed_retry_duplicate_keys_null_capability_and_identity_injection_never_dispatch() {
    let root = tempfile::tempdir().unwrap();
    let host_path = root.path().join("host.sock");
    let host_log = Arc::new(StdMutex::new(Vec::new()));
    let host_thread = spawn_ndjson_server(host_path.clone(), 1, host_handler(host_log));
    while !host_path.exists() {
        tokio::task::yield_now().await;
    }
    let backend_capture = BackendCapture::default();
    let backend_url = backend_server(backend_capture.clone(), false).await;
    let app = bridge(&backend_url, root.path(), &host_path, None).router();

    let challenge = response_json(
        app.clone()
            .oneshot(call(challenge_body("a", "one")))
            .await
            .unwrap(),
    )
    .await;
    let capability = challenge["error"]["data"]["host_invocation_capability"].clone();
    let mut cross_binding = capability.clone();
    cross_binding["transport_binding_id"] = json!("tb_other");
    let cases = [
        retry_body("b", "two", capability.clone()),
        json!({"jsonrpc":"2.0","id":"b-cwd","method":"tools/call","params":{"name":"run_command","arguments":{"command":"one","cwd":"/other","timeout":30,"host_invocation_capability":capability.clone()}}}).to_string(),
        json!({"jsonrpc":"2.0","id":"b-timeout","method":"tools/call","params":{"name":"run_command","arguments":{"command":"one","cwd":"/tmp","timeout":31,"host_invocation_capability":capability.clone()}}}).to_string(),
        retry_body("b-cross", "one", cross_binding),
        r#"{"jsonrpc":"2.0","id":"c","method":"tools/call","params":{"name":"run_command","arguments":{"command":"one","command":"two"}}}"#.into(),
        r#"{"jsonrpc":"2.0","id":"d","method":"tools/call","params":{"name":"run_command","arguments":{"command":"one","host_invocation_capability":null}}}"#.into(),
        r#"{"jsonrpc":"2.0","id":"d2","method":"tools/call","params":{"name":"run_command","arguments":{"command":"one","host_invocation_capability":"not-an-object"}}}"#.into(),
        r#"{"jsonrpc":"2.0","id":"e","method":"tools/call","params":{"name":"run_command","arguments":{"command":"one","bridge_call_id":"bc_caller"}}}"#.into(),
        r#"{"jsonrpc":"2.0","id":"f","method":"tools/call","params":{"name":"run_command","arguments":{"command":"one","metadata":{"conversation_id":"caller"}}}}"#.into(),
    ];
    for raw in cases {
        let response = app.clone().oneshot(call(raw)).await.unwrap();
        assert!(matches!(
            response.status(),
            StatusCode::BAD_REQUEST | StatusCode::CONFLICT
        ));
    }
    assert_eq!(backend_capture.0.lock().await.len(), 0);
    host_thread.join().unwrap();
}

#[tokio::test]
async fn backend_response_id_mismatch_is_persisted_unknown_and_never_reexecuted() {
    let root = tempfile::tempdir().unwrap();
    let host_path = root.path().join("host.sock");
    let host_log = Arc::new(StdMutex::new(Vec::new()));
    let host_thread = spawn_ndjson_server(host_path.clone(), 2, host_handler(host_log));
    while !host_path.exists() {
        tokio::task::yield_now().await;
    }
    let backend_capture = BackendCapture::default();
    let backend_url = backend_server(backend_capture.clone(), true).await;
    let app = bridge(&backend_url, root.path(), &host_path, None).router();
    let first = response_json(
        app.clone()
            .oneshot(call(challenge_body("a", "one")))
            .await
            .unwrap(),
    )
    .await;
    let capability = first["error"]["data"]["host_invocation_capability"].clone();
    let bridge_call_id = capability["bridge_call_id"].as_str().unwrap().to_string();
    let retry = retry_body("b", "one", capability);
    let failed = app.clone().oneshot(call(retry.clone())).await.unwrap();
    assert_eq!(failed.status(), StatusCode::BAD_GATEWAY);
    assert_eq!(backend_capture.0.lock().await.len(), 1);
    drop(app);
    let restarted = restarted_bridge(&backend_url, root.path(), &host_path, None).router();
    let again = restarted.oneshot(call(retry)).await.unwrap();
    assert_eq!(again.status(), StatusCode::CONFLICT);
    assert_eq!(backend_capture.0.lock().await.len(), 1);
    assert_eq!(
        ToolReceiptJournal::load(root.path().join("tool-receipt-journal.json"))
            .unwrap()
            .state(&bridge_call_id),
        Some(ToolReceiptState::ResultUnknownAfterDispatch)
    );
    host_thread.join().unwrap();
}

#[tokio::test]
async fn runtime_pre_rejection_has_zero_backend_side_effects() {
    let root = tempfile::tempdir().unwrap();
    let host_path = root.path().join("host.sock");
    let runtime_path = root.path().join("runtime.sock");
    let host_log = Arc::new(StdMutex::new(Vec::new()));
    let runtime_log = Arc::new(StdMutex::new(Vec::new()));
    let _host = spawn_ndjson_server(host_path.clone(), 2, host_handler(host_log));
    let _runtime = spawn_ndjson_server(
        runtime_path.clone(),
        1,
        runtime_handler(runtime_log, true, false),
    );
    while !host_path.exists() || !runtime_path.exists() {
        tokio::task::yield_now().await;
    }
    let backend_capture = BackendCapture::default();
    let backend_url = backend_server(backend_capture.clone(), false).await;
    let app = bridge(&backend_url, root.path(), &host_path, Some(&runtime_path)).router();
    let challenge = response_json(
        app.clone()
            .oneshot(call(challenge_body("a", "one")))
            .await
            .unwrap(),
    )
    .await;
    let retry = retry_body(
        "b",
        "one",
        challenge["error"]["data"]["host_invocation_capability"].clone(),
    );
    let response = app.oneshot(call(retry)).await.unwrap();
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert_eq!(backend_capture.0.lock().await.len(), 0);
}

#[tokio::test]
async fn concurrent_consumers_have_one_winner_and_one_backend_dispatch() {
    let root = tempfile::tempdir().unwrap();
    let host_path = root.path().join("host.sock");
    let host_log = Arc::new(StdMutex::new(Vec::new()));
    let _host = spawn_ndjson_server(host_path.clone(), 3, host_handler(host_log));
    while !host_path.exists() {
        tokio::task::yield_now().await;
    }
    let backend_capture = BackendCapture::default();
    let backend_url = backend_server(backend_capture.clone(), false).await;
    let app = bridge(&backend_url, root.path(), &host_path, None).router();
    let challenge = response_json(
        app.clone()
            .oneshot(call(challenge_body("a", "one")))
            .await
            .unwrap(),
    )
    .await;
    let capability = challenge["error"]["data"]["host_invocation_capability"].clone();
    let left = app
        .clone()
        .oneshot(call(retry_body("b", "one", capability.clone())));
    let right = app
        .clone()
        .oneshot(call(retry_body("c", "one", capability)));
    let (left, right) = tokio::join!(left, right);
    let statuses = [left.unwrap().status(), right.unwrap().status()];
    assert_eq!(
        statuses
            .iter()
            .filter(|status| **status == StatusCode::OK)
            .count(),
        1
    );
    assert_eq!(
        statuses
            .iter()
            .filter(|status| **status == StatusCode::CONFLICT)
            .count(),
        1
    );
    assert_eq!(backend_capture.0.lock().await.len(), 1);
}

#[tokio::test]
async fn terminal_failures_become_durable_unknown_after_one_dispatch() {
    for reject_runtime in [false, true] {
        let root = tempfile::tempdir().unwrap();
        let host_path = root.path().join("host.sock");
        let runtime_path = root.path().join("runtime.sock");
        let host_log = Arc::new(StdMutex::new(Vec::new()));
        let runtime_log = Arc::new(StdMutex::new(Vec::new()));
        let host_calls = if reject_runtime { 3 } else { 2 };
        let _host = spawn_ndjson_server(host_path.clone(), host_calls, host_handler(host_log));
        let _runtime = if reject_runtime {
            Some(spawn_ndjson_server(
                runtime_path.clone(),
                2,
                runtime_handler(runtime_log, false, true),
            ))
        } else {
            None
        };
        while !host_path.exists() || (reject_runtime && !runtime_path.exists()) {
            tokio::task::yield_now().await;
        }
        let backend_capture = BackendCapture::default();
        let backend_url = backend_server(backend_capture.clone(), false).await;
        let app = bridge(
            &backend_url,
            root.path(),
            &host_path,
            reject_runtime.then_some(runtime_path.as_path()),
        )
        .router();
        let challenge = response_json(
            app.clone()
                .oneshot(call(challenge_body("a", "one")))
                .await
                .unwrap(),
        )
        .await;
        let capability = challenge["error"]["data"]["host_invocation_capability"].clone();
        let bridge_call_id = capability["bridge_call_id"].as_str().unwrap().to_string();
        let response = app
            .oneshot(call(retry_body("b", "one", capability)))
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::BAD_GATEWAY);
        assert_eq!(backend_capture.0.lock().await.len(), 1);
        assert_eq!(
            ToolReceiptJournal::load(root.path().join("tool-receipt-journal.json"))
                .unwrap()
                .state(&bridge_call_id),
            Some(ToolReceiptState::ResultUnknownAfterDispatch)
        );
    }
}

#[tokio::test]
async fn challenge_state_persistence_failure_never_dispatches() {
    let root = tempfile::tempdir().unwrap();
    let host_path = root.path().join("host.sock");
    let host_log = Arc::new(StdMutex::new(Vec::new()));
    let host = spawn_ndjson_server(host_path.clone(), 1, host_handler(host_log));
    while !host_path.exists() {
        tokio::task::yield_now().await;
    }
    let backend_capture = BackendCapture::default();
    let backend_url = backend_server(backend_capture.clone(), false).await;
    let app = bridge(&backend_url, root.path(), &host_path, None).router();
    let journal_path = root.path().join("tool-receipt-journal.json");
    symlink(root.path().join("authority-state.json"), &journal_path).unwrap();
    let response = app.oneshot(call(challenge_body("a", "one"))).await.unwrap();
    assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
    assert_eq!(backend_capture.0.lock().await.len(), 0);
    host.join().unwrap();
}

#[tokio::test]
async fn host_cannot_substitute_retry_outer_digest_before_dispatch() {
    let root = tempfile::tempdir().unwrap();
    let host_path = root.path().join("host.sock");
    let host_log = Arc::new(StdMutex::new(Vec::new()));
    let base = host_handler(host_log);
    let _host = spawn_ndjson_server(host_path.clone(), 2, move |request| {
        let consume = request["op"] == "tool_receipt_consume";
        let mut result = base(request)?;
        if consume {
            result["pre_receipt"]["retry_outer_request_sha256"] = json!("0".repeat(64));
        }
        Ok(result)
    });
    while !host_path.exists() {
        tokio::task::yield_now().await;
    }
    let backend_capture = BackendCapture::default();
    let backend_url = backend_server(backend_capture.clone(), false).await;
    let app = bridge(&backend_url, root.path(), &host_path, None).router();
    let challenge = response_json(
        app.clone()
            .oneshot(call(challenge_body("a", "one")))
            .await
            .unwrap(),
    )
    .await;
    let retry = retry_body(
        "b",
        "one",
        challenge["error"]["data"]["host_invocation_capability"].clone(),
    );
    let response = app.oneshot(call(retry)).await.unwrap();
    assert_eq!(response.status(), StatusCode::BAD_GATEWAY);
    assert_eq!(backend_capture.0.lock().await.len(), 0);
}

#[tokio::test]
async fn backend_transport_loss_after_dispatch_intent_is_unknown_and_not_retried() {
    let root = tempfile::tempdir().unwrap();
    let host_path = root.path().join("host.sock");
    let host_log = Arc::new(StdMutex::new(Vec::new()));
    let _host = spawn_ndjson_server(host_path.clone(), 2, host_handler(host_log));
    while !host_path.exists() {
        tokio::task::yield_now().await;
    }
    let backend_calls = Arc::new(AtomicUsize::new(0));
    let backend_url = disconnect_backend_server(backend_calls.clone());
    let app = bridge(&backend_url, root.path(), &host_path, None).router();
    let challenge = response_json(
        app.clone()
            .oneshot(call(challenge_body("a", "one")))
            .await
            .unwrap(),
    )
    .await;
    let capability = challenge["error"]["data"]["host_invocation_capability"].clone();
    let bridge_call_id = capability["bridge_call_id"].as_str().unwrap().to_string();
    let retry = retry_body("b", "one", capability);
    let response = app.oneshot(call(retry.clone())).await.unwrap();
    assert_eq!(response.status(), StatusCode::BAD_GATEWAY);
    assert_eq!(backend_calls.load(Ordering::SeqCst), 1);
    let restarted = restarted_bridge(&backend_url, root.path(), &host_path, None).router();
    assert_eq!(
        restarted.oneshot(call(retry)).await.unwrap().status(),
        StatusCode::CONFLICT
    );
    assert_eq!(backend_calls.load(Ordering::SeqCst), 1);
    assert_eq!(
        ToolReceiptJournal::load(root.path().join("tool-receipt-journal.json"))
            .unwrap()
            .state(&bridge_call_id),
        Some(ToolReceiptState::ResultUnknownAfterDispatch)
    );
}

#[tokio::test]
async fn backend_timeout_after_dispatch_intent_is_durable_unknown() {
    let root = tempfile::tempdir().unwrap();
    let host_path = root.path().join("host.sock");
    let host_log = Arc::new(StdMutex::new(Vec::new()));
    let _host = spawn_ndjson_server(host_path.clone(), 2, host_handler(host_log));
    while !host_path.exists() {
        tokio::task::yield_now().await;
    }
    let backend_calls = Arc::new(AtomicUsize::new(0));
    let backend_url = hanging_backend_server(backend_calls.clone());
    let app = bridge(&backend_url, root.path(), &host_path, None).router();
    let challenge = response_json(
        app.clone()
            .oneshot(call(challenge_body_with_timeout("a", "one", 1)))
            .await
            .unwrap(),
    )
    .await;
    let capability = challenge["error"]["data"]["host_invocation_capability"].clone();
    let bridge_call_id = capability["bridge_call_id"].as_str().unwrap().to_string();
    let retry = retry_body_with_timeout("b", "one", 1, capability);
    let response = tokio::time::timeout(Duration::from_secs(3), app.oneshot(call(retry)))
        .await
        .expect("backend timeout was not bounded")
        .unwrap();
    assert_eq!(response.status(), StatusCode::BAD_GATEWAY);
    assert_eq!(backend_calls.load(Ordering::SeqCst), 1);
    assert_eq!(
        ToolReceiptJournal::load(root.path().join("tool-receipt-journal.json"))
            .unwrap()
            .state(&bridge_call_id),
        Some(ToolReceiptState::ResultUnknownAfterDispatch)
    );
}

#[tokio::test]
async fn two_preconstructed_bridges_cannot_erase_dispatch_intent_or_other_records() {
    let root = tempfile::tempdir().unwrap();
    let host_path = root.path().join("host.sock");
    let host_log = Arc::new(StdMutex::new(Vec::new()));
    let host = spawn_ndjson_server(host_path.clone(), 4, host_handler(host_log));
    while !host_path.exists() {
        tokio::task::yield_now().await;
    }
    let gated = GatedBackend::default();
    let backend_url = gated_backend_server(gated.clone()).await;
    let first_bridge = bridge(&backend_url, root.path(), &host_path, None).router();
    let stale_second_bridge =
        restarted_bridge(&backend_url, root.path(), &host_path, None).router();

    let first_challenge = response_json(
        first_bridge
            .clone()
            .oneshot(call(challenge_body("a", "first")))
            .await
            .unwrap(),
    )
    .await;
    let first_capability = first_challenge["error"]["data"]["host_invocation_capability"].clone();
    let first_id = first_capability["bridge_call_id"]
        .as_str()
        .unwrap()
        .to_string();
    let first_retry = retry_body("b", "first", first_capability);
    let restart_retry = first_retry.clone();
    let in_flight =
        tokio::spawn(async move { first_bridge.oneshot(call(first_retry)).await.unwrap() });
    tokio::time::timeout(Duration::from_secs(3), gated.started.notified())
        .await
        .expect("first Bridge did not reach backend after durable dispatch intent");
    assert_eq!(
        ToolReceiptJournal::load(root.path().join("tool-receipt-journal.json"))
            .unwrap()
            .state(&first_id),
        Some(ToolReceiptState::DispatchIntent)
    );
    let intent_restart = restarted_bridge(&backend_url, root.path(), &host_path, None).router();
    assert_eq!(
        intent_restart
            .oneshot(call(restart_retry))
            .await
            .unwrap()
            .status(),
        StatusCode::CONFLICT
    );
    assert_eq!(gated.capture.0.lock().await.len(), 1);

    let second_challenge = response_json(
        stale_second_bridge
            .oneshot(call(challenge_body("c", "second")))
            .await
            .unwrap(),
    )
    .await;
    let second_id =
        second_challenge["error"]["data"]["host_invocation_capability"]["bridge_call_id"]
            .as_str()
            .unwrap()
            .to_string();
    gated.release.notify_one();
    let response = tokio::time::timeout(Duration::from_secs(5), in_flight)
        .await
        .expect("first Bridge did not finish")
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    host.join().unwrap();

    let restarted =
        ToolReceiptJournal::load(root.path().join("tool-receipt-journal.json")).unwrap();
    assert_eq!(restarted.state(&first_id), Some(ToolReceiptState::Terminal));
    assert_eq!(restarted.state(&second_id), Some(ToolReceiptState::Issued));
}

#[tokio::test]
async fn terminal_receipt_requires_every_exact_task1_field_before_success() {
    let mutations = [
        ("schema_version", None),
        ("provenance", Some(json!("wrong"))),
        ("request_digest_profile", Some(json!("wrong"))),
        ("terminal_receipt_id", Some(json!("bad"))),
        ("pre_receipt_id", Some(json!("hpr_other"))),
        ("pre_receipt_sha256", Some(json!("0".repeat(64)))),
        ("capability_id", Some(json!("hic_other"))),
        ("host_tool_execution_id", Some(json!("hte_other"))),
        ("outer_request_sha256", Some(json!("0".repeat(64)))),
        ("forwarded_request_sha256", Some(json!("0".repeat(64)))),
        ("normalized_request_sha256", Some(json!("0".repeat(64)))),
        ("retry_outer_request_sha256", Some(json!("0".repeat(64)))),
        ("backend_route", Some(json!("/wrong-route"))),
        ("http_status", Some(json!(500))),
        ("response_body_sha256", Some(json!("0".repeat(64)))),
        ("terminal_classification", Some(json!("WRONG"))),
        ("conversation_id", Some(json!("other-conversation"))),
        ("browser_target_id", Some(json!("other-target"))),
        ("top_frame_id", Some(json!("other-frame"))),
        ("loader_id", Some(json!("other-loader"))),
        ("generation_anchor_sha256", Some(json!("9".repeat(64)))),
        ("extension_binding_sha256", Some(json!("8".repeat(64)))),
        ("extension_instance_id_sha256", Some(json!("7".repeat(64)))),
        ("extension_loaded_at_ms", Some(json!(999))),
        ("host_epoch_binding_sha256", Some(json!("0".repeat(64)))),
        ("issued_at_unix_ms", Some(json!(0))),
        ("host_mac_sha256", None),
        ("unexpected_terminal_field", Some(json!(true))),
    ];

    for (field, replacement) in mutations {
        let root = tempfile::tempdir().unwrap();
        let host_path = root.path().join("host.sock");
        let host_log = Arc::new(StdMutex::new(Vec::new()));
        let base = host_handler(host_log);
        let _host = spawn_ndjson_server(host_path.clone(), 3, move |request| {
            let terminal = request["op"] == "tool_receipt_terminal";
            let mut response = base(request)?;
            if terminal {
                let receipt = response["terminal_receipt"].as_object_mut().unwrap();
                match &replacement {
                    Some(value) => {
                        receipt.insert(field.to_string(), value.clone());
                    }
                    None => {
                        receipt.remove(field);
                    }
                }
            }
            Ok(response)
        });
        while !host_path.exists() {
            tokio::task::yield_now().await;
        }
        let backend_capture = BackendCapture::default();
        let backend_url = backend_server(backend_capture.clone(), false).await;
        let app = bridge(&backend_url, root.path(), &host_path, None).router();
        let challenge = response_json(
            app.clone()
                .oneshot(call(challenge_body("a", field)))
                .await
                .unwrap(),
        )
        .await;
        let capability = challenge["error"]["data"]["host_invocation_capability"].clone();
        let call_id = capability["bridge_call_id"].as_str().unwrap().to_string();
        let retry = retry_body("b", field, capability);
        let response = app.clone().oneshot(call(retry.clone())).await.unwrap();
        assert_eq!(
            response.status(),
            StatusCode::BAD_GATEWAY,
            "terminal mutation {field} was accepted"
        );
        assert_eq!(backend_capture.0.lock().await.len(), 1);
        assert_eq!(
            ToolReceiptJournal::load(root.path().join("tool-receipt-journal.json"))
                .unwrap()
                .state(&call_id),
            Some(ToolReceiptState::ResultUnknownAfterDispatch),
            "terminal mutation {field} did not persist unknown"
        );
        drop(app);
        let restarted = restarted_bridge(&backend_url, root.path(), &host_path, None).router();
        assert_eq!(
            restarted.oneshot(call(retry)).await.unwrap().status(),
            StatusCode::CONFLICT
        );
        assert_eq!(backend_capture.0.lock().await.len(), 1);
    }
}

#[tokio::test]
async fn invalid_backend_jsonrpc_profiles_are_unknown_and_never_replayed() {
    let profiles = [
        BackendProfile::MissingResultAndError,
        BackendProfile::ResultAndError,
        BackendProfile::WrongVersion,
        BackendProfile::ScalarError,
        BackendProfile::DuplicateResult,
        BackendProfile::UnexpectedMember,
    ];
    for profile in profiles {
        let root = tempfile::tempdir().unwrap();
        let host_path = root.path().join("host.sock");
        let host_log = Arc::new(StdMutex::new(Vec::new()));
        let _host = spawn_ndjson_server(host_path.clone(), 3, host_handler(host_log));
        while !host_path.exists() {
            tokio::task::yield_now().await;
        }
        let backend_capture = BackendCapture::default();
        let backend_url = profile_backend_server(backend_capture.clone(), profile).await;
        let app = bridge(&backend_url, root.path(), &host_path, None).router();
        let challenge = response_json(
            app.clone()
                .oneshot(call(challenge_body("a", "profile")))
                .await
                .unwrap(),
        )
        .await;
        let capability = challenge["error"]["data"]["host_invocation_capability"].clone();
        let call_id = capability["bridge_call_id"].as_str().unwrap().to_string();
        let retry = retry_body("b", "profile", capability);
        assert_eq!(
            app.clone()
                .oneshot(call(retry.clone()))
                .await
                .unwrap()
                .status(),
            StatusCode::BAD_GATEWAY
        );
        assert_eq!(
            ToolReceiptJournal::load(root.path().join("tool-receipt-journal.json"))
                .unwrap()
                .state(&call_id),
            Some(ToolReceiptState::ResultUnknownAfterDispatch)
        );
        drop(app);
        let restarted = restarted_bridge(&backend_url, root.path(), &host_path, None).router();
        assert_eq!(
            restarted.oneshot(call(retry)).await.unwrap().status(),
            StatusCode::CONFLICT
        );
        assert_eq!(backend_capture.0.lock().await.len(), 1);
    }
}

#[tokio::test]
async fn backend_jsonrpc_profile_accepts_legal_fraction_and_exponent_numbers() {
    for (profile, field) in [
        (BackendProfile::LegalNumbers, "result"),
        (BackendProfile::LegalError, "error"),
    ] {
        let root = tempfile::tempdir().unwrap();
        let host_path = root.path().join("host.sock");
        let host_log = Arc::new(StdMutex::new(Vec::new()));
        let host = spawn_ndjson_server(host_path.clone(), 3, host_handler(host_log));
        while !host_path.exists() {
            tokio::task::yield_now().await;
        }
        let backend_capture = BackendCapture::default();
        let backend_url = profile_backend_server(backend_capture, profile).await;
        let app = bridge(&backend_url, root.path(), &host_path, None).router();
        let challenge = response_json(
            app.clone()
                .oneshot(call(challenge_body("a", field)))
                .await
                .unwrap(),
        )
        .await;
        let response = app
            .oneshot(call(retry_body(
                "b",
                field,
                challenge["error"]["data"]["host_invocation_capability"].clone(),
            )))
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let response = response_json(response).await;
        assert!(response.get(field).is_some());
        if field == "result" {
            assert_eq!(response["result"]["fraction"], json!(1.25));
            assert_eq!(response["result"]["exponent"], json!(1e10));
        } else {
            assert_eq!(response["error"]["data"]["fraction"], json!(1.25));
        }
        host.join().unwrap();
    }
}

#[tokio::test]
async fn disabled_receipt_chain_preserves_legacy_run_command_body_and_reserved_field() {
    let root = tempfile::tempdir().unwrap();
    let backend_capture = BackendCapture::default();
    let backend_url = backend_server(backend_capture.clone(), false).await;
    let app = legacy_bridge(&backend_url, root.path()).router();
    let raw = r#"{"jsonrpc":"2.0","id":"legacy","method":"tools/call","params":{"_meta":{"progressToken":"p-1"},"name":"run_command","arguments":{"command":"one","host_invocation_capability":{"caller":"legacy"}}}}"#;
    let response = app.oneshot(call(raw.to_string())).await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let captured = backend_capture.0.lock().await;
    assert_eq!(captured.len(), 1);
    assert_eq!(captured[0].1.as_ref(), raw.as_bytes());
    assert_eq!(captured[0].0["mcp-session-id"], "caller-controlled-session");
    assert_eq!(
        captured[0].0["x-lab-request-id"],
        "caller-controlled-proof-id"
    );
}

#[tokio::test]
async fn stalled_private_server_has_one_bounded_overall_deadline() {
    let root = tempfile::tempdir().unwrap();
    let host_path = root.path().join("host.sock");
    let stalled_path = host_path.clone();
    let stalled = thread::spawn(move || {
        let listener = UnixListener::bind(&stalled_path).unwrap();
        std::fs::set_permissions(&stalled_path, std::fs::Permissions::from_mode(0o600)).unwrap();
        let (mut stream, _) = listener.accept().unwrap();
        let mut line = String::new();
        BufReader::new(stream.try_clone().unwrap())
            .read_line(&mut line)
            .unwrap();
        thread::sleep(Duration::from_secs(7));
        let _ = stream.write_all(b"never observed\n");
    });
    while !host_path.exists() {
        tokio::task::yield_now().await;
    }
    let backend_capture = BackendCapture::default();
    let backend_url = backend_server(backend_capture.clone(), false).await;
    let app = bridge(&backend_url, root.path(), &host_path, None).router();
    let response = tokio::time::timeout(
        Duration::from_millis(5_500),
        app.oneshot(call(challenge_body("a", "stalled"))),
    )
    .await
    .expect("private Host operation exceeded its single five-second budget")
    .unwrap();
    assert_eq!(response.status(), StatusCode::BAD_GATEWAY);
    assert_eq!(backend_capture.0.lock().await.len(), 0);
    stalled.join().unwrap();
}
