#[cfg(target_os = "macos")]
mod macos {
    use axum::{
        Router,
        body::Body,
        http::{Request, StatusCode},
        routing::post,
    };
    use bridge_security_core::{
        Capability, CapabilityAuthority, CapabilityGrant, ExecutionContext,
    };
    use computer_runtime::{ComputerRuntime, NativeMacProvider};
    use local_tool_bridge::{BridgeIngress, ToolPolicy};
    use serde_json::{Value, json};
    use tower::ServiceExt;

    const INGRESS_KEY: &str = "bridge-computer-ingress-0123456789abcdef0123456789abcdef";
    const DEFAULT_KEY: &str = "default-backend-key-0123456789abcdef0123456789abcdef";
    const COMPUTER_KEY: &str = "computer-backend-key-0123456789abcdef0123456789abcdef";

    async fn default_backend() -> axum::Json<Value> {
        axum::Json(json!({"jsonrpc":"2.0","id":"unused","result":{"tools":[]}}))
    }

    async fn serve(app: Router) -> String {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        format!("http://127.0.0.1:{}/mcp", address.port())
    }

    fn bridge(default_url: &str, computer_url: &str) -> Router {
        let context = ExecutionContext {
            principal_id: "bridge-e2e-principal".into(),
            execution_session_id: "bridge-e2e-session".into(),
            capability_id: "bridge-e2e-capability".into(),
            workspace_id: "bridge-e2e-workspace".into(),
        };
        let grant = CapabilityGrant {
            principal_id: context.principal_id.clone(),
            execution_session_id: context.execution_session_id.clone(),
            capability_id: context.capability_id.clone(),
            workspace_id: context.workspace_id.clone(),
            capabilities: vec![Capability::Discovery, Capability::Computer],
        };
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_millis() as u64;
        let mut authority = CapabilityAuthority::new_for_test([41_u8; 32]);
        let issued = authority.issue(grant, now - 100, 60_000, 1).unwrap();
        let state_dir = tempfile::tempdir().unwrap().keep();
        authority.save_private(&state_dir).unwrap();
        BridgeIngress::new_with_policy(
            authority,
            issued,
            context,
            INGRESS_KEY.as_bytes(),
            default_url,
            DEFAULT_KEY,
            ToolPolicy::catdesk_default(),
            state_dir,
        )
        .unwrap()
        .with_computer_backend(computer_url, COMPUTER_KEY)
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
        serde_json::from_slice(&body).unwrap()
    }

    #[tokio::test]
    async fn real_native_desktop_state_flows_through_local_tool_bridge_computer_capability() {
        let computer_app = ComputerRuntime::new(COMPUTER_KEY.as_bytes(), NativeMacProvider)
            .unwrap()
            .router();
        let computer_url = serve(computer_app).await;
        let default_url = serve(Router::new().route("/mcp", post(default_backend))).await;
        let app = bridge(&default_url, &computer_url);

        let opened = app
            .clone()
            .oneshot(request("open", "computer_session_open", json!({})))
            .await
            .unwrap();
        assert_eq!(opened.status(), StatusCode::OK);
        let opened = response_json(opened).await;
        let session = opened["result"]["structuredContent"]["session_id"]
            .as_str()
            .unwrap();

        let apps = app
            .clone()
            .oneshot(request(
                "apps",
                "computer_list_apps",
                json!({"session_id":session,"expected_session_generation":1}),
            ))
            .await
            .unwrap();
        assert_eq!(apps.status(), StatusCode::OK);
        let apps = response_json(apps).await;
        let listed = apps["result"]["structuredContent"]["apps"]
            .as_array()
            .unwrap();
        assert!(
            !listed.is_empty(),
            "expected real running macOS apps through Bridge"
        );
        assert!(
            listed
                .iter()
                .all(|app| app["pid"].as_i64().unwrap_or(0) > 0)
        );

        let active = app
            .clone()
            .oneshot(request(
                "active",
                "computer_active_window",
                json!({"session_id":session,"expected_session_generation":1}),
            ))
            .await
            .unwrap();
        assert_eq!(active.status(), StatusCode::OK);
        let active = response_json(active).await;
        let observation = active["result"]["structuredContent"]["observation_id"]
            .as_str()
            .unwrap();
        let window = &active["result"]["structuredContent"]["window"];
        assert!(!window.is_null(), "expected a real active macOS window");
        let window_id = window["window_id"].as_str().unwrap();
        let pid = window["pid"].as_i64().unwrap();

        let permissions = app
            .clone()
            .oneshot(request(
                "permissions",
                "computer_permissions",
                json!({"session_id":session,"expected_session_generation":1}),
            ))
            .await
            .unwrap();
        assert_eq!(permissions.status(), StatusCode::OK);
        let permissions = response_json(permissions).await;
        let screen_recording = permissions["result"]["structuredContent"]["screen_recording"]
            .as_bool()
            .unwrap_or(false);

        let screenshot = app
            .oneshot(request(
                "shot",
                "computer_screenshot",
                json!({
                    "session_id":session,
                    "expected_session_generation":1,
                    "observation_id":observation,
                    "window_id":window_id,
                    "pid":pid,
                    "max_width":640,
                    "max_height":480
                }),
            ))
            .await
            .unwrap();
        if screen_recording {
            assert_eq!(screenshot.status(), StatusCode::OK);
            let screenshot = response_json(screenshot).await;
            assert_eq!(
                screenshot["result"]["structuredContent"]["mime_type"],
                "image/png"
            );
            assert!(
                screenshot["result"]["structuredContent"]["bytes"]
                    .as_u64()
                    .unwrap()
                    > 0
            );
            assert!(
                screenshot["result"]["structuredContent"]["width"]
                    .as_u64()
                    .unwrap()
                    <= 640
            );
            assert!(
                screenshot["result"]["structuredContent"]["height"]
                    .as_u64()
                    .unwrap()
                    <= 480
            );
        } else {
            assert_eq!(screenshot.status(), StatusCode::FORBIDDEN);
        }
    }
}
