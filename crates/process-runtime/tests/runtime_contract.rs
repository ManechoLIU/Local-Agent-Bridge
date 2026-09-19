use axum::{
    body::Body,
    http::{Request, StatusCode},
};
use process_runtime::{ProcessRuntime, ProcessSandboxPolicy};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::path::PathBuf;
use tower::ServiceExt;

const KEY: &str = "process-runtime-backend-key-0123456789abcdef0123456789abcdef";

fn policy() -> ProcessSandboxPolicy {
    let root = std::env::current_dir()
        .unwrap()
        .join(".lab-staging")
        .join("process-runtime-tests")
        .join("http");
    let _ = std::fs::remove_dir_all(&root);
    let workspace = root.join("workspace");
    std::fs::create_dir_all(&workspace).unwrap();
    ProcessSandboxPolicy::new(
        workspace,
        root.join("scratch"),
        root.join("cache"),
        vec![PathBuf::from("/bin"), PathBuf::from("/usr")],
    )
    .unwrap()
}

fn signed_request(body: &str, key: &str, workspace_id: &str) -> Request<Body> {
    let digest = Sha256::digest(body.as_bytes());
    let body_sha: String = digest.iter().map(|byte| format!("{byte:02x}")).collect();
    Request::builder()
        .method("POST")
        .uri("/mcp")
        .header("content-type", "application/json")
        .header("x-lab-backend-key", key)
        .header("x-lab-capability-id", "cap-process")
        .header("x-lab-principal-id", "principal-a")
        .header("x-lab-execution-session-id", "session-a")
        .header("x-lab-workspace-id", workspace_id)
        .header("x-lab-request-id", "req-process")
        .header("x-lab-request-nonce", "nonce-process")
        .header("x-lab-body-sha256", body_sha)
        .header("x-lab-ingress-provenance", "local_ingress_hmac_v1")
        .header("x-lab-request-mac", "test-mac-from-authenticated-bridge")
        .body(Body::from(body.to_string()))
        .unwrap()
}

async fn body_json(response: axum::response::Response) -> Value {
    let bytes = axum::body::to_bytes(response.into_body(), usize::MAX)
        .await
        .unwrap();
    serde_json::from_slice(&bytes).unwrap()
}

#[tokio::test]
async fn run_command_http_executes_and_unknown_tool_fails_closed() {
    let app = ProcessRuntime::new(KEY.as_bytes(), policy(), "workspace-a")
        .unwrap()
        .router();
    let body = json!({
        "jsonrpc":"2.0",
        "id":"run",
        "method":"tools/call",
        "params":{"name":"run_command","arguments":{"command":"printf 'HTTP-OK\\n'","timeout_ms":10000}}
    })
    .to_string();
    let response = app
        .clone()
        .oneshot(signed_request(&body, KEY, "workspace-a"))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let value = body_json(response).await;
    let structured = &value["result"]["structuredContent"];
    assert_eq!(structured["provenance"], "lab_process_runtime_v1");
    assert_eq!(structured["success"], true);
    assert!(structured["stdout"].as_str().unwrap().contains("HTTP-OK"));

    let unknown = json!({
        "jsonrpc":"2.0",
        "id":"jobs",
        "method":"tools/call",
        "params":{"name":"not_a_real_tool","arguments":{}}
    })
    .to_string();
    let response = app
        .clone()
        .oneshot(signed_request(&unknown, KEY, "workspace-a"))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
}

#[tokio::test]
async fn missing_backend_key_and_workspace_mismatch_fail_closed() {
    let app = ProcessRuntime::new(KEY.as_bytes(), policy(), "workspace-a")
        .unwrap()
        .router();
    let body = json!({
        "jsonrpc":"2.0",
        "id":"run",
        "method":"tools/call",
        "params":{"name":"run_command","arguments":{"command":"printf leak"}}
    })
    .to_string();
    let unsigned = Request::builder()
        .method("POST")
        .uri("/mcp")
        .header("content-type", "application/json")
        .body(Body::from(body.clone()))
        .unwrap();
    let response = app.clone().oneshot(unsigned).await.unwrap();
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);

    let response = app
        .oneshot(signed_request(&body, KEY, "other-workspace"))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
}
