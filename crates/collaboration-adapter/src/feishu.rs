use std::collections::BTreeMap;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::time::Duration;

use crate::{
    CollaborationError, CollaborationErrorKind, CollaborationOperation, ProcessOutput, ProviderRunner,
};

pub const LARK_CLI_PINNED_VERSION: &str = "1.0.96";

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LarkCliInstallSpec {
    npm_program: PathBuf,
    install_root: PathBuf,
}

impl LarkCliInstallSpec {
    pub fn new(
        npm_program: impl AsRef<Path>,
        install_root: impl AsRef<Path>,
    ) -> Result<Self, CollaborationError> {
        let npm_program = npm_program.as_ref();
        let install_root = install_root.as_ref();
        if !npm_program.is_absolute() {
            return Err(CollaborationError::invalid_argument(
                "npm program path must be absolute",
            ));
        }
        if npm_program.file_name().and_then(|value| value.to_str()) != Some("npm") {
            return Err(CollaborationError::invalid_argument(
                "provider installer program must be npm",
            ));
        }
        if !install_root.is_absolute() {
            return Err(CollaborationError::invalid_argument(
                "provider install root must be absolute",
            ));
        }
        if npm_program.as_os_str().is_empty() || install_root.as_os_str().is_empty() {
            return Err(CollaborationError::invalid_argument(
                "provider installer paths must not be empty",
            ));
        }
        Ok(Self {
            npm_program: npm_program.to_path_buf(),
            install_root: install_root.to_path_buf(),
        })
    }

    pub fn npm_program(&self) -> &Path {
        &self.npm_program
    }

    pub fn install_root(&self) -> &Path {
        &self.install_root
    }

    pub fn npm_args(&self) -> Vec<String> {
        vec![
            "install".into(),
            "--prefix".into(),
            self.install_root.to_string_lossy().into_owned(),
            "--no-audit".into(),
            "--no-fund".into(),
            "--package-lock=false".into(),
            format!("@larksuite/cli@{LARK_CLI_PINNED_VERSION}"),
        ]
    }

    pub fn installed_cli_path(&self) -> PathBuf {
        self.install_root
            .join("node_modules")
            .join("@larksuite")
            .join("cli")
            .join("bin")
            .join("lark-cli")
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum LarkCliAdminAction {
    Install,
    ConfigInit,
    AuthBegin,
    AuthComplete { device_code: String },
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LarkCliFixedCommand {
    pub program: PathBuf,
    pub args: Vec<String>,
    pub environment: Vec<(String, String)>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LarkCliAdminSpec {
    install: LarkCliInstallSpec,
    runtime_home: PathBuf,
    config_dir: PathBuf,
}

impl LarkCliAdminSpec {
    pub fn new(
        install: LarkCliInstallSpec,
        runtime_home: impl AsRef<Path>,
        config_dir: impl AsRef<Path>,
    ) -> Result<Self, CollaborationError> {
        let runtime_home = runtime_home.as_ref();
        let config_dir = config_dir.as_ref();
        if !runtime_home.is_absolute() || !config_dir.is_absolute() {
            return Err(CollaborationError::invalid_argument(
                "Feishu admin runtime home and config dir must be absolute",
            ));
        }
        Ok(Self {
            install,
            runtime_home: runtime_home.to_path_buf(),
            config_dir: config_dir.to_path_buf(),
        })
    }

    pub fn command(
        &self,
        action: LarkCliAdminAction,
    ) -> Result<LarkCliFixedCommand, CollaborationError> {
        let (program, args) = match action {
            LarkCliAdminAction::Install => {
                (self.install.npm_program().to_path_buf(), self.install.npm_args())
            }
            LarkCliAdminAction::ConfigInit => (
                self.install.installed_cli_path(),
                vec![
                    "config".into(),
                    "init".into(),
                    "--new".into(),
                    "--lang".into(),
                    "zh".into(),
                ],
            ),
            LarkCliAdminAction::AuthBegin => (
                self.install.installed_cli_path(),
                vec![
                    "auth".into(),
                    "login".into(),
                    "--domain".into(),
                    "base,im".into(),
                    "--no-wait".into(),
                    "--json".into(),
                ],
            ),
            LarkCliAdminAction::AuthComplete { device_code } => {
                validate_device_code(&device_code)?;
                (
                    self.install.installed_cli_path(),
                    vec![
                        "auth".into(),
                        "login".into(),
                        "--device-code".into(),
                        device_code,
                        "--json".into(),
                    ],
                )
            }
        };

        Ok(LarkCliFixedCommand {
            program,
            args,
            environment: self.environment(),
        })
    }

    pub fn environment(&self) -> Vec<(String, String)> {
        let tmp_dir = self.runtime_home.join("tmp");
        let npm_cache = self.runtime_home.join("npm-cache");
        vec![
            ("HOME".into(), self.runtime_home.to_string_lossy().into_owned()),
            ("TMPDIR".into(), tmp_dir.to_string_lossy().into_owned()),
            (
                "PATH".into(),
                "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin".into(),
            ),
            (
                "LARKSUITE_CLI_CONFIG_DIR".into(),
                self.config_dir.to_string_lossy().into_owned(),
            ),
            ("LARKSUITE_CLI_NO_UPDATE_NOTIFIER".into(), "1".into()),
            ("LARKSUITE_CLI_NO_SKILLS_NOTIFIER".into(), "1".into()),
            (
                "npm_config_cache".into(),
                npm_cache.to_string_lossy().into_owned(),
            ),
            ("npm_config_audit".into(), "false".into()),
            ("npm_config_fund".into(), "false".into()),
            ("LANG".into(), "C.UTF-8".into()),
        ]
    }

    pub fn install_spec(&self) -> &LarkCliInstallSpec {
        &self.install
    }
}

fn validate_device_code(value: &str) -> Result<(), CollaborationError> {
    if value.is_empty() || value.len() > 256 {
        return Err(CollaborationError::invalid_argument(
            "device_code length is invalid",
        ));
    }
    if !value.bytes().all(|byte| {
        byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-' | b'.')
    }) {
        return Err(CollaborationError::invalid_argument(
            "device_code contains unsupported characters",
        ));
    }
    Ok(())
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Identity {
    User,
    Bot,
}

impl Identity {
    fn as_str(self) -> &'static str {
        match self {
            Self::User => "user",
            Self::Bot => "bot",
        }
    }
}

#[derive(Clone, Debug)]
pub struct LarkCliProvider {
    cli_path: PathBuf,
    profile: Option<String>,
    environment: Vec<(String, String)>,
}

impl LarkCliProvider {
    pub fn new(
        cli_path: impl AsRef<Path>,
        profile: Option<String>,
    ) -> Result<Self, CollaborationError> {
        let cli_path = cli_path.as_ref();
        if !cli_path.is_absolute() {
            return Err(CollaborationError::invalid_argument(
                "lark-cli path must be absolute",
            ));
        }
        let metadata = std::fs::symlink_metadata(cli_path).map_err(|error| {
            CollaborationError {
                kind: CollaborationErrorKind::CliUnavailable,
                message: format!("lark-cli path is unavailable: {error}"),
                dispatch_attempted: false,
            }
        })?;
        if metadata.file_type().is_symlink() || !metadata.is_file() {
            return Err(CollaborationError::invalid_argument(
                "lark-cli path must be a real regular file",
            ));
        }
        if metadata.permissions().mode() & 0o111 == 0 {
            return Err(CollaborationError::invalid_argument(
                "lark-cli path must be executable",
            ));
        }
        if let Some(profile) = profile.as_deref() {
            validate_text("profile", profile, 128)?;
        }
        Ok(Self {
            cli_path: cli_path.to_path_buf(),
            profile,
            environment: Vec::new(),
        })
    }

    pub fn new_isolated(
        cli_path: impl AsRef<Path>,
        profile: Option<String>,
        runtime_home: impl AsRef<Path>,
        config_dir: impl AsRef<Path>,
    ) -> Result<Self, CollaborationError> {
        let runtime_home = runtime_home.as_ref();
        let config_dir = config_dir.as_ref();
        if !runtime_home.is_absolute() || !config_dir.is_absolute() {
            return Err(CollaborationError::invalid_argument(
                "Feishu runtime home and config dir must be absolute",
            ));
        }
        let mut provider = Self::new(cli_path, profile)?;
        let tmp_dir = runtime_home.join("tmp");
        for path in [runtime_home, tmp_dir.as_path(), config_dir] {
            std::fs::create_dir_all(path).map_err(|error| CollaborationError {
                kind: CollaborationErrorKind::ProviderRejected,
                message: format!("Feishu private runtime directory create failed: {error}"),
                dispatch_attempted: false,
            })?;
            std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o700)).map_err(
                |error| CollaborationError {
                    kind: CollaborationErrorKind::ProviderRejected,
                    message: format!("Feishu private runtime directory chmod failed: {error}"),
                    dispatch_attempted: false,
                },
            )?;
        }
        provider.environment = vec![
            ("HOME".into(), runtime_home.to_string_lossy().into_owned()),
            ("TMPDIR".into(), tmp_dir.to_string_lossy().into_owned()),
            (
                "PATH".into(),
                "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin".into(),
            ),
            (
                "LARKSUITE_CLI_CONFIG_DIR".into(),
                config_dir.to_string_lossy().into_owned(),
            ),
            ("LARKSUITE_CLI_NO_UPDATE_NOTIFIER".into(), "1".into()),
            ("LARKSUITE_CLI_NO_SKILLS_NOTIFIER".into(), "1".into()),
            ("LANG".into(), "C.UTF-8".into()),
        ];
        Ok(provider)
    }

    pub fn for_test(cli_path: impl AsRef<Path>, profile: Option<String>) -> Self {
        Self {
            cli_path: cli_path.as_ref().to_path_buf(),
            profile,
            environment: Vec::new(),
        }
    }

    pub fn cli_path(&self) -> &Path {
        &self.cli_path
    }

    pub fn environment(&self) -> BTreeMap<String, String> {
        self.environment.iter().cloned().collect()
    }

    pub fn execute_argv_with<R: ProviderRunner>(
        &self,
        argv: &[String],
        runner: &R,
        timeout: Duration,
        output_limit: usize,
        write_operation: bool,
    ) -> Result<ProcessOutput, CollaborationError> {
        runner.run_with_env(
            self.cli_path.to_string_lossy().as_ref(),
            argv,
            &self.environment,
            timeout,
            output_limit,
            write_operation,
        )
    }

    pub fn execute_with<R: ProviderRunner>(
        &self,
        operation: &CollaborationOperation,
        runner: &R,
        timeout: Duration,
        output_limit: usize,
    ) -> Result<ProcessOutput, CollaborationError> {
        if let CollaborationOperation::BaseCreateRecord {
            base_token,
            table_id,
            ..
        }
        | CollaborationOperation::BaseUpdateRecord {
            base_token,
            table_id,
            ..
        } = operation
        {
            let preflight = CollaborationOperation::BaseListFields {
                base_token: base_token.clone(),
                table_id: table_id.clone(),
            };
            let argv = self.argv_for(&preflight)?;
            let output = runner.run_with_env(
                self.cli_path.to_string_lossy().as_ref(),
                &argv,
                &self.environment,
                timeout,
                output_limit,
                false,
            )?;
            require_json_output(&output.stdout)?;
        }

        let argv = self.argv_for(operation)?;
        let output = runner.run_with_env(
            self.cli_path.to_string_lossy().as_ref(),
            &argv,
            &self.environment,
            timeout,
            output_limit,
            operation.is_write(),
        )?;
        require_json_output(&output.stdout)?;
        Ok(output)
    }

    pub fn argv_for(
        &self,
        operation: &CollaborationOperation,
    ) -> Result<Vec<String>, CollaborationError> {
        let mut argv = Vec::new();
        if let Some(profile) = self.profile.as_deref() {
            validate_text("profile", profile, 128)?;
            argv.extend(["--profile".into(), profile.into()]);
        }

        match operation {
            CollaborationOperation::Status => {
                argv.push("whoami".into());
            }
            CollaborationOperation::BaseListTables { base_token } => {
                validate_token("base_token", base_token)?;
                argv.extend([
                    "base".into(),
                    "+table-list".into(),
                    "--base-token".into(),
                    base_token.clone(),
                    "--format".into(),
                    "json".into(),
                    "--as".into(),
                    "user".into(),
                ]);
            }
            CollaborationOperation::BaseListFields {
                base_token,
                table_id,
            } => {
                validate_token("base_token", base_token)?;
                validate_token("table_id", table_id)?;
                argv.extend([
                    "base".into(),
                    "+field-list".into(),
                    "--base-token".into(),
                    base_token.clone(),
                    "--table-id".into(),
                    table_id.clone(),
                    "--format".into(),
                    "json".into(),
                    "--as".into(),
                    "user".into(),
                ]);
            }
            CollaborationOperation::BaseListRecords {
                base_token,
                table_id,
                view_id,
                page_size,
                page_token,
            } => {
                validate_token("base_token", base_token)?;
                validate_token("table_id", table_id)?;
                argv.extend([
                    "base".into(),
                    "+record-list".into(),
                    "--base-token".into(),
                    base_token.clone(),
                    "--table-id".into(),
                    table_id.clone(),
                ]);
                if let Some(view_id) = view_id {
                    validate_token("view_id", view_id)?;
                    argv.extend(["--view-id".into(), view_id.clone()]);
                }
                if let Some(page_size) = page_size {
                    validate_page_size(*page_size, 500)?;
                    argv.extend(["--page-size".into(), page_size.to_string()]);
                }
                if let Some(page_token) = page_token {
                    validate_text("page_token", page_token, 512)?;
                    argv.extend(["--page-token".into(), page_token.clone()]);
                }
                argv.extend([
                    "--format".into(),
                    "json".into(),
                    "--as".into(),
                    "user".into(),
                ]);
            }
            CollaborationOperation::ChatSearch { query, page_size } => {
                validate_text("query", query, 512)?;
                argv.extend([
                    "im".into(),
                    "+chat-search".into(),
                    "--query".into(),
                    query.clone(),
                ]);
                if let Some(page_size) = page_size {
                    validate_page_size(*page_size, 50)?;
                    argv.extend(["--page-size".into(), page_size.to_string()]);
                }
                argv.extend([
                    "--format".into(),
                    "json".into(),
                    "--as".into(),
                    "user".into(),
                ]);
            }
            CollaborationOperation::MessageList {
                chat_id,
                page_size,
                page_token,
            } => {
                validate_chat_id(chat_id)?;
                argv.extend([
                    "im".into(),
                    "+chat-messages-list".into(),
                    "--chat-id".into(),
                    chat_id.clone(),
                ]);
                let size = page_size.unwrap_or(50).max(20);
                validate_page_size(size, 50)?;
                argv.extend(["--page-size".into(), size.to_string()]);
                if let Some(page_token) = page_token {
                    validate_text("page_token", page_token, 512)?;
                    argv.extend(["--page-token".into(), page_token.clone()]);
                }
                argv.extend([
                    "--format".into(),
                    "json".into(),
                    "--as".into(),
                    "user".into(),
                ]);
            }
            CollaborationOperation::BaseCreate {
                name,
                folder_token,
                table_name,
                fields_json,
                time_zone,
            } => {
                validate_text("name", name, 256)?;
                argv.extend(["base".into(), "+base-create".into(), "--name".into(), name.clone()]);
                if let Some(folder_token) = folder_token {
                    validate_token("folder_token", folder_token)?;
                    argv.extend(["--folder-token".into(), folder_token.clone()]);
                }
                match (table_name.as_ref(), fields_json.as_ref()) {
                    (None, None) => {}
                    (Some(table_name), Some(fields_json)) => {
                        validate_text("table_name", table_name, 256)?;
                        validate_json_array("fields", fields_json)?;
                        argv.extend([
                            "--table-name".into(),
                            table_name.clone(),
                            "--fields".into(),
                            fields_json.clone(),
                        ]);
                    }
                    _ => {
                        return Err(CollaborationError::invalid_argument(
                            "table_name and fields must be provided together",
                        ));
                    }
                }
                if let Some(time_zone) = time_zone {
                    validate_text("time_zone", time_zone, 64)?;
                    argv.extend(["--time-zone".into(), time_zone.clone()]);
                }
                argv.extend(["--format".into(), "json".into(), "--as".into(), "user".into()]);
            }
            CollaborationOperation::BaseCreateView {
                base_token,
                table_id,
                view_json,
            } => {
                validate_token("base_token", base_token)?;
                validate_token("table_id", table_id)?;
                validate_json_text(view_json)?;
                argv.extend([
                    "base".into(),
                    "+view-create".into(),
                    "--base-token".into(),
                    base_token.clone(),
                    "--table-id".into(),
                    table_id.clone(),
                    "--json".into(),
                    view_json.clone(),
                    "--format".into(),
                    "json".into(),
                    "--as".into(),
                    "user".into(),
                ]);
            }
            CollaborationOperation::BaseCreateRecord {
                base_token,
                table_id,
                fields_json,
            } => {
                validate_token("base_token", base_token)?;
                validate_token("table_id", table_id)?;
                validate_json_text(fields_json)?;
                argv.extend([
                    "base".into(),
                    "+record-upsert".into(),
                    "--base-token".into(),
                    base_token.clone(),
                    "--table-id".into(),
                    table_id.clone(),
                    "--json".into(),
                    fields_json.clone(),
                    "--format".into(),
                    "json".into(),
                    "--as".into(),
                    "user".into(),
                ]);
            }
            CollaborationOperation::BaseUpdateRecord {
                base_token,
                table_id,
                record_id,
                fields_json,
            } => {
                validate_token("base_token", base_token)?;
                validate_token("table_id", table_id)?;
                validate_token("record_id", record_id)?;
                validate_json_text(fields_json)?;
                argv.extend([
                    "base".into(),
                    "+record-upsert".into(),
                    "--base-token".into(),
                    base_token.clone(),
                    "--table-id".into(),
                    table_id.clone(),
                    "--record-id".into(),
                    record_id.clone(),
                    "--json".into(),
                    fields_json.clone(),
                    "--format".into(),
                    "json".into(),
                    "--as".into(),
                    "user".into(),
                ]);
            }
            CollaborationOperation::MessageSend {
                chat_id,
                text,
                identity,
                idempotency_key,
            } => {
                validate_chat_id(chat_id)?;
                validate_text("text", text, 8_192)?;
                validate_text("idempotency_key", idempotency_key, 50)?;
                argv.extend([
                    "im".into(),
                    "+messages-send".into(),
                    "--chat-id".into(),
                    chat_id.clone(),
                    "--text".into(),
                    text.clone(),
                    "--as".into(),
                    identity.as_str().into(),
                    "--idempotency-key".into(),
                    idempotency_key.clone(),
                    "--format".into(),
                    "json".into(),
                ]);
            }
            CollaborationOperation::MessageReply {
                message_id,
                text,
                identity,
                idempotency_key,
            } => {
                validate_message_id(message_id)?;
                validate_text("text", text, 8_192)?;
                validate_text("idempotency_key", idempotency_key, 50)?;
                argv.extend([
                    "im".into(),
                    "+messages-reply".into(),
                    "--message-id".into(),
                    message_id.clone(),
                    "--text".into(),
                    text.clone(),
                    "--as".into(),
                    identity.as_str().into(),
                    "--idempotency-key".into(),
                    idempotency_key.clone(),
                    "--format".into(),
                    "json".into(),
                ]);
            }
            CollaborationOperation::WikiNodeGet { node_token } => {
                validate_text("node_token", node_token, 1024)?;
                argv.extend([
                    "wiki".into(),
                    "+node-get".into(),
                    "--node-token".into(),
                    node_token.clone(),
                    "--as".into(),
                    "user".into(),
                    "--format".into(),
                    "json".into(),
                ]);
            }
            CollaborationOperation::DocsSearch { query, page_size } => {
                validate_text("query", query, 512)?;
                argv.extend([
                    "docs".into(),
                    "+search".into(),
                    "--query".into(),
                    query.clone(),
                    "--as".into(),
                    "user".into(),
                    "--format".into(),
                    "json".into(),
                ]);
                if let Some(page_size) = page_size {
                    validate_page_size(*page_size, 20)?;
                    argv.extend(["--page-size".into(), page_size.to_string()]);
                }
            }
            CollaborationOperation::DocsFetch { doc, doc_format } => {
                validate_text("doc", doc, 1024)?;
                argv.extend([
                    "docs".into(),
                    "+fetch".into(),
                    "--doc".into(),
                    doc.clone(),
                    "--as".into(),
                    "user".into(),
                    "--doc-format".into(),
                    doc_format
                        .clone()
                        .unwrap_or_else(|| "markdown".into()),
                ]);
            }
            CollaborationOperation::DocsUpdate {
                doc,
                command,
                pattern,
                content,
                doc_format,
                block_id,
                src_block_ids,
            } => {
                validate_text("doc", doc, 1024)?;
                validate_text("command", command, 64)?;
                match command.as_str() {
                    "str_replace" | "overwrite" | "append" | "block_move_after" | "block_delete" => {}
                    _ => {
                        return Err(CollaborationError::invalid_argument(
                            "unsupported docs update command",
                        ));
                    }
                }
                argv.extend([
                    "docs".into(),
                    "+update".into(),
                    "--doc".into(),
                    doc.clone(),
                    "--command".into(),
                    command.clone(),
                    "--as".into(),
                    "user".into(),
                ]);
                if let Some(pattern) = pattern {
                    validate_text("pattern", pattern, 8 * 1024)?;
                    argv.extend(["--pattern".into(), pattern.clone()]);
                }
                if let Some(content) = content {
                    validate_text("content", content, 512 * 1024)?;
                    argv.extend(["--content".into(), content.clone()]);
                }
                if let Some(doc_format) = doc_format {
                    validate_text("doc_format", doc_format, 32)?;
                    argv.extend(["--doc-format".into(), doc_format.clone()]);
                }
                if let Some(block_id) = block_id {
                    validate_text("block_id", block_id, 256)?;
                    argv.extend(["--block-id".into(), block_id.clone()]);
                }
                if let Some(src_block_ids) = src_block_ids {
                    validate_text("src_block_ids", src_block_ids, 1024)?;
                    argv.extend(["--src-block-ids".into(), src_block_ids.clone()]);
                }
            }
            CollaborationOperation::DocsMediaInsert {
                doc,
                file,
                width,
                caption,
                align,
            } => {
                validate_text("doc", doc, 1024)?;
                validate_text("file", file, 1024)?;
                if !file.starts_with('/') {
                    return Err(CollaborationError::invalid_argument(
                        "file must be an absolute path",
                    ));
                }
                argv.extend([
                    "docs".into(),
                    "+media-insert".into(),
                    "--doc".into(),
                    doc.clone(),
                    "--file".into(),
                    file.clone(),
                    "--as".into(),
                    "user".into(),
                    "--type".into(),
                    "image".into(),
                ]);
                if let Some(width) = width {
                    if *width == 0 {
                        return Err(CollaborationError::invalid_argument("width must be positive"));
                    }
                    argv.extend(["--width".into(), width.to_string()]);
                }
                if let Some(caption) = caption {
                    validate_text("caption", caption, 512)?;
                    argv.extend(["--caption".into(), caption.clone()]);
                }
                if let Some(align) = align {
                    match align.as_str() {
                        "left" | "center" | "right" => {}
                        _ => {
                            return Err(CollaborationError::invalid_argument(
                                "align must be left, center, or right",
                            ));
                        }
                    }
                    argv.extend(["--align".into(), align.clone()]);
                }
            }
        }

        Ok(argv)
    }
}

fn validate_text(name: &str, value: &str, max_len: usize) -> Result<(), CollaborationError> {
    if value.trim().is_empty() {
        return Err(CollaborationError::invalid_argument(format!(
            "{name} must not be empty"
        )));
    }
    if value.len() > max_len {
        return Err(CollaborationError::invalid_argument(format!(
            "{name} exceeds {max_len} bytes"
        )));
    }
    if value.as_bytes().contains(&0) {
        return Err(CollaborationError::invalid_argument(format!(
            "{name} contains NUL"
        )));
    }
    Ok(())
}

fn validate_token(name: &str, value: &str) -> Result<(), CollaborationError> {
    validate_text(name, value, 512)
}

fn validate_chat_id(value: &str) -> Result<(), CollaborationError> {
    validate_text("chat_id", value, 256)?;
    if !value.starts_with("oc_") {
        return Err(CollaborationError::invalid_argument(
            "chat_id must start with oc_",
        ));
    }
    Ok(())
}

fn validate_message_id(value: &str) -> Result<(), CollaborationError> {
    validate_text("message_id", value, 256)?;
    if !(value.starts_with("om_") || value.starts_with("omt_")) {
        return Err(CollaborationError::invalid_argument(
            "message_id must start with om_ or omt_",
        ));
    }
    Ok(())
}

fn validate_page_size(value: u16, max: u16) -> Result<(), CollaborationError> {
    if value == 0 || value > max {
        return Err(CollaborationError::invalid_argument(format!(
            "page_size must be between 1 and {max}"
        )));
    }
    Ok(())
}

fn validate_json_array(name: &str, value: &str) -> Result<(), CollaborationError> {
    validate_text(name, value, 64 * 1024)?;
    let trimmed = value.trim();
    if !(trimmed.starts_with('[') && trimmed.ends_with(']')) {
        return Err(CollaborationError::invalid_argument(format!(
            "{name} must be a JSON array string"
        )));
    }
    Ok(())
}

fn validate_json_text(value: &str) -> Result<(), CollaborationError> {
    validate_text("fields_json", value, 64 * 1024)?;
    let trimmed = value.trim();
    if !(trimmed.starts_with('{') && trimmed.ends_with('}')) {
        return Err(CollaborationError::invalid_argument(
            "fields_json must be a JSON object string",
        ));
    }
    Ok(())
}

fn require_json_output(value: &str) -> Result<(), CollaborationError> {
    let trimmed = value.trim();
    let object = trimmed.starts_with('{') && trimmed.ends_with('}');
    let array = trimmed.starts_with('[') && trimmed.ends_with(']');
    if object || array {
        Ok(())
    } else {
        Err(CollaborationError {
            kind: CollaborationErrorKind::InvalidProviderOutput,
            message: "provider output is not JSON".into(),
            dispatch_attempted: true,
        })
    }
}
