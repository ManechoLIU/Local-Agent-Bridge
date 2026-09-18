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
    ]
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
