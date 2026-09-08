use axum::{
    Router,
    body::{Body, Bytes},
    extract::State,
    http::{HeaderMap, Request, StatusCode, header},
    routing::post,
};
use bridge_security_core::{Capability, CapabilityAuthority, CapabilityGrant, ExecutionContext};
use local_tool_bridge::BridgeIngress;
use std::sync::Arc;
use tokio::sync::Mutex;
use tower::ServiceExt;

const KEY: &str = "0123456789abcdef0123456789abcdef0123456789abcdef";
const BACKEND_KEY: &str = "backend-0123456789abcdef0123456789abcdef0123456789abcdef";

#[derive(Clone, Default)]
struct Capture(Arc<Mutex<Option<(HeaderMap, Bytes)>>>);

async fn backend(
    State(capture): State<Capture>,
    headers: HeaderMap,
    body: Bytes,
) -> (StatusCode, &'static str) {
    *capture.0.lock().await = Some((headers, body));
    (StatusCode::OK, "backend-ok")
}

async fn backend_server(capture: Capture) -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let app = Router::new()
        .route("/mcp", post(backend))
        .with_state(capture);
    tokio::spawn(async move {
        axum::serve(listener, app).await.unwrap();
    });
    format!("http://127.0.0.1:{}/mcp", addr.port())
}

fn bridge(backend_url: &str, capabilities: Vec<Capability>) -> BridgeIngress {
    let context = ExecutionContext {
        principal_id: "openai-tunnel:subject-fp".into(),
        execution_session_id: "bridge-session-1".into(),
        capability_id: "cap-ingress-1".into(),
        workspace_id: "workspace-1".into(),
    };
    let grant = CapabilityGrant {
        principal_id: context.principal_id.clone(),
        execution_session_id: context.execution_session_id.clone(),
        capability_id: context.capability_id.clone(),
        workspace_id: context.workspace_id.clone(),
        capabilities,
    };
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as u64;
    let mut authority = CapabilityAuthority::new_for_test([21_u8; 32]);
    let issued = authority.issue(grant, now - 1000, 60_000, 1).unwrap();
    let state_dir = tempfile::tempdir().unwrap().keep();
    authority.save_private(&state_dir).unwrap();
    BridgeIngress::new(
        authority,
        issued,
        context,
        KEY.as_bytes(),
        backend_url,
        BACKEND_KEY,
        state_dir,
    )
    .unwrap()
}

fn trusted_bridge(
    backend_url: &str,
    state_dir: &std::path::Path,
    capabilities: Vec<Capability>,
) -> BridgeIngress {
    let context = ExecutionContext {
        principal_id: "uds-local-principal".into(),
        execution_session_id: "uds-session-1".into(),
        capability_id: "cap-uds-1".into(),
        workspace_id: "workspace-uds-1".into(),
    };
    let grant = CapabilityGrant {
        principal_id: context.principal_id.clone(),
        execution_session_id: context.execution_session_id.clone(),
        capability_id: context.capability_id.clone(),
        workspace_id: context.workspace_id.clone(),
        capabilities,
    };
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as u64;
    let mut authority = CapabilityAuthority::new_for_test([31_u8; 32]);
    let issued = authority.issue(grant, now - 1000, 60_000, 1).unwrap();
    authority.save_private(state_dir).unwrap();
    BridgeIngress::new_trusted_unix_socket(
        authority,
        issued,
        context,
        backend_url,
        BACKEND_KEY,
        local_tool_bridge::ToolPolicy::catdesk_default(),
        state_dir,
    )
    .unwrap()
}

fn mcp_request(body: &str, key: Option<&str>) -> Request<Body> {
    let mut builder = Request::builder()
        .method("POST")
        .uri("/mcp")
        .header("content-type", "application/json")
        .header("mcp-method", "tools/call")
        .header("mcp-name", "read")
        .header("x-openai-subject", "correlation-only-subject");
    if let Some(key) = key {
        builder = builder.header("x-lab-ingress-key", key);
    }
    builder.body(Body::from(body.to_string())).unwrap()
}

#[tokio::test]
async fn authenticated_loopback_ingress_forwards_signed_proof_without_secret() {
    let capture = Capture::default();
    let backend_url = backend_server(capture.clone()).await;
    let app = bridge(&backend_url, vec![Capability::FileRead]).router();
    let body = r#"{"jsonrpc":"2.0","id":"1","method":"tools/call","params":{"name":"read","arguments":{}}}"#;

    let response = app.oneshot(mcp_request(body, Some(KEY))).await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let (headers, forwarded_body) = capture.0.lock().await.clone().unwrap();
    assert_eq!(forwarded_body, Bytes::from(body.to_string()));
    assert!(headers.get("x-lab-ingress-key").is_none());
    assert_eq!(
        headers.get("x-lab-principal-id").unwrap(),
        "openai-tunnel:subject-fp"
    );
    assert_eq!(
        headers.get("x-lab-execution-session-id").unwrap(),
        "bridge-session-1"
    );
    assert!(headers.get("x-lab-request-mac").is_some());
    assert!(headers.get("x-lab-request-nonce").is_some());
    assert_eq!(headers.get("x-lab-backend-key").unwrap(), BACKEND_KEY);
    assert!(headers.get("x-openai-subject").is_none());
}

#[tokio::test]
async fn backend_redirect_is_not_followed() {
    use axum::response::IntoResponse;

    let redirected_capture = Capture::default();
    let redirected_url = backend_server(redirected_capture.clone()).await;
    let redirect_listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let redirect_addr = redirect_listener.local_addr().unwrap();
    let location = redirected_url.clone();
    let app_redirect = Router::new().route(
        "/mcp",
        post(move || {
            let location = location.clone();
            async move { (StatusCode::FOUND, [(header::LOCATION, location)]).into_response() }
        }),
    );
    tokio::spawn(async move {
        axum::serve(redirect_listener, app_redirect).await.unwrap();
    });
    let first_url = format!("http://127.0.0.1:{}/mcp", redirect_addr.port());
    let app = bridge(&first_url, vec![Capability::FileRead]).router();
    let body = r#"{"jsonrpc":"2.0","id":"redirect","method":"tools/call","params":{"name":"read","arguments":{}}}"#;

    let response = app.oneshot(mcp_request(body, Some(KEY))).await.unwrap();
    assert_eq!(response.status(), StatusCode::FOUND);
    assert!(redirected_capture.0.lock().await.is_none());
}

#[tokio::test]
async fn correlation_headers_cannot_replace_local_ingress_key() {
    let capture = Capture::default();
    let backend_url = backend_server(capture.clone()).await;
    let app = bridge(&backend_url, vec![Capability::FileRead]).router();
    let body = r#"{"jsonrpc":"2.0","id":"1","method":"tools/call","params":{"name":"read","arguments":{}}}"#;

    let response = app.oneshot(mcp_request(body, None)).await.unwrap();
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert!(capture.0.lock().await.is_none());
}

#[tokio::test]
async fn missing_capability_fails_before_backend_dispatch() {
    let capture = Capture::default();
    let backend_url = backend_server(capture.clone()).await;
    let app = bridge(&backend_url, vec![Capability::FileRead]).router();
    let body = r#"{"jsonrpc":"2.0","id":"2","method":"tools/call","params":{"name":"write","arguments":{}}}"#;
    let request = Request::builder()
        .method("POST")
        .uri("/mcp")
        .header("content-type", "application/json")
        .header("mcp-method", "tools/call")
        .header("mcp-name", "write")
        .header("x-lab-ingress-key", KEY)
        .body(Body::from(body))
        .unwrap();

    let response = app.oneshot(request).await.unwrap();
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert!(capture.0.lock().await.is_none());
}

#[tokio::test]
async fn trusted_uds_ingress_persists_request_id_replay_before_backend() {
    let capture = Capture::default();
    let backend_url = backend_server(capture.clone()).await;
    let state_dir = tempfile::tempdir().unwrap();
    let app = trusted_bridge(&backend_url, state_dir.path(), vec![Capability::FileRead]).router();
    let body = r#"{"jsonrpc":"2.0","id":"same-json-id","method":"tools/call","params":{"name":"read","arguments":{}}}"#;
    let request = || {
        Request::builder()
            .method("POST")
            .uri("/mcp")
            .header("content-type", "application/json")
            .header("mcp-method", "tools/call")
            .header("mcp-name", "read")
            .header("x-request-id", "provider-request-123")
            .body(Body::from(body.to_string()))
            .unwrap()
    };

    let first = app.clone().oneshot(request()).await.unwrap();
    assert_eq!(first.status(), StatusCode::OK);
    assert!(capture.0.lock().await.is_some());
    *capture.0.lock().await = None;

    let second = app.oneshot(request()).await.unwrap();
    assert_eq!(second.status(), StatusCode::FORBIDDEN);
    assert!(capture.0.lock().await.is_none());

    let restarted = CapabilityAuthority::load_or_create_private(state_dir.path()).unwrap();
    let lease = restarted.lease("cap-uds-1").unwrap();
    assert_eq!(lease.grant.execution_session_id, "uds-session-1");
    let issued = restarted.issued_capability("cap-uds-1").unwrap();
    let context = ExecutionContext {
        principal_id: lease.grant.principal_id.clone(),
        execution_session_id: lease.grant.execution_session_id.clone(),
        capability_id: lease.grant.capability_id.clone(),
        workspace_id: lease.grant.workspace_id.clone(),
    };
    let restarted_app = BridgeIngress::new_trusted_unix_socket(
        restarted,
        issued,
        context,
        &backend_url,
        BACKEND_KEY,
        local_tool_bridge::ToolPolicy::catdesk_default(),
        state_dir.path(),
    )
    .unwrap()
    .router();
    let after_restart = restarted_app.oneshot(request()).await.unwrap();
    assert_eq!(after_restart.status(), StatusCode::FORBIDDEN);
    assert!(capture.0.lock().await.is_none());
}

#[test]
fn resources_read_requires_file_read_capability() {
    assert_eq!(
        local_tool_bridge::required_capability("resources/read", "").unwrap(),
        Capability::FileRead
    );
    assert_eq!(
        local_tool_bridge::required_capability("resources/list", "").unwrap(),
        Capability::Discovery
    );
}

#[test]
fn backend_must_be_loopback() {
    let context = ExecutionContext {
        principal_id: "p".into(),
        execution_session_id: "s".into(),
        capability_id: "c".into(),
        workspace_id: "w".into(),
    };
    let grant = CapabilityGrant {
        principal_id: "p".into(),
        execution_session_id: "s".into(),
        capability_id: "c".into(),
        workspace_id: "w".into(),
        capabilities: vec![Capability::Discovery],
    };
    let mut authority = CapabilityAuthority::new_for_test([23_u8; 32]);
    let issued = authority.issue(grant, 1, 1000, 1).unwrap();
    assert!(
        BridgeIngress::new(
            authority,
            issued,
            context,
            KEY.as_bytes(),
            "https://example.com/mcp",
            BACKEND_KEY,
            tempfile::tempdir().unwrap().keep(),
        )
        .is_err()
    );
}

#[test]
fn backend_hostname_localhost_is_rejected() {
    let context = ExecutionContext {
        principal_id: "p2".into(),
        execution_session_id: "s2".into(),
        capability_id: "c2".into(),
        workspace_id: "w2".into(),
    };
    let grant = CapabilityGrant {
        principal_id: "p2".into(),
        execution_session_id: "s2".into(),
        capability_id: "c2".into(),
        workspace_id: "w2".into(),
        capabilities: vec![Capability::Discovery],
    };
    let mut authority = CapabilityAuthority::new_for_test([25_u8; 32]);
    let issued = authority.issue(grant, 1, 1000, 1).unwrap();
    assert!(
        BridgeIngress::new(
            authority,
            issued,
            context,
            KEY.as_bytes(),
            "http://localhost:9999/mcp",
            BACKEND_KEY,
            tempfile::tempdir().unwrap().keep(),
        )
        .is_err()
    );
}

#[derive(Clone, Default)]
struct JsonCapture(Arc<Mutex<Vec<(HeaderMap, serde_json::Value)>>>);

async fn json_backend(
    State((capture, kind)): State<(JsonCapture, &'static str)>,
    headers: HeaderMap,
    body: Bytes,
) -> (StatusCode, axum::Json<serde_json::Value>) {
    let parsed: serde_json::Value = serde_json::from_slice(&body).unwrap();
    capture.0.lock().await.push((headers, parsed.clone()));
    let method = parsed
        .get("method")
        .and_then(serde_json::Value::as_str)
        .unwrap_or("");
    let id = parsed.get("id").cloned().unwrap_or(serde_json::Value::Null);
    if method == "tools/list" {
        let tools = if kind == "computer" {
            vec![
                serde_json::json!({"name":"computer_session_open","inputSchema":{"type":"object"}}),
            ]
        } else {
            vec![serde_json::json!({"name":"read","inputSchema":{"type":"object"}})]
        };
        return (
            StatusCode::OK,
            axum::Json(serde_json::json!({"jsonrpc":"2.0","id":id,"result":{"tools":tools}})),
        );
    }
    (
        StatusCode::OK,
        axum::Json(serde_json::json!({"jsonrpc":"2.0","id":id,"result":{"backend":kind}})),
    )
}

async fn json_backend_server(capture: JsonCapture, kind: &'static str) -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let app = Router::new()
        .route("/mcp", post(json_backend))
        .with_state((capture, kind));
    tokio::spawn(async move {
        axum::serve(listener, app).await.unwrap();
    });
    format!("http://127.0.0.1:{}/mcp", addr.port())
}

#[tokio::test]
async fn computer_tools_are_capability_gated_routed_and_merged_into_tools_list() {
    let default_capture = JsonCapture::default();
    let computer_capture = JsonCapture::default();
    let default_url = json_backend_server(default_capture.clone(), "default").await;
    let computer_url = json_backend_server(computer_capture.clone(), "computer").await;
    let app = bridge(
        &default_url,
        vec![Capability::Discovery, Capability::Computer],
    )
    .with_computer_backend(&computer_url, BACKEND_KEY)
    .unwrap()
    .router();

    let list_body = r#"{"jsonrpc":"2.0","id":"list","method":"tools/list","params":{}}"#;
    let list_request = Request::builder()
        .method("POST")
        .uri("/mcp")
        .header("content-type", "application/json")
        .header("mcp-method", "tools/list")
        .header("x-lab-ingress-key", KEY)
        .body(Body::from(list_body))
        .unwrap();
    let list_response = app.clone().oneshot(list_request).await.unwrap();
    assert_eq!(list_response.status(), StatusCode::OK);
    let list_bytes = axum::body::to_bytes(list_response.into_body(), usize::MAX)
        .await
        .unwrap();
    let list_json: serde_json::Value = serde_json::from_slice(&list_bytes).unwrap();
    let names: Vec<_> = list_json["result"]["tools"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|tool| tool["name"].as_str())
        .collect();
    assert!(names.contains(&"read"));
    assert!(names.contains(&"computer_session_open"));

    let computer_body = r#"{"jsonrpc":"2.0","id":"computer","method":"tools/call","params":{"name":"computer_session_open","arguments":{}}}"#;
    let computer_request = Request::builder()
        .method("POST")
        .uri("/mcp")
        .header("content-type", "application/json")
        .header("mcp-method", "tools/call")
        .header("mcp-name", "computer_session_open")
        .header("x-lab-ingress-key", KEY)
        .body(Body::from(computer_body))
        .unwrap();
    let response = app.oneshot(computer_request).await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let computer_calls = computer_capture.0.lock().await;
    assert!(computer_calls.iter().any(|(_, body)| {
        body.get("params")
            .and_then(|v| v.get("name"))
            .and_then(serde_json::Value::as_str)
            == Some("computer_session_open")
    }));
    assert!(computer_calls.iter().all(|(headers, _)| {
        headers
            .get("x-lab-backend-key")
            .and_then(|v| v.to_str().ok())
            == Some(BACKEND_KEY)
    }));
    let default_calls = default_capture.0.lock().await;
    assert!(!default_calls.iter().any(|(_, body)| {
        body.get("params")
            .and_then(|v| v.get("name"))
            .and_then(serde_json::Value::as_str)
            == Some("computer_session_open")
    }));
}

#[tokio::test]
async fn computer_tool_without_computer_capability_fails_before_computer_backend() {
    let default_capture = JsonCapture::default();
    let computer_capture = JsonCapture::default();
    let default_url = json_backend_server(default_capture, "default").await;
    let computer_url = json_backend_server(computer_capture.clone(), "computer").await;
    let app = bridge(&default_url, vec![Capability::Discovery])
        .with_computer_backend(&computer_url, BACKEND_KEY)
        .unwrap()
        .router();
    let body = r#"{"jsonrpc":"2.0","id":"computer","method":"tools/call","params":{"name":"computer_session_open","arguments":{}}}"#;
    let request = Request::builder()
        .method("POST")
        .uri("/mcp")
        .header("content-type", "application/json")
        .header("mcp-method", "tools/call")
        .header("mcp-name", "computer_session_open")
        .header("x-lab-ingress-key", KEY)
        .body(Body::from(body))
        .unwrap();
    let response = app.oneshot(request).await.unwrap();
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert!(computer_capture.0.lock().await.is_empty());
}
