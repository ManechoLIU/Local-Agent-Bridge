use axum::{
    Router,
    body::{Body, Bytes},
    extract::State,
    http::{HeaderMap, Request, StatusCode, header},
    routing::post,
};
use bridge_security_core::{Capability, CapabilityAuthority, CapabilityGrant, ExecutionContext};
use local_tool_bridge::{BridgeIngress, ProductionReleaseFence};
use std::sync::Arc;
use tokio::sync::Mutex;
use tower::ServiceExt;
use uuid::Uuid;

const KEY: &str = "0123456789abcdef0123456789abcdef0123456789abcdef";
const BACKEND_KEY: &str = "backend-0123456789abcdef0123456789abcdef0123456789abcdef";
const REVISION: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

fn production_fence() -> ProductionReleaseFence {
    ProductionReleaseFence {
        contract_version: "lab-production-connector-v1".into(),
        release_revision: REVISION.into(),
        endpoint_generation: 7,
        workspace: "/Users/echoman/Documents/ChatGPT/Local-Agent-Bridge".into(),
        tunnel_profile_name: "local-agent-bridge-production".into(),
        capability_ttl_ms: 86_400_000,
        capability_renew_before_ms: 3_600_000,
    }
}

fn host_fence_json(revision: &str) -> serde_json::Value {
    serde_json::json!({
        "connector_id": "local-agent-bridge-production",
        "contract_version": "lab-production-connector-v1",
        "release_revision": revision,
        "endpoint_generation": 7,
        "workspace": "/Users/echoman/Documents/ChatGPT/Local-Agent-Bridge",
        "tunnel_profile_name": "local-agent-bridge-production"
    })
}

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

fn discovery_request(body: &str, request_id: Option<&str>) -> Request<Body> {
    let mut builder = Request::builder()
        .method("POST")
        .uri("/mcp")
        .header("content-type", "application/json")
        .header("mcp-protocol-version", "2025-06-18")
        .header("x-lab-ingress-key", KEY);
    if let Some(request_id) = request_id {
        builder = builder.header("x-request-id", request_id);
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
async fn explicit_renewal_allows_an_expired_trusted_capability_without_changing_its_context() {
    let capture = Capture::default();
    let backend_url = backend_server(capture.clone()).await;
    let state_dir = tempfile::tempdir().unwrap();
    let context = ExecutionContext {
        principal_id: "renew-principal".into(),
        execution_session_id: "renew-session".into(),
        capability_id: "renew-capability".into(),
        workspace_id: "renew-workspace".into(),
    };
    let grant = CapabilityGrant {
        principal_id: context.principal_id.clone(),
        execution_session_id: context.execution_session_id.clone(),
        capability_id: context.capability_id.clone(),
        workspace_id: context.workspace_id.clone(),
        capabilities: vec![Capability::FileRead],
    };
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as u64;
    let mut authority = CapabilityAuthority::new_for_test([71; 32]);
    let issued = authority.issue(grant, now - 10_000, 1, 1).unwrap();
    authority.save_private(state_dir.path()).unwrap();
    let app = BridgeIngress::new_trusted_unix_socket(
        authority,
        issued,
        context.clone(),
        &backend_url,
        BACKEND_KEY,
        local_tool_bridge::ToolPolicy::catdesk_default(),
        state_dir.path(),
    )
    .unwrap()
    .with_capability_renewal(1_000, 100)
    .unwrap()
    .router();
    let body = r#"{"jsonrpc":"2.0","id":"renew","method":"tools/call","params":{"name":"read","arguments":{}}}"#;

    let response = app.oneshot(mcp_request(body, None)).await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let restarted = CapabilityAuthority::load_or_create_private(state_dir.path()).unwrap();
    let lease = restarted.lease(&context.capability_id).unwrap();
    assert_eq!(
        lease.grant.execution_session_id,
        context.execution_session_id
    );
    assert_eq!(lease.grant.capability_id, context.capability_id);
    assert_eq!(lease.grant.workspace_id, context.workspace_id);
    assert!(lease.expires_at_ms > now);
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
async fn discovery_reconnects_with_reused_json_rpc_ids_without_request_id_header() {
    let capture = Capture::default();
    let backend_url = backend_server(capture.clone()).await;
    let app = bridge(&backend_url, vec![Capability::Discovery]).router();
    let cases = [
        r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"tunnel","version":"1"}}}"#,
        r#"{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}"#,
    ];

    for body in cases {
        let first = app
            .clone()
            .oneshot(discovery_request(body, None))
            .await
            .unwrap();
        assert_eq!(first.status(), StatusCode::OK);
        let (first_headers, first_body) = capture.0.lock().await.clone().unwrap();
        let forwarded = String::from_utf8(first_body.to_vec()).unwrap();
        if body.contains("initialize") {
            assert!(forwarded.contains("2025-06-18"), "{forwarded}");
            assert_eq!(
                first_headers.get("mcp-protocol-version").unwrap(),
                "2025-06-18"
            );
        } else {
            assert_eq!(
                first_headers.get("mcp-protocol-version").unwrap(),
                "2026-07-28"
            );
        }
        let first_proof_request_id = first_headers
            .get("x-lab-request-id")
            .unwrap()
            .to_str()
            .unwrap()
            .to_string();
        assert!(Uuid::parse_str(&first_proof_request_id).is_ok());

        let second = app
            .clone()
            .oneshot(discovery_request(body, None))
            .await
            .unwrap();
        assert_eq!(second.status(), StatusCode::OK);
        let (second_headers, second_body) = capture.0.lock().await.clone().unwrap();
        let second_forwarded = String::from_utf8(second_body.to_vec()).unwrap();
        if body.contains("initialize") {
            assert!(second_forwarded.contains("2025-06-18"), "{second_forwarded}");
            assert_eq!(
                second_headers.get("mcp-protocol-version").unwrap(),
                "2025-06-18"
            );
        } else {
            assert_eq!(
                second_headers.get("mcp-protocol-version").unwrap(),
                "2026-07-28"
            );
        }
        let second_proof_request_id = second_headers
            .get("x-lab-request-id")
            .unwrap()
            .to_str()
            .unwrap()
            .to_string();
        assert!(Uuid::parse_str(&second_proof_request_id).is_ok());
        assert_ne!(first_proof_request_id, second_proof_request_id);
        assert_ne!(second_proof_request_id, "1");
    }
}

#[tokio::test]
async fn discovery_reconnects_when_provider_reuses_request_id_header_across_methods() {
    let capture = Capture::default();
    let backend_url = backend_server(capture.clone()).await;
    let app = bridge(&backend_url, vec![Capability::Discovery]).router();
    let provider_request_id = "provider-discovery-123";
    let initialize = r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}"#;
    let tools_list = r#"{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}"#;

    let first = app
        .clone()
        .oneshot(discovery_request(initialize, Some(provider_request_id)))
        .await
        .unwrap();
    assert_eq!(first.status(), StatusCode::OK);
    let (headers, forwarded_body) = capture.0.lock().await.clone().unwrap();
    assert_eq!(forwarded_body, Bytes::from(initialize.to_string()));
    let initialize_proof_id = headers
        .get("x-lab-request-id")
        .unwrap()
        .to_str()
        .unwrap()
        .to_string();
    assert!(Uuid::parse_str(&initialize_proof_id).is_ok());
    assert_ne!(initialize_proof_id, provider_request_id);
    *capture.0.lock().await = None;

    let second = app
        .oneshot(discovery_request(tools_list, Some(provider_request_id)))
        .await
        .unwrap();
    assert_eq!(second.status(), StatusCode::OK);
    let (headers, forwarded_body) = capture.0.lock().await.clone().unwrap();
    assert_eq!(forwarded_body, Bytes::from(tools_list.to_string()));
    let tools_list_proof_id = headers
        .get("x-lab-request-id")
        .unwrap()
        .to_str()
        .unwrap()
        .to_string();
    assert!(Uuid::parse_str(&tools_list_proof_id).is_ok());
    assert_ne!(tools_list_proof_id, provider_request_id);
    assert_ne!(initialize_proof_id, tools_list_proof_id);
}

#[tokio::test]
async fn initialized_notification_reconnects_with_reused_provider_request_id() {
    let capture = Capture::default();
    let backend_url = backend_server(capture.clone()).await;
    let app = bridge(&backend_url, vec![Capability::Discovery]).router();
    let provider_request_id = "provider-initialized-123";
    let initialize = r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}"#;
    let initialized = r#"{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}"#;

    let first = app
        .clone()
        .oneshot(discovery_request(initialize, Some(provider_request_id)))
        .await
        .unwrap();
    assert_eq!(first.status(), StatusCode::OK);
    let (headers, _) = capture.0.lock().await.clone().unwrap();
    let initialize_proof_id = headers
        .get("x-lab-request-id")
        .unwrap()
        .to_str()
        .unwrap()
        .to_string();
    *capture.0.lock().await = None;

    let second = app
        .clone()
        .oneshot(discovery_request(initialized, Some(provider_request_id)))
        .await
        .unwrap();
    assert_eq!(second.status(), StatusCode::OK);
    let (headers, _) = capture.0.lock().await.clone().unwrap();
    let first_initialized_proof_id = headers
        .get("x-lab-request-id")
        .unwrap()
        .to_str()
        .unwrap()
        .to_string();
    *capture.0.lock().await = None;

    let third = app
        .oneshot(discovery_request(initialized, Some(provider_request_id)))
        .await
        .unwrap();
    assert_eq!(third.status(), StatusCode::OK);
    let (headers, _) = capture.0.lock().await.clone().unwrap();
    let second_initialized_proof_id = headers
        .get("x-lab-request-id")
        .unwrap()
        .to_str()
        .unwrap()
        .to_string();

    for proof_id in [
        &initialize_proof_id,
        &first_initialized_proof_id,
        &second_initialized_proof_id,
    ] {
        assert!(Uuid::parse_str(proof_id).is_ok());
        assert_ne!(proof_id, provider_request_id);
    }
    assert_ne!(initialize_proof_id, first_initialized_proof_id);
    assert_ne!(first_initialized_proof_id, second_initialized_proof_id);
}

#[tokio::test]
async fn discovery_capability_tool_call_request_id_header_remains_replay_protected() {
    let capture = Capture::default();
    let backend_url = backend_server(capture.clone()).await;
    let app = bridge(&backend_url, vec![Capability::Discovery]).router();
    let body = r#"{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"catdesk_instruction","arguments":{}}}"#;
    let request = || {
        Request::builder()
            .method("POST")
            .uri("/mcp")
            .header("content-type", "application/json")
            .header("mcp-method", "tools/call")
            .header("mcp-name", "catdesk_instruction")
            .header("x-lab-ingress-key", KEY)
            .header("x-request-id", "provider-catdesk-instruction-123")
            .body(Body::from(body.to_string()))
            .unwrap()
    };

    let first = app.clone().oneshot(request()).await.unwrap();
    assert_eq!(first.status(), StatusCode::OK);
    let (headers, forwarded_body) = capture.0.lock().await.clone().unwrap();
    assert_eq!(forwarded_body, Bytes::from(body.to_string()));
    assert!(headers.get("x-lab-request-id").unwrap().to_str().unwrap().starts_with("rpc-v2:"));
    *capture.0.lock().await = None;

    let second = app.oneshot(request()).await.unwrap();
    assert_eq!(second.status(), StatusCode::FORBIDDEN);
    assert!(capture.0.lock().await.is_none());
}

#[tokio::test]
async fn non_reconnectable_notification_request_id_header_remains_replay_protected() {
    let capture = Capture::default();
    let backend_url = backend_server(capture.clone()).await;
    let app = bridge(&backend_url, vec![Capability::Discovery]).router();
    let body = r#"{"jsonrpc":"2.0","method":"notifications/cancelled","params":{}}"#;
    let request = || discovery_request(body, Some("provider-notification-123"));

    let first = app.clone().oneshot(request()).await.unwrap();
    assert_eq!(first.status(), StatusCode::OK);
    let (headers, forwarded_body) = capture.0.lock().await.clone().unwrap();
    assert_eq!(forwarded_body, Bytes::from(body.to_string()));
    assert!(headers.get("x-lab-request-id").unwrap().to_str().unwrap().starts_with("rpc-v2:"));
    *capture.0.lock().await = None;

    let second = app.oneshot(request()).await.unwrap();
    assert_eq!(second.status(), StatusCode::FORBIDDEN);
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

#[tokio::test]
async fn distinct_rpc_calls_in_one_provider_request_group_do_not_collide() {
    let capture = Capture::default();
    let backend_url = backend_server(capture.clone()).await;
    let state_dir = tempfile::tempdir().unwrap();
    let app = trusted_bridge(&backend_url, state_dir.path(), vec![Capability::FileRead]).router();
    let request = |id: &str, arguments: serde_json::Value| {
        Request::builder()
            .method("POST")
            .uri("/mcp")
            .header("content-type", "application/json")
            .header("x-request-id", "shared-provider-group")
            .body(Body::from(
                serde_json::json!({
                    "jsonrpc": "2.0", "id": id, "method": "tools/call",
                    "params": {"name": "read", "arguments": arguments}
                })
                .to_string(),
            ))
            .unwrap()
    };
    for id in ["rpc-a", "rpc-b"] {
        let response = app
            .clone()
            .oneshot(request(id, serde_json::json!({})))
            .await
            .unwrap();
        assert_eq!(
            response.status(),
            StatusCode::OK,
            "distinct RPC {id} must reach backend"
        );
    }
    *capture.0.lock().await = None;
    // Changing arguments cannot turn an already-used RPC identity into a fresh call.
    let replay = app
        .oneshot(request("rpc-a", serde_json::json!({"path": "changed"})))
        .await
        .unwrap();
    assert_eq!(replay.status(), StatusCode::FORBIDDEN);
    assert!(capture.0.lock().await.is_none());
}

#[tokio::test]
async fn legacy_bare_provider_replay_key_stays_rejected_after_upgrade() {
    let capture = Capture::default();
    let backend_url = backend_server(capture.clone()).await;
    let dir = tempfile::tempdir().unwrap();
    drop(trusted_bridge(
        &backend_url,
        dir.path(),
        vec![Capability::FileRead],
    ));
    let path = dir.path().join("authority-state.json");
    let mut state: serde_json::Value =
        serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
    state["records"]["cap-uds-1"]["used_request_ids"] =
        serde_json::json!(["legacy-provider-group"]);
    std::fs::write(&path, serde_json::to_vec(&state).unwrap()).unwrap();
    let authority = CapabilityAuthority::load_or_create_private(dir.path()).unwrap();
    let grant = authority.lease("cap-uds-1").unwrap().grant.clone();
    let issued = authority.issued_capability("cap-uds-1").unwrap();
    let app = BridgeIngress::new_trusted_unix_socket(
        authority,
        issued,
        ExecutionContext {
            principal_id: grant.principal_id,
            execution_session_id: grant.execution_session_id,
            capability_id: grant.capability_id,
            workspace_id: grant.workspace_id,
        },
        &backend_url,
        BACKEND_KEY,
        local_tool_bridge::ToolPolicy::catdesk_default(),
        dir.path(),
    )
    .unwrap()
    .router();
    let request = Request::builder().method("POST").uri("/mcp")
        .header("content-type", "application/json").header("x-request-id", "legacy-provider-group")
        .body(Body::from(r#"{"jsonrpc":"2.0","id":"new-rpc","method":"tools/call","params":{"name":"read","arguments":{}}}"#)).unwrap();
    let response = app.oneshot(request).await.unwrap();
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert!(capture.0.lock().await.is_none());
}

#[tokio::test]
async fn provider_group_rpc_ids_keep_json_types_distinct() {
    let capture = Capture::default();
    let backend_url = backend_server(capture).await;
    let dir = tempfile::tempdir().unwrap();
    let app = trusted_bridge(&backend_url, dir.path(), vec![Capability::FileRead]).router();
    for id in [serde_json::json!(1), serde_json::json!("1")] {
        let request = Request::builder()
            .method("POST")
            .uri("/mcp")
            .header("content-type", "application/json")
            .header("x-request-id", "typed-group")
            .body(Body::from(
                serde_json::json!({"jsonrpc":"2.0", "id":id, "method":"tools/call",
                "params":{"name":"read","arguments":{}}})
                .to_string(),
            ))
            .unwrap();
        assert_eq!(
            app.clone().oneshot(request).await.unwrap().status(),
            StatusCode::OK
        );
    }
}

#[tokio::test]
async fn null_rpc_id_and_missing_notification_id_have_separate_replay_fences() {
    let capture = Capture::default();
    let backend_url = backend_server(capture.clone()).await;
    let dir = tempfile::tempdir().unwrap();
    let app = trusted_bridge(&backend_url, dir.path(), vec![Capability::FileRead]).router();
    let request = |id: Option<serde_json::Value>, args: serde_json::Value| {
        let mut body = serde_json::json!({"jsonrpc":"2.0", "method":"tools/call",
            "params":{"name":"read","arguments":args}});
        if let Some(id) = id { body["id"] = id; }
        Request::builder().method("POST").uri("/mcp")
            .header("content-type", "application/json").header("x-request-id", "null-group")
            .body(Body::from(body.to_string())).unwrap()
    };
    let first = app.clone().oneshot(request(Some(serde_json::Value::Null), serde_json::json!({}))).await.unwrap();
    assert_eq!(first.status(), StatusCode::OK);
    *capture.0.lock().await = None;
    let changed = app.clone().oneshot(request(Some(serde_json::Value::Null), serde_json::json!({"changed":true}))).await.unwrap();
    assert_eq!(changed.status(), StatusCode::FORBIDDEN);
    assert!(capture.0.lock().await.is_none());
    let missing = app.clone().oneshot(request(None, serde_json::json!({}))).await.unwrap();
    assert_eq!(missing.status(), StatusCode::OK);
    *capture.0.lock().await = None;
    let replay = app.oneshot(request(None, serde_json::json!({}))).await.unwrap();
    assert_eq!(replay.status(), StatusCode::FORBIDDEN);
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
            [
                "computer_session_open",
                "computer_session_close",
                "computer_permissions",
                "computer_list_apps",
                "computer_list_windows",
                "computer_active_window",
                "computer_activate_app",
                "computer_screenshot",
                "computer_find",
                "computer_click",
                "computer_click_at",
                "computer_list_displays",
                "computer_focus_window",
                "computer_move_at",
                "computer_drag_at",
                "computer_paste",
                "computer_set_value",
                "computer_type",
                "computer_key",
                "computer_scroll",
            ]
            .into_iter()
            .map(|name| serde_json::json!({"name":name,"inputSchema":{"type":"object"}}))
            .collect()
        } else if kind.starts_with("host") {
            let revision = if kind == "host-stale" {
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
            } else {
                REVISION
            };
            let mut host_tools = vec![serde_json::json!({
                "name":"discover_current_entry",
                "inputSchema":{
                    "type":"object",
                    "required":["target_generation","ownership_generation"],
                    "properties":{
                        "target_generation":{"type":"integer","minimum":1},
                        "ownership_generation":{"type":"integer","minimum":1}
                    },
                    "additionalProperties":false
                },
                "_meta":{"local-agent-bridge/production-fence":host_fence_json(revision)}
            })];
            if kind == "host-extra" {
                host_tools.push(serde_json::json!({"name":"read","inputSchema":{"type":"object"}}));
            }
            host_tools
        } else {
            vec![
                serde_json::json!({"name":"read","inputSchema":{"type":"object"}}),
                serde_json::json!({"name":"run_command","inputSchema":{
                    "type":"object",
                    "properties":{"command":{"type":"string"}},
                    "required":["command"],
                    "additionalProperties":false
                }}),
            ]
        };
        return (
            StatusCode::OK,
            axum::Json(serde_json::json!({"jsonrpc":"2.0","id":id,"result":{"tools":tools}})),
        );
    }
    let mut result = serde_json::json!({"backend":kind});
    if kind.starts_with("host") {
        result["_meta"] = serde_json::json!({
            "local-agent-bridge/production-fence": host_fence_json(
                if kind == "host-stale" { "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" } else { REVISION }
            )
        });
    }
    (
        StatusCode::OK,
        axum::Json(serde_json::json!({"jsonrpc":"2.0","id":id,"result":result})),
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
    let run_command = list_json["result"]["tools"]
        .as_array()
        .unwrap()
        .iter()
        .find(|tool| tool["name"] == "run_command")
        .unwrap();
    assert!(
        run_command["inputSchema"]["properties"]
            .get("host_invocation_capability")
            .is_none(),
        "receipt-disabled tools/list must not advertise the protected capability"
    );
    assert_eq!(
        run_command["inputSchema"]["properties"]["command"]["type"],
        "string"
    );
    assert_eq!(
        run_command["inputSchema"]["required"],
        serde_json::json!(["command"])
    );
    for expected in [
        "computer_session_open",
        "computer_screenshot",
        "computer_find",
        "computer_click",
        "computer_click_at",
        "computer_drag_at",
        "computer_type",
        "computer_key",
        "computer_scroll",
    ] {
        assert!(
            names.contains(&expected),
            "merged tools/list missing {expected}"
        );
    }

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
    let response = app.clone().oneshot(computer_request).await.unwrap();
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

    drop(computer_calls);
    let screenshot_body = r#"{"jsonrpc":"2.0","id":"shot","method":"tools/call","params":{"name":"computer_screenshot","arguments":{}}}"#;
    let screenshot_request = Request::builder()
        .method("POST")
        .uri("/mcp")
        .header("content-type", "application/json")
        .header("mcp-method", "tools/call")
        .header("mcp-name", "computer_screenshot")
        .header("x-lab-ingress-key", KEY)
        .body(Body::from(screenshot_body))
        .unwrap();
    let response = app.clone().oneshot(screenshot_request).await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert!(computer_capture.0.lock().await.iter().any(|(_, body)| {
        body.get("params")
            .and_then(|v| v.get("name"))
            .and_then(serde_json::Value::as_str)
            == Some("computer_screenshot")
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

#[tokio::test]
async fn host_attestation_tool_is_capability_gated_routed_and_merged_into_tools_list() {
    let default_capture = JsonCapture::default();
    let host_capture = JsonCapture::default();
    let default_url = json_backend_server(default_capture.clone(), "default").await;
    let host_url = json_backend_server(host_capture.clone(), "host").await;
    let app = bridge(
        &default_url,
        vec![Capability::Discovery, Capability::HostAttestation],
    )
    .with_host_backend(&host_url, BACKEND_KEY, production_fence())
    .unwrap()
    .router();

    let list_body = r#"{"jsonrpc":"2.0","id":"list-host","method":"tools/list","params":{}}"#;
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
    assert!(names.contains(&"discover_current_entry"));

    let body = r#"{"jsonrpc":"2.0","id":"host","method":"tools/call","params":{"name":"discover_current_entry","arguments":{"target_generation":8,"ownership_generation":8}}}"#;
    let request = Request::builder()
        .method("POST")
        .uri("/mcp")
        .header("content-type", "application/json")
        .header("mcp-method", "tools/call")
        .header("mcp-name", "discover_current_entry")
        .header("x-lab-ingress-key", KEY)
        .body(Body::from(body))
        .unwrap();
    let response = app.oneshot(request).await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert!(host_capture.0.lock().await.iter().any(|(_, body)| {
        body.get("params")
            .and_then(|v| v.get("name"))
            .and_then(serde_json::Value::as_str)
            == Some("discover_current_entry")
    }));
    assert!(!default_capture.0.lock().await.iter().any(|(_, body)| {
        body.get("params")
            .and_then(|v| v.get("name"))
            .and_then(serde_json::Value::as_str)
            == Some("discover_current_entry")
    }));
}

#[tokio::test]
async fn host_attestation_tool_without_capability_fails_before_host_backend() {
    let default_capture = JsonCapture::default();
    let host_capture = JsonCapture::default();
    let default_url = json_backend_server(default_capture, "default").await;
    let host_url = json_backend_server(host_capture.clone(), "host").await;
    let app = bridge(&default_url, vec![Capability::Discovery])
        .with_host_backend(&host_url, BACKEND_KEY, production_fence())
        .unwrap()
        .router();
    let body = r#"{"jsonrpc":"2.0","id":"host-denied","method":"tools/call","params":{"name":"discover_current_entry","arguments":{"target_generation":8,"ownership_generation":8}}}"#;
    let request = Request::builder()
        .method("POST")
        .uri("/mcp")
        .header("content-type", "application/json")
        .header("mcp-method", "tools/call")
        .header("mcp-name", "discover_current_entry")
        .header("x-lab-ingress-key", KEY)
        .body(Body::from(body))
        .unwrap();
    let response = app.oneshot(request).await.unwrap();
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert!(host_capture.0.lock().await.is_empty());
}

#[tokio::test]
async fn host_backend_fence_mismatch_is_rejected_for_tools_list_and_call() {
    let default_capture = JsonCapture::default();
    let host_capture = JsonCapture::default();
    let default_url = json_backend_server(default_capture, "default").await;
    let host_url = json_backend_server(host_capture, "host-stale").await;
    let app = bridge(
        &default_url,
        vec![Capability::Discovery, Capability::HostAttestation],
    )
    .with_host_backend(&host_url, BACKEND_KEY, production_fence())
    .unwrap()
    .router();

    let list_request = Request::builder()
        .method("POST")
        .uri("/mcp")
        .header("content-type", "application/json")
        .header("mcp-method", "tools/list")
        .header("x-lab-ingress-key", KEY)
        .body(Body::from(
            r#"{"jsonrpc":"2.0","id":"stale-list","method":"tools/list","params":{}}"#,
        ))
        .unwrap();
    let list_response = app.clone().oneshot(list_request).await.unwrap();
    assert_eq!(list_response.status(), StatusCode::BAD_GATEWAY);

    let call_request = Request::builder()
        .method("POST")
        .uri("/mcp")
        .header("content-type", "application/json")
        .header("mcp-method", "tools/call")
        .header("mcp-name", "discover_current_entry")
        .header("x-lab-ingress-key", KEY)
        .body(Body::from(
            r#"{"jsonrpc":"2.0","id":"stale-call","method":"tools/call","params":{"name":"discover_current_entry","arguments":{"target_generation":8,"ownership_generation":8}}}"#,
        ))
        .unwrap();
    let call_response = app.oneshot(call_request).await.unwrap();
    assert_eq!(call_response.status(), StatusCode::BAD_GATEWAY);
}

#[tokio::test]
async fn dedicated_host_backend_rejects_unexpected_extra_tools() {
    let default_url = json_backend_server(JsonCapture::default(), "default").await;
    let host_url = json_backend_server(JsonCapture::default(), "host-extra").await;
    let app = bridge(
        &default_url,
        vec![Capability::Discovery, Capability::HostAttestation],
    )
    .with_host_backend(&host_url, BACKEND_KEY, production_fence())
    .unwrap()
    .router();
    let request = Request::builder()
        .method("POST")
        .uri("/mcp")
        .header("content-type", "application/json")
        .header("mcp-method", "tools/list")
        .header("x-lab-ingress-key", KEY)
        .body(Body::from(
            r#"{"jsonrpc":"2.0","id":"extra-host-tool","method":"tools/list","params":{}}"#,
        ))
        .unwrap();
    assert_eq!(
        app.oneshot(request).await.unwrap().status(),
        StatusCode::BAD_GATEWAY
    );
}
