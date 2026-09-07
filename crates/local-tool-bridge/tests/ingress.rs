use axum::{
    Router,
    body::{Body, Bytes},
    extract::State,
    http::{HeaderMap, Request, StatusCode},
    routing::post,
};
use bridge_security_core::{Capability, CapabilityAuthority, CapabilityGrant, ExecutionContext};
use local_tool_bridge::BridgeIngress;
use std::sync::Arc;
use tokio::sync::Mutex;
use tower::ServiceExt;

const KEY: &str = "0123456789abcdef0123456789abcdef0123456789abcdef";

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
    BridgeIngress::new(authority, issued, context, KEY.as_bytes(), backend_url).unwrap()
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
    assert!(headers.get("x-openai-subject").is_none());
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
            "https://example.com/mcp"
        )
        .is_err()
    );
}
