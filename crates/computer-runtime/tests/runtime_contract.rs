use axum::{
    body::Body,
    http::{Request, StatusCode},
};
use computer_runtime::{AppInfo, ComputerProvider, ComputerRuntime};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::sync::{Arc, Mutex};
use tower::ServiceExt;

const KEY: &str = "computer-runtime-backend-key-0123456789abcdef0123456789abcdef";

#[derive(Clone, Default)]
struct MockProvider {
    activations: Arc<Mutex<Vec<(i32, String)>>>,
}

impl ComputerProvider for MockProvider {
    fn permissions(&self) -> Result<Value, String> {
        Ok(json!({"platform":"mock","accessibility":true,"screen_recording":true}))
    }

    fn list_apps(&self) -> Result<Vec<AppInfo>, String> {
        Ok(vec![AppInfo {
            pid: 4242,
            bundle_id: "com.example.fixture".into(),
            display_name: "Fixture".into(),
            is_active: false,
            is_hidden: false,
            is_terminated: false,
        }])
    }

    fn list_windows(&self) -> Result<Value, String> {
        Ok(json!([{"window_id":"wid_fixture","pid":4242,"title":"Fixture"}]))
    }

    fn active_window(&self) -> Result<Value, String> {
        Ok(json!({"window_id":"wid_fixture","pid":4242,"title":"Fixture"}))
    }

    fn activate_app(&self, pid: i32, bundle_id: &str) -> Result<Value, String> {
        self.activations
            .lock()
            .unwrap()
            .push((pid, bundle_id.into()));
        Ok(json!({"activated":true,"pid":pid,"bundle_id":bundle_id}))
    }
}

fn bridge_request(body: &str, key: &str, session: &str) -> Request<Body> {
    let digest = Sha256::digest(body.as_bytes());
    let body_sha: String = digest.iter().map(|byte| format!("{byte:02x}")).collect();
    Request::builder()
        .method("POST")
        .uri("/mcp")
        .header("content-type", "application/json")
        .header("x-lab-backend-key", key)
        .header("x-lab-capability-id", "cap-computer")
        .header("x-lab-principal-id", "principal-a")
        .header("x-lab-execution-session-id", session)
        .header("x-lab-workspace-id", "workspace-a")
        .header("x-lab-request-id", format!("req-{session}"))
        .header("x-lab-request-nonce", format!("nonce-{session}"))
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

fn session_id(value: &Value) -> String {
    value["result"]["structuredContent"]["session_id"]
        .as_str()
        .unwrap()
        .to_string()
}

#[tokio::test]
async fn backend_key_and_signed_bridge_context_are_both_required() {
    let app = ComputerRuntime::new(KEY.as_bytes(), MockProvider::default())
        .unwrap()
        .router();
    let body = r#"{"jsonrpc":"2.0","id":"open","method":"tools/call","params":{"name":"computer_session_open","arguments":{}}}"#;

    let bad_key = app
        .clone()
        .oneshot(bridge_request(
            body,
            "wrong-wrong-wrong-wrong-wrong-wrong",
            "exec-a",
        ))
        .await
        .unwrap();
    assert_eq!(bad_key.status(), StatusCode::UNAUTHORIZED);

    let missing_bridge_context = Request::builder()
        .method("POST")
        .uri("/mcp")
        .header("content-type", "application/json")
        .header("x-lab-backend-key", KEY)
        .body(Body::from(body))
        .unwrap();
    let response = app.oneshot(missing_bridge_context).await.unwrap();
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
}

#[tokio::test]
async fn computer_session_ownership_is_bound_to_bridge_execution_context() {
    let app = ComputerRuntime::new(KEY.as_bytes(), MockProvider::default())
        .unwrap()
        .router();
    let open = r#"{"jsonrpc":"2.0","id":"open","method":"tools/call","params":{"name":"computer_session_open","arguments":{}}}"#;
    let response = app
        .clone()
        .oneshot(bridge_request(open, KEY, "exec-a"))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let opened = body_json(response).await;
    let session = session_id(&opened);

    let list = json!({
        "jsonrpc":"2.0","id":"apps","method":"tools/call",
        "params":{"name":"computer_list_apps","arguments":{"session_id":session}}
    })
    .to_string();
    let other_execution = app
        .oneshot(bridge_request(&list, KEY, "exec-b"))
        .await
        .unwrap();
    assert_eq!(other_execution.status(), StatusCode::FORBIDDEN);
}

#[tokio::test]
async fn bounded_action_requires_target_from_same_session_observation() {
    let provider = MockProvider::default();
    let activations = Arc::clone(&provider.activations);
    let app = ComputerRuntime::new(KEY.as_bytes(), provider)
        .unwrap()
        .router();

    let open = r#"{"jsonrpc":"2.0","id":"open","method":"tools/call","params":{"name":"computer_session_open","arguments":{}}}"#;
    let opened = body_json(
        app.clone()
            .oneshot(bridge_request(open, KEY, "exec-a"))
            .await
            .unwrap(),
    )
    .await;
    let session = session_id(&opened);

    let list = json!({
        "jsonrpc":"2.0","id":"apps","method":"tools/call",
        "params":{"name":"computer_list_apps","arguments":{"session_id":session}}
    })
    .to_string();
    let listed = body_json(
        app.clone()
            .oneshot(bridge_request(&list, KEY, "exec-a"))
            .await
            .unwrap(),
    )
    .await;
    let observation = listed["result"]["structuredContent"]["observation_id"]
        .as_str()
        .unwrap();

    let forged_target = json!({
        "jsonrpc":"2.0","id":"activate","method":"tools/call",
        "params":{"name":"computer_activate_app","arguments":{
            "session_id":session,"observation_id":observation,"pid":9999,"bundle_id":"com.example.other"
        }}
    })
    .to_string();
    let forbidden = app
        .clone()
        .oneshot(bridge_request(&forged_target, KEY, "exec-a"))
        .await
        .unwrap();
    assert_eq!(forbidden.status(), StatusCode::FORBIDDEN);
    assert!(activations.lock().unwrap().is_empty());

    let allowed = json!({
        "jsonrpc":"2.0","id":"activate","method":"tools/call",
        "params":{"name":"computer_activate_app","arguments":{
            "session_id":session,"observation_id":observation,"pid":4242,"bundle_id":"com.example.fixture"
        }}
    })
    .to_string();
    let response = app
        .oneshot(bridge_request(&allowed, KEY, "exec-a"))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(
        activations.lock().unwrap().as_slice(),
        &[(4242, "com.example.fixture".to_string())]
    );
}

#[tokio::test]
async fn tampered_body_hash_fails_closed() {
    let app = ComputerRuntime::new(KEY.as_bytes(), MockProvider::default())
        .unwrap()
        .router();
    let body = r#"{"jsonrpc":"2.0","id":"open","method":"tools/call","params":{"name":"computer_session_open","arguments":{}}}"#;
    let mut request = bridge_request(body, KEY, "exec-a");
    request
        .headers_mut()
        .insert("x-lab-body-sha256", "00".repeat(32).parse().unwrap());
    let response = app.oneshot(request).await.unwrap();
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
}
