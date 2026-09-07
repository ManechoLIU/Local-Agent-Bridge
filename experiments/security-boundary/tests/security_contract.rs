use bridge_security_core::*;
use std::fs;
use std::os::unix::fs::symlink;
use std::path::{Path, PathBuf};
use tempfile::tempdir;

fn grant() -> (ExecutionContext, CapabilityGrant) {
    let context = ExecutionContext {
        principal_id: "principal-a".into(),
        execution_session_id: "session-a".into(),
        capability_id: "cap-a".into(),
        workspace_id: "workspace-a".into(),
    };
    let grant = CapabilityGrant {
        principal_id: context.principal_id.clone(),
        execution_session_id: context.execution_session_id.clone(),
        capability_id: context.capability_id.clone(),
        workspace_id: context.workspace_id.clone(),
        capabilities: vec![
            Capability::FileRead,
            Capability::FileWrite,
            Capability::FileMove,
            Capability::Shell,
        ],
    };
    (context, grant)
}

#[test]
fn authorization_binds_principal_session_capability_and_workspace() {
    let (context, grant) = grant();
    grant.authorize(&context, Capability::FileWrite).unwrap();
    let mut wrong = context.clone();
    wrong.execution_session_id = "other-session".into();
    assert!(grant.authorize(&wrong, Capability::FileWrite).is_err());
    let mut wrong_workspace = context;
    wrong_workspace.workspace_id = "other-workspace".into();
    assert!(
        grant
            .authorize(&wrong_workspace, Capability::FileWrite)
            .is_err()
    );
}

#[test]
fn capability_issuance_expiry_revocation_and_replay_fail_closed() {
    let (context, grant) = grant();
    let mut authority = CapabilityAuthority::new_for_test([7_u8; 32]);
    let issued = authority.issue(grant, 1_000, 5_000, 3).unwrap();
    let body = br#"{"jsonrpc":"2.0","id":"r1","method":"tools/call"}"#;
    let proof = sign_request(
        &issued,
        &context,
        "r1",
        "nonce-1",
        "tools/call",
        "write",
        body,
    )
    .unwrap();
    let context = authority
        .verify_request(
            &proof,
            body,
            "tools/call",
            "write",
            Capability::FileWrite,
            2_000,
        )
        .unwrap();
    assert_eq!(context.capability_id, "cap-a");

    let replay = authority.verify_request(
        &proof,
        body,
        "tools/call",
        "write",
        Capability::FileWrite,
        2_001,
    );
    assert!(replay.unwrap_err().contains("replay"));

    let expired = sign_request(
        &issued,
        &context,
        "r2",
        "nonce-2",
        "tools/call",
        "write",
        body,
    )
    .unwrap();
    assert!(
        authority
            .verify_request(
                &expired,
                body,
                "tools/call",
                "write",
                Capability::FileWrite,
                6_001,
            )
            .unwrap_err()
            .contains("expired")
    );

    assert!(authority.revoke("cap-a"));
    let revoked = sign_request(
        &issued,
        &context,
        "r3",
        "nonce-3",
        "tools/call",
        "write",
        body,
    )
    .unwrap();
    assert!(
        authority
            .verify_request(
                &revoked,
                body,
                "tools/call",
                "write",
                Capability::FileWrite,
                2_500,
            )
            .unwrap_err()
            .contains("revoked")
    );
}

#[test]
fn request_mac_binds_body_method_tool_and_observed_context() {
    let (context, grant) = grant();
    let mut authority = CapabilityAuthority::new_for_test([9_u8; 32]);
    let issued = authority.issue(grant, 10, 10_000, 1).unwrap();
    let body = b"canonical-body";
    let proof = sign_request(
        &issued,
        &context,
        "request-1",
        "nonce-a",
        "tools/call",
        "run_command",
        body,
    )
    .unwrap();

    let mut tampered = proof.clone();
    tampered.mac_sha256.replace_range(0..2, "00");
    assert!(
        authority
            .verify_request(
                &tampered,
                body,
                "tools/call",
                "run_command",
                Capability::Shell,
                100,
            )
            .unwrap_err()
            .contains("MAC")
    );

    let proof = sign_request(
        &issued,
        &context,
        "request-2",
        "nonce-b",
        "tools/call",
        "run_command",
        body,
    )
    .unwrap();
    assert!(
        authority
            .verify_request(
                &proof,
                b"changed-body",
                "tools/call",
                "run_command",
                Capability::Shell,
                100,
            )
            .unwrap_err()
            .contains("body hash")
    );

    let wrong_context = ExecutionContext {
        principal_id: "other-principal".into(),
        execution_session_id: context.execution_session_id.clone(),
        capability_id: context.capability_id.clone(),
        workspace_id: context.workspace_id.clone(),
    };
    let proof = sign_request(
        &issued,
        &wrong_context,
        "request-3",
        "nonce-c",
        "tools/call",
        "run_command",
        body,
    )
    .unwrap();
    assert!(
        authority
            .verify_request(
                &proof,
                body,
                "tools/call",
                "run_command",
                Capability::Shell,
                100,
            )
            .unwrap_err()
            .contains("principal mismatch")
    );
}

#[test]
fn session_generation_rotation_invalidates_older_capabilities() {
    let (context, grant) = grant();
    let mut authority = CapabilityAuthority::new_for_test([11_u8; 32]);
    let issued = authority.issue(grant, 100, 10_000, 4).unwrap();
    assert_eq!(
        authority.rotate_session_generation("session-a", 4).unwrap(),
        5
    );
    let body = b"body";
    let proof = sign_request(
        &issued,
        &context,
        "request-4",
        "nonce-generation",
        "tools/call",
        "read",
        body,
    )
    .unwrap();
    assert!(
        authority
            .verify_request(
                &proof,
                body,
                "tools/call",
                "read",
                Capability::FileRead,
                200,
            )
            .unwrap_err()
            .contains("stale execution session generation")
    );
}

#[test]
fn signed_ingress_headers_round_trip_without_exposing_signing_secret() {
    let (context, grant) = grant();
    let mut authority = CapabilityAuthority::new_for_test([12_u8; 32]);
    let issued = authority.issue(grant, 100, 10_000, 1).unwrap();
    let body = b"body";
    let proof = sign_request(
        &issued,
        &context,
        "request-headers",
        "nonce-headers",
        "tools/call",
        "read",
        body,
    )
    .unwrap();
    let headers = proof.to_headers();
    assert!(!headers.keys().any(|key| key.contains("secret")));
    assert!(!headers.values().any(|value| value.contains("<redacted>")));
    let restored = RequestProof::from_headers(&headers, "tools/call", "read").unwrap();
    assert_eq!(restored, proof);
    authority
        .verify_request(
            &restored,
            body,
            "tools/call",
            "read",
            Capability::FileRead,
            200,
        )
        .unwrap();
}

#[test]
fn openai_tunnel_correlation_facts_are_diagnostic_not_authorization() {
    let observation = CorrelationObservation::openai_tunnel(
        Some("subject-fingerprint"),
        Some("session-fingerprint"),
    );
    assert!(!observation.is_authorization_source());
    assert_eq!(
        observation.provenance,
        "openai_tunnel_correlation_unverified_semantics"
    );
}

#[test]
fn forged_ingress_context_without_capability_secret_fails_mac_verification() {
    let (context, grant) = grant();
    let mut authority = CapabilityAuthority::new_for_test([13_u8; 32]);
    let issued = authority.issue(grant, 100, 10_000, 1).unwrap();
    let body = b"body";
    let mut proof = sign_request(
        &issued,
        &context,
        "request-forged",
        "nonce-forged",
        "tools/call",
        "read",
        body,
    )
    .unwrap();
    proof.principal_id = "forged-principal".into();
    let error = authority
        .verify_request(
            &proof,
            body,
            "tools/call",
            "read",
            Capability::FileRead,
            200,
        )
        .unwrap_err();
    assert!(error.contains("MAC"), "{error}");
}

#[test]
fn ingress_compares_actual_method_and_tool_to_signed_proof() {
    let (context, grant) = grant();
    let mut authority = CapabilityAuthority::new_for_test([15_u8; 32]);
    let issued = authority.issue(grant, 100, 10_000, 1).unwrap();
    let body = b"body";
    let proof = sign_request(
        &issued,
        &context,
        "request-method",
        "nonce-method",
        "tools/call",
        "read",
        body,
    )
    .unwrap();
    assert!(
        authority
            .verify_request(
                &proof,
                body,
                "resources/read",
                "read",
                Capability::FileRead,
                200,
            )
            .unwrap_err()
            .contains("method")
    );
    assert!(
        authority
            .verify_request(
                &proof,
                body,
                "tools/call",
                "run_command",
                Capability::FileRead,
                200,
            )
            .unwrap_err()
            .contains("tool name")
    );
}

#[test]
fn revocation_and_replay_state_survive_private_restart() {
    use std::os::unix::fs::PermissionsExt;

    let state_dir = tempdir().unwrap();
    let (context, grant) = grant();
    let mut authority = CapabilityAuthority::new_for_test([17_u8; 32]);
    let issued = authority.issue(grant, 100, 10_000, 1).unwrap();
    let body = b"body";
    let first = sign_request(
        &issued,
        &context,
        "request-persisted",
        "nonce-persisted",
        "tools/call",
        "read",
        body,
    )
    .unwrap();
    authority
        .verify_request(
            &first,
            body,
            "tools/call",
            "read",
            Capability::FileRead,
            200,
        )
        .unwrap();
    authority.save_private(state_dir.path()).unwrap();

    let key_mode = fs::metadata(state_dir.path().join("authority.key"))
        .unwrap()
        .permissions()
        .mode()
        & 0o777;
    let state_mode = fs::metadata(state_dir.path().join("authority-state.json"))
        .unwrap()
        .permissions()
        .mode()
        & 0o777;
    assert_eq!(key_mode, 0o600);
    assert_eq!(state_mode, 0o600);

    let mut restarted = CapabilityAuthority::load_or_create_private(state_dir.path()).unwrap();
    assert!(
        restarted
            .verify_request(
                &first,
                body,
                "tools/call",
                "read",
                Capability::FileRead,
                201,
            )
            .unwrap_err()
            .contains("replay")
    );
    assert!(restarted.revoke("cap-a"));
    restarted.save_private(state_dir.path()).unwrap();
    let mut restarted_again =
        CapabilityAuthority::load_or_create_private(state_dir.path()).unwrap();
    let second = sign_request(
        &issued,
        &context,
        "request-after-revoke",
        "nonce-after-revoke",
        "tools/call",
        "read",
        body,
    )
    .unwrap();
    assert!(
        restarted_again
            .verify_request(
                &second,
                body,
                "tools/call",
                "read",
                Capability::FileRead,
                300,
            )
            .unwrap_err()
            .contains("revoked")
    );
}

#[test]
fn hardlink_read_escape_is_rejected() {
    let outside_dir = tempdir().unwrap();
    let workspace_dir = tempdir().unwrap();
    let outside = outside_dir.path().join("outside-secret.txt");
    fs::write(&outside, "outside-secret").unwrap();
    fs::hard_link(&outside, workspace_dir.path().join("alias.txt")).unwrap();
    let workspace = Workspace::open(workspace_dir.path()).unwrap();
    let error = workspace.read_text("alias.txt").unwrap_err();
    assert!(error.contains("multiple hard links"), "{error}");
}

#[test]
fn nonexistent_write_under_symlink_parent_is_rejected() {
    let outside_dir = tempdir().unwrap();
    let workspace_dir = tempdir().unwrap();
    symlink(outside_dir.path(), workspace_dir.path().join("escape")).unwrap();
    let workspace = Workspace::open(workspace_dir.path()).unwrap();
    let error = workspace.write_text("escape/new.txt", "nope").unwrap_err();
    assert!(
        error.contains("unsafe or missing parent component"),
        "{error}"
    );
    assert!(!outside_dir.path().join("new.txt").exists());
}

#[test]
fn existing_symlink_write_is_rejected_without_touching_target() {
    let outside_dir = tempdir().unwrap();
    let workspace_dir = tempdir().unwrap();
    let outside = outside_dir.path().join("outside.txt");
    fs::write(&outside, "unchanged").unwrap();
    symlink(&outside, workspace_dir.path().join("alias.txt")).unwrap();
    let workspace = Workspace::open(workspace_dir.path()).unwrap();
    assert!(workspace.write_text("alias.txt", "mutated").is_err());
    assert_eq!(fs::read_to_string(outside).unwrap(), "unchanged");
}

#[test]
fn nonexistent_move_destination_under_symlink_parent_is_rejected() {
    let outside_dir = tempdir().unwrap();
    let workspace_dir = tempdir().unwrap();
    fs::write(workspace_dir.path().join("source.txt"), "inside").unwrap();
    symlink(outside_dir.path(), workspace_dir.path().join("escape")).unwrap();
    let workspace = Workspace::open(workspace_dir.path()).unwrap();
    let error = workspace
        .move_path("source.txt", "escape/moved.txt", false)
        .unwrap_err();
    assert!(
        error.contains("unsafe or missing parent component"),
        "{error}"
    );
    assert!(!outside_dir.path().join("moved.txt").exists());
    assert!(workspace_dir.path().join("source.txt").exists());
}

#[test]
fn normal_write_and_move_stay_inside_workspace() {
    let workspace_dir = tempdir().unwrap();
    fs::create_dir(workspace_dir.path().join("sub")).unwrap();
    let workspace = Workspace::open(workspace_dir.path()).unwrap();
    workspace.write_text("sub/a.txt", "hello").unwrap();
    assert_eq!(workspace.read_text("sub/a.txt").unwrap(), "hello");
    workspace
        .move_path("sub/a.txt", "sub/b.txt", false)
        .unwrap();
    assert_eq!(workspace.read_text("sub/b.txt").unwrap(), "hello");
    assert!(!workspace_dir.path().join("sub/a.txt").exists());
}

#[test]
fn macos_shell_blocks_sensitive_fs_network_and_env_but_allows_workspace_io() {
    if !Path::new("/usr/bin/sandbox-exec").exists() {
        return;
    }
    use std::net::TcpListener;

    let home = PathBuf::from(std::env::var("HOME").unwrap());
    let workspace_dir = tempfile::Builder::new()
        .prefix("lab-security-workspace-")
        .tempdir_in(&home)
        .unwrap();
    let outside_home = tempfile::Builder::new()
        .prefix("lab-security-outside-")
        .tempfile_in(&home)
        .unwrap();
    let outside_tmp = tempfile::Builder::new()
        .prefix("lab-security-tmp-")
        .tempfile_in("/tmp")
        .unwrap();
    fs::write(outside_home.path(), "outside-home-secret").unwrap();
    fs::write(outside_tmp.path(), "outside-tmp-secret").unwrap();
    fs::write(workspace_dir.path().join("inside.txt"), "inside-ok").unwrap();

    let allowed = run_home_confined_shell(
        workspace_dir.path(),
        "/bin/cat inside.txt && printf shell-ok > shell-out.txt",
    )
    .unwrap();
    assert!(
        allowed.status.success(),
        "{}",
        String::from_utf8_lossy(&allowed.stderr)
    );
    assert!(String::from_utf8_lossy(&allowed.stdout).contains("inside-ok"));
    assert_eq!(
        fs::read_to_string(workspace_dir.path().join("shell-out.txt")).unwrap(),
        "shell-ok"
    );

    for outside in [outside_home.path(), outside_tmp.path()] {
        let escaped = run_home_confined_shell(
            workspace_dir.path(),
            &format!("/bin/cat '{}'", outside.display()),
        )
        .unwrap();
        assert!(
            !escaped.status.success(),
            "outside read unexpectedly succeeded"
        );
        assert!(!String::from_utf8_lossy(&escaped.stdout).contains("secret"));
    }

    let metadata = run_home_confined_shell(
        workspace_dir.path(),
        &format!("/bin/ls '{}'", home.display()),
    )
    .unwrap();
    assert!(
        !metadata.status.success(),
        "outside user-data metadata unexpectedly remained enumerable"
    );

    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let network = run_home_confined_shell(
        workspace_dir.path(),
        &format!("/usr/bin/nc -z 127.0.0.1 {port}"),
    )
    .unwrap();
    assert!(
        !network.status.success(),
        "network escape unexpectedly succeeded"
    );

    unsafe { std::env::set_var("LAB_SECURITY_SECRET", "must-not-leak") };
    let env = run_home_confined_shell(workspace_dir.path(), "/usr/bin/env").unwrap();
    unsafe { std::env::remove_var("LAB_SECURITY_SECRET") };
    let env_text = String::from_utf8_lossy(&env.stdout);
    assert!(
        env.status.success(),
        "{}",
        String::from_utf8_lossy(&env.stderr)
    );
    assert!(!env_text.contains("LAB_SECURITY_SECRET"));
    assert!(!env_text.contains("must-not-leak"));
    assert!(env_text.contains("HOME="));
    assert!(env_text.contains("TMPDIR="));
}

#[test]
fn traversal_and_hardlink_writes_fail_closed() {
    let outside_dir = tempdir().unwrap();
    let workspace_dir = tempdir().unwrap();
    let outside = outside_dir.path().join("outside.txt");
    fs::write(&outside, "unchanged").unwrap();
    fs::hard_link(&outside, workspace_dir.path().join("hard.txt")).unwrap();
    let workspace = Workspace::open(workspace_dir.path()).unwrap();
    assert!(workspace.write_text("../escape.txt", "nope").is_err());
    let error = workspace.write_text("hard.txt", "mutated").unwrap_err();
    assert!(error.contains("multiple hard links"), "{error}");
    assert_eq!(fs::read_to_string(&outside).unwrap(), "unchanged");
}
