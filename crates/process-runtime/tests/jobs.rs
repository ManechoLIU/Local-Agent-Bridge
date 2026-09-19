use axum::{
    body::Body,
    http::{Request, StatusCode},
};
use process_runtime::{ProcessRuntime, ProcessSandboxPolicy};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    path::PathBuf,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use tower::ServiceExt;

const KEY: &str = "process-runtime-backend-key-0123456789abcdef0123456789abcdef";

fn policy(label: &str) -> ProcessSandboxPolicy {
    let root = std::env::current_dir()
        .unwrap()
        .join(".lab-staging")
        .join("process-runtime-tests")
        .join(label);
    let _ = std::fs::remove_dir_all(&root);
    let workspace = root.join("workspace");
    std::fs::create_dir_all(&workspace).unwrap();
    ProcessSandboxPolicy::new(
        workspace,
        root.join("scratch"),
        root.join("cache"),
        vec![
            PathBuf::from("/opt/homebrew"),
            PathBuf::from("/usr/local"),
            PathBuf::from("/usr"),
            PathBuf::from("/bin"),
            PathBuf::from("/sbin"),
        ],
    )
    .unwrap()
}

fn signed_request(body: &str, key: &str, workspace_id: &str, principal: &str, session: &str) -> Request<Body> {
    let digest = Sha256::digest(body.as_bytes());
    let body_sha: String = digest.iter().map(|byte| format!("{byte:02x}")).collect();
    Request::builder()
        .method("POST")
        .uri("/mcp")
        .header("content-type", "application/json")
        .header("x-lab-backend-key", key)
        .header("x-lab-capability-id", "cap-process")
        .header("x-lab-principal-id", principal)
        .header("x-lab-execution-session-id", session)
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
    serde_json::from_slice(&bytes)
        .unwrap_or_else(|_| json!({"_raw": String::from_utf8_lossy(&bytes)}))
}

fn call(name: &str, id: &str, arguments: Value) -> String {
    json!({
        "jsonrpc":"2.0",
        "id":id,
        "method":"tools/call",
        "params":{"name":name,"arguments":arguments}
    })
    .to_string()
}

async fn invoke(
    app: &axum::Router,
    name: &str,
    id: &str,
    arguments: Value,
) -> (StatusCode, Value) {
    invoke_as(app, name, id, arguments, "workspace-a", "principal-a", "session-a").await
}

async fn invoke_as(
    app: &axum::Router,
    name: &str,
    id: &str,
    arguments: Value,
    workspace_id: &str,
    principal: &str,
    session: &str,
) -> (StatusCode, Value) {
    let body = call(name, id, arguments);
    let response = app
        .clone()
        .oneshot(signed_request(&body, KEY, workspace_id, principal, session))
        .await
        .unwrap();
    let status = response.status();
    (status, body_json(response).await)
}

async fn poll_until(app: &axum::Router, job_id: &str, deadline: Duration) -> Value {
    let started = Instant::now();
    loop {
        let (status, value) = invoke(
            app,
            "poll_command",
            "poll",
            json!({"job_id": job_id}),
        )
        .await;
        assert_eq!(status, StatusCode::OK, "poll failed: {value}");
        let structured = value["result"]["structuredContent"].clone();
        let state = structured["state"].as_str().unwrap_or("");
        if state != "running" {
            return structured;
        }
        if started.elapsed() >= deadline {
            panic!("job {job_id} still running after {deadline:?}: {structured}");
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
}

#[tokio::test]
async fn start_poll_job_succeeds_with_devnull() {
    let app = ProcessRuntime::new(KEY.as_bytes(), policy("job-ok"), "workspace-a")
        .unwrap()
        .router();
    let (status, started) = invoke(
        &app,
        "start_command",
        "start",
        json!({
            "command": "printf 'JOB-OK\n' >/dev/null && printf 'JOB-OK\n'",
            "timeout_ms": 20000
        }),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "start failed: {started}");
    let started = &started["result"]["structuredContent"];
    assert_eq!(started["provenance"], "lab_process_runtime_v1");
    assert_eq!(started["state"], "running");
    let job_id = started["job_id"].as_str().expect("job_id");

    let done = poll_until(&app, job_id, Duration::from_secs(20)).await;
    assert_eq!(done["provenance"], "lab_process_runtime_v1");
    assert_eq!(done["state"], "succeeded", "job did not succeed: {done}");
    assert_eq!(done["success"], true);
    assert_eq!(done["timed_out"], false);
    assert_eq!(done["exit_code"], 0);
    assert!(
        done["stdout"].as_str().unwrap_or("").contains("JOB-OK"),
        "stdout missing JOB-OK: {done}"
    );
}

#[tokio::test]
async fn cancel_command_kills_running_job() {
    let app = ProcessRuntime::new(KEY.as_bytes(), policy("job-cancel"), "workspace-a")
        .unwrap()
        .router();
    let (status, started) = invoke(
        &app,
        "start_command",
        "start",
        json!({"command":"sleep 30","timeout_ms":60000}),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "start failed: {started}");
    let job_id = started["result"]["structuredContent"]["job_id"]
        .as_str()
        .expect("job_id")
        .to_string();

    let (status, cancelled) = invoke(
        &app,
        "cancel_command",
        "cancel",
        json!({"job_id": job_id}),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "cancel failed: {cancelled}");
    assert_eq!(
        cancelled["result"]["structuredContent"]["provenance"],
        "lab_process_runtime_v1"
    );

    let done = poll_until(&app, &job_id, Duration::from_secs(10)).await;
    assert_eq!(done["state"], "cancelled", "job was not cancelled: {done}");
    assert_eq!(done["success"], false);
    assert_eq!(done["provenance"], "lab_process_runtime_v1");
}

#[tokio::test]
async fn job_timeout_kills_process_tree() {
    let app = ProcessRuntime::new(KEY.as_bytes(), policy("job-timeout"), "workspace-a")
        .unwrap()
        .router();
    let (status, started) = invoke(
        &app,
        "start_command",
        "start",
        json!({"command":"sleep 30","timeout_ms":400}),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "start failed: {started}");
    let job_id = started["result"]["structuredContent"]["job_id"]
        .as_str()
        .expect("job_id")
        .to_string();

    let done = poll_until(&app, &job_id, Duration::from_secs(10)).await;
    assert_eq!(done["state"], "timed_out", "job did not time out: {done}");
    assert_eq!(done["timed_out"], true);
    assert_eq!(done["success"], false);
    assert_eq!(done["provenance"], "lab_process_runtime_v1");
}

#[tokio::test]
async fn job_denies_write_outside_workspace_and_has_no_network() {
    let app = ProcessRuntime::new(KEY.as_bytes(), policy("job-boundary"), "workspace-a")
        .unwrap()
        .router();
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let leak = PathBuf::from(format!("/tmp/lab-proc02-should-not-exist-{stamp}"));
    let _ = std::fs::remove_file(&leak);

    let (status, started) = invoke(
        &app,
        "start_command",
        "start-leak",
        json!({
            "command": format!("printf leak > {}", leak.display()),
            "timeout_ms": 10000
        }),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "start leak failed: {started}");
    let job_id = started["result"]["structuredContent"]["job_id"]
        .as_str()
        .expect("job_id")
        .to_string();
    let done = poll_until(&app, &job_id, Duration::from_secs(10)).await;
    assert_ne!(done["state"], "succeeded", "outside write must fail: {done}");
    assert_eq!(done["success"], false);
    assert!(!leak.exists(), "sandbox leaked a file at {}", leak.display());

    let network_cmd = if PathBuf::from("/usr/bin/curl").is_file() {
        "/usr/bin/curl -s --connect-timeout 2 --max-time 3 https://example.com/"
    } else {
        "/usr/bin/python3 -c \"import socket; socket.create_connection(('1.1.1.1', 443), 2)\""
    };
    let (status, started) = invoke(
        &app,
        "start_command",
        "start-net",
        json!({"command": network_cmd, "timeout_ms": 8000}),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "start net failed: {started}");
    let job_id = started["result"]["structuredContent"]["job_id"]
        .as_str()
        .expect("job_id")
        .to_string();
    let done = poll_until(&app, &job_id, Duration::from_secs(10)).await;
    assert_eq!(done["success"], false, "ordinary shell must not reach the network: {done}");
    assert_ne!(done["state"], "succeeded", "network job succeeded: {done}");
}

#[tokio::test]
async fn job_owner_mismatch_and_cwd_escape_fail_closed() {
    let app = ProcessRuntime::new(KEY.as_bytes(), policy("job-owner"), "workspace-a")
        .unwrap()
        .router();
    let (status, started) = invoke(
        &app,
        "start_command",
        "start",
        json!({"command":"sleep 5","timeout_ms":10000}),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "start failed: {started}");
    let job_id = started["result"]["structuredContent"]["job_id"]
        .as_str()
        .expect("job_id")
        .to_string();

    let (status, value) = invoke_as(
        &app,
        "poll_command",
        "poll-other",
        json!({"job_id": job_id}),
        "workspace-a",
        "principal-b",
        "session-a",
    )
    .await;
    assert_eq!(status, StatusCode::FORBIDDEN, "owner mismatch must fail-closed: {value}");

    let (status, value) = invoke(
        &app,
        "start_command",
        "cwd",
        json!({"command":"pwd","cwd":"/tmp","timeout_ms":5000}),
    )
    .await;
    assert_eq!(status, StatusCode::FORBIDDEN, "cwd escape must fail-closed: {value}");

    let _ = invoke(
        &app,
        "cancel_command",
        "cleanup",
        json!({"job_id": job_id}),
    )
    .await;
}
