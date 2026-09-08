use std::{
    fs,
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    os::unix::fs::PermissionsExt,
    process::{Child, Command, Stdio},
    thread,
    time::{Duration, Instant},
};

struct ChildGuard(Child);

impl Drop for ChildGuard {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

fn unused_loopback_addr() -> String {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let addr = listener.local_addr().unwrap();
    drop(listener);
    addr.to_string()
}

fn http_post(addr: &str, body: &str, backend_key: &str) -> std::io::Result<String> {
    let mut stream = TcpStream::connect(addr)?;
    stream.set_read_timeout(Some(Duration::from_secs(2)))?;
    let request = format!(
        "POST /mcp HTTP/1.1\r\nHost: {addr}\r\nContent-Type: application/json\r\nConnection: close\r\nX-Lab-Backend-Key: {backend_key}\r\nContent-Length: {}\r\n\r\n{}",
        body.as_bytes().len(),
        body
    );
    stream.write_all(request.as_bytes())?;
    let mut response = String::new();
    stream.read_to_string(&mut response)?;
    Ok(response)
}

#[test]
fn computer_runtime_process_starts_without_adaptive_agent_runtime() {
    let dir = tempfile::tempdir().unwrap();
    let key_path = dir.path().join("computer.key");
    let backend_key = "computer-runtime-independent-key-0001";
    fs::write(&key_path, backend_key).unwrap();
    fs::set_permissions(&key_path, fs::Permissions::from_mode(0o600)).unwrap();
    let addr = unused_loopback_addr();

    let mut command = Command::new(env!("CARGO_BIN_EXE_computer-runtime"));
    command
        .env_clear()
        .env("PATH", std::env::var("PATH").unwrap_or_default())
        .env("HOME", std::env::var("HOME").unwrap_or_default())
        .env("COMPUTER_RUNTIME_BIND", &addr)
        .env("COMPUTER_RUNTIME_BACKEND_KEY_FILE", &key_path)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());

    let child = command.spawn().unwrap();
    let mut child = ChildGuard(child);
    let deadline = Instant::now() + Duration::from_secs(5);
    let body = r#"{"jsonrpc":"2.0","id":"list","method":"tools/list"}"#;
    let response = loop {
        if let Some(status) = child.0.try_wait().unwrap() {
            panic!("computer-runtime exited before readiness: {status}");
        }
        match http_post(&addr, body, backend_key) {
            Ok(response) => break response,
            Err(_) if Instant::now() < deadline => thread::sleep(Duration::from_millis(50)),
            Err(error) => panic!("computer-runtime did not become ready: {error}"),
        }
    };

    assert!(response.starts_with("HTTP/1.1 200"), "unexpected response: {response}");
    assert!(response.contains("computer_screenshot"));
    assert!(response.contains("computer_click"));
    assert!(response.contains("computer_scroll"));

    let _ = child.0.kill();
    let _ = child.0.wait();
}
