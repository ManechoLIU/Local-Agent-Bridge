use std::time::Duration;

use std::cell::{Cell, RefCell};

use collaboration_adapter::{
    CollaborationError, CollaborationErrorKind, CollaborationOperation, Identity, LarkCliProvider,
    ProcessOutput, ProcessRunner, ProviderRunner,
};

#[test]
fn maps_read_operations_to_fixed_lark_cli_argv() {
    let provider = LarkCliProvider::for_test("/opt/homebrew/bin/lark-cli", None);
    assert_eq!(
        provider.argv_for(&CollaborationOperation::BaseListTables {
            base_token: "bas_x".into(),
        }).unwrap(),
        vec!["base", "+table-list", "--base-token", "bas_x", "--format", "json", "--as", "user"]
    );
    assert_eq!(
        provider.argv_for(&CollaborationOperation::BaseListFields {
            base_token: "bas_x".into(),
            table_id: "tbl_x".into(),
        }).unwrap(),
        vec!["base", "+field-list", "--base-token", "bas_x", "--table-id", "tbl_x", "--format", "json", "--as", "user"]
    );
    assert_eq!(
        provider.argv_for(&CollaborationOperation::BaseListRecords {
            base_token: "bas_x".into(),
            table_id: "tbl_x".into(),
            view_id: Some("vew_x".into()),
            page_size: Some(50),
            page_token: Some("pg_x".into()),
        }).unwrap(),
        vec![
            "base", "+record-list", "--base-token", "bas_x", "--table-id", "tbl_x",
            "--view-id", "vew_x", "--page-size", "50", "--page-token", "pg_x",
            "--format", "json", "--as", "user"
        ]
    );
    assert_eq!(
        provider.argv_for(&CollaborationOperation::ChatSearch {
            query: "产品群".into(),
            page_size: Some(20),
        }).unwrap(),
        vec!["im", "+chat-search", "--query", "产品群", "--page-size", "20", "--format", "json", "--as", "user"]
    );
    assert_eq!(
        provider.argv_for(&CollaborationOperation::MessageList {
            chat_id: "oc_123".into(),
            page_size: Some(20),
            page_token: None,
        }).unwrap(),
        vec!["im", "+chat-messages-list", "--chat-id", "oc_123", "--page-size", "20", "--format", "json", "--as", "user"]
    );
}

#[test]
fn maps_base_create_and_kanban_view() {
    let provider = LarkCliProvider::for_test("/opt/homebrew/bin/lark-cli", None);
    assert_eq!(
        provider.argv_for(&CollaborationOperation::BaseCreate {
            name: "项目表".into(),
            folder_token: None,
            table_name: Some("任务".into()),
            fields_json: Some(r#"[{"name":"Title","type":"text"}]"#.into()),
            time_zone: Some("Asia/Shanghai".into()),
        }).unwrap(),
        vec![
            "base", "+base-create", "--name", "项目表",
            "--table-name", "任务", "--fields", r#"[{"name":"Title","type":"text"}]"#,
            "--time-zone", "Asia/Shanghai", "--format", "json", "--as", "user"
        ]
    );
    assert_eq!(
        provider.argv_for(&CollaborationOperation::BaseCreateView {
            base_token: "bascn_x".into(),
            table_id: "tbl_x".into(),
            view_json: r#"{"name":"看板","type":"kanban"}"#.into(),
        }).unwrap(),
        vec![
            "base", "+view-create", "--base-token", "bascn_x", "--table-id", "tbl_x",
            "--json", r#"{"name":"看板","type":"kanban"}"#, "--format", "json", "--as", "user"
        ]
    );
}

#[test]
fn maps_message_writes_with_explicit_identity_and_no_shell() {
    let provider = LarkCliProvider::for_test("/opt/homebrew/bin/lark-cli", None);
    let argv = provider.argv_for(&CollaborationOperation::MessageSend {
        chat_id: "oc_123".into(),
        text: "hello; rm -rf /".into(),
        identity: Identity::Bot,
        idempotency_key: "lab-123".into(),
    }).unwrap();
    assert_eq!(
        argv,
        vec![
            "im", "+messages-send", "--chat-id", "oc_123", "--text", "hello; rm -rf /",
            "--as", "bot", "--idempotency-key", "lab-123", "--format", "json"
        ]
    );
    assert!(!argv.iter().any(|v| v == "sh" || v == "-c"));
}

#[test]
fn maps_reply_to_official_shortcut() {
    let provider = LarkCliProvider::for_test("/opt/homebrew/bin/lark-cli", None);
    let argv = provider.argv_for(&CollaborationOperation::MessageReply {
        message_id: "om_123".into(),
        text: "收到".into(),
        identity: Identity::Bot,
        idempotency_key: "lab-456".into(),
    }).unwrap();
    assert_eq!(
        argv,
        vec![
            "im", "+messages-reply", "--message-id", "om_123", "--text", "收到",
            "--as", "bot", "--idempotency-key", "lab-456", "--format", "json"
        ]
    );
}

#[test]
fn rejects_unbounded_or_malformed_arguments_before_dispatch() {
    let provider = LarkCliProvider::for_test("/opt/homebrew/bin/lark-cli", None);
    let error = provider.argv_for(&CollaborationOperation::ChatSearch {
        query: "x".repeat(513),
        page_size: Some(20),
    }).unwrap_err();
    assert_eq!(error.kind, CollaborationErrorKind::InvalidArgument);

    let error = provider.argv_for(&CollaborationOperation::MessageSend {
        chat_id: "not-a-chat-id".into(),
        text: "hello".into(),
        identity: Identity::Bot,
        idempotency_key: "lab-1".into(),
    }).unwrap_err();
    assert_eq!(error.kind, CollaborationErrorKind::InvalidArgument);
}

#[test]
fn write_operations_are_marked_as_side_effecting() {
    assert!(CollaborationOperation::MessageSend {
        chat_id: "oc_123".into(),
        text: "hello".into(),
        identity: Identity::Bot,
        idempotency_key: "lab-1".into(),
    }.is_write());
    assert!(!CollaborationOperation::ChatSearch {
        query: "产品群".into(),
        page_size: None,
    }.is_write());
}

#[test]
fn profile_is_appended_as_a_global_flag_without_changing_operation_mapping() {
    let provider = LarkCliProvider::for_test("/opt/homebrew/bin/lark-cli", Some("lab-feishu".into()));
    let argv = provider.argv_for(&CollaborationOperation::Status).unwrap();
    assert_eq!(argv, vec!["--profile", "lab-feishu", "whoami"]);
}

#[test]
fn process_runner_executes_program_directly_and_captures_output() {
    let runner = ProcessRunner;
    let output = runner
        .run(
            "/bin/echo",
            &["{\"ok\":true}".into()],
            Duration::from_secs(1),
            1024,
            false,
        )
        .unwrap();
    assert_eq!(output.exit_code, 0);
    assert_eq!(output.stdout.trim(), "{\"ok\":true}");
    assert!(output.dispatch_attempted);
}

#[test]
fn process_runner_rejects_oversized_output() {
    let runner = ProcessRunner;
    let error = runner
        .run(
            "/usr/bin/printf",
            &["1234567890".repeat(20)],
            Duration::from_secs(1),
            32,
            false,
        )
        .unwrap_err();
    assert_eq!(error.kind, CollaborationErrorKind::InvalidProviderOutput);
    assert!(error.dispatch_attempted);
}

#[test]
fn write_timeout_is_result_unknown_after_dispatch() {
    let runner = ProcessRunner;
    let error = runner
        .run(
            "/bin/sleep",
            &["2".into()],
            Duration::from_millis(20),
            1024,
            true,
        )
        .unwrap_err();
    assert_eq!(error.kind, CollaborationErrorKind::ResultUnknownAfterDispatch);
    assert!(error.dispatch_attempted);
}

#[test]
fn read_timeout_is_classified_separately_from_write_unknown() {
    let runner = ProcessRunner;
    let error = runner
        .run(
            "/bin/sleep",
            &["2".into()],
            Duration::from_millis(20),
            1024,
            false,
        )
        .unwrap_err();
    assert_eq!(error.kind, CollaborationErrorKind::ProviderTimeoutAfterDispatch);
    assert!(error.dispatch_attempted);
}

#[test]
fn missing_binary_fails_before_dispatch() {
    let runner = ProcessRunner;
    let error = runner
        .run(
            "/definitely/not/a/real/lark-cli",
            &[],
            Duration::from_millis(20),
            1024,
            false,
        )
        .unwrap_err();
    assert_eq!(error.kind, CollaborationErrorKind::CliUnavailable);
    assert!(!error.dispatch_attempted);
}

#[derive(Default)]
struct FakeRunner {
    calls: RefCell<Vec<Vec<String>>>,
    fail_at: Cell<Option<usize>>,
    result_unknown_at: Cell<Option<usize>>,
}

impl ProviderRunner for FakeRunner {
    fn run(
        &self,
        _program: &str,
        args: &[String],
        _timeout: Duration,
        _output_limit: usize,
        _write_operation: bool,
    ) -> Result<ProcessOutput, CollaborationError> {
        let index = self.calls.borrow().len();
        self.calls.borrow_mut().push(args.to_vec());
        if self.fail_at.get() == Some(index) {
            return Err(CollaborationError {
                kind: CollaborationErrorKind::ProviderRejected,
                message: "preflight failed".into(),
                dispatch_attempted: true,
            });
        }
        if self.result_unknown_at.get() == Some(index) {
            return Err(CollaborationError {
                kind: CollaborationErrorKind::ResultUnknownAfterDispatch,
                message: "write result unknown".into(),
                dispatch_attempted: true,
            });
        }
        Ok(ProcessOutput {
            exit_code: 0,
            stdout: "{\"ok\":true}".into(),
            stderr: String::new(),
            dispatch_attempted: true,
        })
    }
}

#[test]
fn base_create_executes_field_preflight_before_single_write() {
    let provider = LarkCliProvider::for_test("/opt/homebrew/bin/lark-cli", None);
    let runner = FakeRunner::default();
    let result = provider.execute_with(
        &CollaborationOperation::BaseCreateRecord {
            base_token: "bas_x".into(),
            table_id: "tbl_x".into(),
            fields_json: "{\"标题\":\"测试\"}".into(),
        },
        &runner,
        Duration::from_secs(1),
        4096,
    ).unwrap();
    assert_eq!(result.stdout, "{\"ok\":true}");
    let calls = runner.calls.borrow();
    assert_eq!(calls.len(), 2);
    assert_eq!(calls[0][0..2], ["base", "+field-list"]);
    assert_eq!(calls[1][0..2], ["base", "+record-upsert"]);
    assert!(!calls[1].iter().any(|v| v == "--record-id"));
}

#[test]
fn base_update_preflight_then_updates_exact_record() {
    let provider = LarkCliProvider::for_test("/opt/homebrew/bin/lark-cli", None);
    let runner = FakeRunner::default();
    provider.execute_with(
        &CollaborationOperation::BaseUpdateRecord {
            base_token: "bas_x".into(),
            table_id: "tbl_x".into(),
            record_id: "rec_x".into(),
            fields_json: "{\"状态\":\"完成\"}".into(),
        },
        &runner,
        Duration::from_secs(1),
        4096,
    ).unwrap();
    let calls = runner.calls.borrow();
    assert_eq!(calls.len(), 2);
    assert!(calls[1].windows(2).any(|pair| pair == ["--record-id", "rec_x"]));
}

#[test]
fn preflight_failure_prevents_base_write_dispatch() {
    let provider = LarkCliProvider::for_test("/opt/homebrew/bin/lark-cli", None);
    let runner = FakeRunner::default();
    runner.fail_at.set(Some(0));
    let error = provider.execute_with(
        &CollaborationOperation::BaseCreateRecord {
            base_token: "bas_x".into(),
            table_id: "tbl_x".into(),
            fields_json: "{\"标题\":\"测试\"}".into(),
        },
        &runner,
        Duration::from_secs(1),
        4096,
    ).unwrap_err();
    assert_eq!(error.kind, CollaborationErrorKind::ProviderRejected);
    assert_eq!(runner.calls.borrow().len(), 1);
}

#[test]
fn result_unknown_write_is_not_retried() {
    let provider = LarkCliProvider::for_test("/opt/homebrew/bin/lark-cli", None);
    let runner = FakeRunner::default();
    runner.result_unknown_at.set(Some(1));
    let error = provider.execute_with(
        &CollaborationOperation::BaseCreateRecord {
            base_token: "bas_x".into(),
            table_id: "tbl_x".into(),
            fields_json: "{\"标题\":\"测试\"}".into(),
        },
        &runner,
        Duration::from_secs(1),
        4096,
    ).unwrap_err();
    assert_eq!(error.kind, CollaborationErrorKind::ResultUnknownAfterDispatch);
    assert_eq!(runner.calls.borrow().len(), 2);
}

#[test]
fn idempotency_key_is_bounded_to_official_50_character_limit() {
    let provider = LarkCliProvider::for_test("/opt/homebrew/bin/lark-cli", None);
    let error = provider.argv_for(&CollaborationOperation::MessageSend {
        chat_id: "oc_123".into(),
        text: "hello".into(),
        identity: Identity::Bot,
        idempotency_key: "x".repeat(51),
    }).unwrap_err();
    assert_eq!(error.kind, CollaborationErrorKind::InvalidArgument);
}

#[test]
fn production_provider_requires_absolute_real_executable() {
    use std::fs;
    use std::os::unix::fs::{symlink, PermissionsExt};

    let root = std::env::current_dir().unwrap().join("target/manual/provider-path-test");
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).unwrap();
    let cli = root.join("lark-cli");
    fs::write(&cli, "#!/bin/sh\nexit 0\n").unwrap();
    fs::set_permissions(&cli, fs::Permissions::from_mode(0o700)).unwrap();

    assert!(LarkCliProvider::new(&cli, None).is_ok());
    assert!(LarkCliProvider::new("relative/lark-cli", None).is_err());

    let link = root.join("lark-cli-link");
    symlink(&cli, &link).unwrap();
    assert!(LarkCliProvider::new(&link, None).is_err());

    let non_exec = root.join("not-executable");
    fs::write(&non_exec, "x").unwrap();
    fs::set_permissions(&non_exec, fs::Permissions::from_mode(0o600)).unwrap();
    assert!(LarkCliProvider::new(&non_exec, None).is_err());
}


#[test]
fn official_lark_cli_installer_is_pinned_and_private() {
    use collaboration_adapter::{LarkCliInstallSpec, LARK_CLI_PINNED_VERSION};

    assert_eq!(LARK_CLI_PINNED_VERSION, "1.0.96");
    let spec = LarkCliInstallSpec::new(
        "/opt/homebrew/bin/npm",
        "/Users/echoman/.local-agent-bridge/providers/feishu/npm",
    ).unwrap();

    assert_eq!(
        spec.npm_args(),
        vec![
            "install",
            "--prefix",
            "/Users/echoman/.local-agent-bridge/providers/feishu/npm",
            "--no-audit",
            "--no-fund",
            "--package-lock=false",
            "@larksuite/cli@1.0.96",
        ]
    );
    assert_eq!(
        spec.installed_cli_path().to_string_lossy(),
        "/Users/echoman/.local-agent-bridge/providers/feishu/npm/node_modules/@larksuite/cli/bin/lark-cli"
    );
}

#[test]
fn installer_rejects_relative_or_non_npm_program_paths() {
    use collaboration_adapter::LarkCliInstallSpec;

    assert!(LarkCliInstallSpec::new(
        "npm",
        "/Users/echoman/.local-agent-bridge/providers/feishu/npm",
    ).is_err());
    assert!(LarkCliInstallSpec::new(
        "/opt/homebrew/bin/npm",
        "relative/provider-root",
    ).is_err());
    assert!(LarkCliInstallSpec::new(
        "/bin/sh",
        "/Users/echoman/.local-agent-bridge/providers/feishu/npm",
    ).is_err());
}


#[test]
fn isolated_runner_does_not_inherit_parent_environment() {
    let inherited_key = std::env::vars()
        .map(|(key, _)| key)
        .find(|key| key != "HOME" && key != "PATH")
        .expect("test process should expose at least one non-allowlisted environment variable");
    let runner = ProcessRunner;
    let output = runner.run_with_env(
        "/usr/bin/env",
        &[],
        &[("HOME".into(), "/tmp/lab-provider-home".into()), ("PATH".into(), "/usr/bin:/bin".into())],
        Duration::from_secs(1),
        4096,
        false,
    ).unwrap();
    assert!(!output.stdout.lines().any(|line| line.starts_with(&format!("{inherited_key}="))));
    assert!(output.stdout.contains("HOME=/tmp/lab-provider-home"));
    assert!(output.stdout.contains("PATH=/usr/bin:/bin"));
}


#[test]
fn isolated_provider_uses_private_home_tmp_and_config_dir() {
    use std::fs;
    use std::os::unix::fs::PermissionsExt;

    let root = std::env::current_dir().unwrap().join("target/manual/provider-env-test");
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).unwrap();
    let cli = root.join("lark-cli");
    fs::write(&cli, "#!/bin/sh\nexit 0\n").unwrap();
    fs::set_permissions(&cli, fs::Permissions::from_mode(0o700)).unwrap();

    let provider = LarkCliProvider::new_isolated(
        &cli,
        None,
        root.join("home"),
        root.join("config"),
    ).unwrap();
    let env = provider.environment();
    assert_eq!(env.get("HOME"), Some(&root.join("home").to_string_lossy().into_owned()));
    assert_eq!(env.get("TMPDIR"), Some(&root.join("home/tmp").to_string_lossy().into_owned()));
    assert_eq!(env.get("LARKSUITE_CLI_CONFIG_DIR"), Some(&root.join("config").to_string_lossy().into_owned()));
    assert_eq!(env.get("LARKSUITE_CLI_NO_UPDATE_NOTIFIER"), Some(&"1".to_string()));
    assert_eq!(env.get("LARKSUITE_CLI_NO_SKILLS_NOTIFIER"), Some(&"1".to_string()));
    assert!(env.get("PATH").unwrap().contains("/opt/homebrew/bin"));
    assert!(!env.contains_key("USER"));
}


#[test]
fn admin_commands_are_fixed_to_install_config_and_minimal_auth_domains() {
    use collaboration_adapter::{LarkCliAdminAction, LarkCliAdminSpec, LarkCliInstallSpec};

    let install = LarkCliInstallSpec::new(
        "/opt/homebrew/bin/npm",
        "/Users/tester/.local-agent-bridge/providers/feishu/npm",
    ).unwrap();
    let admin = LarkCliAdminSpec::new(
        install,
        "/Users/tester/.local-agent-bridge/providers/feishu/home",
        "/Users/tester/.local-agent-bridge/providers/feishu/config",
    ).unwrap();

    let install_cmd = admin.command(LarkCliAdminAction::Install).unwrap();
    assert_eq!(install_cmd.program.to_string_lossy(), "/opt/homebrew/bin/npm");
    assert_eq!(install_cmd.args.last().unwrap(), "@larksuite/cli@1.0.96");

    let config_cmd = admin.command(LarkCliAdminAction::ConfigInit).unwrap();
    assert_eq!(
        config_cmd.args,
        vec!["config", "init", "--new", "--lang", "zh"]
    );

    let auth = admin.command(LarkCliAdminAction::AuthBegin).unwrap();
    assert_eq!(
        auth.args,
        vec![
            "auth", "login", "--domain", "base,im", "--no-wait", "--json"
        ]
    );
}

#[test]
fn auth_complete_rejects_untrusted_device_code_shape() {
    use collaboration_adapter::{LarkCliAdminAction, LarkCliAdminSpec, LarkCliInstallSpec};

    let admin = LarkCliAdminSpec::new(
        LarkCliInstallSpec::new(
            "/opt/homebrew/bin/npm",
            "/Users/tester/.local-agent-bridge/providers/feishu/npm",
        ).unwrap(),
        "/Users/tester/.local-agent-bridge/providers/feishu/home",
        "/Users/tester/.local-agent-bridge/providers/feishu/config",
    ).unwrap();

    assert!(admin.command(LarkCliAdminAction::AuthComplete {
        device_code: "bad code; rm -rf /".into(),
    }).is_err());

    let cmd = admin.command(LarkCliAdminAction::AuthComplete {
        device_code: "dc_AbC123-._".into(),
    }).unwrap();
    assert_eq!(
        cmd.args,
        vec!["auth", "login", "--device-code", "dc_AbC123-._", "--json"]
    );
}
