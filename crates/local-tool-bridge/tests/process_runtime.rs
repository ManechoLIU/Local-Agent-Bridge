use axum::{
    Router,
    body::Body,
    extract::State,
    http::{HeaderMap, Request, StatusCode},
    routing::post,
};
use bridge_security_core::{Capability, CapabilityAuthority, CapabilityGrant, ExecutionContext};
use local_tool_bridge::BridgeIngress;
use process_runtime::{ProcessRuntime, ProcessSandboxPolicy};
use serde_json::{Value, json};
use std::{path::PathBuf, sync::Arc};
use tokio::sync::Mutex;
use tower::ServiceExt;

const INGRESS_KEY: &str = "bridge-process-ingress-0123456789abcdef0123456789abcdef";
const DEFAULT_KEY: &str = "default-backend-key-0123456789abcdef0123456789abcdef";
const PROCESS_KEY: &str = "process-runtime-backend-key-0123456789abcdef0123456789abcdef";
const WORKSPACE_ID: &str = "workspace-process-1";

#[derive(Clone, Default)]
struct Capture(Arc<Mutex<Vec<(HeaderMap, Value)>>>);

async fn default_backend(
    State(capture): State<Capture>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> (StatusCode, axum::Json<Value>) {
    let parsed: Value = serde_json::from_slice(&body).unwrap();
    capture.0.lock().await.push((headers, parsed.clone()));
    let method = parsed.get("method").and_then(Value::as_str).unwrap_or("");
    let id = parsed.get("id").cloned().unwrap_or(Value::Null);
    if method == "tools/list" {
        return (
            StatusCode::OK,
            axum::Json(json!({
                "jsonrpc":"2.0",
                "id":id,
                "result":{"tools":[
                    {"name":"read","inputSchema":{"type":"object"}},
                    {"name":"run_command","inputSchema":{
                        "type":"object",
                        "properties":{"command":{"type":"string"},"cwd":{"type":"string"},"timeout":{"type":"number"}},
                        "required":["command"]
                    }}
                ]}
            })),
        );
    }
    (
        StatusCode::OK,
        axum::Json(json!({"jsonrpc":"2.0","id":id,"result":{"backend":"catdesk"}})),
    )
}

async fn serve(app: Router) -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    tokio::spawn(async move {
        axum::serve(listener, app).await.unwrap();
    });
    format!("http://127.0.0.1:{}/mcp", address.port())
}

fn process_policy(label: &str) -> (ProcessSandboxPolicy, PathBuf) {
    let root = std::env::current_dir()
        .unwrap()
        .join(".lab-staging")
        .join("bridge-process-runtime-tests")
        .join(label);
    let _ = std::fs::remove_dir_all(&root);
    let workspace = root.join("workspace");
    std::fs::create_dir_all(&workspace).unwrap();
    let policy = ProcessSandboxPolicy::new(
        &workspace,
        root.join("scratch"),
        root.join("cache"),
        vec![PathBuf::from("/bin"), PathBuf::from("/usr")],
    )
    .unwrap();
    (policy, workspace)
}

fn bridge(default_url: &str, process_url: &str) -> Router {
    let context = ExecutionContext {
        principal_id: "openai-tunnel:subject-fp".into(),
        execution_session_id: "bridge-session-process".into(),
        capability_id: "cap-process-1".into(),
        workspace_id: WORKSPACE_ID.into(),
    };
    let grant = CapabilityGrant {
        principal_id: context.principal_id.clone(),
        execution_session_id: context.execution_session_id.clone(),
        capability_id: context.capability_id.clone(),
        workspace_id: context.workspace_id.clone(),
        capabilities: vec![
            Capability::Discovery,
            Capability::Shell,
            Capability::ProcessJobs,
        ],
    };
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as u64;
    let mut authority = CapabilityAuthority::new_for_test([41_u8; 32]);
    let issued = authority.issue(grant, now - 1000, 60_000, 1).unwrap();
    let state_dir = tempfile::tempdir().unwrap().keep();
    authority.save_private(&state_dir).unwrap();
    BridgeIngress::new(
        authority,
        issued,
        context,
        INGRESS_KEY.as_bytes(),
        default_url,
        DEFAULT_KEY,
        state_dir,
    )
    .unwrap()
    .with_process_backend(process_url, PROCESS_KEY)
    .unwrap()
    .router()
}

fn request(id: &str, name: &str, arguments: Value) -> Request<Body> {
    let body = json!({
        "jsonrpc":"2.0",
        "id":id,
        "method":"tools/call",
        "params":{"name":name,"arguments":arguments}
    })
    .to_string();
    Request::builder()
        .method("POST")
        .uri("/mcp")
        .header("content-type", "application/json")
        .header("mcp-method", "tools/call")
        .header("mcp-name", name)
        .header("x-lab-ingress-key", INGRESS_KEY)
        .body(Body::from(body))
        .unwrap()
}

async fn response_json(response: axum::response::Response) -> Value {
    let body = axum::body::to_bytes(response.into_body(), usize::MAX)
        .await
        .unwrap();
    serde_json::from_slice(&body)
        .unwrap_or_else(|_| json!({"_raw": String::from_utf8_lossy(&body)}))
}

async fn response_text(response: axum::response::Response) -> String {
    let body = axum::body::to_bytes(response.into_body(), usize::MAX)
        .await
        .unwrap();
    String::from_utf8_lossy(&body).into_owned()
}

#[tokio::test]
async fn run_command_routes_through_process_runtime_provenance_and_fail_closes_outside_workspace() {
    let (policy, workspace) = process_policy("bridge-route");
    let process_app = ProcessRuntime::new(PROCESS_KEY.as_bytes(), policy, WORKSPACE_ID)
        .unwrap()
        .router();
    let process_url = serve(process_app).await;
    let default_capture = Capture::default();
    let default_url = serve(
        Router::new()
            .route("/mcp", post(default_backend))
            .with_state(default_capture.clone()),
    )
    .await;
    let app = bridge(&default_url, &process_url);

    let list_body = r#"{"jsonrpc":"2.0","id":"list","method":"tools/list","params":{}}"#;
    let list_request = Request::builder()
        .method("POST")
        .uri("/mcp")
        .header("content-type", "application/json")
        .header("mcp-method", "tools/list")
        .header("x-lab-ingress-key", INGRESS_KEY)
        .body(Body::from(list_body))
        .unwrap();
    let list_response = app.clone().oneshot(list_request).await.unwrap();
    assert_eq!(list_response.status(), StatusCode::OK);
    let list_json = response_json(list_response).await;
    let tools = list_json["result"]["tools"].as_array().unwrap();
    let run_commands: Vec<_> = tools
        .iter()
        .filter(|tool| tool["name"] == "run_command")
        .collect();
    assert_eq!(
        run_commands.len(),
        1,
        "tools/list must expose one run_command after Process Runtime routing: {list_json}"
    );
    assert_eq!(
        run_commands[0]["inputSchema"]["properties"]["timeout_ms"]["type"],
        "integer"
    );
    assert!(
        tools.iter().any(|tool| tool["name"] == "read"),
        "file tools must remain on the default backend"
    );

    let ok = app
        .clone()
        .oneshot(request(
            "run",
            "run_command",
            json!({"command":"printf 'LAB-PROC-01-BRIDGE\\n'","timeout_ms":10000}),
        ))
        .await
        .unwrap();
    assert_eq!(ok.status(), StatusCode::OK);
    let ok = response_json(ok).await;
    let structured = &ok["result"]["structuredContent"];
    assert_eq!(structured["provenance"], "lab_process_runtime_v1");
    assert_eq!(structured["success"], true);
    assert!(
        structured["stdout"]
            .as_str()
            .unwrap_or("")
            .contains("LAB-PROC-01-BRIDGE"),
        "stdout missing process-runtime marker: {ok}"
    );
    let marker = workspace.join("lab-proc01-bridge.txt");
    let written = app
        .clone()
        .oneshot(request(
            "write",
            "run_command",
            json!({
                "command":"printf 'LAB-PROC-01-BRIDGE\\n' | tee lab-proc01-bridge.txt",
                "timeout_ms":15000
            }),
        ))
        .await
        .unwrap();
    assert_eq!(written.status(), StatusCode::OK, "write status");
    let written_json = response_json(written).await;
    let written_structured = &written_json["result"]["structuredContent"];
    assert_eq!(
        written_structured["provenance"], "lab_process_runtime_v1",
        "write provenance: {written_json}"
    );
    assert_eq!(
        written_structured["success"], true,
        "write failed: {written_json}"
    );
    assert_eq!(
        std::fs::read_to_string(&marker).unwrap().trim(),
        "LAB-PROC-01-BRIDGE"
    );

    let escaped = app
        .clone()
        .oneshot(request(
            "cwd",
            "run_command",
            json!({"command":"pwd","cwd":"/tmp","timeout_ms":5000}),
        ))
        .await
        .unwrap();
    assert_eq!(escaped.status(), StatusCode::FORBIDDEN);
    let escaped_body = response_text(escaped).await;
    assert!(
        escaped_body.to_lowercase().contains("cwd"),
        "outside workspace cwd must fail-closed, got {escaped_body}"
    );

    let parent_escape = app
        .clone()
        .oneshot(request(
            "parent",
            "run_command",
            json!({"command":"pwd","cwd":"../escape","timeout_ms":5000}),
        ))
        .await
        .unwrap();
    assert_eq!(parent_escape.status(), StatusCode::FORBIDDEN);

    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let leak = PathBuf::from(format!("/tmp/lab-proc01-bridge-should-not-exist-{stamp}"));
    let _ = std::fs::remove_file(&leak);
    let outside_write = app
        .clone()
        .oneshot(request(
            "leak",
            "run_command",
            json!({"command":format!("printf leak > {}", leak.display()),"timeout_ms":10000}),
        ))
        .await
        .unwrap();
    assert_eq!(outside_write.status(), StatusCode::OK);
    let outside_write = response_json(outside_write).await;
    assert_eq!(
        outside_write["result"]["structuredContent"]["provenance"],
        "lab_process_runtime_v1"
    );
    assert_eq!(
        outside_write["result"]["structuredContent"]["success"],
        false
    );
    assert!(
        !leak.exists(),
        "sandbox leaked a file at {}",
        leak.display()
    );

    let start = app
        .oneshot(request("jobs", "start_command", json!({})))
        .await
        .unwrap();
    assert_eq!(start.status(), StatusCode::OK);
    let start = response_json(start).await;
    assert_eq!(start["result"]["backend"], "catdesk");

    let default_calls = default_capture.0.lock().await;
    assert!(
        default_calls.iter().all(|(_, body)| {
            body.pointer("/params/name").and_then(Value::as_str) != Some("run_command")
        }),
        "run_command must not fall back to CatDesk when Process Runtime is configured: {default_calls:?}"
    );
    assert!(
        default_calls.iter().any(|(_, body)| {
            body.pointer("/params/name").and_then(Value::as_str) == Some("start_command")
        }),
        "start_command may remain on the default backend this slice"
    );
}
