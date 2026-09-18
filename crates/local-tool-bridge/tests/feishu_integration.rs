use axum::{
    body::Body,
    http::{Request, StatusCode},
};
use bridge_security_core::{Capability, CapabilityAuthority, CapabilityGrant, ExecutionContext};
use collaboration_adapter::LarkCliProvider;
use local_tool_bridge::BridgeIngress;
use std::{
    fs,
    os::unix::fs::PermissionsExt,
    path::Path,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tower::ServiceExt;

const KEY: &str = "0123456789abcdef0123456789abcdef0123456789abcdef";
const BACKEND_KEY: &str = "backend-0123456789abcdef0123456789abcdef0123456789abcdef";
const DEAD_BACKEND: &str = "http://127.0.0.1:9/mcp";

fn bridge(state_dir: &Path) -> BridgeIngress {
    let context = ExecutionContext {
        principal_id: "feishu-test-principal".into(),
        execution_session_id: "feishu-test-session".into(),
        capability_id: "feishu-test-capability".into(),
        workspace_id: "feishu-test-workspace".into(),
    };
    let grant = CapabilityGrant {
        principal_id: context.principal_id.clone(),
        execution_session_id: context.execution_session_id.clone(),
        capability_id: context.capability_id.clone(),
        workspace_id: context.workspace_id.clone(),
        capabilities: vec![
            Capability::Discovery,
            Capability::CollaborationRead,
            Capability::CollaborationWrite,
            Capability::ProviderAdmin,
        ],
    };
    let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_millis() as u64;
    let mut authority = CapabilityAuthority::new_for_test([77_u8; 32]);
    let issued = authority.issue(grant, now - 1_000, 60_000, 1).unwrap();
    authority.save_private(state_dir).unwrap();
    BridgeIngress::new(
        authority,
        issued,
        context,
        KEY.as_bytes(),
        DEAD_BACKEND,
        BACKEND_KEY,
        state_dir,
    )
    .unwrap()
}

fn call_request(tool: &str, body: &str) -> Request<Body> {
    Request::builder()
        .method("POST")
        .uri("/mcp")
        .header("content-type", "application/json")
        .header("mcp-method", "tools/call")
        .header("mcp-name", tool)
        .header("x-lab-ingress-key", KEY)
        .body(Body::from(body.to_string()))
        .unwrap()
}

fn fake_lark_cli(root: &Path) -> LarkCliProvider {
    fs::create_dir_all(root).unwrap();
    let path = root.join("fake-lark-cli");
    fs::write(
        &path,
        "#!/bin/sh\ncase \" $* \" in\n  *\" whoami \"*) printf '%s\\n' '{\"available\":true,\"token\":\"top-secret\",\"nested\":{\"client_secret\":\"hidden\",\"auth_url\":\"https://secret.example\"}}' ;;\n  *) printf '%s\\n' '{\"ok\":true,\"source\":\"fake-lark-cli\"}' ;;\nesac\n",
    )
    .unwrap();
    fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).unwrap();
    LarkCliProvider::new(&path, None).unwrap()
}

async fn response_text(response: axum::response::Response) -> String {
    let bytes = axum::body::to_bytes(response.into_body(), 1024 * 1024).await.unwrap();
    String::from_utf8(bytes.to_vec()).unwrap()
}

async fn response_json(response: axum::response::Response) -> serde_json::Value {
    let text = response_text(response).await;
    serde_json::from_str(&text)
        .unwrap_or_else(|error| panic!("response is not JSON: {error}; body={text:?}"))
}

fn private_test_root(name: &str) -> std::path::PathBuf {
    let root = std::env::current_dir()
        .unwrap()
        .join(".lab-staging")
        .join("feishu-router-tests")
        .join(name);
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).unwrap();
    root
}

#[tokio::test]
async fn feishu_call_is_unavailable_when_provider_is_not_configured() {
    let root = private_test_root("provider-absent");
    let app = bridge(&root).router();
    let body = r#"{"jsonrpc":"2.0","id":"search","method":"tools/call","params":{"name":"feishu_chat_search","arguments":{"query":"产品"}}}"#;
    let response = app
        .oneshot(call_request("feishu_chat_search", body))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
    assert!(response_text(response).await.contains("Feishu provider is not configured"));
}

#[tokio::test]
async fn feishu_call_runs_local_provider_even_when_default_backend_is_unreachable() {
    let root = private_test_root("provider-present");
    let provider = fake_lark_cli(&root.join("bin"));
    let app = bridge(&root)
        .with_feishu_provider(provider, Duration::from_secs(2), 64 * 1024)
        .unwrap()
        .router();

    let body = r#"{"jsonrpc":"2.0","id":"search","method":"tools/call","params":{"name":"feishu_chat_search","arguments":{"query":"产品"}}}"#;
    let response = app
        .oneshot(call_request("feishu_chat_search", body))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let value = response_json(response).await;
    assert_eq!(value["result"]["structuredContent"]["source"], "fake-lark-cli");
    assert_eq!(value["result"]["structuredContent"]["ok"], true);
}

#[tokio::test]
async fn feishu_status_redacts_credential_material_recursively() {
    let root = private_test_root("status-redaction");
    let provider = fake_lark_cli(&root.join("bin"));
    let app = bridge(&root)
        .with_feishu_provider(provider, Duration::from_secs(2), 64 * 1024)
        .unwrap()
        .router();

    let body = r#"{"jsonrpc":"2.0","id":"status","method":"tools/call","params":{"name":"feishu_status","arguments":{}}}"#;
    let response = app
        .oneshot(call_request("feishu_status", body))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let value = response_json(response).await;
    assert_eq!(value["result"]["structuredContent"]["available"], true);
    assert_eq!(value["result"]["structuredContent"]["token"], "[REDACTED]");
    assert_eq!(
        value["result"]["structuredContent"]["nested"]["client_secret"],
        "[REDACTED]"
    );
    assert_eq!(
        value["result"]["structuredContent"]["nested"]["auth_url"],
        "[REDACTED]"
    );
}


#[test]
fn feishu_admin_tools_require_provider_admin_capability() {
    use local_tool_bridge::required_capability;

    for tool in [
        "feishu_setup_start",
        "feishu_setup_poll",
        "feishu_auth_begin",
        "feishu_auth_complete",
    ] {
        assert_eq!(
            required_capability("tools/call", tool).unwrap(),
            Capability::ProviderAdmin
        );
    }
    assert!(required_capability("tools/call", "feishu_setup_raw").is_err());
}


fn fake_feishu_admin(root: &Path) -> collaboration_adapter::LarkCliAdminSpec {
    use collaboration_adapter::{LarkCliAdminSpec, LarkCliInstallSpec};

    let bin = root.join("admin-bin");
    fs::create_dir_all(&bin).unwrap();
    let npm = bin.join("npm");
    fs::write(&npm, "#!/bin/sh\nprintf '%s\\n' '{\"installed\":true}'\n").unwrap();
    fs::set_permissions(&npm, fs::Permissions::from_mode(0o700)).unwrap();

    let install_root = root.join("provider-npm");
    let cli = install_root
        .join("node_modules")
        .join("@larksuite")
        .join("cli")
        .join("bin")
        .join("lark-cli");
    fs::create_dir_all(cli.parent().unwrap()).unwrap();
    fs::write(
        &cli,
        r#"#!/bin/sh
case " $* " in
  *" --no-wait "*) printf '%s\n' '{"verification_url":"https://example.test/verify","device_code":"dc_test_123","expires_in":600}' ;;
  *" --device-code "*) printf '%s\n' '{"status":"authorized"}' ;;
  *" config init "*) printf '%s\n' 'Open https://example.test/config to continue'; sleep 1 ;;
  *) printf '%s\n' '{"ok":true}' ;;
esac
"#,
    ).unwrap();
    fs::set_permissions(&cli, fs::Permissions::from_mode(0o700)).unwrap();

    LarkCliAdminSpec::new(
        LarkCliInstallSpec::new(&npm, &install_root).unwrap(),
        root.join("provider-home"),
        root.join("provider-config"),
    ).unwrap()
}

#[tokio::test]
async fn feishu_admin_install_runs_as_bridge_local_background_job() {
    let root = private_test_root("admin-install");
    let app = bridge(&root)
        .with_feishu_admin(fake_feishu_admin(&root), Duration::from_secs(2), 64 * 1024)
        .unwrap()
        .router();

    let body = r#"{"jsonrpc":"2.0","id":"start","method":"tools/call","params":{"name":"feishu_setup_start","arguments":{"action":"install"}}}"#;
    let response = app.clone()
        .oneshot(call_request("feishu_setup_start", body))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let value = response_json(response).await;
    let job_id = value["result"]["structuredContent"]["job_id"]
        .as_str().unwrap().to_string();

    let mut terminal = None;
    for attempt in 0..50 {
        let body = serde_json::json!({
            "jsonrpc":"2.0",
            "id":format!("poll-{attempt}"),
            "method":"tools/call",
            "params":{
                "name":"feishu_setup_poll",
                "arguments":{"job_id":job_id}
            }
        }).to_string();
        let response = app.clone()
            .oneshot(call_request("feishu_setup_poll", &body))
            .await
            .unwrap();
        let value = response_json(response).await;
        let state = value["result"]["structuredContent"]["state"].as_str().unwrap();
        if state != "running" {
            terminal = Some(state.to_string());
            break;
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    assert_eq!(terminal.as_deref(), Some("succeeded"));
}

#[tokio::test]
async fn feishu_auth_flow_hides_device_code_behind_opaque_flow_id() {
    let root = private_test_root("admin-auth");
    let app = bridge(&root)
        .with_feishu_admin(fake_feishu_admin(&root), Duration::from_secs(2), 64 * 1024)
        .unwrap()
        .router();

    let begin = r#"{"jsonrpc":"2.0","id":"auth","method":"tools/call","params":{"name":"feishu_auth_begin","arguments":{}}}"#;
    let response = app.clone()
        .oneshot(call_request("feishu_auth_begin", begin))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let value = response_json(response).await;
    let data = &value["result"]["structuredContent"];
    assert_eq!(data["verification_url"], "https://example.test/verify");
    assert!(data.get("device_code").is_none());
    let flow_id = data["flow_id"].as_str().unwrap().to_string();

    let complete = serde_json::json!({
        "jsonrpc":"2.0",
        "id":"complete",
        "method":"tools/call",
        "params":{
            "name":"feishu_auth_complete",
            "arguments":{"flow_id":flow_id}
        }
    }).to_string();
    let response = app.clone()
        .oneshot(call_request("feishu_auth_complete", &complete))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let value = response_json(response).await;
    assert_eq!(value["result"]["structuredContent"]["status"], "authorized");
}
