use bridge_security_core::{Capability, CapabilityAuthority, CapabilityGrant};
use std::{
    collections::BTreeMap,
    os::unix::{
        fs::{MetadataExt, PermissionsExt},
        net::{UnixListener, UnixStream},
    },
    path::Path,
    process::{Command, Output, Stdio},
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

const KEY: &str = "config-check-backend-key-0123456789abcdef";

fn configured(root: &Path) -> Command {
    let state = root.join("state");
    let mut authority = CapabilityAuthority::new_for_test([71; 32]);
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_millis() as u64;
    authority
        .issue(
            CapabilityGrant {
                principal_id: "config-principal".into(),
                execution_session_id: "config-session".into(),
                capability_id: "config-capability".into(),
                workspace_id: "config-workspace".into(),
                capabilities: vec![Capability::Discovery, Capability::FileRead],
            },
            now,
            60_000,
            1,
        )
        .unwrap();
    authority.save_private(&state).unwrap();
    let key = root.join("backend.key");
    std::fs::write(&key, KEY).unwrap();
    std::fs::set_permissions(&key, std::fs::Permissions::from_mode(0o600)).unwrap();
    let mut command = Command::new(env!("CARGO_BIN_EXE_local-tool-bridge"));
    command
        .env_clear()
        .env("LAB_BRIDGE_SOCKET", root.join("bridge.sock"))
        .env("LAB_BRIDGE_BACKEND_URL", "http://127.0.0.1:9/mcp")
        .env("LAB_BRIDGE_BACKEND_KEY_FILE", key)
        .env("LAB_BRIDGE_STATE_DIR", state)
        .env("LAB_BRIDGE_WORKSPACE_ID", "config-workspace")
        .env("LAB_BRIDGE_PRINCIPAL_ID", "config-principal")
        .env("LAB_BRIDGE_EXECUTION_SESSION_ID", "config-session")
        .env("LAB_BRIDGE_CAPABILITY_ID", "config-capability")
        .env("LAB_BRIDGE_CAPABILITIES", "discovery,file_read");
    command
}

fn run_bounded(command: &mut Command) -> Output {
    let mut child = command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let deadline = Instant::now() + Duration::from_secs(3);
    while child.try_wait().unwrap().is_none() && Instant::now() < deadline {
        thread::sleep(Duration::from_millis(10));
    }
    if child.try_wait().unwrap().is_none() {
        child.kill().unwrap();
    }
    child.wait_with_output().unwrap()
}

fn state_snapshot(root: &Path) -> BTreeMap<String, (u32, Vec<u8>)> {
    std::fs::read_dir(root)
        .unwrap()
        .map(|entry| {
            let entry = entry.unwrap();
            (
                entry.file_name().to_string_lossy().into_owned(),
                (
                    entry.metadata().unwrap().mode(),
                    std::fs::read(entry.path()).unwrap(),
                ),
            )
        })
        .collect()
}

#[test]
fn check_config_preserves_live_socket_and_existing_authority() {
    let root = tempfile::tempdir().unwrap();
    let mut command = configured(root.path());
    let socket = root.path().join("bridge.sock");
    let listener = UnixListener::bind(&socket).unwrap();
    std::fs::set_permissions(&socket, std::fs::Permissions::from_mode(0o660)).unwrap();
    let before = std::fs::symlink_metadata(&socket).unwrap();
    let state = state_snapshot(&root.path().join("state"));
    let output = run_bounded(command.arg("--check-config"));
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        serde_json::from_slice::<serde_json::Value>(&output.stdout).unwrap(),
        serde_json::json!({"check_config":true,"production_mode":false,"listening":false})
    );
    assert!(output.stderr.is_empty());
    let after = std::fs::symlink_metadata(&socket).unwrap();
    assert_eq!(
        (before.dev(), before.ino(), before.mode()),
        (after.dev(), after.ino(), after.mode())
    );
    assert_eq!(state, state_snapshot(&root.path().join("state")));
    let _client = UnixStream::connect(&socket).unwrap();
    listener.set_nonblocking(true).unwrap();
    assert!(listener.accept().is_ok());
    assert!(!String::from_utf8_lossy(&output.stdout).contains(KEY));
    assert!(!String::from_utf8_lossy(&output.stdout).contains("127.0.0.1"));
}

#[test]
fn check_config_validates_late_configuration_without_touching_socket() {
    let root = tempfile::tempdir().unwrap();
    let mut command = configured(root.path());
    let socket = root.path().join("bridge.sock");
    let _listener = UnixListener::bind(&socket).unwrap();
    let before = std::fs::symlink_metadata(&socket).unwrap();
    let state = state_snapshot(&root.path().join("state"));
    let output = run_bounded(
        command
            .arg("--check-config")
            .env("LAB_FEISHU_TIMEOUT_MS", "0"),
    );
    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
    assert!(String::from_utf8_lossy(&output.stderr).contains("LAB_FEISHU_CLI is required"));
    let after = std::fs::symlink_metadata(&socket).unwrap();
    assert_eq!((before.ino(), before.mode()), (after.ino(), after.mode()));
    assert_eq!(state, state_snapshot(&root.path().join("state")));
}

#[test]
fn check_config_refuses_missing_authority_missing_lease_and_rotation() {
    for fault in ["authority", "lease", "rotation"] {
        let root = tempfile::tempdir().unwrap();
        let mut command = configured(root.path());
        let original = state_snapshot(&root.path().join("state"));
        match fault {
            "authority" => {
                command.env("LAB_BRIDGE_STATE_DIR", root.path().join("missing-state"));
            }
            "lease" => {
                command.env("LAB_BRIDGE_CAPABILITY_ID", "missing-capability");
            }
            "rotation" => {
                command
                    .env("LAB_BRIDGE_ROTATE_MASTER_KEY", "1")
                    .env("LAB_BRIDGE_EXPECTED_KEY_GENERATION", "1");
            }
            _ => unreachable!(),
        }
        let output = run_bounded(command.arg("--check-config"));
        assert!(!output.status.success(), "{fault}");
        assert!(output.stdout.is_empty(), "{fault}");
        assert!(!root.path().join("missing-state").exists(), "{fault}");
        assert!(!root.path().join("bridge.sock").exists(), "{fault}");
        assert_eq!(
            original,
            state_snapshot(&root.path().join("state")),
            "{fault}"
        );
    }
}

#[test]
fn unknown_arguments_are_rejected_before_environment_reads() {
    for args in [
        vec!["--unknown"],
        vec!["--check-config", "--unknown"],
        vec!["--check-config", "--check-config"],
    ] {
        let output = run_bounded(
            Command::new(env!("CARGO_BIN_EXE_local-tool-bridge"))
                .env_clear()
                .args(args),
        );
        assert!(!output.status.success());
        assert!(output.stdout.is_empty());
        assert!(
            String::from_utf8_lossy(&output.stderr).contains("unsupported command-line arguments"),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
    }
}

#[test]
fn check_config_accepts_canonical_production_fence_and_rejects_side_socket() {
    let root = tempfile::tempdir().unwrap();
    let mut command = configured(root.path());
    let manifest = root.path().join("release.json");
    let revision = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    let workspace = "/Users/echoman/Documents/ChatGPT/Local-Agent-Bridge";
    std::fs::write(&manifest, serde_json::json!({
        "connector_id":"local-agent-bridge-production", "contract_version":"lab-production-connector-v1",
        "release_revision":revision, "endpoint_generation":7, "fail_closed":true, "workspace":workspace,
        "tunnel":{"profile_name":"local-agent-bridge-production"},
        "bridge":{"contract_version":"lab-production-connector-v1", "release_revision":revision,
            "endpoint_generation":7,"capability_ttl_ms":86_400_000,"capability_renew_before_ms":3_600_000}
    }).to_string()).unwrap();
    std::fs::set_permissions(&manifest, std::fs::Permissions::from_mode(0o600)).unwrap();
    command
        .arg("--check-config")
        .env(
            "LAB_BRIDGE_SOCKET",
            "/tmp/local-agent-bridge-production/bridge.sock",
        )
        .env(
            "LAB_PRODUCTION_CONNECTOR_ID",
            "local-agent-bridge-production",
        )
        .env("LAB_PRODUCTION_RELEASE_MANIFEST_FILE", &manifest)
        .env(
            "LAB_PRODUCTION_CONTRACT_VERSION",
            "lab-production-connector-v1",
        )
        .env("LAB_PRODUCTION_RELEASE_REVISION", revision)
        .env("LAB_PRODUCTION_ENDPOINT_GENERATION", "7")
        .env("LAB_PRODUCTION_WORKSPACE", workspace)
        .env(
            "LAB_PRODUCTION_TUNNEL_PROFILE_NAME",
            "local-agent-bridge-production",
        )
        .env("LAB_BRIDGE_CAPABILITY_TTL_MS", "86400000")
        .env("LAB_BRIDGE_CAPABILITY_RENEW_BEFORE_MS", "3600000")
        .env("LAB_HOST_BACKEND_URL", "http://127.0.0.1:9/mcp")
        .env("LAB_HOST_BACKEND_KEY_FILE", root.path().join("backend.key"))
        .env("LAB_HOST_ADAPTER_SOCKET", root.path().join("host.sock"))
        .env(
            "LAB_RUNTIME_HOST_TOOL_HOOK_SOCKET",
            root.path().join("hook.sock"),
        )
        .env("LAB_COMPUTER_BACKEND_URL", "http://127.0.0.1:9/mcp")
        .env(
            "LAB_COMPUTER_BACKEND_KEY_FILE",
            root.path().join("backend.key"),
        )
        .env("LAB_PROCESS_BACKEND_URL", "http://127.0.0.1:9/mcp")
        .env(
            "LAB_PROCESS_BACKEND_KEY_FILE",
            root.path().join("backend.key"),
        );
    let state = state_snapshot(&root.path().join("state"));
    let output = run_bounded(&mut command);
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        serde_json::from_slice::<serde_json::Value>(&output.stdout).unwrap(),
        serde_json::json!({"check_config":true,"production_mode":true,"listening":false})
    );
    assert!(output.stderr.is_empty());
    assert_eq!(state, state_snapshot(&root.path().join("state")));

    let side = root.path().join("side.sock");
    let output = run_bounded(command.env("LAB_BRIDGE_SOCKET", &side));
    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
    assert!(
        String::from_utf8_lossy(&output.stderr)
            .contains("production Bridge must use canonical socket")
    );
    assert!(!side.exists());
    assert_eq!(state, state_snapshot(&root.path().join("state")));
}
