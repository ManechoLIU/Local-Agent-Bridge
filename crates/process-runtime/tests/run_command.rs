use std::fs;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

use process_runtime::{ProcessSandboxPolicy, RunCommandRequest};

fn fixture(name: &str) -> (PathBuf, PathBuf, PathBuf) {
    let root = std::env::current_dir()
        .unwrap()
        .join(".lab-staging")
        .join("process-runtime-tests")
        .join(name);
    let _ = fs::remove_dir_all(&root);
    let workspace = root.join("workspace");
    let scratch = root.join("scratch");
    let cache = root.join("cache");
    fs::create_dir_all(workspace.join("sub")).unwrap();
    fs::create_dir_all(&scratch).unwrap();
    fs::create_dir_all(&cache).unwrap();
    (workspace, scratch, cache)
}

fn policy(name: &str) -> (ProcessSandboxPolicy, PathBuf) {
    let (workspace, scratch, cache) = fixture(name);
    let policy = ProcessSandboxPolicy::new(
        &workspace,
        &scratch,
        &cache,
        vec![
            PathBuf::from("/opt/homebrew"),
            PathBuf::from("/usr/local"),
            PathBuf::from("/usr"),
            PathBuf::from("/bin"),
            PathBuf::from("/sbin"),
        ],
    )
    .unwrap();
    (policy, workspace)
}

#[test]
fn run_command_executes_in_workspace_and_returns_output() {
    let (policy, workspace) = policy("run-ok");
    let marker = workspace.join("lab-proc01-out.txt");
    let result = policy
        .run(RunCommandRequest {
            command: "printf 'LAB-PROC-01-OK\\n' | tee lab-proc01-out.txt".into(),
            cwd: None,
            timeout_ms: 15_000,
        })
        .unwrap();

    assert!(
        result.success,
        "expected success, exit={} stdout={:?} stderr={:?}",
        result.exit_code, result.stdout, result.stderr
    );
    assert!(!result.timed_out);
    assert_eq!(result.exit_code, 0);
    assert_eq!(result.provenance, "lab_process_runtime_v1");
    assert!(
        result.stdout.contains("LAB-PROC-01-OK"),
        "stdout missing marker: {:?}",
        result.stdout
    );
    let written = fs::read_to_string(&marker).unwrap();
    assert!(written.contains("LAB-PROC-01-OK"));
}

#[test]
fn run_command_rejects_cwd_outside_workspace_before_launch() {
    let (policy, _workspace) = policy("cwd-escape");
    let error = policy
        .run(RunCommandRequest {
            command: "pwd".into(),
            cwd: Some("../escape".into()),
            timeout_ms: 5_000,
        })
        .unwrap_err();
    assert!(
        error.contains("cwd"),
        "expected cwd fail-closed, got {error}"
    );

    let error = policy
        .run(RunCommandRequest {
            command: "pwd".into(),
            cwd: Some("/tmp".into()),
            timeout_ms: 5_000,
        })
        .unwrap_err();
    assert!(
        error.contains("cwd"),
        "expected absolute cwd fail-closed, got {error}"
    );
}

#[test]
fn run_command_denies_write_outside_workspace() {
    let (policy, workspace) = policy("outside-write");
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let leak = PathBuf::from(format!("/tmp/lab-proc01-should-not-exist-{stamp}"));
    let _ = fs::remove_file(&leak);
    let result = policy
        .run(RunCommandRequest {
            command: format!("printf leak > {}", leak.display()),
            cwd: None,
            timeout_ms: 10_000,
        })
        .unwrap();

    assert!(
        !result.success,
        "outside write must fail-closed, stdout={:?} stderr={:?}",
        result.stdout, result.stderr
    );
    assert!(!leak.exists(), "sandbox leaked a file at {}", leak.display());
    assert!(
        !workspace.join(leak.file_name().unwrap()).exists(),
        "outside write must not be redirected into workspace"
    );
}
