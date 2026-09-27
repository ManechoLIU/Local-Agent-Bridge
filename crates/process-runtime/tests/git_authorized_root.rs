use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use process_runtime::{ProcessSandboxPolicy, RunCommandRequest};

static EXTRA_ROOTS_ENV_LOCK: Mutex<()> = Mutex::new(());

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

fn policy_with_roots(name: &str) -> (ProcessSandboxPolicy, PathBuf) {
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

fn run(
    policy: &ProcessSandboxPolicy,
    command: &str,
    cwd: Option<&str>,
) -> process_runtime::RunCommandResult {
    policy
        .run(RunCommandRequest {
            command: command.into(),
            cwd: cwd.map(str::to_string),
            timeout_ms: 30_000,
        })
        .unwrap_or_else(|error| panic!("command launch failed: {error}"))
}

fn ancestor_literals(path: &Path) -> Vec<PathBuf> {
    let mut ancestors = Vec::new();
    let mut current = path;
    while let Some(parent) = current.parent() {
        if parent.as_os_str().is_empty() {
            break;
        }
        ancestors.push(parent.to_path_buf());
        current = parent;
    }
    ancestors
}

fn with_authorized_roots<T>(roots: &[PathBuf], f: impl FnOnce() -> T) -> T {
    let _guard = EXTRA_ROOTS_ENV_LOCK.lock().unwrap();
    let file = tempfile::NamedTempFile::new().unwrap();
    let body = roots
        .iter()
        .map(|path| path.display().to_string())
        .collect::<Vec<_>>()
        .join("\n");
    fs::write(file.path(), body).unwrap();
    let previous = std::env::var("PROCESS_RUNTIME_AUTHORIZED_ROOTS_FILE").ok();
    unsafe {
        std::env::set_var("PROCESS_RUNTIME_AUTHORIZED_ROOTS_FILE", file.path());
    }
    let result = f();
    unsafe {
        match previous {
            Some(value) => std::env::set_var("PROCESS_RUNTIME_AUTHORIZED_ROOTS_FILE", value),
            None => std::env::remove_var("PROCESS_RUNTIME_AUTHORIZED_ROOTS_FILE"),
        }
    }
    result
}

fn assert_denied(result: &process_runtime::RunCommandResult, context: &str) {
    assert!(
        !result.success,
        "{context} must fail-closed, exit={} stdout={:?} stderr={:?}",
        result.exit_code, result.stdout, result.stderr
    );
}

#[test]
fn git_lifecycle_works_in_authorized_workspace_under_users() {
    let (policy, workspace) = policy_with_roots("git-workspace");
    assert!(
        workspace.starts_with("/Users"),
        "this regression requires a /Users workspace, got {}",
        workspace.display()
    );

    let profile = policy
        .prepare(RunCommandRequest {
            command: "true".into(),
            cwd: None,
            timeout_ms: 1_000,
        })
        .unwrap()
        .args
        .iter()
        .find(|arg| arg.starts_with("(version 1)"))
        .cloned()
        .expect("sandbox profile");

    for ancestor in ancestor_literals(&workspace) {
        let metadata = format!(
            "(allow file-read-metadata (literal \"{}\"))",
            ancestor.display()
        );
        assert!(
            profile.contains(&metadata),
            "missing ancestor metadata allow for {}: {profile}",
            ancestor.display()
        );
        let data = format!(
            "(allow file-read-data (literal \"{}\"))",
            ancestor.display()
        );
        let write_literal = format!("(allow file-write* (literal \"{}\"))", ancestor.display());
        let write_subpath = format!("(allow file-write* (subpath \"{}\"))", ancestor.display());
        let data_subpath = format!("(allow file-read-data (subpath \"{}\"))", ancestor.display());
        assert!(!profile.contains(&data), "ancestor data allow leaked: {data}");
        assert!(
            !profile.contains(&write_literal),
            "ancestor write allow leaked: {write_literal}"
        );
        assert!(
            !profile.contains(&write_subpath),
            "ancestor write subpath leaked: {write_subpath}"
        );
        assert!(
            !profile.contains(&data_subpath),
            "ancestor data subpath leaked: {data_subpath}"
        );
    }
    assert!(!profile.contains("(allow file-read-data (subpath \"/Users\"))"));
    assert!(!profile.contains("(allow file-write* (subpath \"/Users\"))"));

    let result = run(
        &policy,
        "printf 'ok\n' > tracked.txt && git init -b main && git add tracked.txt && git -c user.name=lab -c user.email=lab@local commit -m 'init' && git status --short --branch && git branch && git rev-parse --show-toplevel && git rev-parse HEAD",
        None,
    );
    assert!(
        result.success,
        "git lifecycle must work in authorized workspace, exit={} stdout={:?} stderr={:?}",
        result.exit_code, result.stdout, result.stderr
    );
    assert!(result.stdout.contains("## main"));
    assert!(result.stdout.contains(&workspace.display().to_string()));
}

#[test]
fn authorized_extra_root_allows_git_but_not_parent_or_sibling_escape() {
    let (policy, workspace) = policy_with_roots("git-extra-ws");
    let parent = workspace.parent().unwrap().join("extra-tree");
    let extra = parent.join("project");
    let sibling = parent.join("sibling-secret");
    let _ = fs::remove_dir_all(&parent);
    fs::create_dir_all(&extra).unwrap();
    fs::write(extra.join("tracked.txt"), "authorized\n").unwrap();
    fs::write(&sibling, "UNAUTH-SIBLING\n").unwrap();
    fs::write(parent.join("parent-secret.txt"), "UNAUTH-PARENT\n").unwrap();

    let extra_canon = extra.canonicalize().unwrap();
    with_authorized_roots(&[extra_canon.clone()], || {
        let prepared = policy
            .prepare(RunCommandRequest {
                command: "pwd".into(),
                cwd: Some(extra_canon.display().to_string()),
                timeout_ms: 5_000,
            })
            .unwrap();
        assert_eq!(prepared.cwd, extra_canon);
        let profile = prepared
            .args
            .iter()
            .find(|arg| arg.starts_with("(version 1)"))
            .cloned()
            .expect("sandbox profile");
        assert!(profile.contains(&format!(
            "(allow file-write* (subpath \"{}\"))",
            extra_canon.display()
        )));
        assert!(profile.contains(&format!(
            "(allow file-read-metadata (literal \"{}\"))",
            parent.canonicalize().unwrap().display()
        )));
        assert!(!profile.contains(&format!(
            "(allow file-read-data (literal \"{}\"))",
            parent.canonicalize().unwrap().display()
        )));
        assert!(!profile.contains(&format!(
            "(allow file-read-data (subpath \"{}\"))",
            parent.canonicalize().unwrap().display()
        )));

        let git = run(
            &policy,
            "git init -b main && git add tracked.txt && git -c user.name=lab -c user.email=lab@local commit -m 'extra' && git status --short --branch && git rev-parse --show-toplevel",
            Some(extra_canon.to_str().unwrap()),
        );
        assert!(
            git.success,
            "git must work in extra authorized root, exit={} stdout={:?} stderr={:?}",
            git.exit_code, git.stdout, git.stderr
        );
        assert!(git.stdout.contains(&extra_canon.display().to_string()));

        assert_denied(
            &run(
                &policy,
                "ls -1 ..",
                Some(extra_canon.to_str().unwrap()),
            ),
            "parent directory listing",
        );
        assert_denied(
            &run(
                &policy,
                "cat ../parent-secret.txt",
                Some(extra_canon.to_str().unwrap()),
            ),
            "parent file read",
        );
        assert_denied(
            &run(
                &policy,
                format!("cat {}", sibling.display()).as_str(),
                Some(extra_canon.to_str().unwrap()),
            ),
            "sibling file read",
        );
        assert_denied(
            &run(
                &policy,
                "printf leaked > ../parent-secret-write.txt",
                Some(extra_canon.to_str().unwrap()),
            ),
            "parent file write",
        );
        assert!(!parent.join("parent-secret-write.txt").exists());

        let symlink = extra_canon.join("escape-link");
        std::os::unix::fs::symlink(&sibling, &symlink).unwrap();
        assert_denied(
            &run(
                &policy,
                "cat escape-link",
                Some(extra_canon.to_str().unwrap()),
            ),
            "symlink escape",
        );

        assert_denied(
            &run(
                &policy,
                "git -c include.path=../parent-secret.txt status",
                Some(extra_canon.to_str().unwrap()),
            ),
            "git include.path escape",
        );

        let hook = extra_canon.join(".git/hooks/pre-commit");
        fs::write(
            &hook,
            format!("#!/bin/sh\ncat '{}'\n", sibling.display()),
        )
        .unwrap();
        fs::set_permissions(&hook, fs::Permissions::from_mode(0o755)).unwrap();
        assert_denied(
            &run(
                &policy,
                "printf 'hook\n' >> tracked.txt && git add tracked.txt && git -c user.name=lab -c user.email=lab@local commit -m 'hook'",
                Some(extra_canon.to_str().unwrap()),
            ),
            "git hook escape",
        );

        assert_denied(
            &run(
                &policy,
                "git worktree add ../escape-worktree",
                Some(extra_canon.to_str().unwrap()),
            ),
            "git worktree escape",
        );
        assert!(!parent.join("escape-worktree").exists());
    });
}
