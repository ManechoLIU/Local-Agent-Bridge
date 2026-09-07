use axum::{
    Router,
    body::Bytes,
    http::{HeaderMap, StatusCode},
    routing::post,
};
use std::{
    os::unix::fs::{PermissionsExt, symlink},
    process::{Child, Command, Stdio},
    time::Duration,
};
use tempfile::TempDir;
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::UnixStream,
};

const BACKEND_KEY: &str = "recovery-backend-0123456789abcdef0123456789abcdef";

struct ChildGuard(Option<Child>);

impl ChildGuard {
    fn new(child: Child) -> Self {
        Self(Some(child))
    }

    fn stop(&mut self) {
        if let Some(mut child) = self.0.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

impl Drop for ChildGuard {
    fn drop(&mut self) {
        self.stop();
    }
}

async fn backend(headers: HeaderMap, body: Bytes) -> (StatusCode, Bytes) {
    if headers
        .get("x-lab-backend-key")
        .and_then(|value| value.to_str().ok())
        != Some(BACKEND_KEY)
    {
        return (
            StatusCode::UNAUTHORIZED,
            Bytes::from_static(b"bad backend key"),
        );
    }
    (StatusCode::OK, body)
}

async fn backend_server() -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        axum::serve(listener, Router::new().route("/mcp", post(backend)))
            .await
            .unwrap();
    });
    format!("http://127.0.0.1:{}/mcp", addr.port())
}

fn spawn_bridge(root: &TempDir, backend_url: &str, rotate: bool) -> Child {
    spawn_bridge_custom(
        root,
        backend_url,
        &root.path().join("bridge.sock"),
        "runtime-recovery-capability",
        "discovery,file_read",
        rotate,
    )
}

fn spawn_bridge_custom(
    root: &TempDir,
    backend_url: &str,
    socket: &std::path::Path,
    capability_id: &str,
    capabilities: &str,
    rotate: bool,
) -> Child {
    let state = root.path().join("state");
    let backend_key = root.path().join("backend.key");
    if !backend_key.exists() {
        std::fs::write(&backend_key, BACKEND_KEY).unwrap();
        std::fs::set_permissions(&backend_key, std::fs::Permissions::from_mode(0o600)).unwrap();
    }
    let mut command = Command::new(env!("CARGO_BIN_EXE_local-tool-bridge"));
    command
        .env("LAB_BRIDGE_SOCKET", socket)
        .env("LAB_BRIDGE_BACKEND_URL", backend_url)
        .env("LAB_BRIDGE_BACKEND_KEY_FILE", &backend_key)
        .env("LAB_BRIDGE_STATE_DIR", &state)
        .env("LAB_BRIDGE_WORKSPACE_ID", "runtime-recovery-workspace")
        .env("LAB_BRIDGE_PRINCIPAL_ID", "runtime-recovery-principal")
        .env(
            "LAB_BRIDGE_EXECUTION_SESSION_ID",
            "runtime-recovery-session",
        )
        .env("LAB_BRIDGE_CAPABILITY_ID", capability_id)
        .env("LAB_BRIDGE_CAPABILITIES", capabilities)
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    if rotate {
        command
            .env("LAB_BRIDGE_ROTATE_MASTER_KEY", "1")
            .env("LAB_BRIDGE_EXPECTED_KEY_GENERATION", "1");
    }
    command.spawn().unwrap()
}

async fn wait_for_socket(path: &std::path::Path) {
    for _ in 0..100 {
        if let Ok(stream) = UnixStream::connect(path).await {
            drop(stream);
            return;
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    panic!("bridge socket did not become connectable");
}

async fn wait_for_exit(child: &mut Child) -> std::process::ExitStatus {
    for _ in 0..100 {
        if let Some(status) = child.try_wait().unwrap() {
            return status;
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    let _ = child.kill();
    panic!("bridge process did not exit when expected");
}

async fn raw_mcp(socket: &std::path::Path, request_id: &str) -> String {
    let body = r#"{"jsonrpc":"2.0","id":"rpc-1","method":"tools/call","params":{"name":"read","arguments":{}}}"#;
    let request = format!(
        "POST /mcp HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nMcp-Method: tools/call\r\nMcp-Name: read\r\nX-Request-Id: {request_id}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
        body.len(),
        body,
    );
    let mut stream = UnixStream::connect(socket).await.unwrap();
    stream.write_all(request.as_bytes()).await.unwrap();
    stream.flush().await.unwrap();
    let mut bytes = Vec::new();
    let mut chunk = [0_u8; 1024];
    tokio::time::timeout(Duration::from_secs(3), async {
        loop {
            let read = stream.read(&mut chunk).await.unwrap();
            if read == 0 {
                break;
            }
            bytes.extend_from_slice(&chunk[..read]);
            if bytes.windows(4).any(|window| window == b"\r\n\r\n") {
                break;
            }
        }
    })
    .await
    .unwrap();
    String::from_utf8_lossy(&bytes).into_owned()
}

#[tokio::test]
async fn process_restart_and_key_rotation_preserve_replay_fence() {
    let backend_url = backend_server().await;
    let root = tempfile::tempdir().unwrap();
    let socket = root.path().join("bridge.sock");

    let mut first = ChildGuard::new(spawn_bridge(&root, &backend_url, false));
    wait_for_socket(&socket).await;
    let response = raw_mcp(&socket, "provider-recovery-request-1").await;
    assert!(response.starts_with("HTTP/1.1 200"), "{response}");
    first.stop();

    let mut second = ChildGuard::new(spawn_bridge(&root, &backend_url, true));
    wait_for_socket(&socket).await;
    let replay = raw_mcp(&socket, "provider-recovery-request-1").await;
    assert!(replay.starts_with("HTTP/1.1 403"), "{replay}");
    let fresh = raw_mcp(&socket, "provider-recovery-request-2").await;
    assert!(fresh.starts_with("HTTP/1.1 200"), "{fresh}");

    let state: serde_json::Value = serde_json::from_slice(
        &std::fs::read(root.path().join("state/authority-state.json")).unwrap(),
    )
    .unwrap();
    assert_eq!(state["key_generation"], 2);
    assert!(!root.path().join("state/authority.key.1").exists());
    assert!(root.path().join("state/authority.key.2").is_file());
    let socket_mode = std::fs::metadata(&socket).unwrap().permissions().mode() & 0o777;
    let socket_parent_mode = std::fs::metadata(root.path()).unwrap().permissions().mode() & 0o777;
    let state_mode = std::fs::metadata(root.path().join("state"))
        .unwrap()
        .permissions()
        .mode()
        & 0o777;
    assert_eq!(socket_mode, 0o600);
    assert_eq!(socket_parent_mode, 0o700);
    assert_eq!(state_mode, 0o700);

    second.stop();
}

#[tokio::test]
async fn symlink_socket_parent_is_rejected_before_bind() {
    let backend_url = backend_server().await;
    let root = tempfile::tempdir().unwrap();
    let real_parent = root.path().join("real-parent");
    std::fs::create_dir(&real_parent).unwrap();
    std::fs::set_permissions(&real_parent, std::fs::Permissions::from_mode(0o700)).unwrap();
    let link_parent = root.path().join("linked-parent");
    symlink(&real_parent, &link_parent).unwrap();
    let socket = link_parent.join("bridge.sock");

    let mut child = spawn_bridge_custom(
        &root,
        &backend_url,
        &socket,
        "symlink-parent-capability",
        "discovery,file_read",
        false,
    );
    let status = wait_for_exit(&mut child).await;
    assert!(!status.success());
    assert!(!real_parent.join("bridge.sock").exists());
}

#[tokio::test]
async fn restart_rejects_narrowed_capability_scope_without_new_identity() {
    let backend_url = backend_server().await;
    let root = tempfile::tempdir().unwrap();
    let socket = root.path().join("bridge.sock");
    let capability_id = "scope-reuse-capability";

    let mut first = ChildGuard::new(spawn_bridge_custom(
        &root,
        &backend_url,
        &socket,
        capability_id,
        "discovery,file_read",
        false,
    ));
    wait_for_socket(&socket).await;
    first.stop();

    let mut narrowed = spawn_bridge_custom(
        &root,
        &backend_url,
        &socket,
        capability_id,
        "discovery",
        false,
    );
    let status = wait_for_exit(&mut narrowed).await;
    assert!(!status.success());
}
