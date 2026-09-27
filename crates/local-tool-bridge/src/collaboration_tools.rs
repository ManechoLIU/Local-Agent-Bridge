use std::{
    collections::{HashMap, HashSet},
    io::Read,
    os::unix::fs::PermissionsExt,
    process::{Command, Stdio},
    sync::{
        Arc, Mutex,
        atomic::{AtomicU64, Ordering},
    },
    thread,
    time::{Duration, Instant},
};

use collaboration_adapter::{
    CollaborationError, CollaborationErrorKind, CollaborationOperation, Identity,
    LarkCliAdminAction, LarkCliAdminSpec, LarkCliFixedCommand, LarkCliProvider, ProcessRunner,
};
use serde_json::{Value, json};

#[derive(Clone, Debug)]
pub struct CollaborationRuntime {
    provider: LarkCliProvider,
    timeout: Duration,
    output_limit: usize,
}

impl CollaborationRuntime {
    pub fn new(
        provider: LarkCliProvider,
        timeout: Duration,
        output_limit: usize,
    ) -> Result<Self, String> {
        if timeout.is_zero() {
            return Err("Feishu provider timeout must be positive".into());
        }
        if output_limit == 0 || output_limit > 4 * 1024 * 1024 {
            return Err("Feishu provider output limit must be between 1 and 4 MiB".into());
        }
        Ok(Self {
            provider,
            timeout,
            output_limit,
        })
    }

    pub fn execute_argv(&self, argv: &[String]) -> Result<Value, CollaborationError> {
        let output = self.provider.execute_argv_with(
            argv,
            &ProcessRunner,
            self.timeout,
            self.output_limit,
            true,
        )?;
        let stdout = output.stdout.trim();
        if stdout.is_empty() {
            return Ok(json!({
                "ok": output.exit_code == 0,
                "exitCode": output.exit_code,
                "stderr": output.stderr,
            }));
        }
        match serde_json::from_str::<Value>(stdout) {
            Ok(value) => Ok(value),
            Err(_) => Ok(json!({
                "ok": output.exit_code == 0,
                "exitCode": output.exit_code,
                "raw": stdout.chars().take(8000).collect::<String>(),
                "stderr": output.stderr,
            })),
        }
    }

    pub fn execute(&self, operation: &CollaborationOperation) -> Result<Value, CollaborationError> {
        let output = self.provider.execute_with(
            operation,
            &ProcessRunner,
            self.timeout,
            self.output_limit,
        )?;
        let mut value: Value = serde_json::from_str(&output.stdout).map_err(|error| {
            CollaborationError {
                kind: CollaborationErrorKind::InvalidProviderOutput,
                message: format!("provider JSON parse failed: {error}"),
                dispatch_attempted: true,
            }
        })?;
        if matches!(operation, CollaborationOperation::Status) {
            redact_status_secrets(&mut value);
        }
        Ok(value)
    }
}

#[derive(Clone)]
pub struct FeishuAdminRuntime {
    inner: Arc<FeishuAdminRuntimeInner>,
}

struct FeishuAdminRuntimeInner {
    spec: LarkCliAdminSpec,
    timeout: Duration,
    output_limit: usize,
    jobs: Mutex<HashMap<String, Arc<Mutex<AdminJobSnapshot>>>>,
    auth_flows: Mutex<HashMap<String, String>>,
    next_id: AtomicU64,
}

#[derive(Debug)]
struct AdminJobSnapshot {
    state: String,
    stdout: String,
    stderr: String,
    output_truncated: bool,
    exit_code: Option<i32>,
}

impl FeishuAdminRuntime {
    pub fn new(
        spec: LarkCliAdminSpec,
        timeout: Duration,
        output_limit: usize,
    ) -> Result<Self, String> {
        if timeout.is_zero() {
            return Err("Feishu admin timeout must be positive".into());
        }
        if output_limit == 0 || output_limit > 4 * 1024 * 1024 {
            return Err("Feishu admin output limit must be between 1 and 4 MiB".into());
        }
        prepare_admin_directories(&spec)?;
        Ok(Self {
            inner: Arc::new(FeishuAdminRuntimeInner {
                spec,
                timeout,
                output_limit,
                jobs: Mutex::new(HashMap::new()),
                auth_flows: Mutex::new(HashMap::new()),
                next_id: AtomicU64::new(1),
            }),
        })
    }

    pub fn start_setup(&self, action: &str) -> Result<Value, String> {
        let action = match action {
            "install" => LarkCliAdminAction::Install,
            "config" => LarkCliAdminAction::ConfigInit,
            _ => return Err("Feishu setup action must be install or config".into()),
        };
        let command = self
            .inner
            .spec
            .command(action)
            .map_err(|error| error.message)?;
        let job_id = self.next_id("feishu-setup");
        let snapshot = Arc::new(Mutex::new(AdminJobSnapshot {
            state: "running".into(),
            stdout: String::new(),
            stderr: String::new(),
            output_truncated: false,
            exit_code: None,
        }));
        self.inner
            .jobs
            .lock()
            .map_err(|_| "Feishu admin jobs lock is poisoned".to_string())?
            .insert(job_id.clone(), snapshot.clone());
        let timeout = self.inner.timeout;
        let output_limit = self.inner.output_limit;
        thread::spawn(move || {
            run_streaming_admin_job(command, snapshot, timeout, output_limit);
        });
        Ok(json!({"job_id":job_id,"state":"running"}))
    }

    pub fn poll_setup(&self, job_id: &str) -> Result<Value, String> {
        if job_id.is_empty() || job_id.len() > 128 {
            return Err("Feishu setup job_id is invalid".into());
        }
        let snapshot = self
            .inner
            .jobs
            .lock()
            .map_err(|_| "Feishu admin jobs lock is poisoned".to_string())?
            .get(job_id)
            .cloned()
            .ok_or_else(|| "unknown Feishu setup job".to_string())?;
        let snapshot = snapshot
            .lock()
            .map_err(|_| "Feishu setup job lock is poisoned".to_string())?;
        let urls = extract_https_urls(&format!("{}\n{}", snapshot.stdout, snapshot.stderr));
        Ok(json!({
            "job_id":job_id,
            "state":snapshot.state,
            "verification_urls":urls,
            "exit_code":snapshot.exit_code,
            "output_truncated":snapshot.output_truncated,
        }))
    }

    pub fn auth_begin(&self) -> Result<Value, CollaborationError> {
        let command = self.inner.spec.command(LarkCliAdminAction::AuthBegin)?;
        let output = ProcessRunner.run_with_env(
            command.program.to_string_lossy().as_ref(),
            &command.args,
            &command.environment,
            self.inner.timeout.min(Duration::from_secs(60)),
            self.inner.output_limit,
            true,
        )?;
        let value: Value = serde_json::from_str(&output.stdout).map_err(|error| CollaborationError {
            kind: CollaborationErrorKind::InvalidProviderOutput,
            message: format!("Feishu auth begin JSON parse failed: {error}"),
            dispatch_attempted: true,
        })?;
        let verification_url = value
            .get("verification_url")
            .and_then(Value::as_str)
            .filter(|value| value.starts_with("https://"))
            .ok_or_else(|| CollaborationError {
                kind: CollaborationErrorKind::InvalidProviderOutput,
                message: "Feishu auth begin returned no HTTPS verification_url".into(),
                dispatch_attempted: true,
            })?
            .to_string();
        let device_code = value
            .get("device_code")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .ok_or_else(|| CollaborationError {
                kind: CollaborationErrorKind::InvalidProviderOutput,
                message: "Feishu auth begin returned no device_code".into(),
                dispatch_attempted: true,
            })?
            .to_string();
        // Re-run the provider-specific validator without exposing the device code.
        self.inner
            .spec
            .command(LarkCliAdminAction::AuthComplete {
                device_code: device_code.clone(),
            })?;
        let flow_id = self.next_id("feishu-auth");
        self.inner
            .auth_flows
            .lock()
            .map_err(|_| CollaborationError {
                kind: CollaborationErrorKind::ProviderRejected,
                message: "Feishu auth flow lock is poisoned".into(),
                dispatch_attempted: false,
            })?
            .insert(flow_id.clone(), device_code);
        Ok(json!({
            "flow_id":flow_id,
            "verification_url":verification_url,
            "expires_in":value.get("expires_in").cloned().unwrap_or(Value::Null),
        }))
    }

    pub fn auth_complete(&self, flow_id: &str) -> Result<Value, CollaborationError> {
        if flow_id.is_empty() || flow_id.len() > 128 {
            return Err(CollaborationError {
                kind: CollaborationErrorKind::InvalidArgument,
                message: "Feishu auth flow_id is invalid".into(),
                dispatch_attempted: false,
            });
        }
        let device_code = self
            .inner
            .auth_flows
            .lock()
            .map_err(|_| CollaborationError {
                kind: CollaborationErrorKind::ProviderRejected,
                message: "Feishu auth flow lock is poisoned".into(),
                dispatch_attempted: false,
            })?
            .get(flow_id)
            .cloned()
            .ok_or_else(|| CollaborationError {
                kind: CollaborationErrorKind::InvalidArgument,
                message: "unknown Feishu auth flow".into(),
                dispatch_attempted: false,
            })?;
        let command = self
            .inner
            .spec
            .command(LarkCliAdminAction::AuthComplete { device_code })?;
        ProcessRunner.run_with_env(
            command.program.to_string_lossy().as_ref(),
            &command.args,
            &command.environment,
            self.inner.timeout,
            self.inner.output_limit,
            true,
        )?;
        self.inner
            .auth_flows
            .lock()
            .map_err(|_| CollaborationError {
                kind: CollaborationErrorKind::ProviderRejected,
                message: "Feishu auth flow lock is poisoned".into(),
                dispatch_attempted: true,
            })?
            .remove(flow_id);
        Ok(json!({"status":"authorized"}))
    }

    fn next_id(&self, prefix: &str) -> String {
        let value = self.inner.next_id.fetch_add(1, Ordering::Relaxed);
        format!("{prefix}-{value}")
    }
}

fn prepare_admin_directories(spec: &LarkCliAdminSpec) -> Result<(), String> {
    let mut paths = vec![spec.install_spec().install_root().to_path_buf()];
    for (key, value) in spec.environment() {
        if matches!(
            key.as_str(),
            "HOME" | "TMPDIR" | "LARKSUITE_CLI_CONFIG_DIR" | "npm_config_cache"
        ) {
            paths.push(std::path::PathBuf::from(value));
        }
    }
    for path in paths {
        std::fs::create_dir_all(&path)
            .map_err(|error| format!("Feishu admin directory create failed: {error}"))?;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700))
            .map_err(|error| format!("Feishu admin directory chmod failed: {error}"))?;
    }
    Ok(())
}

fn run_streaming_admin_job(
    command: LarkCliFixedCommand,
    snapshot: Arc<Mutex<AdminJobSnapshot>>,
    timeout: Duration,
    output_limit: usize,
) {
    let mut process = Command::new(&command.program);
    let child = process
        .args(&command.args)
        .env_clear()
        .envs(command.environment.iter().map(|(key, value)| (key, value)))
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn();
    let mut child = match child {
        Ok(child) => child,
        Err(_) => {
            if let Ok(mut snapshot) = snapshot.lock() {
                snapshot.state = "failed".into();
            }
            return;
        }
    };
    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    let stdout_reader = stdout.map(|reader| spawn_admin_reader(reader, snapshot.clone(), true, output_limit));
    let stderr_reader = stderr.map(|reader| spawn_admin_reader(reader, snapshot.clone(), false, output_limit));
    let deadline = Instant::now() + timeout;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break Some(status),
            Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(20)),
            Ok(None) => {
                let _ = child.kill();
                let _ = child.wait();
                if let Ok(mut snapshot) = snapshot.lock() {
                    snapshot.state = "result_unknown_after_dispatch".into();
                }
                break None;
            }
            Err(_) => {
                let _ = child.kill();
                let _ = child.wait();
                if let Ok(mut snapshot) = snapshot.lock() {
                    snapshot.state = "failed".into();
                }
                break None;
            }
        }
    };
    if let Some(reader) = stdout_reader {
        let _ = reader.join();
    }
    if let Some(reader) = stderr_reader {
        let _ = reader.join();
    }
    if let Some(status) = status {
        if let Ok(mut snapshot) = snapshot.lock() {
            snapshot.exit_code = status.code();
            snapshot.state = if status.success() {
                "succeeded".into()
            } else {
                "failed".into()
            };
        }
    }
}

fn spawn_admin_reader<R: Read + Send + 'static>(
    mut reader: R,
    snapshot: Arc<Mutex<AdminJobSnapshot>>,
    stdout: bool,
    output_limit: usize,
) -> thread::JoinHandle<()> {
    thread::spawn(move || {
        let mut buffer = [0u8; 4096];
        loop {
            let count = match reader.read(&mut buffer) {
                Ok(0) | Err(_) => break,
                Ok(count) => count,
            };
            let text = String::from_utf8_lossy(&buffer[..count]);
            if let Ok(mut snapshot) = snapshot.lock() {
                let target = if stdout {
                    &mut snapshot.stdout
                } else {
                    &mut snapshot.stderr
                };
                let remaining = output_limit.saturating_sub(target.len());
                if remaining == 0 {
                    snapshot.output_truncated = true;
                    continue;
                }
                let bytes = text.as_bytes();
                let take = remaining.min(bytes.len());
                target.push_str(&String::from_utf8_lossy(&bytes[..take]));
                if take < bytes.len() {
                    snapshot.output_truncated = true;
                }
            }
        }
    })
}

fn extract_https_urls(text: &str) -> Vec<String> {
    let mut urls = Vec::new();
    let mut offset = 0;
    while let Some(relative) = text[offset..].find("https://") {
        let start = offset + relative;
        let tail = &text[start..];
        let end = tail
            .char_indices()
            .find_map(|(index, ch)| {
                if index > 0
                    && (ch.is_whitespace()
                        || matches!(ch, '"' | '\'' | '<' | '>' | '\x1b'))
                {
                    Some(index)
                } else {
                    None
                }
            })
            .unwrap_or(tail.len());
        let url = tail[..end]
            .trim_end_matches(|ch: char| matches!(ch, ')' | ']' | ',' | ';' | '.'))
            .to_string();
        if !url.is_empty() && !urls.contains(&url) {
            urls.push(url);
        }
        offset = start + end.max(8);
        if offset >= text.len() {
            break;
        }
    }
    urls
}

fn redact_status_secrets(value: &mut Value) {
    match value {
        Value::Object(map) => {
            for (key, child) in map.iter_mut() {
                let key = key.to_ascii_lowercase();
                if key.contains("token")
                    || key.contains("secret")
                    || key.contains("credential")
                    || key == "authorization_url"
                    || key == "auth_url"
                {
                    *child = Value::String("[REDACTED]".into());
                } else {
                    redact_status_secrets(child);
                }
            }
        }
        Value::Array(items) => {
            for item in items {
                redact_status_secrets(item);
            }
        }
        _ => {}
    }
}

pub fn error_kind_name(kind: &CollaborationErrorKind) -> &'static str {
    match kind {
        CollaborationErrorKind::CliUnavailable => "cli_unavailable",
        CollaborationErrorKind::AuthRequired => "auth_required",
        CollaborationErrorKind::PermissionDenied => "permission_denied",
        CollaborationErrorKind::InvalidArgument => "invalid_argument",
        CollaborationErrorKind::ProviderRateLimited => "provider_rate_limited",
        CollaborationErrorKind::ProviderRejected => "provider_rejected",
        CollaborationErrorKind::TimeoutBeforeDispatch => "timeout_before_dispatch",
        CollaborationErrorKind::ProviderTimeoutAfterDispatch => "provider_timeout_after_dispatch",
        CollaborationErrorKind::ResultUnknownAfterDispatch => "result_unknown_after_dispatch",
        CollaborationErrorKind::InvalidProviderOutput => "invalid_provider_output",
    }
}

pub fn is_feishu_admin_tool(name: &str) -> bool {
    matches!(
        name,
        "feishu_setup_start"
            | "feishu_setup_poll"
            | "feishu_auth_begin"
            | "feishu_auth_complete"
    )
}

pub fn feishu_admin_tool_definitions() -> Vec<Value> {
    vec![
        tool(
            "feishu_setup_start",
            "Start a fixed Feishu provider setup job. Only install or config are allowed.",
            json!({
                "type":"object",
                "properties":{"action":{"type":"string","enum":["install","config"]}},
                "required":["action"],
                "additionalProperties":false
            }),
        ),
        tool(
            "feishu_setup_poll",
            "Poll a Feishu provider setup job without exposing raw credential output.",
            json!({
                "type":"object",
                "properties":{"job_id":{"type":"string"}},
                "required":["job_id"],
                "additionalProperties":false
            }),
        ),
        tool(
            "feishu_auth_begin",
            "Begin minimal Base+IM user authorization and return an HTTPS verification URL plus opaque flow id.",
            json!({
                "type":"object","properties":{},"additionalProperties":false
            }),
        ),
        tool(
            "feishu_auth_complete",
            "Complete a previously started Feishu authorization using an opaque flow id.",
            json!({
                "type":"object",
                "properties":{"flow_id":{"type":"string"}},
                "required":["flow_id"],
                "additionalProperties":false
            }),
        ),
    ]
}

pub fn parse_feishu_setup_start(arguments: &Value) -> Result<String, String> {
    let args = arguments
        .as_object()
        .ok_or_else(|| "Feishu setup arguments must be an object".to_string())?;
    reject_unknown(args, &["action"])?;
    let action = required_string(args, "action")?;
    if !matches!(action.as_str(), "install" | "config") {
        return Err("Feishu setup action must be install or config".into());
    }
    Ok(action)
}

pub fn parse_feishu_setup_poll(arguments: &Value) -> Result<String, String> {
    let args = arguments
        .as_object()
        .ok_or_else(|| "Feishu setup arguments must be an object".to_string())?;
    reject_unknown(args, &["job_id"])?;
    required_string(args, "job_id")
}

pub fn parse_feishu_auth_begin(arguments: &Value) -> Result<(), String> {
    let args = arguments
        .as_object()
        .ok_or_else(|| "Feishu auth arguments must be an object".to_string())?;
    reject_unknown(args, &[])?;
    Ok(())
}

pub fn parse_feishu_auth_complete(arguments: &Value) -> Result<String, String> {
    let args = arguments
        .as_object()
        .ok_or_else(|| "Feishu auth arguments must be an object".to_string())?;
    reject_unknown(args, &["flow_id"])?;
    required_string(args, "flow_id")
}

pub fn feishu_tool_definitions() -> Vec<Value> {
    vec![
        tool("feishu_status", "Check Feishu CLI/auth readiness without returning credentials.", json!({
            "type":"object","properties":{},"additionalProperties":false
        })),
        tool("feishu_base_list_tables", "List tables in a Feishu Base.", json!({
            "type":"object","properties":{"base_token":{"type":"string"}},"required":["base_token"],"additionalProperties":false
        })),
        tool("feishu_base_list_fields", "List fields/schema for a Feishu Base table.", json!({
            "type":"object","properties":{"base_token":{"type":"string"},"table_id":{"type":"string"}},"required":["base_token","table_id"],"additionalProperties":false
        })),
        tool("feishu_base_list_records", "List records from a Feishu Base table.", json!({
            "type":"object","properties":{
                "base_token":{"type":"string"},"table_id":{"type":"string"},"view_id":{"type":"string"},
                "page_size":{"type":"integer","minimum":1,"maximum":500},"page_token":{"type":"string"}
            },"required":["base_token","table_id"],"additionalProperties":false
        })),
        tool("feishu_chat_search", "Search visible Feishu group chats.", json!({
            "type":"object","properties":{"query":{"type":"string"},"page_size":{"type":"integer","minimum":1,"maximum":50}},
            "required":["query"],"additionalProperties":false
        })),
        tool("feishu_message_list", "List recent messages in a Feishu chat.", json!({
            "type":"object","properties":{
                "chat_id":{"type":"string"},"page_size":{"type":"integer","minimum":1,"maximum":50},"page_token":{"type":"string"}
            },"required":["chat_id"],"additionalProperties":false
        })),
        tool("feishu_base_create_record", "Create one record after a mandatory field-schema preflight.", json!({
            "type":"object","properties":{"base_token":{"type":"string"},"table_id":{"type":"string"},"fields":{"type":"object"}},
            "required":["base_token","table_id","fields"],"additionalProperties":false
        })),
        tool("feishu_base_update_record", "Update one exact record after a mandatory field-schema preflight.", json!({
            "type":"object","properties":{
                "base_token":{"type":"string"},"table_id":{"type":"string"},"record_id":{"type":"string"},"fields":{"type":"object"}
            },"required":["base_token","table_id","record_id","fields"],"additionalProperties":false
        })),
        tool("feishu_message_send", "Send a plain-text Feishu message. Automated sends default to bot identity.", json!({
            "type":"object","properties":{
                "chat_id":{"type":"string"},"text":{"type":"string"},"identity":{"type":"string","enum":["user","bot"]}
            },"required":["chat_id","text"],"additionalProperties":false
        })),
        tool("feishu_message_reply", "Reply with plain text to one exact Feishu message. Automated replies default to bot identity.", json!({
            "type":"object","properties":{
                "message_id":{"type":"string"},"text":{"type":"string"},"identity":{"type":"string","enum":["user","bot"]}
            },"required":["message_id","text"],"additionalProperties":false
        })),
        tool("feishu_base_create", "Create a new Feishu Base (multidimensional table). Optional first table schema via table_name+fields. Does not start OAuth.", json!({
            "type":"object",
            "properties":{
                "name":{"type":"string"},
                "folder_token":{"type":"string"},
                "table_name":{"type":"string"},
                "fields":{"type":"array","items":{"type":"object"}},
                "time_zone":{"type":"string"}
            },
            "required":["name"],
            "additionalProperties":false
        })),
        tool("feishu_base_create_view", "Create a view in a Feishu Base table. view.type: grid, kanban, gallery, calendar, or gantt. Example kanban: name plus type kanban.", json!({
            "type":"object",
            "properties":{
                "base_token":{"type":"string"},
                "table_id":{"type":"string"},
                "view":{"type":"object","description":"Must include name; type defaults to grid. Use type=kanban for a board."}
            },
            "required":["base_token","table_id","view"],
            "additionalProperties":false
        })),
        tool("feishu_wiki_node_get", "Resolve a Feishu wiki/docx URL to obj_token. Use before docs fetch/update when you only have a wiki link.", json!({
            "type":"object","properties":{"node_token":{"type":"string"}},"required":["node_token"],"additionalProperties":false
        })),
        tool("feishu_docs_search", "Search Feishu docs and wiki by keyword. Does not start OAuth.", json!({
            "type":"object","properties":{"query":{"type":"string"},"page_size":{"type":"integer","minimum":1,"maximum":20}},"required":["query"],"additionalProperties":false
        })),
        tool("feishu_docs_fetch", "Read a Feishu cloud document. doc is a URL or token. Prefer markdown.", json!({
            "type":"object","properties":{"doc":{"type":"string"},"doc_format":{"type":"string","enum":["markdown","xml","im-markdown"]}},"required":["doc"],"additionalProperties":false
        })),
        tool("feishu_docs_update", "Edit a Feishu cloud document. Do not ask the user for extra authorization. command=overwrite is allowed by default for full-document rewrites. Use str_replace only for small exact edits from the latest fetch. A str_replace miss is invalid_argument, not UNAVAILABLE. Not for image upload.", json!({
            "type":"object",
            "properties":{
                "doc":{"type":"string"},
                "command":{"type":"string","enum":["str_replace","overwrite","append","block_move_after","block_delete"]},
                "pattern":{"type":"string"},
                "content":{"type":"string"},
                "doc_format":{"type":"string","enum":["markdown","xml"]},
                "block_id":{"type":"string"},
                "src_block_ids":{"type":"string"}
            },
            "required":["doc","command"],
            "additionalProperties":false
        })),
        tool("feishu_docs_media_insert", "Insert an image you created into a Feishu cloud doc. Do not ask permission. file must be an absolute path that already exists on this Mac. First write PNG/JPG/SVG into the Local Agent Bridge workspace; SVG is converted automatically. Then call this tool. Then feishu_docs_update command=block_move_after to place it. Chat-bubble Image Gen files, /mnt/data, and Downloads are invalid.", json!({
            "type":"object",
            "properties":{
                "doc":{"type":"string"},
                "file":{"type":"string"},
                "width":{"type":"integer"},
                "caption":{"type":"string"},
                "align":{"type":"string","enum":["left","center","right"]}
            },
            "required":["doc","file"],
            "additionalProperties":false
        })),
        tool("feishu_cli", "Escape hatch for already-logged-in lark-cli. Prefer named docs/IM/Base tools. Do not pass auth/login. High-risk-write requires yes=true.", json!({
            "type":"object",
            "properties":{
                "argv":{"type":"array","items":{"type":"string"},"description":"Arguments after lark-cli, e.g. [base, +base-create, --name, Project]"},
                "yes":{"type":"boolean","description":"Set true to pass --yes for high-risk-write"}
            },
            "required":["argv"],
            "additionalProperties":false
        })),
    ]
}


pub fn feishu_client_instructions() -> &'static str {
    "Feishu cloud docs are enabled. Do not ask the user for extra authorization. Read with feishu_docs_fetch first. Full rewrites: feishu_docs_update command=overwrite is allowed by default. Small edits: str_replace using exact current text from fetch; a miss is invalid_argument, not UNAVAILABLE. Images: write PNG/JPG/SVG to an absolute path in the Local Agent Bridge workspace, then feishu_docs_media_insert, then feishu_docs_update command=block_move_after. Chat-bubble images, /mnt/data, and Downloads are invalid. Do not use office_im for cloud docs. Do not start a second Feishu login."
}

pub fn merge_feishu_initialize_instructions(payload: &mut Value) {
    let Some(result) = payload.get_mut("result") else {
        return;
    };
    if !result.is_object() {
        return;
    }
    let existing = result
        .get("instructions")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    let extra = feishu_client_instructions();
    if existing.contains("feishu_docs_media_insert") && existing.contains("overwrite") {
        return;
    }
    let merged = if existing.is_empty() {
        extra.to_string()
    } else {
        format!("{existing}\n{extra}")
    };
    result["instructions"] = Value::String(merged);
}

fn tool(name: &str, description: &str, input_schema: Value) -> Value {
    json!({
        "name": name,
        "description": description,
        "inputSchema": input_schema,
    })
}

pub fn parse_feishu_operation(
    tool_name: &str,
    arguments: &Value,
    generated_idempotency_key: &str,
) -> Result<CollaborationOperation, String> {
    let args = arguments
        .as_object()
        .ok_or_else(|| "Feishu tool arguments must be an object".to_string())?;

    match tool_name {
        "feishu_status" => {
            reject_unknown(args, &[])?;
            Ok(CollaborationOperation::Status)
        }
        "feishu_base_list_tables" => {
            reject_unknown(args, &["base_token"])?;
            Ok(CollaborationOperation::BaseListTables {
                base_token: required_string(args, "base_token")?,
            })
        }
        "feishu_base_list_fields" => {
            reject_unknown(args, &["base_token", "table_id"])?;
            Ok(CollaborationOperation::BaseListFields {
                base_token: required_string(args, "base_token")?,
                table_id: required_string(args, "table_id")?,
            })
        }
        "feishu_base_list_records" => {
            reject_unknown(
                args,
                &["base_token", "table_id", "view_id", "page_size", "page_token"],
            )?;
            Ok(CollaborationOperation::BaseListRecords {
                base_token: required_string(args, "base_token")?,
                table_id: required_string(args, "table_id")?,
                view_id: optional_string(args, "view_id")?,
                page_size: optional_u16(args, "page_size")?,
                page_token: optional_string(args, "page_token")?,
            })
        }
        "feishu_chat_search" => {
            reject_unknown(args, &["query", "page_size"])?;
            Ok(CollaborationOperation::ChatSearch {
                query: required_string(args, "query")?,
                page_size: optional_u16(args, "page_size")?,
            })
        }
        "feishu_message_list" => {
            reject_unknown(args, &["chat_id", "page_size", "page_token"])?;
            Ok(CollaborationOperation::MessageList {
                chat_id: required_string(args, "chat_id")?,
                page_size: optional_u16(args, "page_size")?,
                page_token: optional_string(args, "page_token")?,
            })
        }
        "feishu_base_create" => {
            reject_unknown(args, &["name", "folder_token", "table_name", "fields", "time_zone"])?;
            let fields_json = match args.get("fields") {
                None | Some(Value::Null) => None,
                Some(value) if value.is_array() => Some(value.to_string()),
                Some(_) => return Err("fields must be an array of field objects".into()),
            };
            Ok(CollaborationOperation::BaseCreate {
                name: required_string(args, "name")?,
                folder_token: optional_string(args, "folder_token")?,
                table_name: optional_string(args, "table_name")?,
                fields_json,
                time_zone: optional_string(args, "time_zone")?,
            })
        }
        "feishu_base_create_view" => {
            reject_unknown(args, &["base_token", "table_id", "view", "name", "type"])?;
            let view = match args.get("view") {
                Some(Value::Object(_)) => args.get("view").cloned().unwrap(),
                Some(Value::String(raw)) => serde_json::from_str(raw)
                    .map_err(|_| "view must be an object or JSON object string".to_string())?,
                None | Some(Value::Null) => {
                    let mut obj = serde_json::Map::new();
                    if let Some(name) = args.get("name") {
                        obj.insert("name".into(), name.clone());
                    }
                    if let Some(kind) = args.get("type") {
                        obj.insert("type".into(), kind.clone());
                    }
                    Value::Object(obj)
                }
                Some(_) => return Err("view must be an object or JSON object string".into()),
            };
            if view
                .get("name")
                .and_then(Value::as_str)
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .is_none()
            {
                return Err("view.name must be a non-empty string".into());
            }
            if let Some(kind) = view.get("type").and_then(Value::as_str) {
                if !matches!(kind, "grid" | "kanban" | "gallery" | "calendar" | "gantt") {
                    return Err("view.type must be grid, kanban, gallery, calendar, or gantt".into());
                }
            }
            Ok(CollaborationOperation::BaseCreateView {
                base_token: required_string(args, "base_token")?,
                table_id: required_string(args, "table_id")?,
                view_json: view.to_string(),
            })
        }
        "feishu_base_create_record" => {
            reject_unknown(args, &["base_token", "table_id", "fields"])?;
            Ok(CollaborationOperation::BaseCreateRecord {
                base_token: required_string(args, "base_token")?,
                table_id: required_string(args, "table_id")?,
                fields_json: required_object_json(args, "fields")?,
            })
        }
        "feishu_base_update_record" => {
            reject_unknown(args, &["base_token", "table_id", "record_id", "fields"])?;
            Ok(CollaborationOperation::BaseUpdateRecord {
                base_token: required_string(args, "base_token")?,
                table_id: required_string(args, "table_id")?,
                record_id: required_string(args, "record_id")?,
                fields_json: required_object_json(args, "fields")?,
            })
        }
        "feishu_message_send" => {
            reject_unknown(args, &["chat_id", "text", "identity"])?;
            Ok(CollaborationOperation::MessageSend {
                chat_id: required_string(args, "chat_id")?,
                text: required_string(args, "text")?,
                identity: optional_identity(args)?,
                idempotency_key: validated_generated_key(generated_idempotency_key)?,
            })
        }
        "feishu_message_reply" => {
            reject_unknown(args, &["message_id", "text", "identity"])?;
            Ok(CollaborationOperation::MessageReply {
                message_id: required_string(args, "message_id")?,
                text: required_string(args, "text")?,
                identity: optional_identity(args)?,
                idempotency_key: validated_generated_key(generated_idempotency_key)?,
            })
        }
        "feishu_wiki_node_get" => {
            reject_unknown(args, &["node_token"])?;
            Ok(CollaborationOperation::WikiNodeGet {
                node_token: required_string(args, "node_token")?,
            })
        }
        "feishu_docs_search" => {
            reject_unknown(args, &["query", "page_size"])?;
            Ok(CollaborationOperation::DocsSearch {
                query: required_string(args, "query")?,
                page_size: optional_u16(args, "page_size")?,
            })
        }
        "feishu_docs_fetch" => {
            reject_unknown(args, &["doc", "doc_format"])?;
            Ok(CollaborationOperation::DocsFetch {
                doc: required_string(args, "doc")?,
                doc_format: optional_string(args, "doc_format")?,
            })
        }
        "feishu_docs_update" => {
            reject_unknown(args, &["doc", "command", "pattern", "content", "doc_format", "block_id", "src_block_ids"])?;
            Ok(CollaborationOperation::DocsUpdate {
                doc: required_string(args, "doc")?,
                command: required_string(args, "command")?,
                pattern: optional_string(args, "pattern")?,
                content: optional_string(args, "content")?,
                doc_format: optional_string(args, "doc_format")?,
                block_id: optional_string(args, "block_id")?,
                src_block_ids: optional_string(args, "src_block_ids")?,
            })
        }
        "feishu_docs_media_insert" => {
            reject_unknown(args, &["doc", "file", "width", "caption", "align"])?;
            Ok(CollaborationOperation::DocsMediaInsert {
                doc: required_string(args, "doc")?,
                file: required_string(args, "file")?,
                width: optional_u16(args, "width")?,
                caption: optional_string(args, "caption")?,
                align: optional_string(args, "align")?,
            })
        }
        _ => Err(format!("unsupported Feishu tool: {tool_name}")),
    }
}

fn reject_unknown(
    args: &serde_json::Map<String, Value>,
    allowed: &[&str],
) -> Result<(), String> {
    let allowed: HashSet<&str> = allowed.iter().copied().collect();
    for key in args.keys() {
        if !allowed.contains(key.as_str()) {
            return Err(format!("unexpected Feishu tool argument: {key}"));
        }
    }
    Ok(())
}

fn required_string(
    args: &serde_json::Map<String, Value>,
    name: &str,
) -> Result<String, String> {
    args.get(name)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .ok_or_else(|| format!("{name} must be a non-empty string"))
}

fn optional_string(
    args: &serde_json::Map<String, Value>,
    name: &str,
) -> Result<Option<String>, String> {
    match args.get(name) {
        None | Some(Value::Null) => Ok(None),
        Some(value) => value
            .as_str()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(|value| Some(value.to_string()))
            .ok_or_else(|| format!("{name} must be a non-empty string when present")),
    }
}

fn optional_u16(
    args: &serde_json::Map<String, Value>,
    name: &str,
) -> Result<Option<u16>, String> {
    match args.get(name) {
        None | Some(Value::Null) => Ok(None),
        Some(value) => {
            let raw = value
                .as_u64()
                .ok_or_else(|| format!("{name} must be a positive integer"))?;
            let converted = u16::try_from(raw)
                .map_err(|_| format!("{name} is out of range"))?;
            if converted == 0 {
                return Err(format!("{name} must be positive"));
            }
            Ok(Some(converted))
        }
    }
}

fn required_object_json(
    args: &serde_json::Map<String, Value>,
    name: &str,
) -> Result<String, String> {
    let value = args
        .get(name)
        .filter(|value| value.is_object())
        .ok_or_else(|| format!("{name} must be a JSON object"))?;
    serde_json::to_string(value).map_err(|error| format!("{name} serialization failed: {error}"))
}

fn optional_identity(args: &serde_json::Map<String, Value>) -> Result<Identity, String> {
    match args.get("identity").and_then(Value::as_str) {
        None | Some("bot") => Ok(Identity::Bot),
        Some("user") => Ok(Identity::User),
        Some(_) => Err("identity must be user or bot".into()),
    }
}

fn validated_generated_key(value: &str) -> Result<String, String> {
    let value = value.trim();
    if value.is_empty() || value.len() > 50 {
        return Err("generated idempotency key is invalid".into());
    }
    Ok(value.to_string())
}


pub fn validate_feishu_cli_argv(arguments: &Value) -> Result<(Vec<String>, bool), String> {
    let args = arguments
        .as_object()
        .ok_or_else(|| "feishu_cli arguments must be an object".to_string())?;
    let yes = args.get("yes").and_then(Value::as_bool).unwrap_or(false);
    let argv_value = args
        .get("argv")
        .ok_or_else(|| "feishu_cli requires argv: array of lark-cli arguments without the binary".to_string())?;
    let items = argv_value
        .as_array()
        .ok_or_else(|| "feishu_cli argv must be an array of strings".to_string())?;
    if items.is_empty() {
        return Err("feishu_cli argv must not be empty".into());
    }
    if items.len() > 40 {
        return Err("feishu_cli argv is limited to 40 items".into());
    }
    let mut argv = Vec::with_capacity(items.len() + 1);
    for item in items {
        let part = item
            .as_str()
            .ok_or_else(|| "feishu_cli argv items must be strings".to_string())?;
        if part.len() > 8000 {
            return Err("feishu_cli argv item is too long".into());
        }
        if part.is_empty() {
            return Err("feishu_cli argv item must not be empty".into());
        }
        argv.push(part.to_string());
    }
    let head = argv[0].as_str();
    if matches!(head, "auth" | "config" | "profile" | "update") {
        return Err("feishu_cli refuses auth/config/profile/update; reuse the already-logged-in CLI".into());
    }
    if argv.iter().any(|part| part == "login") {
        return Err("feishu_cli refuses login; do not start a second OAuth".into());
    }
    if yes && !argv.iter().any(|part| part == "--yes") {
        argv.push("--yes".into());
    }
    Ok((argv, yes))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn initialize_instructions_cover_docs_workflow() {
        let text = feishu_client_instructions().to_ascii_lowercase();
        for needle in [
            "feishu_docs_fetch",
            "overwrite",
            "feishu_docs_media_insert",
            "block_move_after",
            "do not ask",
        ] {
            assert!(text.contains(needle), "missing {needle}");
        }
    }

    #[test]
    fn merge_preserves_existing_instructions_and_is_idempotent() {
        let mut value = json!({"jsonrpc":"2.0","id":1,"result":{"instructions":"CatDesk local tools."}});
        merge_feishu_initialize_instructions(&mut value);
        merge_feishu_initialize_instructions(&mut value);
        let text = value["result"]["instructions"].as_str().unwrap();
        assert!(text.starts_with("CatDesk local tools."));
        assert_eq!(text.matches("feishu_docs_media_insert").count(), 1);
    }

    #[test]
    fn docs_tool_descriptions_do_not_wait_for_permission() {
        let tools = feishu_tool_definitions();
        let update = tools
            .iter()
            .find(|tool| tool["name"] == "feishu_docs_update")
            .unwrap();
        let media = tools
            .iter()
            .find(|tool| tool["name"] == "feishu_docs_media_insert")
            .unwrap();
        let update_text = update["description"].as_str().unwrap().to_ascii_lowercase();
        let media_text = media["description"].as_str().unwrap().to_ascii_lowercase();
        assert!(update_text.contains("do not ask"));
        assert!(update_text.contains("overwrite"));
        assert!(media_text.contains("do not ask"));
        assert!(media_text.contains("block_move_after"));
        assert!(media_text.contains("workspace"));
    }
}
