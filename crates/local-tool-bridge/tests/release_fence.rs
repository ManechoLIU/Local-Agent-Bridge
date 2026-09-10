use local_tool_bridge::validate_production_release_manifest;
use std::process::Command;

const REVISION: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const WORKSPACE: &str = "/Users/echoman/Documents/ChatGPT/Local-Agent-Bridge";
const PROFILE: &str = "local-agent-bridge-production";
const CONTRACT: &str = "lab-production-connector-v1";
const PRODUCTION_SOCKET: &str = "/tmp/local-agent-bridge-production/bridge.sock";

fn manifest() -> String {
    manifest_value().to_string()
}

fn manifest_value() -> serde_json::Value {
    serde_json::json!({
        "connector_id": "local-agent-bridge-production",
        "contract_version": CONTRACT,
        "release_revision": REVISION,
        "endpoint_generation": 7,
        "fail_closed": true,
        "workspace": WORKSPACE,
        "tunnel": {"profile_name": PROFILE},
        "bridge": {
            "contract_version": CONTRACT,
            "release_revision": REVISION,
            "endpoint_generation": 7
        }
    })
}

#[test]
fn production_bridge_release_fence_accepts_only_exact_manifest_contract() {
    let fence = validate_production_release_manifest(
        &manifest(),
        CONTRACT,
        REVISION,
        7,
        WORKSPACE,
        PROFILE,
    )
    .unwrap();
    assert_eq!(fence.contract_version, CONTRACT);
    assert_eq!(fence.release_revision, REVISION);
    assert_eq!(fence.endpoint_generation, 7);
    assert_eq!(fence.workspace, WORKSPACE);
    assert_eq!(fence.tunnel_profile_name, PROFILE);
}

#[test]
fn production_bridge_release_fence_rejects_stale_or_legacy_values() {
    for (contract, revision, generation, workspace, profile) in [
        ("old-v0", REVISION, 7, WORKSPACE, PROFILE),
        (
            CONTRACT,
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            7,
            WORKSPACE,
            PROFILE,
        ),
        (CONTRACT, REVISION, 6, WORKSPACE, PROFILE),
        (
            CONTRACT,
            REVISION,
            7,
            "/private/tmp/lab-catdesk-candidate-e2e/workspace",
            PROFILE,
        ),
        (CONTRACT, REVISION, 7, WORKSPACE, "local-agent-bridge-poc"),
    ] {
        assert!(
            validate_production_release_manifest(
                &manifest(),
                contract,
                revision,
                generation,
                workspace,
                profile,
            )
            .is_err()
        );
    }
}

#[test]
fn production_bridge_release_fence_rejects_noncanonical_manifest_identity_and_shape() {
    for (path, value) in [
        (vec!["connector_id"], serde_json::json!("legacy-connector")),
        (vec!["fail_closed"], serde_json::json!(false)),
        (vec!["release_revision"], serde_json::json!("short-sha")),
        (vec!["endpoint_generation"], serde_json::json!(0)),
        (
            vec!["workspace"],
            serde_json::json!("/private/tmp/lab-catdesk-candidate-e2e/workspace"),
        ),
        (
            vec!["tunnel", "profile_name"],
            serde_json::json!("local-agent-bridge-poc"),
        ),
    ] {
        let mut candidate = manifest_value();
        let (last, parents) = path.split_last().unwrap();
        let mut target = &mut candidate;
        for key in parents {
            target = target.get_mut(*key).unwrap();
        }
        target[*last] = value;
        assert!(
            validate_production_release_manifest(
                &candidate.to_string(),
                CONTRACT,
                REVISION,
                7,
                WORKSPACE,
                PROFILE,
            )
            .is_err(),
            "manifest mutation at {path:?} must fail closed"
        );
    }
}

#[test]
fn production_bridge_release_fence_rejects_bridge_subsection_drift() {
    for (key, value) in [
        ("contract_version", serde_json::json!("old-v0")),
        (
            "release_revision",
            serde_json::json!("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
        ),
        ("endpoint_generation", serde_json::json!(6)),
    ] {
        let mut candidate = manifest_value();
        candidate["bridge"][key] = value;
        assert!(
            validate_production_release_manifest(
                &candidate.to_string(),
                CONTRACT,
                REVISION,
                7,
                WORKSPACE,
                PROFILE,
            )
            .is_err(),
            "Bridge manifest mutation at {key} must fail closed"
        );
    }
}

#[test]
fn production_bridge_release_fence_rejects_malformed_or_nonimmutable_observation() {
    assert!(
        validate_production_release_manifest(
            "not-json", CONTRACT, REVISION, 7, WORKSPACE, PROFILE,
        )
        .is_err()
    );
    assert!(
        validate_production_release_manifest(
            &manifest(),
            CONTRACT,
            "short-sha",
            7,
            WORKSPACE,
            PROFILE,
        )
        .is_err()
    );
    assert!(
        validate_production_release_manifest(
            &manifest(),
            CONTRACT,
            REVISION,
            0,
            WORKSPACE,
            PROFILE,
        )
        .is_err()
    );
}

#[test]
fn production_bridge_with_host_backend_refuses_to_start_without_release_fence() {
    let root = tempfile::tempdir().unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_local-tool-bridge"))
        .env("LAB_BRIDGE_SOCKET", PRODUCTION_SOCKET)
        .env("LAB_BRIDGE_BACKEND_URL", "http://127.0.0.1:30101/mcp")
        .env(
            "LAB_BRIDGE_BACKEND_KEY_FILE",
            root.path().join("missing-backend.key"),
        )
        .env("LAB_BRIDGE_STATE_DIR", root.path().join("state"))
        .env("LAB_HOST_BACKEND_URL", "http://127.0.0.1:30102/mcp")
        .env(
            "LAB_HOST_BACKEND_KEY_FILE",
            root.path().join("missing-host.key"),
        )
        .env(
            "LAB_PRODUCTION_CONNECTOR_ID",
            "local-agent-bridge-production",
        )
        .env_remove("LAB_PRODUCTION_RELEASE_MANIFEST_FILE")
        .env_remove("LAB_PRODUCTION_CONTRACT_VERSION")
        .env_remove("LAB_PRODUCTION_RELEASE_REVISION")
        .env_remove("LAB_PRODUCTION_ENDPOINT_GENERATION")
        .env_remove("LAB_PRODUCTION_WORKSPACE")
        .env_remove("LAB_PRODUCTION_TUNNEL_PROFILE_NAME")
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr)
            .contains("LAB_PRODUCTION_RELEASE_MANIFEST_FILE is required")
    );
    assert!(!root.path().join("state").exists());
}

#[test]
fn production_bridge_rejects_observed_connector_identity_before_state_creation() {
    let root = tempfile::tempdir().unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_local-tool-bridge"))
        .env("LAB_BRIDGE_SOCKET", PRODUCTION_SOCKET)
        .env("LAB_BRIDGE_BACKEND_URL", "http://127.0.0.1:30101/mcp")
        .env(
            "LAB_BRIDGE_BACKEND_KEY_FILE",
            root.path().join("missing-backend.key"),
        )
        .env("LAB_BRIDGE_STATE_DIR", root.path().join("state"))
        .env("LAB_HOST_BACKEND_URL", "http://127.0.0.1:30102/mcp")
        .env(
            "LAB_HOST_BACKEND_KEY_FILE",
            root.path().join("missing-host.key"),
        )
        .env("LAB_PRODUCTION_CONNECTOR_ID", "legacy-connector")
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("LAB_PRODUCTION_CONNECTOR_ID must be")
    );
    assert!(!root.path().join("state").exists());
}

#[test]
fn production_bridge_refuses_canonical_socket_when_host_pair_is_absent() {
    let root = tempfile::tempdir().unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_local-tool-bridge"))
        .env("LAB_BRIDGE_SOCKET", PRODUCTION_SOCKET)
        .env("LAB_BRIDGE_BACKEND_URL", "http://127.0.0.1:30101/mcp")
        .env(
            "LAB_BRIDGE_BACKEND_KEY_FILE",
            root.path().join("missing-backend.key"),
        )
        .env("LAB_BRIDGE_STATE_DIR", root.path().join("state"))
        .env_remove("LAB_HOST_BACKEND_URL")
        .env_remove("LAB_HOST_BACKEND_KEY_FILE")
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("production Bridge requires"));
    assert!(!root.path().join("state").exists());
}

#[cfg(target_os = "macos")]
#[test]
fn production_bridge_refuses_private_tmp_alias_without_production_fence() {
    let root = tempfile::tempdir().unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_local-tool-bridge"))
        .env(
            "LAB_BRIDGE_SOCKET",
            "/private/tmp/local-agent-bridge-production/bridge.sock",
        )
        .env("LAB_BRIDGE_BACKEND_URL", "http://127.0.0.1:30101/mcp")
        .env(
            "LAB_BRIDGE_BACKEND_KEY_FILE",
            root.path().join("missing-backend.key"),
        )
        .env("LAB_BRIDGE_STATE_DIR", root.path().join("state"))
        .env_remove("LAB_HOST_BACKEND_URL")
        .env_remove("LAB_HOST_BACKEND_KEY_FILE")
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("canonical socket"));
    assert!(!root.path().join("state").exists());
}
