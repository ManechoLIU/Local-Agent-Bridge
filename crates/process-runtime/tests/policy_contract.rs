use std::fs;
use std::path::PathBuf;

use process_runtime::{ProcessSandboxPolicy, RunCommandRequest};

fn fixture(name: &str) -> (PathBuf, PathBuf, PathBuf) {
    let root = std::env::current_dir().unwrap()
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

#[test]
fn policy_allows_devnull_workspace_and_trusted_toolchains_without_network() {
    let (workspace, scratch, cache) = fixture("policy");
    let policy = ProcessSandboxPolicy::new(
        &workspace,
        &scratch,
        &cache,
        vec![
            PathBuf::from("/opt/homebrew"),
            PathBuf::from("/usr/local"),
        ],
    ).unwrap();

    let profile = policy.profile();
    assert!(profile.contains("(literal \"/dev/null\")"));
    assert!(profile.contains(&format!("(subpath \"{}\")", workspace.display())));
    assert!(profile.contains(&format!("(subpath \"{}\")", scratch.display())));
    assert!(profile.contains(&format!("(subpath \"{}\")", cache.display())));
    assert!(profile.contains("(subpath \"/opt/homebrew\")"));
    assert!(profile.contains("(subpath \"/usr/local\")"));
    assert!(!profile.contains("(allow network"));
    assert!(!profile.contains("network*"));
    assert!(!profile.contains("(allow file-write* (subpath \"/tmp\")"));
    assert!(!profile.contains("(allow file-write* (subpath \"/Users\")"));
}

#[test]
fn prepared_command_has_explicit_safe_environment_and_no_inherited_secret() {
    let (workspace, scratch, cache) = fixture("env");
    let policy = ProcessSandboxPolicy::new(
        &workspace,
        &scratch,
        &cache,
        vec![PathBuf::from("/opt/homebrew"), PathBuf::from("/usr/local")],
    ).unwrap();

    let prepared = policy.prepare(RunCommandRequest {
        command: "git --version".into(),
        cwd: Some("sub".into()),
        timeout_ms: 30_000,
    }).unwrap();

    assert_eq!(prepared.program, PathBuf::from("/usr/bin/sandbox-exec"));
    assert_eq!(prepared.cwd, workspace.join("sub"));
    assert_eq!(prepared.env.get("HOME").unwrap(), &scratch.display().to_string());
    assert_eq!(prepared.env.get("TMPDIR").unwrap(), &scratch.join("tmp").display().to_string());
    assert_eq!(prepared.env.get("CARGO_HOME").unwrap(), &cache.join("cargo").display().to_string());
    assert_eq!(prepared.env.get("npm_config_cache").unwrap(), &cache.join("npm").display().to_string());
    assert_eq!(prepared.env.get("GIT_CONFIG_GLOBAL").unwrap(), "/dev/null");
    assert_eq!(
        prepared.env.get("PATH").unwrap(),
        "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    );
    assert!(!prepared.env.contains_key("LAB_SECURITY_SECRET"));
    assert!(prepared.args.iter().any(|arg| arg == "/bin/sh"));
    assert!(prepared.args.iter().any(|arg| arg == "git --version"));
}

#[test]
fn cwd_must_resolve_inside_workspace() {
    let (workspace, scratch, cache) = fixture("cwd");
    let policy = ProcessSandboxPolicy::new(
        &workspace,
        &scratch,
        &cache,
        vec![PathBuf::from("/opt/homebrew")],
    ).unwrap();

    let error = policy.prepare(RunCommandRequest {
        command: "pwd".into(),
        cwd: Some("../escape".into()),
        timeout_ms: 1000,
    }).unwrap_err();
    assert!(error.contains("cwd"));

    let error = policy.prepare(RunCommandRequest {
        command: "pwd".into(),
        cwd: Some("/tmp".into()),
        timeout_ms: 1000,
    }).unwrap_err();
    assert!(error.contains("cwd"));
}

#[test]
fn request_bounds_fail_before_process_launch() {
    let (workspace, scratch, cache) = fixture("bounds");
    let policy = ProcessSandboxPolicy::new(
        &workspace,
        &scratch,
        &cache,
        vec![],
    ).unwrap();

    for timeout_ms in [0, 120_001] {
        assert!(policy.prepare(RunCommandRequest {
            command: "echo ok".into(),
            cwd: None,
            timeout_ms,
        }).is_err());
    }

    assert!(policy.prepare(RunCommandRequest {
        command: "".into(),
        cwd: None,
        timeout_ms: 1000,
    }).is_err());

    assert!(policy.prepare(RunCommandRequest {
        command: "x".repeat(65_537),
        cwd: None,
        timeout_ms: 1000,
    }).is_err());
}

#[test]
fn policy_rejects_unsafe_scratch_or_cache_aliasing_workspace_parent() {
    let (workspace, scratch, cache) = fixture("roots");
    let workspace_parent = workspace.parent().unwrap();
    assert!(ProcessSandboxPolicy::new(
        &workspace,
        workspace_parent,
        &cache,
        vec![],
    ).is_err());
    assert!(ProcessSandboxPolicy::new(
        &workspace,
        &scratch,
        workspace_parent,
        vec![],
    ).is_err());
}
