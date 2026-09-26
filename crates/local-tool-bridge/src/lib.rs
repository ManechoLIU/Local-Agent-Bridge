use axum::{
    Router,
    body::{Body, Bytes},
    extract::State,
    http::{HeaderMap, Response, StatusCode, header},
    routing::post,
};
use bridge_security_core::{
    Capability, CapabilityAuthority, ExecutionContext, IssuedCapability, sign_request,
    sign_request_for_execution,
};
use collaboration_adapter::{LarkCliAdminSpec, LarkCliProvider};

mod collaboration_tools;
use reqwest::Url;
use sha2::{Digest, Sha256};
use std::{
    collections::{HashMap, HashSet},
    fs::{File, OpenOptions},
    io::Write,
    net::IpAddr,
    os::fd::AsRawFd,
    os::unix::fs::{FileTypeExt, MetadataExt, OpenOptionsExt, PermissionsExt},
    path::{Path, PathBuf},
    sync::Arc,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use subtle::ConstantTimeEq;
use tokio::{
    io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader as TokioBufReader},
    sync::Mutex,
};
use uuid::Uuid;

const INGRESS_KEY_HEADER: &str = "x-lab-ingress-key";
const PRODUCTION_CONNECTOR_ID: &str = "local-agent-bridge-production";
const PRODUCTION_CONTRACT_VERSION: &str = "lab-production-connector-v1";
const PRODUCTION_WORKSPACE: &str = "/Users/echoman/Documents/ChatGPT/Local-Agent-Bridge";
const PRODUCTION_TUNNEL_PROFILE: &str = "local-agent-bridge-production";
const RUN_COMMAND_DIGEST_PROFILE: &str = "lab_run_command_digest_v1";
const RUNTIME_HOOK_PROTOCOL: &str = "runtime_host_tool_hook_v1";
const RUNTIME_TOOL_INTENT_PROVENANCE: &str = "runtime_host_tool_intent_v1";
const RUNTIME_BACKEND_RESPONSE_PROVENANCE: &str = "runtime_host_backend_response_v1";
const RECEIPT_JOURNAL_FILE: &str = "tool-receipt-journal.json";
const MAX_PROTECTED_REQUEST_BYTES: usize = 64 * 1024;
const MAX_PROTECTED_RESPONSE_BYTES: usize = 64 * 1024;
const MAX_PROTECTED_JSON_DEPTH: usize = 32;
pub const PRODUCTION_CAPABILITY_TTL_MS: u64 = 86_400_000;
pub const PRODUCTION_CAPABILITY_RENEW_BEFORE_MS: u64 = 3_600_000;
const FORWARDED_HEADERS: &[&str] = &[
    "content-type",
    "accept",
    "mcp-protocol-version",
    "mcp-method",
    "mcp-name",
    "mcp-session-id",
];

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ProductionReleaseFence {
    pub contract_version: String,
    pub release_revision: String,
    pub endpoint_generation: u64,
    pub workspace: String,
    pub tunnel_profile_name: String,
    pub capability_ttl_ms: u64,
    pub capability_renew_before_ms: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ToolReceiptState {
    Issued,
    HostValidatedInFlight,
    RuntimeAllowed,
    DispatchIntent,
    BackendResponse,
    TerminalPending,
    Terminal,
    ResultUnknownAfterDispatch,
}

impl ToolReceiptState {
    fn as_str(self) -> &'static str {
        match self {
            Self::Issued => "ISSUED",
            Self::HostValidatedInFlight => "HOST_VALIDATED_IN_FLIGHT",
            Self::RuntimeAllowed => "RUNTIME_ALLOWED",
            Self::DispatchIntent => "DISPATCH_INTENT",
            Self::BackendResponse => "BACKEND_RESPONSE",
            Self::TerminalPending => "TERMINAL_PENDING",
            Self::Terminal => "TERMINAL",
            Self::ResultUnknownAfterDispatch => "RESULT_UNKNOWN_AFTER_DISPATCH",
        }
    }

    fn parse(value: &str) -> Result<Self, String> {
        match value {
            "ISSUED" => Ok(Self::Issued),
            "HOST_VALIDATED_IN_FLIGHT" => Ok(Self::HostValidatedInFlight),
            "RUNTIME_ALLOWED" => Ok(Self::RuntimeAllowed),
            "DISPATCH_INTENT" => Ok(Self::DispatchIntent),
            "BACKEND_RESPONSE" => Ok(Self::BackendResponse),
            "TERMINAL_PENDING" => Ok(Self::TerminalPending),
            "TERMINAL" => Ok(Self::Terminal),
            "RESULT_UNKNOWN_AFTER_DISPATCH" => Ok(Self::ResultUnknownAfterDispatch),
            _ => Err("tool receipt journal state is invalid".into()),
        }
    }

    fn can_transition_to(self, next: Self) -> bool {
        matches!(
            (self, next),
            (Self::Issued, Self::HostValidatedInFlight)
                | (Self::HostValidatedInFlight, Self::RuntimeAllowed)
                | (Self::RuntimeAllowed, Self::DispatchIntent)
                | (Self::DispatchIntent, Self::BackendResponse)
                | (Self::DispatchIntent, Self::ResultUnknownAfterDispatch)
                | (Self::BackendResponse, Self::TerminalPending)
                | (Self::BackendResponse, Self::ResultUnknownAfterDispatch)
                | (Self::TerminalPending, Self::Terminal)
                | (Self::TerminalPending, Self::ResultUnknownAfterDispatch)
                | (Self::Terminal, Self::ResultUnknownAfterDispatch)
                | (
                    Self::ResultUnknownAfterDispatch,
                    Self::ResultUnknownAfterDispatch
                )
        )
    }
}

#[derive(Clone, Debug)]
struct ToolReceiptRecord {
    bridge_call_id: String,
    bridge_challenge_nonce: String,
    transport_binding_id: String,
    capability_id: String,
    capability: serde_json::Value,
    outer_request_sha256: String,
    forwarded_request_sha256: String,
    normalized_request_sha256: String,
    frozen_forwarded: Vec<u8>,
    state: ToolReceiptState,
    host_tool_execution_id: Option<String>,
    retry_outer_request_sha256: Option<String>,
    pre_receipt: Option<serde_json::Value>,
    backend_http_status: Option<u16>,
    backend_response_sha256: Option<String>,
}

impl ToolReceiptRecord {
    fn to_json(&self) -> serde_json::Value {
        serde_json::json!({
            "bridge_call_id": self.bridge_call_id,
            "bridge_challenge_nonce": self.bridge_challenge_nonce,
            "transport_binding_id": self.transport_binding_id,
            "capability_id": self.capability_id,
            "capability": self.capability,
            "outer_request_sha256": self.outer_request_sha256,
            "forwarded_request_sha256": self.forwarded_request_sha256,
            "normalized_request_sha256": self.normalized_request_sha256,
            "frozen_forwarded": String::from_utf8_lossy(&self.frozen_forwarded),
            "state": self.state.as_str(),
            "host_tool_execution_id": self.host_tool_execution_id,
            "retry_outer_request_sha256": self.retry_outer_request_sha256,
            "pre_receipt": self.pre_receipt,
            "backend_http_status": self.backend_http_status,
            "backend_response_sha256": self.backend_response_sha256,
        })
    }

    fn from_json(value: &serde_json::Value) -> Result<Self, String> {
        let text = |name: &str| {
            value
                .get(name)
                .and_then(serde_json::Value::as_str)
                .filter(|text| !text.is_empty())
                .map(str::to_string)
                .ok_or_else(|| format!("tool receipt journal {name} is invalid"))
        };
        let optional_text = |name: &str| {
            value
                .get(name)
                .and_then(serde_json::Value::as_str)
                .map(str::to_string)
        };
        Ok(Self {
            bridge_call_id: text("bridge_call_id")?,
            bridge_challenge_nonce: text("bridge_challenge_nonce")?,
            transport_binding_id: text("transport_binding_id")?,
            capability_id: text("capability_id")?,
            capability: value
                .get("capability")
                .cloned()
                .filter(serde_json::Value::is_object)
                .ok_or_else(|| "tool receipt journal capability is invalid".to_string())?,
            outer_request_sha256: digest_text(
                &text("outer_request_sha256")?,
                "outer_request_sha256",
            )?,
            forwarded_request_sha256: digest_text(
                &text("forwarded_request_sha256")?,
                "forwarded_request_sha256",
            )?,
            normalized_request_sha256: digest_text(
                &text("normalized_request_sha256")?,
                "normalized_request_sha256",
            )?,
            frozen_forwarded: text("frozen_forwarded")?.into_bytes(),
            state: ToolReceiptState::parse(&text("state")?)?,
            host_tool_execution_id: optional_text("host_tool_execution_id"),
            retry_outer_request_sha256: optional_text("retry_outer_request_sha256"),
            pre_receipt: value
                .get("pre_receipt")
                .cloned()
                .filter(|value| !value.is_null()),
            backend_http_status: value
                .get("backend_http_status")
                .and_then(serde_json::Value::as_u64)
                .map(|value| value as u16),
            backend_response_sha256: optional_text("backend_response_sha256"),
        })
    }
}

#[derive(Debug)]
pub struct ToolReceiptJournal {
    path: PathBuf,
    records: HashMap<String, ToolReceiptRecord>,
}

impl ToolReceiptJournal {
    pub fn load(path: impl Into<PathBuf>) -> Result<Self, String> {
        let path = path.into();
        let mut journal = Self {
            path,
            records: HashMap::new(),
        };
        journal.reload()?;
        Ok(journal)
    }

    pub fn state(&self, bridge_call_id: &str) -> Option<ToolReceiptState> {
        self.records
            .values()
            .find(|record| record.bridge_call_id == bridge_call_id)
            .map(|record| record.state)
    }

    fn reload(&mut self) -> Result<(), String> {
        if !self.path.exists() {
            self.records.clear();
            return Ok(());
        }
        let metadata = std::fs::symlink_metadata(&self.path)
            .map_err(|error| format!("tool receipt journal inspect failed: {error}"))?;
        if metadata.file_type().is_symlink()
            || !metadata.is_file()
            || metadata.uid() != current_euid()
            || metadata.permissions().mode() & 0o077 != 0
        {
            return Err("tool receipt journal must be an owned private regular file".into());
        }
        let bytes = std::fs::read(&self.path)
            .map_err(|error| format!("tool receipt journal read failed: {error}"))?;
        let root: serde_json::Value = serde_json::from_slice(&bytes)
            .map_err(|error| format!("tool receipt journal parse failed: {error}"))?;
        if root
            .get("schema_version")
            .and_then(serde_json::Value::as_u64)
            != Some(1)
        {
            return Err("tool receipt journal schema is unsupported".into());
        }
        let records = root
            .get("records")
            .and_then(serde_json::Value::as_array)
            .ok_or_else(|| "tool receipt journal records are invalid".to_string())?;
        let mut loaded = HashMap::new();
        let mut bridge_call_ids = HashSet::new();
        for value in records {
            let record = ToolReceiptRecord::from_json(value)?;
            if !bridge_call_ids.insert(record.bridge_call_id.clone()) {
                return Err("tool receipt journal has duplicate bridge call ids".into());
            }
            if loaded
                .insert(record.capability_id.clone(), record)
                .is_some()
            {
                return Err("tool receipt journal has duplicate capabilities".into());
            }
        }
        self.records = loaded;
        Ok(())
    }

    fn persist(&self) -> Result<(), String> {
        let mut records = self
            .records
            .values()
            .map(ToolReceiptRecord::to_json)
            .collect::<Vec<_>>();
        records.sort_by(|left, right| {
            left["capability_id"]
                .as_str()
                .cmp(&right["capability_id"].as_str())
        });
        let bytes = serde_json::to_vec(&serde_json::json!({"schema_version":1,"records":records}))
            .map_err(|error| format!("tool receipt journal serialization failed: {error}"))?;
        write_private_atomic(&self.path, &bytes, "tool receipt journal")
    }

    fn insert(&mut self, record: ToolReceiptRecord) -> Result<(), String> {
        self.mutate_locked(move |records| {
            if records.contains_key(&record.capability_id) {
                return Err("Host returned a duplicate capability id".into());
            }
            if records
                .values()
                .any(|current| current.bridge_call_id == record.bridge_call_id)
            {
                return Err("Bridge generated a duplicate bridge call id".into());
            }
            if record.state != ToolReceiptState::Issued {
                return Err("new tool receipt record must start at ISSUED".into());
            }
            records.insert(record.capability_id.clone(), record);
            Ok(())
        })
    }

    fn update<F>(&mut self, capability_id: &str, mutation: F) -> Result<(), String>
    where
        F: FnOnce(&mut ToolReceiptRecord) -> Result<(), String>,
    {
        self.mutate_locked(|records| {
            let record = records
                .get_mut(capability_id)
                .ok_or_else(|| "unknown Host invocation capability".to_string())?;
            let old_state = record.state;
            let old_capability_id = record.capability_id.clone();
            let old_bridge_call_id = record.bridge_call_id.clone();
            mutation(record)?;
            if record.capability_id != old_capability_id
                || record.bridge_call_id != old_bridge_call_id
            {
                return Err("tool receipt journal identity fields are immutable".into());
            }
            if !old_state.can_transition_to(record.state) {
                return Err(format!(
                    "illegal tool receipt journal transition {} -> {}",
                    old_state.as_str(),
                    record.state.as_str()
                ));
            }
            Ok(())
        })
    }

    fn record(&mut self, capability_id: &str) -> Result<Option<ToolReceiptRecord>, String> {
        let _lock = lock_private_journal(&self.path)?;
        self.reload()?;
        Ok(self.records.get(capability_id).cloned())
    }

    fn mutate_locked<T, F>(&mut self, mutation: F) -> Result<T, String>
    where
        F: FnOnce(&mut HashMap<String, ToolReceiptRecord>) -> Result<T, String>,
    {
        let _lock = lock_private_journal(&self.path)?;
        self.reload()?;
        let original = self.records.clone();
        let result = match mutation(&mut self.records) {
            Ok(result) => result,
            Err(error) => {
                self.records = original;
                return Err(error);
            }
        };
        if let Err(error) = self.persist() {
            self.records = original;
            return Err(error);
        }
        Ok(result)
    }
}

struct JournalFileLock(File);

impl Drop for JournalFileLock {
    fn drop(&mut self) {
        unsafe {
            libc::flock(self.0.as_raw_fd(), libc::LOCK_UN);
        }
    }
}

fn lock_private_journal(path: &Path) -> Result<JournalFileLock, String> {
    let parent = path
        .parent()
        .ok_or_else(|| "tool receipt journal path has no parent".to_string())?;
    let parent_metadata = std::fs::symlink_metadata(parent)
        .map_err(|error| format!("tool receipt journal parent inspect failed: {error}"))?;
    if parent_metadata.file_type().is_symlink()
        || !parent_metadata.is_dir()
        || parent_metadata.uid() != current_euid()
        || parent_metadata.permissions().mode() & 0o077 != 0
    {
        return Err("tool receipt journal parent must be an owned private directory".into());
    }
    let lock_path = parent.join(format!(
        "{}.lock",
        path.file_name()
            .and_then(|name| name.to_str())
            .unwrap_or(RECEIPT_JOURNAL_FILE)
    ));
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(&lock_path)
        .map_err(|error| format!("tool receipt journal lock open failed: {error}"))?;
    let metadata = file
        .metadata()
        .map_err(|error| format!("tool receipt journal lock inspect failed: {error}"))?;
    if !metadata.is_file()
        || metadata.uid() != current_euid()
        || metadata.permissions().mode() & 0o777 != 0o600
    {
        return Err("tool receipt journal lock must be an owned 0600 regular file".into());
    }
    if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX) } != 0 {
        return Err(format!(
            "tool receipt journal lock acquisition failed: {}",
            std::io::Error::last_os_error()
        ));
    }
    Ok(JournalFileLock(file))
}

#[derive(Clone)]
struct ToolReceiptChain {
    host_socket: PathBuf,
    runtime_socket: Option<PathBuf>,
    production_fence: ProductionReleaseFence,
    journal: Arc<Mutex<ToolReceiptJournal>>,
}

#[derive(Clone, Debug)]
pub struct BufferedBackendResponse {
    pub status: StatusCode,
    pub content_type: Option<String>,
    pub bytes: Bytes,
}

pub fn validate_production_release_manifest(
    manifest_json: &str,
    observed_contract_version: &str,
    observed_release_revision: &str,
    observed_endpoint_generation: u64,
    observed_workspace: &str,
    observed_tunnel_profile_name: &str,
) -> Result<ProductionReleaseFence, String> {
    let manifest: serde_json::Value = serde_json::from_str(manifest_json)
        .map_err(|error| format!("production release manifest is not valid JSON: {error}"))?;
    let manifest = manifest
        .as_object()
        .ok_or_else(|| "production release manifest must be a JSON object".to_string())?;

    if manifest
        .get("connector_id")
        .and_then(serde_json::Value::as_str)
        != Some(PRODUCTION_CONNECTOR_ID)
    {
        return Err("production release manifest connector identity mismatch".into());
    }
    if manifest
        .get("fail_closed")
        .and_then(serde_json::Value::as_bool)
        != Some(true)
    {
        return Err("production release manifest must be fail-closed".into());
    }
    if observed_contract_version != PRODUCTION_CONTRACT_VERSION {
        return Err("observed production contract version is stale or noncanonical".into());
    }
    validate_full_git_sha(observed_release_revision)?;
    if observed_endpoint_generation == 0 {
        return Err("observed production endpoint generation must be a positive integer".into());
    }
    if observed_workspace != PRODUCTION_WORKSPACE {
        return Err("observed production workspace is noncanonical".into());
    }
    if observed_tunnel_profile_name != PRODUCTION_TUNNEL_PROFILE {
        return Err("observed production tunnel profile is noncanonical".into());
    }

    require_manifest_str(manifest, "contract_version", observed_contract_version)?;
    require_manifest_str(manifest, "release_revision", observed_release_revision)?;
    require_manifest_u64(
        manifest,
        "endpoint_generation",
        observed_endpoint_generation,
    )?;
    require_manifest_str(manifest, "workspace", observed_workspace)?;

    let tunnel = manifest
        .get("tunnel")
        .and_then(serde_json::Value::as_object)
        .ok_or_else(|| "production release manifest tunnel subsection is required".to_string())?;
    require_manifest_str(tunnel, "profile_name", observed_tunnel_profile_name)?;

    let bridge = manifest
        .get("bridge")
        .and_then(serde_json::Value::as_object)
        .ok_or_else(|| "production release manifest Bridge subsection is required".to_string())?;
    require_manifest_str(bridge, "contract_version", observed_contract_version)?;
    require_manifest_str(bridge, "release_revision", observed_release_revision)?;
    require_manifest_u64(bridge, "endpoint_generation", observed_endpoint_generation)?;
    require_manifest_u64(bridge, "capability_ttl_ms", PRODUCTION_CAPABILITY_TTL_MS)?;
    require_manifest_u64(
        bridge,
        "capability_renew_before_ms",
        PRODUCTION_CAPABILITY_RENEW_BEFORE_MS,
    )?;

    Ok(ProductionReleaseFence {
        contract_version: observed_contract_version.to_string(),
        release_revision: observed_release_revision.to_string(),
        endpoint_generation: observed_endpoint_generation,
        workspace: observed_workspace.to_string(),
        tunnel_profile_name: observed_tunnel_profile_name.to_string(),
        capability_ttl_ms: PRODUCTION_CAPABILITY_TTL_MS,
        capability_renew_before_ms: PRODUCTION_CAPABILITY_RENEW_BEFORE_MS,
    })
}

fn validate_full_git_sha(value: &str) -> Result<(), String> {
    if value.len() != 40
        || !value
            .as_bytes()
            .iter()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(byte))
    {
        return Err("production release revision must be a full 40-character Git SHA".into());
    }
    Ok(())
}

fn require_manifest_str(
    manifest: &serde_json::Map<String, serde_json::Value>,
    key: &str,
    expected: &str,
) -> Result<(), String> {
    match manifest.get(key).and_then(serde_json::Value::as_str) {
        Some(value) if value == expected => Ok(()),
        _ => Err(format!("production release manifest {key} mismatch")),
    }
}

fn require_manifest_u64(
    manifest: &serde_json::Map<String, serde_json::Value>,
    key: &str,
    expected: u64,
) -> Result<(), String> {
    match manifest.get(key).and_then(serde_json::Value::as_u64) {
        Some(value) if value > 0 && value == expected => Ok(()),
        _ => Err(format!("production release manifest {key} mismatch")),
    }
}

#[derive(Clone, Debug, Default)]
pub struct ToolPolicy {
    tools: HashMap<String, Capability>,
}

impl ToolPolicy {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn insert(mut self, tool_name: impl Into<String>, capability: Capability) -> Self {
        self.tools.insert(tool_name.into(), capability);
        self
    }

    pub fn catdesk_default() -> Self {
        Self::new()
            .insert("catdesk_instruction", Capability::Discovery)
            .insert("discover_current_entry", Capability::HostAttestation)
            .insert("read", Capability::FileRead)
            .insert("search", Capability::FileRead)
            .insert("write", Capability::FileWrite)
            .insert("edit", Capability::FileWrite)
            .insert("delete", Capability::FileWrite)
            .insert("run_command", Capability::Shell)
            .insert("start_command", Capability::ProcessJobs)
            .insert("poll_command", Capability::ProcessJobs)
            .insert("cancel_command", Capability::ProcessJobs)
            .insert("computer_session_open", Capability::Computer)
            .insert("computer_session_close", Capability::Computer)
            .insert("computer_permissions", Capability::Computer)
            .insert("computer_list_apps", Capability::Computer)
            .insert("computer_list_windows", Capability::Computer)
            .insert("computer_active_window", Capability::Computer)
            .insert("computer_activate_app", Capability::Computer)
            .insert("computer_screenshot", Capability::Computer)
            .insert("computer_find", Capability::Computer)
            .insert("computer_click", Capability::Computer)
            .insert("computer_click_at", Capability::Computer)
            .insert("computer_list_displays", Capability::Computer)
            .insert("computer_focus_window", Capability::Computer)
            .insert("computer_move_at", Capability::Computer)
            .insert("computer_drag_at", Capability::Computer)
            .insert("computer_paste", Capability::Computer)
            .insert("computer_set_value", Capability::Computer)
            .insert("computer_type", Capability::Computer)
            .insert("computer_key", Capability::Computer)
            .insert("computer_scroll", Capability::Computer)
            .insert("feishu_status", Capability::CollaborationRead)
            .insert("feishu_base_list_tables", Capability::CollaborationRead)
            .insert("feishu_base_list_fields", Capability::CollaborationRead)
            .insert("feishu_base_list_records", Capability::CollaborationRead)
            .insert("feishu_chat_search", Capability::CollaborationRead)
            .insert("feishu_message_list", Capability::CollaborationRead)
            .insert("feishu_base_create", Capability::CollaborationWrite)
            .insert("feishu_base_create_view", Capability::CollaborationWrite)
            .insert("feishu_base_create_record", Capability::CollaborationWrite)
            .insert("feishu_base_update_record", Capability::CollaborationWrite)
            .insert("feishu_message_send", Capability::CollaborationWrite)
            .insert("feishu_message_reply", Capability::CollaborationWrite)
            .insert("feishu_wiki_node_get", Capability::CollaborationRead)
            .insert("feishu_docs_search", Capability::CollaborationRead)
            .insert("feishu_docs_fetch", Capability::CollaborationRead)
            .insert("feishu_docs_update", Capability::CollaborationWrite)
            .insert("feishu_docs_media_insert", Capability::CollaborationWrite)
            .insert("feishu_cli", Capability::CollaborationWrite)
            .insert("feishu_setup_start", Capability::ProviderAdmin)
            .insert("feishu_setup_poll", Capability::ProviderAdmin)
            .insert("feishu_auth_begin", Capability::ProviderAdmin)
            .insert("feishu_auth_complete", Capability::ProviderAdmin)
    }

    pub fn required_capability(&self, method: &str, tool_name: &str) -> Result<Capability, String> {
        match method {
            "server/discover"
            | "initialize"
            | "tools/list"
            | "resources/list"
            | "resources/templates/list"
            | "ping" => Ok(Capability::Discovery),
            "resources/read" => Ok(Capability::FileRead),
            method if method.starts_with("notifications/") => Ok(Capability::Discovery),
            "tools/call" => self.tools.get(tool_name).cloned().ok_or_else(|| {
                format!("tool is not authorized by the current Bridge capability map: {tool_name}")
            }),
            _ => Err(format!(
                "MCP method is not authorized by the current Bridge capability map: {method}"
            )),
        }
    }
}

#[derive(Clone, Debug)]
enum LocalIngressAuth {
    SharedKey([u8; 32]),
    TrustedUnixSocket,
}

#[derive(Clone)]
pub struct BridgeIngress {
    inner: Arc<BridgeIngressInner>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct CapabilityRenewal {
    pub ttl_ms: u64,
    pub renew_before_ms: u64,
}

impl CapabilityRenewal {
    pub fn new(ttl_ms: u64, renew_before_ms: u64) -> Result<Self, String> {
        if ttl_ms == 0 {
            return Err("capability renewal ttl must be positive".into());
        }
        if renew_before_ms == 0 || renew_before_ms >= ttl_ms {
            return Err("capability renewal threshold must be positive and less than ttl".into());
        }
        Ok(Self {
            ttl_ms,
            renew_before_ms,
        })
    }
}

#[derive(Clone)]
struct BackendTarget {
    url: Url,
    key: String,
}

struct BridgeIngressInner {
    authority: Mutex<CapabilityAuthority>,
    context: ExecutionContext,
    renewal: Option<CapabilityRenewal>,
    local_auth: LocalIngressAuth,
    default_backend: BackendTarget,
    host_backend: Option<BackendTarget>,
    host_release_fence: Option<ProductionReleaseFence>,
    computer_backend: Option<BackendTarget>,
    process_backend: Option<BackendTarget>,
    collaboration: Option<collaboration_tools::CollaborationRuntime>,
    feishu_admin: Option<collaboration_tools::FeishuAdminRuntime>,
    client: reqwest::Client,
    policy: ToolPolicy,
    state_dir: Option<PathBuf>,
    tool_receipts: Option<ToolReceiptChain>,
}

impl BridgeIngress {
    pub fn new(
        authority: CapabilityAuthority,
        issued: IssuedCapability,
        context: ExecutionContext,
        ingress_key: &[u8],
        backend_url: &str,
        backend_key: &str,
        state_dir: impl Into<PathBuf>,
    ) -> Result<Self, String> {
        Self::new_with_policy(
            authority,
            issued,
            context,
            ingress_key,
            backend_url,
            backend_key,
            ToolPolicy::catdesk_default(),
            state_dir,
        )
    }

    pub fn new_with_policy(
        authority: CapabilityAuthority,
        issued: IssuedCapability,
        context: ExecutionContext,
        ingress_key: &[u8],
        backend_url: &str,
        backend_key: &str,
        policy: ToolPolicy,
        state_dir: impl Into<PathBuf>,
    ) -> Result<Self, String> {
        if ingress_key.len() < 32 {
            return Err("local ingress key must be at least 32 bytes".into());
        }
        if issued.capability_id != context.capability_id {
            return Err("issued capability does not match ingress execution context".into());
        }
        let backend_url = validate_backend_url(backend_url)?;
        validate_backend_key(backend_key)?;
        let mut ingress_key_sha256 = [0_u8; 32];
        ingress_key_sha256.copy_from_slice(&Sha256::digest(ingress_key));
        Ok(Self {
            inner: Arc::new(BridgeIngressInner {
                authority: Mutex::new(authority),
                context,
                renewal: None,
                local_auth: LocalIngressAuth::SharedKey(ingress_key_sha256),
                default_backend: BackendTarget {
                    url: backend_url,
                    key: backend_key.to_string(),
                },
                host_backend: None,
                host_release_fence: None,
                computer_backend: None,
                process_backend: None,
                collaboration: None,
                feishu_admin: None,
                client: backend_client()?,
                policy,
                state_dir: Some(state_dir.into()),
                tool_receipts: None,
            }),
        })
    }

    pub fn new_trusted_unix_socket(
        authority: CapabilityAuthority,
        issued: IssuedCapability,
        context: ExecutionContext,
        backend_url: &str,
        backend_key: &str,
        policy: ToolPolicy,
        state_dir: impl Into<PathBuf>,
    ) -> Result<Self, String> {
        if issued.capability_id != context.capability_id {
            return Err("issued capability does not match ingress execution context".into());
        }
        let backend_url = validate_backend_url(backend_url)?;
        validate_backend_key(backend_key)?;
        Ok(Self {
            inner: Arc::new(BridgeIngressInner {
                authority: Mutex::new(authority),
                context,
                renewal: None,
                local_auth: LocalIngressAuth::TrustedUnixSocket,
                default_backend: BackendTarget {
                    url: backend_url,
                    key: backend_key.to_string(),
                },
                host_backend: None,
                host_release_fence: None,
                computer_backend: None,
                process_backend: None,
                collaboration: None,
                feishu_admin: None,
                client: backend_client()?,
                policy,
                state_dir: Some(state_dir.into()),
                tool_receipts: None,
            }),
        })
    }

    /// Enables bounded renewal for this ingress. It is opt-in so non-production
    /// callers retain the prior fixed-lease behavior.
    pub fn with_capability_renewal(
        mut self,
        ttl_ms: u64,
        renew_before_ms: u64,
    ) -> Result<Self, String> {
        let renewal = CapabilityRenewal::new(ttl_ms, renew_before_ms)?;
        let inner = Arc::get_mut(&mut self.inner).ok_or_else(|| {
            "capability renewal must be configured before BridgeIngress is cloned".to_string()
        })?;
        inner.renewal = Some(renewal);
        Ok(self)
    }

    pub fn with_host_backend(
        mut self,
        backend_url: &str,
        backend_key: &str,
        production_fence: ProductionReleaseFence,
    ) -> Result<Self, String> {
        let backend_url = validate_backend_url(backend_url)?;
        validate_backend_key(backend_key)?;
        let inner = Arc::get_mut(&mut self.inner).ok_or_else(|| {
            "Host backend must be configured before BridgeIngress is cloned".to_string()
        })?;
        inner.host_backend = Some(BackendTarget {
            url: backend_url,
            key: backend_key.to_string(),
        });
        inner.host_release_fence = Some(production_fence);
        Ok(self)
    }

    pub fn with_computer_backend(
        mut self,
        backend_url: &str,
        backend_key: &str,
    ) -> Result<Self, String> {
        let backend_url = validate_backend_url(backend_url)?;
        validate_backend_key(backend_key)?;
        let inner = Arc::get_mut(&mut self.inner).ok_or_else(|| {
            "computer backend must be configured before BridgeIngress is cloned".to_string()
        })?;
        inner.computer_backend = Some(BackendTarget {
            url: backend_url,
            key: backend_key.to_string(),
        });
        Ok(self)
    }

    pub fn with_process_backend(
        mut self,
        backend_url: &str,
        backend_key: &str,
    ) -> Result<Self, String> {
        let backend_url = validate_backend_url(backend_url)?;
        validate_backend_key(backend_key)?;
        let inner = Arc::get_mut(&mut self.inner).ok_or_else(|| {
            "process backend must be configured before BridgeIngress is cloned".to_string()
        })?;
        inner.process_backend = Some(BackendTarget {
            url: backend_url,
            key: backend_key.to_string(),
        });
        Ok(self)
    }

    pub fn with_feishu_provider(
        mut self,
        provider: LarkCliProvider,
        timeout: Duration,
        output_limit: usize,
    ) -> Result<Self, String> {
        let inner = Arc::get_mut(&mut self.inner).ok_or_else(|| {
            "Feishu provider must be configured before BridgeIngress is cloned".to_string()
        })?;
        inner.collaboration = Some(collaboration_tools::CollaborationRuntime::new(
            provider,
            timeout,
            output_limit,
        )?);
        Ok(self)
    }

    pub fn with_feishu_admin(
        mut self,
        spec: LarkCliAdminSpec,
        timeout: Duration,
        output_limit: usize,
    ) -> Result<Self, String> {
        let inner = Arc::get_mut(&mut self.inner).ok_or_else(|| {
            "Feishu admin runtime must be configured before BridgeIngress is cloned".to_string()
        })?;
        inner.feishu_admin = Some(collaboration_tools::FeishuAdminRuntime::new(
            spec,
            timeout,
            output_limit,
        )?);
        Ok(self)
    }

    pub fn with_tool_receipt_chain(
        mut self,
        host_socket: impl AsRef<Path>,
        runtime_socket: Option<&Path>,
        production_fence: ProductionReleaseFence,
    ) -> Result<Self, String> {
        let inner = Arc::get_mut(&mut self.inner).ok_or_else(|| {
            "tool receipt chain must be configured before BridgeIngress is cloned".to_string()
        })?;
        if production_fence.workspace != PRODUCTION_WORKSPACE
            || production_fence.contract_version != PRODUCTION_CONTRACT_VERSION
            || production_fence.endpoint_generation == 0
        {
            return Err("tool receipt chain requires the canonical production fence".into());
        }
        let state_dir = inner
            .state_dir
            .as_ref()
            .ok_or_else(|| "tool receipt chain requires durable Bridge state".to_string())?;
        let journal = ToolReceiptJournal::load(state_dir.join(RECEIPT_JOURNAL_FILE))?;
        inner.tool_receipts = Some(ToolReceiptChain {
            host_socket: host_socket.as_ref().to_path_buf(),
            runtime_socket: runtime_socket.map(Path::to_path_buf),
            production_fence,
            journal: Arc::new(Mutex::new(journal)),
        });
        Ok(self)
    }

    pub fn router(self) -> Router {
        Router::new()
            .route("/mcp", post(handle_mcp))
            .with_state(self)
    }
}

async fn handle_mcp(
    State(state): State<BridgeIngress>,
    headers: HeaderMap,
    body: Bytes,
) -> Response<Body> {
    match &state.inner.local_auth {
        LocalIngressAuth::SharedKey(expected) if !valid_ingress_key(&headers, expected) => {
            return text_response(
                StatusCode::UNAUTHORIZED,
                "local ingress authentication failed",
            );
        }
        LocalIngressAuth::SharedKey(_) | LocalIngressAuth::TrustedUnixSocket => {}
    }

    // Sign the bytes that backends actually receive. Protocol translation
    // must happen before x-lab-body-sha256 is computed; Process Runtime
    // rejects a rewritten body that no longer matches the signed hash.
    let method_hint = jsonrpc_method(&body).or_else(|| {
        headers
            .get("mcp-method")
            .and_then(|value| value.to_str().ok())
            .map(str::to_string)
    });
    let backend_protocol = backend_protocol_for_method(method_hint.as_deref());
    let (body, _) = rewrite_protocol_for_backend(body, backend_protocol);

    let parsed: serde_json::Value = match serde_json::from_slice(&body) {
        Ok(value) => value,
        Err(_) => return text_response(StatusCode::BAD_REQUEST, "invalid JSON-RPC body"),
    };
    let method = match parsed.get("method").and_then(serde_json::Value::as_str) {
        Some(value) if !value.is_empty() => value,
        _ => return text_response(StatusCode::BAD_REQUEST, "JSON-RPC method is required"),
    };
    let tool_name = parsed
        .get("params")
        .and_then(|value| value.get("name"))
        .and_then(serde_json::Value::as_str)
        .unwrap_or("");
    let protected = if state.inner.process_backend.is_none()
        && state.inner.tool_receipts.is_some()
        && (tool_name == "run_command" || header_text(&headers, "mcp-name") == Some("run_command"))
    {
        match parse_protected_run_command(&body) {
            Ok(value) => Some(value),
            Err(error) => return text_response(StatusCode::BAD_REQUEST, &error),
        }
    } else {
        None
    };

    if let Some(header_method) = header_text(&headers, "mcp-method") {
        if header_method != method {
            return text_response(
                StatusCode::BAD_REQUEST,
                "Mcp-Method does not match body method",
            );
        }
    }
    if method == "tools/call" {
        if tool_name.is_empty() {
            return text_response(StatusCode::BAD_REQUEST, "tools/call requires params.name");
        }
        if let Some(header_name) = header_text(&headers, "mcp-name") {
            if header_name != tool_name {
                return text_response(StatusCode::BAD_REQUEST, "Mcp-Name does not match tool name");
            }
        }
    }

    let capability = match state.inner.policy.required_capability(method, tool_name) {
        Ok(value) => value,
        Err(error) => return text_response(StatusCode::FORBIDDEN, &error),
    };
    let request_id =
        if protected.is_some() || is_reconnectable_discovery_method(method, &capability) {
            // Discovery reconnects may reuse provider request IDs across JSON-RPC
            // methods. Proof IDs are authority replay keys, so keep them unique.
            Uuid::new_v4().to_string()
        } else {
            // Non-discovery paths only treat a non-empty x-request-id as the
            // authority replay key. ChatGPT/tunnel reuses JSON-RPC id 0 without
            // that header, so body id must not be an authority key.
            header_text(&headers, "x-request-id")
                .filter(|value| !value.is_empty())
                .map(str::to_string)
                .unwrap_or_else(|| Uuid::new_v4().to_string())
        };
    let nonce = Uuid::new_v4().to_string();
    let now_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX);
    let proof = {
        let mut authority = state.inner.authority.lock().await;
        if let Some(renewal) = state.inner.renewal {
            let lease = match authority.lease(&state.inner.context.capability_id) {
                Some(lease) => lease,
                None => return text_response(StatusCode::FORBIDDEN, "unknown capability"),
            };
            let renew_at = lease.expires_at_ms.saturating_sub(renewal.renew_before_ms);
            if now_ms >= renew_at {
                if let Some(state_dir) = &state.inner.state_dir {
                    if let Err(error) = authority.renew_exact_grant_private(
                        state_dir,
                        &state.inner.context.capability_id,
                        &lease.grant,
                        now_ms,
                        renewal.ttl_ms,
                    ) {
                        return text_response(
                            StatusCode::INTERNAL_SERVER_ERROR,
                            &format!("capability renewal failed: {error}"),
                        );
                    }
                } else {
                    return text_response(
                        StatusCode::INTERNAL_SERVER_ERROR,
                        "capability renewal requires durable Bridge state",
                    );
                }
            }
        }
        let issued =
            match authority.issued_capability_at(&state.inner.context.capability_id, now_ms) {
                Ok(issued) => issued,
                Err(error) => return text_response(StatusCode::FORBIDDEN, &error),
            };
        let proof = match sign_request(
            &issued,
            &state.inner.context,
            &request_id,
            &nonce,
            method,
            tool_name,
            &body,
        ) {
            Ok(value) => value,
            Err(error) => return text_response(StatusCode::INTERNAL_SERVER_ERROR, &error),
        };
        let authority_before_verify = authority.clone();
        if let Err(error) =
            authority.verify_request(&proof, &body, method, tool_name, capability, now_ms)
        {
            return text_response(StatusCode::FORBIDDEN, &error);
        }
        if let Some(state_dir) = &state.inner.state_dir {
            if let Err(error) = authority.save_private(state_dir) {
                *authority = authority_before_verify;
                return text_response(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    &format!("capability replay state persistence failed: {error}"),
                );
            }
        }
        proof
    };

    if let (Some(protected), Some(chain)) = (protected, state.inner.tool_receipts.as_ref()) {
        if state.inner.process_backend.is_none() {
            return match handle_protected_run_command(&state, &headers, protected, body, chain).await {
                Ok(response) => response,
                Err(error) => text_response(error.status, &error.message),
            };
        }
    }

    if method == "tools/call" && collaboration_tools::is_feishu_admin_tool(tool_name) {
        return handle_feishu_admin_call(&state, &parsed, tool_name).await;
    }

    if method == "tools/call" && tool_name.starts_with("feishu_") {
        return handle_feishu_call(&state, &parsed, tool_name).await;
    }

    if method == "tools/list"
        && (state.inner.host_backend.is_some()
            || state.inner.computer_backend.is_some()
            || state.inner.process_backend.is_some()
            || state.inner.collaboration.is_some()
            || state.inner.feishu_admin.is_some())
    {
        return match forward_merged_tools_list(&state, &headers, &proof.to_headers(), body).await {
            Ok(response) => response,
            Err(error) => text_response(StatusCode::BAD_GATEWAY, &error),
        };
    }

    let target = if method == "tools/call" && tool_name == "discover_current_entry" {
        match state.inner.host_backend.as_ref() {
            Some(target) => target,
            None => {
                return text_response(
                    StatusCode::SERVICE_UNAVAILABLE,
                    "Web Host attestation backend is not configured",
                );
            }
        }
    } else if method == "tools/call" && tool_name.starts_with("computer_") {
        match state.inner.computer_backend.as_ref() {
            Some(target) => target,
            None => {
                return text_response(
                    StatusCode::SERVICE_UNAVAILABLE,
                    "Computer Runtime backend is not configured",
                );
            }
        }
    } else if method == "tools/call"
        && matches!(
            tool_name,
            "run_command" | "start_command" | "poll_command" | "cancel_command"
        )
    {
        backend_for_run_command(&state)
    } else {
        &state.inner.default_backend
    };

    match forward_to_backend(&state, target, &headers, &proof.to_headers(), body).await {
        Ok(response) if method == "tools/call" && tool_name == "discover_current_entry" => {
            match validate_host_call_response(response, &state.inner.host_release_fence).await {
                Ok(response) => response,
                Err(error) => text_response(StatusCode::BAD_GATEWAY, &error),
            }
        }
        Ok(response) if method == "tools/list" && state.inner.tool_receipts.is_some() => {
            match decorate_tools_list_response(response).await {
                Ok(response) => response,
                Err(error) => text_response(StatusCode::BAD_GATEWAY, &error),
            }
        }
        Ok(response) => response,
        Err(error) => text_response(StatusCode::BAD_GATEWAY, &error),
    }
}

async fn handle_feishu_admin_call(
    state: &BridgeIngress,
    parsed: &serde_json::Value,
    tool_name: &str,
) -> Response<Body> {
    let runtime = match state.inner.feishu_admin.as_ref() {
        Some(runtime) => runtime.clone(),
        None => {
            return text_response(
                StatusCode::SERVICE_UNAVAILABLE,
                "Feishu admin runtime is not configured",
            );
        }
    };
    let arguments = parsed
        .get("params")
        .and_then(|value| value.get("arguments"))
        .cloned()
        .unwrap_or_else(|| serde_json::json!({}));
    let id = parsed
        .get("id")
        .cloned()
        .unwrap_or(serde_json::Value::Null);

    let result: Result<serde_json::Value, Response<Body>> = match tool_name {
        "feishu_setup_start" => {
            let action = match collaboration_tools::parse_feishu_setup_start(&arguments) {
                Ok(action) => action,
                Err(error) => return text_response(StatusCode::BAD_REQUEST, &error),
            };
            runtime
                .start_setup(&action)
                .map_err(|_| text_response(StatusCode::INTERNAL_SERVER_ERROR, "Feishu setup job failed to start"))
        }
        "feishu_setup_poll" => {
            let job_id = match collaboration_tools::parse_feishu_setup_poll(&arguments) {
                Ok(job_id) => job_id,
                Err(error) => return text_response(StatusCode::BAD_REQUEST, &error),
            };
            runtime
                .poll_setup(&job_id)
                .map_err(|error| text_response(StatusCode::BAD_REQUEST, &error))
        }
        "feishu_auth_begin" => {
            if let Err(error) = collaboration_tools::parse_feishu_auth_begin(&arguments) {
                return text_response(StatusCode::BAD_REQUEST, &error);
            }
            match tokio::task::spawn_blocking(move || runtime.auth_begin()).await {
                Ok(Ok(value)) => Ok(value),
                Ok(Err(error)) => Err(feishu_admin_provider_error(id.clone(), error)),
                Err(_) => Err(text_response(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "Feishu auth worker failed",
                )),
            }
        }
        "feishu_auth_complete" => {
            let flow_id = match collaboration_tools::parse_feishu_auth_complete(&arguments) {
                Ok(flow_id) => flow_id,
                Err(error) => return text_response(StatusCode::BAD_REQUEST, &error),
            };
            match tokio::task::spawn_blocking(move || runtime.auth_complete(&flow_id)).await {
                Ok(Ok(value)) => Ok(value),
                Ok(Err(error)) => Err(feishu_admin_provider_error(id.clone(), error)),
                Err(_) => Err(text_response(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "Feishu auth worker failed",
                )),
            }
        }
        _ => return text_response(StatusCode::BAD_REQUEST, "unsupported Feishu admin tool"),
    };

    match result {
        Ok(data) => {
            let text = serde_json::to_string(&data).unwrap_or_else(|_| "{}".into());
            json_response(
                StatusCode::OK,
                serde_json::json!({
                    "jsonrpc":"2.0",
                    "id":id,
                    "result":{
                        "content":[{"type":"text","text":text}],
                        "structuredContent":data,
                    }
                }),
            )
        }
        Err(response) => response,
    }
}

fn feishu_admin_provider_error(
    id: serde_json::Value,
    error: collaboration_adapter::CollaborationError,
) -> Response<Body> {
    let kind = collaboration_tools::error_kind_name(&error.kind);
    let message = match error.kind {
        collaboration_adapter::CollaborationErrorKind::InvalidArgument => error.message,
        collaboration_adapter::CollaborationErrorKind::CliUnavailable => {
            "Feishu setup executable is unavailable".into()
        }
        collaboration_adapter::CollaborationErrorKind::PermissionDenied => {
            "Feishu setup permission was denied".into()
        }
        _ => "Feishu setup/auth operation failed".into(),
    };
    json_response(
        StatusCode::OK,
        serde_json::json!({
            "jsonrpc":"2.0",
            "id":id,
            "error":{
                "code":-32011,
                "message":message,
                "data":{
                    "provider":"feishu",
                    "kind":kind,
                    "dispatch_attempted":error.dispatch_attempted,
                }
            }
        }),
    )
}

async fn handle_feishu_call(
    state: &BridgeIngress,
    parsed: &serde_json::Value,
    tool_name: &str,
) -> Response<Body> {
    let runtime = match state.inner.collaboration.as_ref() {
        Some(runtime) => runtime.clone(),
        None => {
            return text_response(
                StatusCode::SERVICE_UNAVAILABLE,
                "Feishu provider is not configured",
            );
        }
    };
    let arguments = parsed
        .get("params")
        .and_then(|value| value.get("arguments"))
        .cloned()
        .unwrap_or_else(|| serde_json::json!({}));
    let id = parsed
        .get("id")
        .cloned()
        .unwrap_or(serde_json::Value::Null);
    if tool_name == "feishu_cli" {
        let argv = match collaboration_tools::validate_feishu_cli_argv(&arguments) {
            Ok((argv, _)) => argv,
            Err(error) => return text_response(StatusCode::BAD_REQUEST, &error),
        };
        return match tokio::task::spawn_blocking(move || runtime.execute_argv(&argv)).await {
            Ok(Ok(data)) => {
                let text = serde_json::to_string(&data).unwrap_or_else(|_| "{}".into());
                json_response(
                    StatusCode::OK,
                    serde_json::json!({
                        "jsonrpc": "2.0",
                        "id": id,
                        "result": {
                            "content": [{"type":"text","text":text}],
                            "structuredContent": data,
                        }
                    }),
                )
            }
            Ok(Err(error)) => json_response(
                StatusCode::OK,
                serde_json::json!({
                    "jsonrpc":"2.0",
                    "id":id,
                    "error":{
                        "code":-32010,
                        "message":error.message,
                        "data":{"provider":"feishu","backend":"lark-cli"}
                    }
                }),
            ),
            Err(_) => text_response(
                StatusCode::INTERNAL_SERVER_ERROR,
                "Feishu CLI worker failed",
            ),
        };
    }
    let operation = match collaboration_tools::parse_feishu_operation(
        tool_name,
        &arguments,
        &format!("lab-{}", Uuid::new_v4()),
    ) {
        Ok(operation) => operation,
        Err(error) => return text_response(StatusCode::BAD_REQUEST, &error),
    };
    let id = parsed
        .get("id")
        .cloned()
        .unwrap_or(serde_json::Value::Null);
    match tokio::task::spawn_blocking(move || runtime.execute(&operation)).await {
        Ok(Ok(data)) => {
            let text = serde_json::to_string(&data).unwrap_or_else(|_| "{}".into());
            let value = serde_json::json!({
                "jsonrpc": "2.0",
                "id": id,
                "result": {
                    "content": [{"type":"text","text":text}],
                    "structuredContent": data,
                }
            });
            json_response(StatusCode::OK, value)
        }
        Ok(Err(error)) => {
            let kind = collaboration_tools::error_kind_name(&error.kind);
            let message = match error.kind {
                collaboration_adapter::CollaborationErrorKind::InvalidArgument => error.message,
                collaboration_adapter::CollaborationErrorKind::CliUnavailable => {
                    "Feishu CLI is unavailable or not configured".into()
                }
                collaboration_adapter::CollaborationErrorKind::AuthRequired => {
                    "Feishu authorization is required".into()
                }
                collaboration_adapter::CollaborationErrorKind::PermissionDenied => {
                    "Feishu permission was denied".into()
                }
                _ => "Feishu provider call failed".into(),
            };
            json_response(
                StatusCode::OK,
                serde_json::json!({
                    "jsonrpc":"2.0",
                    "id":id,
                    "error":{
                        "code":-32010,
                        "message":message,
                        "data":{
                            "provider":"feishu",
                            "backend":"lark-cli",
                            "kind":kind,
                            "dispatch_attempted":error.dispatch_attempted,
                        }
                    }
                }),
            )
        }
        Err(_) => text_response(
            StatusCode::INTERNAL_SERVER_ERROR,
            "Feishu provider worker failed",
        ),
    }
}

fn json_response(status: StatusCode, value: serde_json::Value) -> Response<Body> {
    Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(value.to_string()))
        .unwrap_or_else(|_| Response::new(Body::from("response build failed")))
}

#[derive(Debug)]
struct FlowError {
    status: StatusCode,
    message: String,
}

impl FlowError {
    fn new(status: StatusCode, message: impl Into<String>) -> Self {
        Self {
            status,
            message: message.into(),
        }
    }
}

#[derive(Clone, Debug)]
struct ProtectedRunCommand {
    value: serde_json::Value,
    caller_id: serde_json::Value,
    capability: Option<serde_json::Value>,
    capability_neutral_arguments: serde_json::Value,
}

impl ProtectedRunCommand {
    fn forwarded_for(&self, bridge_call_id: &str) -> Result<Vec<u8>, String> {
        let mut value = self.value.clone();
        value["id"] = serde_json::Value::String(bridge_call_id.to_string());
        let params = value
            .get_mut("params")
            .and_then(serde_json::Value::as_object_mut)
            .ok_or_else(|| "protected run_command params are invalid".to_string())?;
        params.remove("_meta");
        let arguments = params
            .get_mut("arguments")
            .and_then(serde_json::Value::as_object_mut)
            .ok_or_else(|| "protected run_command arguments are invalid".to_string())?;
        arguments.remove("host_invocation_capability");
        canonical_json_bytes(&value)
    }

    fn normalized_bytes(&self) -> Result<Vec<u8>, String> {
        canonical_json_bytes(&serde_json::json!({
            "tool_name": "run_command",
            "arguments": self.capability_neutral_arguments,
        }))
    }

    fn backend_deadline(&self) -> Duration {
        let requested = self
            .capability_neutral_arguments
            .get("timeout")
            .and_then(serde_json::Value::as_u64)
            .unwrap_or(300);
        Duration::from_secs(requested.saturating_add(1).max(1))
    }
}

fn runtime_tool_intent(normalized: &[u8]) -> Result<serde_json::Value, String> {
    let normalized_request_utf8 = std::str::from_utf8(normalized)
        .map_err(|_| "canonical normalized request is not UTF-8".to_string())?;
    Ok(serde_json::json!({
        "schema_version": 1,
        "provenance": RUNTIME_TOOL_INTENT_PROVENANCE,
        "request_digest_profile": RUN_COMMAND_DIGEST_PROFILE,
        "normalized_request_utf8": normalized_request_utf8,
    }))
}

fn runtime_backend_response(bytes: &[u8]) -> Result<serde_json::Value, String> {
    let response_body_utf8 = std::str::from_utf8(bytes)
        .map_err(|_| "validated backend response is not UTF-8".to_string())?;
    Ok(serde_json::json!({
        "schema_version": 1,
        "provenance": RUNTIME_BACKEND_RESPONSE_PROVENANCE,
        "response_body_utf8": response_body_utf8,
    }))
}

fn parse_protected_run_command(body: &[u8]) -> Result<ProtectedRunCommand, String> {
    if body.len() > MAX_PROTECTED_REQUEST_BYTES {
        return Err("protected run_command request exceeds 64 KiB".into());
    }
    let value = StrictJsonParser::new(body).parse()?;
    let envelope = value
        .as_object()
        .ok_or_else(|| "protected run_command requires one JSON-RPC object".to_string())?;
    let allowed_envelope = ["jsonrpc", "id", "method", "params"];
    if envelope
        .keys()
        .any(|key| !allowed_envelope.contains(&key.as_str()))
    {
        return Err("protected run_command contains unsupported envelope members".into());
    }
    if envelope.get("jsonrpc").and_then(serde_json::Value::as_str) != Some("2.0")
        || envelope.get("method").and_then(serde_json::Value::as_str) != Some("tools/call")
    {
        return Err("protected run_command JSON-RPC profile is invalid".into());
    }
    let caller_id = envelope
        .get("id")
        .cloned()
        .filter(valid_correlation_id)
        .ok_or_else(|| "protected run_command requires a string or safe-integer id".to_string())?;
    let params = envelope
        .get("params")
        .and_then(serde_json::Value::as_object)
        .ok_or_else(|| "protected run_command params must be an object".to_string())?;
    if params
        .keys()
        .any(|key| !["name", "arguments", "_meta"].contains(&key.as_str()))
    {
        return Err("protected run_command contains unsupported params members".into());
    }
    if params.get("_meta").is_some_and(|value| !value.is_object()) {
        return Err("protected run_command params._meta must be an object".into());
    }
    if params.get("name").and_then(serde_json::Value::as_str) != Some("run_command") {
        return Err("protected profile is only valid for run_command".into());
    }
    let arguments = params
        .get("arguments")
        .and_then(serde_json::Value::as_object)
        .ok_or_else(|| "protected run_command arguments must be an object".to_string())?;
    let capability = match arguments.get("host_invocation_capability") {
        None => None,
        Some(value) if value.is_object() => Some(value.clone()),
        Some(_) => return Err("host_invocation_capability must be a non-null object".into()),
    };
    let mut neutral = arguments.clone();
    neutral.remove("host_invocation_capability");
    reject_caller_identity_fields(&serde_json::Value::Object(neutral.clone()))?;
    Ok(ProtectedRunCommand {
        value,
        caller_id,
        capability,
        capability_neutral_arguments: serde_json::Value::Object(neutral),
    })
}

fn reject_caller_identity_fields(value: &serde_json::Value) -> Result<(), String> {
    const FORBIDDEN: &[&str] = &[
        "bridge_call_id",
        "bridge_challenge_nonce",
        "transport_binding_id",
        "host_tool_execution_id",
        "bridge_execution_id",
        "execution_session_id",
        "conversation_id",
        "browser_target_id",
        "top_frame_id",
        "loader_id",
        "generation_anchor_sha256",
        "extension_binding_sha256",
        "extension_instance_id_sha256",
        "machine_context",
        "x-openai-subject",
    ];
    match value {
        serde_json::Value::Object(values) => {
            for (key, value) in values {
                if FORBIDDEN.contains(&key.as_str()) {
                    return Err(format!(
                        "caller-provided execution or Host identity is forbidden: {key}"
                    ));
                }
                reject_caller_identity_fields(value)?;
            }
        }
        serde_json::Value::Array(values) => {
            for value in values {
                reject_caller_identity_fields(value)?;
            }
        }
        _ => {}
    }
    Ok(())
}

fn valid_correlation_id(value: &serde_json::Value) -> bool {
    match value {
        serde_json::Value::String(value) => !value.is_empty() && value.len() <= 256,
        serde_json::Value::Number(value) => {
            value
                .as_i64()
                .is_some_and(|number| number.unsigned_abs() <= 9_007_199_254_740_991)
                || value
                    .as_u64()
                    .is_some_and(|number| number <= 9_007_199_254_740_991)
        }
        _ => false,
    }
}

async fn handle_protected_run_command(
    state: &BridgeIngress,
    incoming: &HeaderMap,
    protected: ProtectedRunCommand,
    raw_body: Bytes,
    chain: &ToolReceiptChain,
) -> Result<Response<Body>, FlowError> {
    let mut journal = chain.journal.lock().await;
    if protected.capability.is_none() {
        let bridge_call_id = format!("bc_{}", Uuid::new_v4());
        let bridge_challenge_nonce = Uuid::new_v4().to_string();
        let transport_binding_id = format!("tb_{}", Uuid::new_v4());
        let forwarded = protected
            .forwarded_for(&bridge_call_id)
            .map_err(|error| FlowError::new(StatusCode::BAD_REQUEST, error))?;
        let normalized = protected
            .normalized_bytes()
            .map_err(|error| FlowError::new(StatusCode::BAD_REQUEST, error))?;
        let outer_sha = sha256_bytes(&raw_body);
        let forwarded_sha = sha256_bytes(&forwarded);
        let normalized_sha = sha256_bytes(&normalized);
        let request = receipt_binding_json(
            chain,
            &state.inner.context,
            &bridge_call_id,
            &bridge_challenge_nonce,
            &transport_binding_id,
            &outer_sha,
            &forwarded_sha,
            &normalized_sha,
        );
        let host = call_private_ndjson(&chain.host_socket, with_op(request, "tool_receipt_issue"))
            .await
            .map_err(|error| FlowError::new(StatusCode::BAD_GATEWAY, error))?;
        let capability = host
            .get("capability")
            .cloned()
            .filter(serde_json::Value::is_object)
            .ok_or_else(|| {
                FlowError::new(
                    StatusCode::BAD_GATEWAY,
                    "Host issue response has no capability",
                )
            })?;
        validate_host_binding(
            &capability,
            &bridge_call_id,
            &bridge_challenge_nonce,
            &transport_binding_id,
            &outer_sha,
            &forwarded_sha,
            &normalized_sha,
            "lab_host_invocation_capability_v1",
        )
        .and_then(|_| validate_receipt_context(&capability, chain, &state.inner.context))
        .and_then(|_| validate_machine_receipt_fields(&capability))
        .map_err(|error| FlowError::new(StatusCode::BAD_GATEWAY, error))?;
        let capability_id = required_value_text(&capability, "capability_id", "hic_")
            .map_err(|error| FlowError::new(StatusCode::BAD_GATEWAY, error))?;
        journal
            .insert(ToolReceiptRecord {
                bridge_call_id,
                bridge_challenge_nonce,
                transport_binding_id,
                capability_id: capability_id.clone(),
                capability: capability.clone(),
                outer_request_sha256: outer_sha,
                forwarded_request_sha256: forwarded_sha,
                normalized_request_sha256: normalized_sha,
                frozen_forwarded: forwarded,
                state: ToolReceiptState::Issued,
                host_tool_execution_id: None,
                retry_outer_request_sha256: None,
                pre_receipt: None,
                backend_http_status: None,
                backend_response_sha256: None,
            })
            .map_err(|error| FlowError::new(StatusCode::INTERNAL_SERVER_ERROR, error))?;
        return challenge_response(&protected.caller_id, capability)
            .map_err(|error| FlowError::new(StatusCode::INTERNAL_SERVER_ERROR, error));
    }

    let submitted_capability = protected.capability.as_ref().expect("checked above");
    let capability_id = required_value_text(submitted_capability, "capability_id", "hic_")
        .map_err(|error| FlowError::new(StatusCode::BAD_REQUEST, error))?;
    let record = journal
        .record(&capability_id)
        .map_err(|error| FlowError::new(StatusCode::INTERNAL_SERVER_ERROR, error))?
        .ok_or_else(|| {
            FlowError::new(StatusCode::CONFLICT, "unknown Host invocation capability")
        })?;
    if record.state != ToolReceiptState::Issued {
        return Err(FlowError::new(
            StatusCode::CONFLICT,
            if record.state == ToolReceiptState::ResultUnknownAfterDispatch {
                "RESULT_UNKNOWN_AFTER_DISPATCH"
            } else {
                "Host invocation capability replayed"
            },
        ));
    }
    if submitted_capability != &record.capability {
        return Err(FlowError::new(
            StatusCode::CONFLICT,
            "Host invocation capability binding mismatch",
        ));
    }
    let forwarded = protected
        .forwarded_for(&record.bridge_call_id)
        .map_err(|error| FlowError::new(StatusCode::BAD_REQUEST, error))?;
    let normalized = protected
        .normalized_bytes()
        .map_err(|error| FlowError::new(StatusCode::BAD_REQUEST, error))?;
    if forwarded != record.frozen_forwarded
        || sha256_bytes(&forwarded) != record.forwarded_request_sha256
        || sha256_bytes(&normalized) != record.normalized_request_sha256
    {
        return Err(FlowError::new(
            StatusCode::CONFLICT,
            "Host invocation capability request mismatch",
        ));
    }
    let tool_intent = runtime_tool_intent(&normalized)
        .map_err(|error| FlowError::new(StatusCode::BAD_REQUEST, error))?;
    let retry_outer_sha = sha256_bytes(&raw_body);
    let host_tool_execution_id = format!("hte_{}", Uuid::new_v4());
    let mut consume = receipt_binding_for_record(chain, &state.inner.context, &record);
    consume["capability"] = record.capability.clone();
    consume["retry_outer_request_sha256"] = serde_json::Value::String(retry_outer_sha.clone());
    consume["host_tool_execution_id"] = serde_json::Value::String(host_tool_execution_id.clone());
    let host = call_private_ndjson(&chain.host_socket, with_op(consume, "tool_receipt_consume"))
        .await
        .map_err(|error| FlowError::new(StatusCode::BAD_GATEWAY, error))?;
    let pre_receipt = host
        .get("pre_receipt")
        .cloned()
        .filter(serde_json::Value::is_object)
        .ok_or_else(|| {
            FlowError::new(
                StatusCode::BAD_GATEWAY,
                "Host consume response has no pre receipt",
            )
        })?;
    validate_host_binding(
        &pre_receipt,
        &record.bridge_call_id,
        &record.bridge_challenge_nonce,
        &record.transport_binding_id,
        &record.outer_request_sha256,
        &record.forwarded_request_sha256,
        &record.normalized_request_sha256,
        "lab_host_tool_pre_receipt_v1",
    )
    .and_then(|_| validate_receipt_context(&pre_receipt, chain, &state.inner.context))
    .and_then(|_| validate_machine_receipt_fields(&pre_receipt))
    .and_then(|_| require_exact_text(&pre_receipt, "capability_id", &record.capability_id))
    .and_then(|_| {
        require_exact_text(
            &pre_receipt,
            "host_tool_execution_id",
            &host_tool_execution_id,
        )
    })
    .and_then(|_| require_exact_text(&pre_receipt, "retry_outer_request_sha256", &retry_outer_sha))
    .map_err(|error| FlowError::new(StatusCode::BAD_GATEWAY, error))?;
    let pre_receipt_id = required_value_text(&pre_receipt, "pre_receipt_id", "hpr_")
        .map_err(|error| FlowError::new(StatusCode::BAD_GATEWAY, error))?;
    journal
        .update(&capability_id, |record| {
            record.state = ToolReceiptState::HostValidatedInFlight;
            record.host_tool_execution_id = Some(host_tool_execution_id.clone());
            record.retry_outer_request_sha256 = Some(retry_outer_sha.clone());
            record.pre_receipt = Some(pre_receipt.clone());
            Ok(())
        })
        .map_err(|error| FlowError::new(StatusCode::INTERNAL_SERVER_ERROR, error))?;

    if let Some(runtime_socket) = &chain.runtime_socket {
        let runtime = call_private_ndjson(
            runtime_socket,
            serde_json::json!({
                "op":"pre", "protocol":RUNTIME_HOOK_PROTOCOL,
                "bridge_call_id":record.bridge_call_id,
                "host_tool_execution_id":host_tool_execution_id,
                "pre_receipt":pre_receipt,
                "tool_intent":tool_intent.clone(),
            }),
        )
        .await
        .map_err(|error| {
            FlowError::new(
                StatusCode::BAD_GATEWAY,
                format!("Runtime pre hook failed: {error}"),
            )
        })?;
        validate_runtime_result(
            &runtime,
            "decision",
            "ALLOW",
            &record.bridge_call_id,
            &host_tool_execution_id,
        )
        .map_err(|error| FlowError::new(StatusCode::FORBIDDEN, error))?;
    }
    journal
        .update(&capability_id, |record| {
            record.state = ToolReceiptState::RuntimeAllowed;
            Ok(())
        })
        .map_err(|error| FlowError::new(StatusCode::INTERNAL_SERVER_ERROR, error))?;
    journal
        .update(&capability_id, |record| {
            record.state = ToolReceiptState::DispatchIntent;
            Ok(())
        })
        .map_err(|error| FlowError::new(StatusCode::INTERNAL_SERVER_ERROR, error))?;

    let now_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX);
    let issued = state
        .inner
        .authority
        .lock()
        .await
        .issued_capability_at(&state.inner.context.capability_id, now_ms)
        .map_err(|error| FlowError::new(StatusCode::FORBIDDEN, error))?;
    let proof = sign_request_for_execution(
        &issued,
        &state.inner.context,
        &host_tool_execution_id,
        &Uuid::new_v4().to_string(),
        &Uuid::new_v4().to_string(),
        "tools/call",
        "run_command",
        &record.frozen_forwarded,
    )
    .map_err(|error| FlowError::new(StatusCode::INTERNAL_SERVER_ERROR, error))?;
    let backend = match send_to_backend_buffered(
        state,
        backend_for_run_command(state),
        incoming,
        &proof.to_headers(),
        Bytes::from(record.frozen_forwarded.clone()),
        protected.backend_deadline(),
    )
    .await
    {
        Ok(value) => value,
        Err(error) => {
            mark_result_unknown(&mut journal, &capability_id, None, None);
            return Err(FlowError::new(
                StatusCode::BAD_GATEWAY,
                format!("RESULT_UNKNOWN_AFTER_DISPATCH: {error}"),
            ));
        }
    };
    let backend_sha = sha256_bytes(&backend.bytes);
    if let Err(error) = validate_backend_response(&backend.bytes, &record.bridge_call_id) {
        mark_result_unknown(
            &mut journal,
            &capability_id,
            Some(backend.status.as_u16()),
            Some(backend_sha),
        );
        return Err(FlowError::new(
            StatusCode::BAD_GATEWAY,
            format!("RESULT_UNKNOWN_AFTER_DISPATCH: {error}"),
        ));
    }
    let backend_response = match runtime_backend_response(&backend.bytes) {
        Ok(value) => value,
        Err(error) => {
            mark_result_unknown(
                &mut journal,
                &capability_id,
                Some(backend.status.as_u16()),
                Some(backend_sha),
            );
            return Err(FlowError::new(
                StatusCode::BAD_GATEWAY,
                format!("RESULT_UNKNOWN_AFTER_DISPATCH: {error}"),
            ));
        }
    };
    if let Err(error) = journal.update(&capability_id, |record| {
        record.state = ToolReceiptState::BackendResponse;
        record.backend_http_status = Some(backend.status.as_u16());
        record.backend_response_sha256 = Some(backend_sha.clone());
        Ok(())
    }) {
        mark_result_unknown(
            &mut journal,
            &capability_id,
            Some(backend.status.as_u16()),
            Some(backend_sha),
        );
        return Err(FlowError::new(
            StatusCode::BAD_GATEWAY,
            format!("RESULT_UNKNOWN_AFTER_DISPATCH: {error}"),
        ));
    }
    if let Err(error) = journal.update(&capability_id, |record| {
        record.state = ToolReceiptState::TerminalPending;
        Ok(())
    }) {
        mark_result_unknown(
            &mut journal,
            &capability_id,
            Some(backend.status.as_u16()),
            Some(backend_sha),
        );
        return Err(FlowError::new(
            StatusCode::BAD_GATEWAY,
            format!("RESULT_UNKNOWN_AFTER_DISPATCH: {error}"),
        ));
    }
    let backend_route = backend_for_run_command(state).url.path().to_string();
    let terminal_classification = if backend.status.is_success() {
        "SUCCESS"
    } else {
        "BACKEND_HTTP_ERROR"
    };
    let mut terminal = receipt_binding_for_record(chain, &state.inner.context, &record);
    terminal["pre_receipt"] = pre_receipt.clone();
    terminal["retry_outer_request_sha256"] = serde_json::Value::String(retry_outer_sha.clone());
    terminal["backend_route"] = serde_json::Value::String(backend_route.clone());
    terminal["http_status"] = serde_json::json!(backend.status.as_u16());
    terminal["response_body_sha256"] = serde_json::Value::String(backend_sha.clone());
    terminal["terminal_classification"] = serde_json::Value::String(terminal_classification.into());
    let host_terminal = match call_private_ndjson(
        &chain.host_socket,
        with_op(terminal, "tool_receipt_terminal"),
    )
    .await
    {
        Ok(value) => value,
        Err(error) => {
            mark_result_unknown(
                &mut journal,
                &capability_id,
                Some(backend.status.as_u16()),
                Some(backend_sha),
            );
            return Err(FlowError::new(
                StatusCode::BAD_GATEWAY,
                format!("RESULT_UNKNOWN_AFTER_DISPATCH: Host terminal failed: {error}"),
            ));
        }
    };
    let terminal_receipt = match host_terminal
        .get("terminal_receipt")
        .cloned()
        .filter(serde_json::Value::is_object)
    {
        Some(value) => value,
        None => {
            mark_result_unknown(
                &mut journal,
                &capability_id,
                Some(backend.status.as_u16()),
                Some(backend_sha),
            );
            return Err(FlowError::new(
                StatusCode::BAD_GATEWAY,
                "RESULT_UNKNOWN_AFTER_DISPATCH: Host terminal receipt missing",
            ));
        }
    };
    if let Err(error) = validate_host_binding(
        &terminal_receipt,
        &record.bridge_call_id,
        &record.bridge_challenge_nonce,
        &record.transport_binding_id,
        &record.outer_request_sha256,
        &record.forwarded_request_sha256,
        &record.normalized_request_sha256,
        "lab_host_tool_terminal_receipt_v1",
    )
    .and_then(|_| validate_receipt_context(&terminal_receipt, chain, &state.inner.context))
    .and_then(|_| validate_machine_receipt_fields(&terminal_receipt))
    .and_then(|_| require_exact_text(&terminal_receipt, "capability_id", &record.capability_id))
    .and_then(|_| require_exact_text(&terminal_receipt, "pre_receipt_id", &pre_receipt_id))
    .and_then(|_| {
        require_exact_text(
            &terminal_receipt,
            "host_tool_execution_id",
            &host_tool_execution_id,
        )
    })
    .and_then(|_| {
        require_exact_text(
            &terminal_receipt,
            "retry_outer_request_sha256",
            &retry_outer_sha,
        )
    })
    .and_then(|_| require_exact_text(&terminal_receipt, "response_body_sha256", &backend_sha))
    .and_then(|_| {
        validate_terminal_receipt(
            &terminal_receipt,
            &pre_receipt,
            &pre_receipt_id,
            &record.capability_id,
            &host_tool_execution_id,
            &retry_outer_sha,
            &backend_route,
            backend.status.as_u16(),
            terminal_classification,
        )
    }) {
        mark_result_unknown(
            &mut journal,
            &capability_id,
            Some(backend.status.as_u16()),
            Some(backend_sha),
        );
        return Err(FlowError::new(
            StatusCode::BAD_GATEWAY,
            format!("RESULT_UNKNOWN_AFTER_DISPATCH: {error}"),
        ));
    }
    if let Some(runtime_socket) = &chain.runtime_socket {
        let runtime = match call_private_ndjson(
            runtime_socket,
            serde_json::json!({
                "op":"terminal", "protocol":RUNTIME_HOOK_PROTOCOL,
                "bridge_call_id":record.bridge_call_id,
                "host_tool_execution_id":host_tool_execution_id,
                "terminal_receipt":terminal_receipt,
                "backend_http_status":backend.status.as_u16(),
                "response_body_sha256":backend_sha,
                "tool_intent":tool_intent,
                "backend_response":backend_response,
            }),
        )
        .await
        {
            Ok(value) => value,
            Err(error) => {
                mark_result_unknown(
                    &mut journal,
                    &capability_id,
                    Some(backend.status.as_u16()),
                    Some(backend_sha),
                );
                return Err(FlowError::new(
                    StatusCode::BAD_GATEWAY,
                    format!("RESULT_UNKNOWN_AFTER_DISPATCH: Runtime terminal failed: {error}"),
                ));
            }
        };
        if let Err(error) = validate_runtime_result(
            &runtime,
            "state",
            "CLOSED",
            &record.bridge_call_id,
            &host_tool_execution_id,
        ) {
            mark_result_unknown(
                &mut journal,
                &capability_id,
                Some(backend.status.as_u16()),
                Some(backend_sha),
            );
            return Err(FlowError::new(
                StatusCode::BAD_GATEWAY,
                format!("RESULT_UNKNOWN_AFTER_DISPATCH: {error}"),
            ));
        }
    }
    if let Err(error) = journal.update(&capability_id, |record| {
        record.state = ToolReceiptState::Terminal;
        Ok(())
    }) {
        mark_result_unknown(
            &mut journal,
            &capability_id,
            Some(backend.status.as_u16()),
            Some(backend_sha),
        );
        return Err(FlowError::new(
            StatusCode::BAD_GATEWAY,
            format!("RESULT_UNKNOWN_AFTER_DISPATCH: {error}"),
        ));
    }
    map_backend_response_id(backend, &protected.caller_id)
        .map_err(|error| FlowError::new(StatusCode::BAD_GATEWAY, error))
}

fn mark_result_unknown(
    journal: &mut ToolReceiptJournal,
    capability_id: &str,
    status: Option<u16>,
    digest: Option<String>,
) {
    let _ = journal.update(capability_id, |record| {
        record.state = ToolReceiptState::ResultUnknownAfterDispatch;
        if status.is_some() {
            record.backend_http_status = status;
        }
        if digest.is_some() {
            record.backend_response_sha256 = digest;
        }
        Ok(())
    });
}

fn challenge_response(
    caller_id: &serde_json::Value,
    capability: serde_json::Value,
) -> Result<Response<Body>, String> {
    let bytes = serde_json::to_vec(&serde_json::json!({
        "jsonrpc":"2.0", "id":caller_id,
        "error": {"code":-32042,"message":"Host invocation capability required",
            "data":{"code":"HOST_INVOCATION_CAPABILITY_REQUIRED","host_invocation_capability":capability}}
    })).map_err(|error| format!("challenge response serialization failed: {error}"))?;
    Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(bytes))
        .map_err(|error| format!("challenge response build failed: {error}"))
}

fn receipt_binding_json(
    chain: &ToolReceiptChain,
    context: &ExecutionContext,
    bridge_call_id: &str,
    bridge_challenge_nonce: &str,
    transport_binding_id: &str,
    outer_sha: &str,
    forwarded_sha: &str,
    normalized_sha: &str,
) -> serde_json::Value {
    serde_json::json!({
        "request_digest_profile":RUN_COMMAND_DIGEST_PROFILE,
        "bridge_call_id":bridge_call_id,
        "bridge_challenge_nonce":bridge_challenge_nonce,
        "transport_binding_id":transport_binding_id,
        "workspace":chain.production_fence.workspace,
        "production_release_revision":chain.production_fence.release_revision,
        "production_endpoint_generation":chain.production_fence.endpoint_generation,
        "bridge_principal_id":context.principal_id,
        "bridge_execution_session_id":context.execution_session_id,
        "bridge_capability_id":context.capability_id,
        "bridge_workspace_id":context.workspace_id,
        "tool_name":"run_command",
        "outer_request_sha256":outer_sha,
        "forwarded_request_sha256":forwarded_sha,
        "normalized_request_sha256":normalized_sha,
    })
}

fn receipt_binding_for_record(
    chain: &ToolReceiptChain,
    context: &ExecutionContext,
    record: &ToolReceiptRecord,
) -> serde_json::Value {
    receipt_binding_json(
        chain,
        context,
        &record.bridge_call_id,
        &record.bridge_challenge_nonce,
        &record.transport_binding_id,
        &record.outer_request_sha256,
        &record.forwarded_request_sha256,
        &record.normalized_request_sha256,
    )
}

fn with_op(mut value: serde_json::Value, op: &str) -> serde_json::Value {
    value["op"] = serde_json::Value::String(op.to_string());
    value
}

fn required_value_text(
    value: &serde_json::Value,
    name: &str,
    prefix: &str,
) -> Result<String, String> {
    let text = value
        .get(name)
        .and_then(serde_json::Value::as_str)
        .filter(|text| !text.is_empty())
        .ok_or_else(|| format!("Host receipt {name} is missing"))?;
    if !text.starts_with(prefix) {
        return Err(format!("Host receipt {name} has an invalid prefix"));
    }
    Ok(text.to_string())
}

fn require_exact_text(value: &serde_json::Value, name: &str, expected: &str) -> Result<(), String> {
    if value.get(name).and_then(serde_json::Value::as_str) != Some(expected) {
        return Err(format!("Host receipt {name} mismatch"));
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn validate_host_binding(
    value: &serde_json::Value,
    call_id: &str,
    nonce: &str,
    transport: &str,
    outer: &str,
    forwarded: &str,
    normalized: &str,
    provenance: &str,
) -> Result<(), String> {
    require_exact_text(value, "provenance", provenance)?;
    require_exact_text(value, "request_digest_profile", RUN_COMMAND_DIGEST_PROFILE)?;
    require_exact_text(value, "bridge_call_id", call_id)?;
    require_exact_text(value, "bridge_challenge_nonce", nonce)?;
    require_exact_text(value, "transport_binding_id", transport)?;
    require_exact_text(value, "outer_request_sha256", outer)?;
    require_exact_text(value, "forwarded_request_sha256", forwarded)?;
    require_exact_text(value, "normalized_request_sha256", normalized)?;
    digest_text(
        value
            .get("host_mac_sha256")
            .and_then(serde_json::Value::as_str)
            .unwrap_or(""),
        "host_mac_sha256",
    )?;
    Ok(())
}

fn validate_runtime_result(
    value: &serde_json::Value,
    field: &str,
    expected: &str,
    call_id: &str,
    execution_id: &str,
) -> Result<(), String> {
    require_exact_text(value, "protocol", RUNTIME_HOOK_PROTOCOL)?;
    require_exact_text(value, field, expected)?;
    require_exact_text(value, "bridge_call_id", call_id)?;
    require_exact_text(value, "host_tool_execution_id", execution_id)
}

fn validate_receipt_context(
    value: &serde_json::Value,
    chain: &ToolReceiptChain,
    context: &ExecutionContext,
) -> Result<(), String> {
    require_exact_text(value, "workspace", &chain.production_fence.workspace)?;
    require_exact_text(
        value,
        "production_release_revision",
        &chain.production_fence.release_revision,
    )?;
    if value
        .get("production_endpoint_generation")
        .and_then(serde_json::Value::as_u64)
        != Some(chain.production_fence.endpoint_generation)
    {
        return Err("Host receipt production_endpoint_generation mismatch".into());
    }
    require_exact_text(value, "bridge_principal_id", &context.principal_id)?;
    require_exact_text(
        value,
        "bridge_execution_session_id",
        &context.execution_session_id,
    )?;
    require_exact_text(value, "bridge_capability_id", &context.capability_id)?;
    require_exact_text(value, "bridge_workspace_id", &context.workspace_id)?;
    require_exact_text(value, "tool_name", "run_command")
}

fn validate_machine_receipt_fields(value: &serde_json::Value) -> Result<(), String> {
    for name in [
        "conversation_id",
        "browser_target_id",
        "top_frame_id",
        "loader_id",
    ] {
        required_value_text(value, name, "")?;
    }
    require_exact_text(value, "secure_origin", "https://chatgpt.com")?;
    for name in [
        "generation_anchor_sha256",
        "extension_binding_sha256",
        "extension_instance_id_sha256",
    ] {
        digest_text(
            value
                .get(name)
                .and_then(serde_json::Value::as_str)
                .unwrap_or(""),
            name,
        )?;
    }
    if value
        .get("extension_loaded_at_ms")
        .and_then(serde_json::Value::as_u64)
        .filter(|value| *value > 0)
        .is_none()
    {
        return Err("Host receipt extension_loaded_at_ms is invalid".into());
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn validate_terminal_receipt(
    value: &serde_json::Value,
    pre_receipt: &serde_json::Value,
    pre_receipt_id: &str,
    capability_id: &str,
    execution_id: &str,
    retry_outer_sha: &str,
    backend_route: &str,
    http_status: u16,
    terminal_classification: &str,
) -> Result<(), String> {
    const TERMINAL_KEYS: &[&str] = &[
        "schema_version",
        "provenance",
        "terminal_receipt_id",
        "pre_receipt_id",
        "pre_receipt_sha256",
        "capability_id",
        "host_tool_execution_id",
        "request_digest_profile",
        "bridge_call_id",
        "bridge_challenge_nonce",
        "transport_binding_id",
        "workspace",
        "production_release_revision",
        "production_endpoint_generation",
        "bridge_principal_id",
        "bridge_execution_session_id",
        "bridge_capability_id",
        "bridge_workspace_id",
        "tool_name",
        "outer_request_sha256",
        "forwarded_request_sha256",
        "normalized_request_sha256",
        "conversation_id",
        "browser_target_id",
        "top_frame_id",
        "loader_id",
        "secure_origin",
        "generation_anchor_sha256",
        "extension_binding_sha256",
        "extension_instance_id_sha256",
        "extension_loaded_at_ms",
        "retry_outer_request_sha256",
        "backend_route",
        "http_status",
        "response_body_sha256",
        "terminal_classification",
        "issued_at_unix_ms",
        "host_epoch_binding_sha256",
        "host_mac_sha256",
    ];
    let object = value
        .as_object()
        .ok_or_else(|| "Host terminal receipt must be an object".to_string())?;
    if object.len() != TERMINAL_KEYS.len()
        || object
            .keys()
            .any(|key| !TERMINAL_KEYS.contains(&key.as_str()))
    {
        return Err("Host terminal receipt schema is not exact".into());
    }
    if value
        .get("schema_version")
        .and_then(serde_json::Value::as_u64)
        != Some(1)
    {
        return Err("Host terminal receipt schema_version mismatch".into());
    }
    required_value_text(value, "terminal_receipt_id", "htr_")?;
    require_exact_text(value, "pre_receipt_id", pre_receipt_id)?;
    require_exact_text(
        value,
        "pre_receipt_sha256",
        &sha256_bytes(&canonical_json_bytes(pre_receipt)?),
    )?;
    require_exact_text(value, "capability_id", capability_id)?;
    require_exact_text(value, "host_tool_execution_id", execution_id)?;
    require_exact_text(value, "retry_outer_request_sha256", retry_outer_sha)?;
    require_exact_text(value, "backend_route", backend_route)?;
    if value.get("http_status").and_then(serde_json::Value::as_u64) != Some(u64::from(http_status))
    {
        return Err("Host terminal receipt http_status mismatch".into());
    }
    require_exact_text(value, "terminal_classification", terminal_classification)?;
    let issued_at = value
        .get("issued_at_unix_ms")
        .and_then(serde_json::Value::as_u64)
        .filter(|value| *value > 0)
        .ok_or_else(|| "Host terminal receipt issued_at_unix_ms is invalid".to_string())?;
    let pre_issued_at = pre_receipt
        .get("issued_at_unix_ms")
        .and_then(serde_json::Value::as_u64)
        .filter(|value| *value > 0)
        .ok_or_else(|| "Host pre receipt issued_at_unix_ms is invalid".to_string())?;
    if issued_at < pre_issued_at {
        return Err("Host terminal receipt timestamp precedes pre receipt".into());
    }
    let pre_epoch = pre_receipt
        .get("host_epoch_binding_sha256")
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| "Host pre receipt epoch binding is missing".to_string())?;
    digest_text(pre_epoch, "host_epoch_binding_sha256")?;
    require_exact_text(value, "host_epoch_binding_sha256", pre_epoch)?;
    for name in [
        "conversation_id",
        "browser_target_id",
        "top_frame_id",
        "loader_id",
        "secure_origin",
        "generation_anchor_sha256",
        "extension_binding_sha256",
        "extension_instance_id_sha256",
        "extension_loaded_at_ms",
    ] {
        if value.get(name) != pre_receipt.get(name) {
            return Err(format!("Host terminal receipt {name} mismatch"));
        }
    }
    Ok(())
}

async fn call_private_ndjson(
    path: &Path,
    mut request: serde_json::Value,
) -> Result<serde_json::Value, String> {
    let request_id = format!("hr_{}", Uuid::new_v4());
    request["request_id"] = serde_json::Value::String(request_id.clone());
    let metadata = std::fs::symlink_metadata(path)
        .map_err(|error| format!("private receipt socket inspect failed: {error}"))?;
    if metadata.file_type().is_symlink()
        || !metadata.file_type().is_socket()
        || metadata.uid() != current_euid()
        || metadata.permissions().mode() & 0o777 != 0o600
    {
        return Err("private receipt socket must be an owned 0600 Unix socket".into());
    }
    let mut bytes = serde_json::to_vec(&request)
        .map_err(|error| format!("private receipt request serialization failed: {error}"))?;
    bytes.push(b'\n');
    if bytes.len() > 64 * 1024 {
        return Err("private receipt request exceeds 64 KiB".into());
    }
    tokio::time::timeout(Duration::from_secs(5), async {
        let mut stream = tokio::net::UnixStream::connect(path)
            .await
            .map_err(|error| format!("private receipt socket connect failed: {error}"))?;
        stream
            .write_all(&bytes)
            .await
            .map_err(|error| format!("private receipt write failed: {error}"))?;
        stream
            .flush()
            .await
            .map_err(|error| format!("private receipt flush failed: {error}"))?;
        let mut reader = TokioBufReader::new(stream).take((64 * 1024 + 1) as u64);
        let mut response = Vec::new();
        reader
            .read_until(b'\n', &mut response)
            .await
            .map_err(|error| format!("private receipt read failed: {error}"))?;
        if response.is_empty() || response.len() > 64 * 1024 || !response.ends_with(b"\n") {
            return Err("private receipt response framing is invalid".into());
        }
        let value: serde_json::Value = serde_json::from_slice(&response[..response.len() - 1])
            .map_err(|error| format!("private receipt response parse failed: {error}"))?;
        if value.get("request_id").and_then(serde_json::Value::as_str) != Some(request_id.as_str())
        {
            return Err("private receipt response request_id mismatch".into());
        }
        if value.get("ok").and_then(serde_json::Value::as_bool) != Some(true) {
            return Err(value
                .get("error")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("private receipt operation failed")
                .to_string());
        }
        value
            .get("result")
            .cloned()
            .filter(serde_json::Value::is_object)
            .ok_or_else(|| "private receipt result is invalid".to_string())
    })
    .await
    .map_err(|_| "private receipt operation exceeded five-second deadline".to_string())?
}

struct StrictJsonParser<'a> {
    bytes: &'a [u8],
    offset: usize,
    safe_integers_only: bool,
}

impl<'a> StrictJsonParser<'a> {
    fn new(bytes: &'a [u8]) -> Self {
        Self {
            bytes,
            offset: 0,
            safe_integers_only: true,
        }
    }

    fn backend_response(bytes: &'a [u8]) -> Self {
        Self {
            bytes,
            offset: 0,
            safe_integers_only: false,
        }
    }

    fn parse(mut self) -> Result<serde_json::Value, String> {
        let value = self.value(0)?;
        self.whitespace();
        if self.offset != self.bytes.len() {
            return Err("protected JSON has trailing bytes".into());
        }
        Ok(value)
    }

    fn value(&mut self, depth: usize) -> Result<serde_json::Value, String> {
        if depth > MAX_PROTECTED_JSON_DEPTH {
            return Err("protected JSON exceeds depth limit".into());
        }
        self.whitespace();
        match self.peek() {
            Some(b'{') => self.object(depth + 1),
            Some(b'[') => self.array(depth + 1),
            Some(b'"') => Ok(serde_json::Value::String(self.string()?)),
            Some(b't') => {
                self.literal(b"true")?;
                Ok(serde_json::Value::Bool(true))
            }
            Some(b'f') => {
                self.literal(b"false")?;
                Ok(serde_json::Value::Bool(false))
            }
            Some(b'n') => {
                self.literal(b"null")?;
                Ok(serde_json::Value::Null)
            }
            Some(b'-' | b'0'..=b'9') => self.number(),
            _ => Err("protected JSON value is invalid".into()),
        }
    }

    fn object(&mut self, depth: usize) -> Result<serde_json::Value, String> {
        self.take(b'{')?;
        self.whitespace();
        let mut map = serde_json::Map::new();
        let mut keys = HashSet::new();
        if self.peek() == Some(b'}') {
            self.offset += 1;
            return Ok(serde_json::Value::Object(map));
        }
        loop {
            self.whitespace();
            if self.peek() != Some(b'"') {
                return Err("protected JSON object key is invalid".into());
            }
            let key = self.string()?;
            if !keys.insert(key.clone()) {
                return Err(format!("protected JSON duplicate key: {key}"));
            }
            self.whitespace();
            self.take(b':')?;
            let value = self.value(depth)?;
            map.insert(key, value);
            self.whitespace();
            match self.peek() {
                Some(b',') => self.offset += 1,
                Some(b'}') => {
                    self.offset += 1;
                    break;
                }
                _ => return Err("protected JSON object separator is invalid".into()),
            }
        }
        Ok(serde_json::Value::Object(map))
    }

    fn array(&mut self, depth: usize) -> Result<serde_json::Value, String> {
        self.take(b'[')?;
        self.whitespace();
        let mut values = Vec::new();
        if self.peek() == Some(b']') {
            self.offset += 1;
            return Ok(serde_json::Value::Array(values));
        }
        loop {
            values.push(self.value(depth)?);
            self.whitespace();
            match self.peek() {
                Some(b',') => self.offset += 1,
                Some(b']') => {
                    self.offset += 1;
                    break;
                }
                _ => return Err("protected JSON array separator is invalid".into()),
            }
        }
        Ok(serde_json::Value::Array(values))
    }

    fn string(&mut self) -> Result<String, String> {
        let start = self.offset;
        self.take(b'"')?;
        let mut escaped = false;
        while let Some(byte) = self.peek() {
            self.offset += 1;
            if escaped {
                escaped = false;
                continue;
            }
            match byte {
                b'\\' => escaped = true,
                b'"' => {
                    return serde_json::from_slice::<String>(&self.bytes[start..self.offset])
                        .map_err(|error| format!("protected JSON string is invalid: {error}"));
                }
                0x00..=0x1f => return Err("protected JSON string has a control byte".into()),
                _ => {}
            }
        }
        Err("protected JSON string is unterminated".into())
    }

    fn number(&mut self) -> Result<serde_json::Value, String> {
        let start = self.offset;
        if self.peek() == Some(b'-') {
            self.offset += 1;
        }
        match self.peek() {
            Some(b'0') => {
                self.offset += 1;
                if self.peek().is_some_and(|byte| byte.is_ascii_digit()) {
                    return Err("protected JSON number has a leading zero".into());
                }
            }
            Some(b'1'..=b'9') => {
                while self.peek().is_some_and(|byte| byte.is_ascii_digit()) {
                    self.offset += 1;
                }
            }
            _ => return Err("protected JSON number is invalid".into()),
        }
        if self.safe_integers_only && matches!(self.peek(), Some(b'.' | b'e' | b'E')) {
            return Err("protected JSON only supports safe integers".into());
        }
        let text = std::str::from_utf8(&self.bytes[start..self.offset])
            .map_err(|_| "protected JSON number is not UTF-8".to_string())?;
        if !self.safe_integers_only {
            if self.peek() == Some(b'.') {
                self.offset += 1;
                let fraction_start = self.offset;
                while self.peek().is_some_and(|byte| byte.is_ascii_digit()) {
                    self.offset += 1;
                }
                if self.offset == fraction_start {
                    return Err("backend JSON number fraction is invalid".into());
                }
            }
            if matches!(self.peek(), Some(b'e' | b'E')) {
                self.offset += 1;
                if matches!(self.peek(), Some(b'+' | b'-')) {
                    self.offset += 1;
                }
                let exponent_start = self.offset;
                while self.peek().is_some_and(|byte| byte.is_ascii_digit()) {
                    self.offset += 1;
                }
                if self.offset == exponent_start {
                    return Err("backend JSON number exponent is invalid".into());
                }
            }
            return serde_json::from_slice(&self.bytes[start..self.offset])
                .map_err(|error| format!("backend JSON number is invalid: {error}"));
        }
        let number = text
            .parse::<i64>()
            .map_err(|_| "protected JSON integer is out of range".to_string())?;
        if number.unsigned_abs() > 9_007_199_254_740_991 {
            return Err("protected JSON integer is not safe".into());
        }
        Ok(serde_json::Value::Number(number.into()))
    }

    fn literal(&mut self, literal: &[u8]) -> Result<(), String> {
        if self.bytes.get(self.offset..self.offset + literal.len()) != Some(literal) {
            return Err("protected JSON literal is invalid".into());
        }
        self.offset += literal.len();
        Ok(())
    }

    fn whitespace(&mut self) {
        while self
            .peek()
            .is_some_and(|byte| matches!(byte, b' ' | b'\n' | b'\r' | b'\t'))
        {
            self.offset += 1;
        }
    }

    fn take(&mut self, byte: u8) -> Result<(), String> {
        if self.peek() != Some(byte) {
            return Err("protected JSON token is invalid".into());
        }
        self.offset += 1;
        Ok(())
    }

    fn peek(&self) -> Option<u8> {
        self.bytes.get(self.offset).copied()
    }
}

fn canonical_json_bytes(value: &serde_json::Value) -> Result<Vec<u8>, String> {
    fn write(value: &serde_json::Value, output: &mut String) -> Result<(), String> {
        match value {
            serde_json::Value::Null => output.push_str("null"),
            serde_json::Value::Bool(value) => {
                output.push_str(if *value { "true" } else { "false" })
            }
            serde_json::Value::Number(value) => {
                if !(value.as_i64().is_some() || value.as_u64().is_some()) {
                    return Err("canonical profile rejects non-integer numbers".into());
                }
                output.push_str(&value.to_string());
            }
            serde_json::Value::String(value) => output.push_str(
                &serde_json::to_string(value)
                    .map_err(|error| format!("canonical string failed: {error}"))?,
            ),
            serde_json::Value::Array(values) => {
                output.push('[');
                for (index, value) in values.iter().enumerate() {
                    if index > 0 {
                        output.push(',');
                    }
                    write(value, output)?;
                }
                output.push(']');
            }
            serde_json::Value::Object(values) => {
                output.push('{');
                let mut keys = values.keys().collect::<Vec<_>>();
                keys.sort();
                for (index, key) in keys.into_iter().enumerate() {
                    if index > 0 {
                        output.push(',');
                    }
                    output.push_str(
                        &serde_json::to_string(key)
                            .map_err(|error| format!("canonical key failed: {error}"))?,
                    );
                    output.push(':');
                    write(&values[key], output)?;
                }
                output.push('}');
            }
        }
        Ok(())
    }
    let mut output = String::new();
    write(value, &mut output)?;
    Ok(output.into_bytes())
}

fn sha256_bytes(value: &[u8]) -> String {
    Sha256::digest(value)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn digest_text(value: &str, name: &str) -> Result<String, String> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(format!("{name} must be a lowercase SHA-256 digest"));
    }
    Ok(value.to_string())
}

fn current_euid() -> u32 {
    unsafe { libc::geteuid() as u32 }
}

fn write_private_atomic(path: &Path, bytes: &[u8], label: &str) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| format!("{label} path has no parent"))?;
    let metadata = std::fs::symlink_metadata(parent)
        .map_err(|error| format!("{label} parent inspect failed: {error}"))?;
    if metadata.file_type().is_symlink()
        || !metadata.is_dir()
        || metadata.uid() != current_euid()
        || metadata.permissions().mode() & 0o077 != 0
    {
        return Err(format!("{label} parent must be an owned private directory"));
    }
    if path.exists() {
        let metadata = std::fs::symlink_metadata(path)
            .map_err(|error| format!("{label} inspect failed: {error}"))?;
        if metadata.file_type().is_symlink()
            || !metadata.is_file()
            || metadata.uid() != current_euid()
            || metadata.permissions().mode() & 0o077 != 0
        {
            return Err(format!(
                "{label} target must be an owned private regular file"
            ));
        }
    }
    let temporary = parent.join(format!(
        ".{}.{}.tmp",
        path.file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("journal"),
        Uuid::new_v4()
    ));
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&temporary)
        .map_err(|error| format!("{label} temp open failed: {error}"))?;
    let result = (|| {
        file.write_all(bytes)
            .map_err(|error| format!("{label} write failed: {error}"))?;
        file.write_all(b"\n")
            .map_err(|error| format!("{label} newline failed: {error}"))?;
        file.sync_all()
            .map_err(|error| format!("{label} fsync failed: {error}"))?;
        std::fs::rename(&temporary, path)
            .map_err(|error| format!("{label} atomic replace failed: {error}"))?;
        File::open(parent)
            .and_then(|dir| dir.sync_all())
            .map_err(|error| format!("{label} parent fsync failed: {error}"))
    })();
    if temporary.exists() {
        let _ = std::fs::remove_file(&temporary);
    }
    result
}

async fn send_to_backend_buffered(
    state: &BridgeIngress,
    target: &BackendTarget,
    incoming: &HeaderMap,
    proof_headers: &HashMap<String, String>,
    body: Bytes,
    timeout: Duration,
) -> Result<BufferedBackendResponse, String> {
    let mut request = state
        .inner
        .client
        .post(target.url.clone())
        .body(body.to_vec())
        .timeout(timeout);
    for name in FORWARDED_HEADERS {
        if let Some(value) = incoming.get(*name).and_then(|value| value.to_str().ok()) {
            if *name != "mcp-session-id" {
                request = request.header(*name, value);
            }
        }
    }
    for (name, value) in proof_headers {
        request = request.header(name, value);
    }
    let backend = request
        .header("x-lab-backend-key", &target.key)
        .send()
        .await
        .map_err(|error| format!("backend request failed: {error}"))?;
    let status = StatusCode::from_u16(backend.status().as_u16())
        .map_err(|error| format!("invalid backend status: {error}"))?;
    let content_type = backend
        .headers()
        .get(header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .map(str::to_string);
    let bytes = read_protected_backend_response(backend).await?;
    Ok(BufferedBackendResponse {
        status,
        content_type,
        bytes,
    })
}

async fn read_protected_backend_response(mut backend: reqwest::Response) -> Result<Bytes, String> {
    if backend
        .content_length()
        .is_some_and(|length| length > MAX_PROTECTED_RESPONSE_BYTES as u64)
    {
        return Err("protected backend response exceeds 64 KiB".into());
    }
    let mut bytes = Vec::new();
    while let Some(chunk) = backend
        .chunk()
        .await
        .map_err(|error| format!("backend response read failed: {error}"))?
    {
        let length = bytes
            .len()
            .checked_add(chunk.len())
            .ok_or_else(|| "protected backend response exceeds 64 KiB".to_string())?;
        if length > MAX_PROTECTED_RESPONSE_BYTES {
            return Err("protected backend response exceeds 64 KiB".into());
        }
        bytes.extend_from_slice(&chunk);
    }
    Ok(Bytes::from(bytes))
}

fn validate_backend_response(bytes: &[u8], expected: &str) -> Result<(), String> {
    let value = StrictJsonParser::backend_response(bytes)
        .parse()
        .map_err(|error| format!("backend response is not strict JSON: {error}"))?;
    let object = value
        .as_object()
        .ok_or_else(|| "backend response must be a JSON-RPC object".to_string())?;
    if object.get("jsonrpc").and_then(serde_json::Value::as_str) != Some("2.0") {
        return Err("backend response jsonrpc version mismatch".into());
    }
    if object
        .keys()
        .any(|key| !["jsonrpc", "id", "result", "error"].contains(&key.as_str()))
    {
        return Err("backend response contains unsupported members".into());
    }
    if object.get("id").and_then(serde_json::Value::as_str) != Some(expected) {
        return Err("backend response ID mismatch".into());
    }
    let has_result = object.contains_key("result");
    let has_error = object.contains_key("error");
    if has_result == has_error {
        return Err("backend response must contain exactly one of result or error".into());
    }
    if let Some(error) = object.get("error") {
        let error = error
            .as_object()
            .ok_or_else(|| "backend JSON-RPC error must be an object".to_string())?;
        if error
            .keys()
            .any(|key| !["code", "message", "data"].contains(&key.as_str()))
            || error
                .get("code")
                .and_then(serde_json::Value::as_i64)
                .is_none()
            || error
                .get("message")
                .and_then(serde_json::Value::as_str)
                .is_none()
        {
            return Err("backend JSON-RPC error object is invalid".into());
        }
    }
    Ok(())
}

fn map_backend_response_id(
    backend: BufferedBackendResponse,
    caller_id: &serde_json::Value,
) -> Result<Response<Body>, String> {
    let mut value: serde_json::Value = serde_json::from_slice(&backend.bytes)
        .map_err(|error| format!("buffered backend response is not JSON: {error}"))?;
    value["id"] = caller_id.clone();
    let bytes = serde_json::to_vec(&value)
        .map_err(|error| format!("caller response serialization failed: {error}"))?;
    let mut response = Response::builder().status(backend.status);
    if let Some(content_type) = backend.content_type {
        response = response.header(header::CONTENT_TYPE, content_type);
    }
    response
        .body(Body::from(bytes))
        .map_err(|error| format!("caller response build failed: {error}"))
}

async fn decorate_tools_list_response(response: Response<Body>) -> Result<Response<Body>, String> {
    if response.status() != StatusCode::OK {
        return Ok(response);
    }
    let status = response.status();
    let content_type = response.headers().get(header::CONTENT_TYPE).cloned();
    let bytes = axum::body::to_bytes(response.into_body(), usize::MAX)
        .await
        .map_err(|error| format!("tools/list response read failed: {error}"))?;
    let mut value: serde_json::Value = match serde_json::from_slice(&bytes) {
        Ok(value) => value,
        Err(_) => return rebuild_response(status, content_type, bytes),
    };
    let Some(tools) = value
        .get_mut("result")
        .and_then(|value| value.get_mut("tools"))
        .and_then(serde_json::Value::as_array_mut)
    else {
        return rebuild_response(status, content_type, bytes);
    };
    if let Some(tool) = tools
        .iter_mut()
        .find(|tool| tool.get("name").and_then(serde_json::Value::as_str) == Some("run_command"))
    {
        let schema = tool
            .get_mut("inputSchema")
            .and_then(serde_json::Value::as_object_mut)
            .ok_or_else(|| "run_command inputSchema must be an object".to_string())?;
        let properties = schema
            .entry("properties")
            .or_insert_with(|| serde_json::json!({}))
            .as_object_mut()
            .ok_or_else(|| "run_command inputSchema.properties must be an object".to_string())?;
        if properties.contains_key("host_invocation_capability") {
            return Err(
                "run_command schema collides with reserved host_invocation_capability".into(),
            );
        }
        properties.insert("host_invocation_capability".into(), serde_json::json!({
            "type":"object",
            "description":"Host-issued single-use capability returned by the first protected call"
        }));
    }
    let mut builder = Response::builder().status(status);
    if let Some(content_type) = content_type {
        builder = builder.header(header::CONTENT_TYPE, content_type);
    }
    builder
        .body(Body::from(serde_json::to_vec(&value).map_err(|error| {
            format!("tools/list serialization failed: {error}")
        })?))
        .map_err(|error| format!("tools/list response build failed: {error}"))
}

fn rebuild_response(
    status: StatusCode,
    content_type: Option<header::HeaderValue>,
    bytes: Bytes,
) -> Result<Response<Body>, String> {
    let mut builder = Response::builder().status(status);
    if let Some(content_type) = content_type {
        builder = builder.header(header::CONTENT_TYPE, content_type);
    }
    builder
        .body(Body::from(bytes))
        .map_err(|error| format!("response rebuild failed: {error}"))
}

const TUNNEL_MCP_PROTOCOL_VERSION: &str = "2026-07-28";
const GROK_MCP_PROTOCOL_VERSION: &str = "2025-11-25";
const MODERN_BACKEND_MCP_PROTOCOL_VERSION: &str = "2026-07-28";
const INITIALIZE_BACKEND_MCP_PROTOCOL_VERSION: &str = "2025-06-18";

fn jsonrpc_method(body: &Bytes) -> Option<String> {
    serde_json::from_slice::<serde_json::Value>(body)
        .ok()
        .and_then(|value| {
            value
                .get("method")
                .and_then(serde_json::Value::as_str)
                .map(str::to_string)
        })
}

fn backend_protocol_for_method(method: Option<&str>) -> &'static str {
    match method {
        Some("initialize") | Some("notifications/initialized") => {
            INITIALIZE_BACKEND_MCP_PROTOCOL_VERSION
        }
        _ => MODERN_BACKEND_MCP_PROTOCOL_VERSION,
    }
}

fn incoming_client_protocol(headers: &HeaderMap) -> Option<&str> {
    headers
        .get("mcp-protocol-version")
        .and_then(|value| value.to_str().ok())
}

fn rewrite_protocol_versions(value: &mut serde_json::Value, from: &str, to: &str) -> bool {
    let mut changed = false;
    match value {
        serde_json::Value::Object(map) => {
            for key in ["protocolVersion", "io.modelcontextprotocol/protocolVersion"] {
                if let Some(serde_json::Value::String(existing)) = map.get_mut(key) {
                    if existing == from {
                        *existing = to.to_string();
                        changed = true;
                    }
                }
            }
            for nested in map.values_mut() {
                if rewrite_protocol_versions(nested, from, to) {
                    changed = true;
                }
            }
        }
        serde_json::Value::Array(items) => {
            for nested in items {
                if rewrite_protocol_versions(nested, from, to) {
                    changed = true;
                }
            }
        }
        _ => {}
    }
    changed
}

fn rewrite_protocol_for_backend(body: Bytes, backend_protocol: &str) -> (Bytes, bool) {
    let Ok(mut parsed) = serde_json::from_slice::<serde_json::Value>(&body) else {
        return (body, false);
    };
    let mut changed = false;
    for from in [
        GROK_MCP_PROTOCOL_VERSION,
        TUNNEL_MCP_PROTOCOL_VERSION,
        INITIALIZE_BACKEND_MCP_PROTOCOL_VERSION,
        MODERN_BACKEND_MCP_PROTOCOL_VERSION,
    ] {
        if from != backend_protocol {
            changed |= rewrite_protocol_versions(&mut parsed, from, backend_protocol);
        }
    }
    if !changed {
        return (body, false);
    }
    match serde_json::to_vec(&parsed) {
        Ok(bytes) => (Bytes::from(bytes), true),
        Err(_) => (body, false),
    }
}

fn restore_client_protocol_version(bytes: Bytes, client_version: &str) -> Bytes {
    let Ok(mut parsed) = serde_json::from_slice::<serde_json::Value>(&bytes) else {
        return bytes;
    };
    let Some(result) = parsed.get_mut("result") else {
        return bytes;
    };
    let modern = rewrite_protocol_versions(result, MODERN_BACKEND_MCP_PROTOCOL_VERSION, client_version);
    let initialize =
        rewrite_protocol_versions(result, INITIALIZE_BACKEND_MCP_PROTOCOL_VERSION, client_version);
    if !modern && !initialize {
        return bytes;
    }
    serde_json::to_vec(&parsed)
        .map(Bytes::from)
        .unwrap_or(bytes)
}

async fn forward_to_backend(
    state: &BridgeIngress,
    target: &BackendTarget,
    incoming: &HeaderMap,
    proof_headers: &HashMap<String, String>,
    body: Bytes,
) -> Result<Response<Body>, String> {
    let incoming_version = incoming_client_protocol(incoming);
    let method = jsonrpc_method(&body).or_else(|| {
        incoming
            .get("mcp-method")
            .and_then(|value| value.to_str().ok())
            .map(str::to_string)
    });
    let backend_protocol = backend_protocol_for_method(method.as_deref());
    let incoming_tunnel_protocol = matches!(
        incoming_version,
        Some(TUNNEL_MCP_PROTOCOL_VERSION | GROK_MCP_PROTOCOL_VERSION)
    );
    let grok_client = incoming_version == Some(GROK_MCP_PROTOCOL_VERSION);
    // Body translation already happened before the request was signed.
    // Do not rewrite again: Process Runtime checks x-lab-body-sha256
    // against the exact bytes received.
    let restore_to_grok = grok_client;
    let mut request = state
        .inner
        .client
        .post(target.url.clone())
        .body(body.to_vec());
    let mut forwarded_protocol_header = false;
    for name in FORWARDED_HEADERS {
        if let Some(value) = incoming.get(*name).and_then(|value| value.to_str().ok()) {
            let value = if *name == "mcp-protocol-version" {
                forwarded_protocol_header = true;
                backend_protocol
            } else {
                value
            };
            request = request.header(*name, value);
        }
    }
    if !forwarded_protocol_header {
        request = request.header("mcp-protocol-version", backend_protocol);
    }
    for (name, value) in proof_headers {
        request = request.header(name, value);
    }
    request = request.header("x-lab-backend-key", &target.key);
    let backend = request
        .send()
        .await
        .map_err(|error| format!("backend request failed: {error}"))?;
    let status = StatusCode::from_u16(backend.status().as_u16())
        .map_err(|error| format!("invalid backend status: {error}"))?;
    let content_type = backend
        .headers()
        .get(header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .map(str::to_string);
    let mut bytes = backend
        .bytes()
        .await
        .map_err(|error| format!("backend response read failed: {error}"))?;
    if restore_to_grok && status == StatusCode::OK {
        bytes = restore_client_protocol_version(bytes, GROK_MCP_PROTOCOL_VERSION);
    }
    let mut response = Response::builder().status(status);
    if let Some(content_type) = content_type {
        response = response.header(header::CONTENT_TYPE, content_type);
    }
    if grok_client {
        response = response.header("mcp-protocol-version", GROK_MCP_PROTOCOL_VERSION);
    } else if incoming_tunnel_protocol {
        response = response.header("mcp-protocol-version", TUNNEL_MCP_PROTOCOL_VERSION);
    }
    response
        .body(Body::from(bytes))
        .map_err(|error| format!("backend response build failed: {error}"))
}

async fn forward_merged_tools_list(
    state: &BridgeIngress,
    incoming: &HeaderMap,
    proof_headers: &HashMap<String, String>,
    body: Bytes,
) -> Result<Response<Body>, String> {
    let request_id = serde_json::from_slice::<serde_json::Value>(&body)
        .ok()
        .and_then(|value| value.get("id").cloned())
        .unwrap_or(serde_json::Value::Null);
    let mut responses = vec![(
        "default",
        forward_to_backend(
            state,
            &state.inner.default_backend,
            incoming,
            proof_headers,
            body.clone(),
        )
        .await?,
    )];
    if let Some(host) = state.inner.host_backend.as_ref() {
        responses.push((
            "host",
            forward_to_backend(state, host, incoming, proof_headers, body.clone()).await?,
        ));
    }
    if let Some(computer) = state.inner.computer_backend.as_ref() {
        responses.push((
            "computer",
            forward_to_backend(state, computer, incoming, proof_headers, body.clone()).await?,
        ));
    }
    if let Some(process) = state.inner.process_backend.as_ref() {
        responses.push((
            "process",
            forward_to_backend(state, process, incoming, proof_headers, body.clone()).await?,
        ));
    }
    let request_id = serde_json::from_slice::<serde_json::Value>(&body)
        .ok()
        .and_then(|value| value.get("id").cloned())
        .unwrap_or(serde_json::Value::Null);
    let mut ok_responses = Vec::new();
    for (kind, response) in responses {
        if response.status() == StatusCode::OK {
            ok_responses.push((kind, response));
        }
    }
    let mut iter = ok_responses.into_iter();
    let mut merged = if let Some((kind, first)) = iter.next() {
        let first_bytes = axum::body::to_bytes(first.into_body(), usize::MAX)
            .await
            .map_err(|error| format!("{kind} tools/list response read failed: {error}"))?;
        serde_json::from_slice(&first_bytes).map_err(|error| {
            format!("{kind} tools/list response is not JSON: {error}")
        })?
    } else {
        serde_json::json!({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": { "tools": [] }
        })
    };
    let tools = merged
        .get_mut("result")
        .and_then(|value| value.get_mut("tools"))
        .and_then(serde_json::Value::as_array_mut)
        .ok_or_else(|| "default tools/list response has no result.tools array".to_string())?;
    for (kind, response) in iter {
        let bytes = axum::body::to_bytes(response.into_body(), usize::MAX)
            .await
            .map_err(|error| format!("{kind} tools/list response read failed: {error}"))?;
        let extra: serde_json::Value = serde_json::from_slice(&bytes)
            .map_err(|error| format!("{kind} tools/list response is not JSON: {error}"))?;
        let extra_tools = extra
            .get("result")
            .and_then(|value| value.get("tools"))
            .and_then(serde_json::Value::as_array)
            .ok_or_else(|| format!("{kind} tools/list response has no result.tools array"))?;
        if kind == "host" {
            validate_host_tools_fence(extra_tools, &state.inner.host_release_fence)?;
        }
        if kind == "process" {
            let process_names = extra_tools
                .iter()
                .filter_map(|tool| tool.get("name").and_then(serde_json::Value::as_str))
                .map(str::to_string)
                .collect::<HashSet<_>>();
            tools.retain(|tool| {
                tool.get("name")
                    .and_then(serde_json::Value::as_str)
                    .map(|name| !process_names.contains(name))
                    .unwrap_or(true)
            });
        }
        tools.extend(extra_tools.iter().cloned());
    }
    if state.inner.collaboration.is_some() || state.inner.feishu_admin.is_some() {
        let mut names = tools
            .iter()
            .filter_map(|tool| tool.get("name").and_then(serde_json::Value::as_str))
            .map(str::to_string)
            .collect::<HashSet<_>>();
        if state.inner.collaboration.is_some() {
            for tool in collaboration_tools::feishu_tool_definitions() {
                let name = tool
                    .get("name")
                    .and_then(serde_json::Value::as_str)
                    .ok_or_else(|| "Feishu tool definition has no name".to_string())?;
                if !names.insert(name.to_string()) {
                    return Err(format!("duplicate merged tool name: {name}"));
                }
                tools.push(tool);
            }
        }
        if state.inner.feishu_admin.is_some() {
            for tool in collaboration_tools::feishu_admin_tool_definitions() {
                let name = tool
                    .get("name")
                    .and_then(serde_json::Value::as_str)
                    .ok_or_else(|| "Feishu admin tool definition has no name".to_string())?;
                if !names.insert(name.to_string()) {
                    return Err(format!("duplicate merged tool name: {name}"));
                }
                tools.push(tool);
            }
        }
    }
    let response = Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(merged.to_string()))
        .map_err(|error| format!("merged tools/list response build failed: {error}"))?;
    if state.inner.tool_receipts.is_some() {
        decorate_tools_list_response(response).await
    } else {
        Ok(response)
    }
}

fn validate_host_tools_fence(
    tools: &[serde_json::Value],
    expected: &Option<ProductionReleaseFence>,
) -> Result<(), String> {
    let expected = expected
        .as_ref()
        .ok_or_else(|| "Host production release fence is not configured".to_string())?;
    if tools.len() != 1 {
        return Err(
            "dedicated Host backend tools/list must expose only discover_current_entry".into(),
        );
    }
    let matching: Vec<_> = tools
        .iter()
        .filter(|tool| {
            tool.get("name").and_then(serde_json::Value::as_str) == Some("discover_current_entry")
        })
        .collect();
    if matching.len() != 1 {
        return Err("Host tools/list must expose exactly one discover_current_entry tool".into());
    }
    let fence = matching[0]
        .get("_meta")
        .and_then(|value| value.get("local-agent-bridge/production-fence"))
        .and_then(serde_json::Value::as_object)
        .ok_or_else(|| {
            "Host discover_current_entry production fence metadata is missing".to_string()
        })?;
    validate_host_fence_value(fence, expected)
}

async fn validate_host_call_response(
    response: Response<Body>,
    expected: &Option<ProductionReleaseFence>,
) -> Result<Response<Body>, String> {
    if response.status() != StatusCode::OK {
        return Ok(response);
    }
    let expected = expected
        .as_ref()
        .ok_or_else(|| "Host production release fence is not configured".to_string())?;
    let content_type = response.headers().get(header::CONTENT_TYPE).cloned();
    let bytes = axum::body::to_bytes(response.into_body(), usize::MAX)
        .await
        .map_err(|error| format!("Host tool response read failed: {error}"))?;
    let value: serde_json::Value = serde_json::from_slice(&bytes)
        .map_err(|error| format!("Host tool response is not JSON: {error}"))?;
    let fence = value
        .get("result")
        .and_then(|value| value.get("_meta"))
        .and_then(|value| value.get("local-agent-bridge/production-fence"))
        .and_then(serde_json::Value::as_object)
        .ok_or_else(|| "Host tool response production fence metadata is missing".to_string())?;
    validate_host_fence_value(fence, expected)?;
    let mut builder = Response::builder().status(StatusCode::OK);
    if let Some(content_type) = content_type {
        builder = builder.header(header::CONTENT_TYPE, content_type);
    }
    builder
        .body(Body::from(bytes))
        .map_err(|error| format!("Host tool response rebuild failed: {error}"))
}

fn validate_host_fence_value(
    fence: &serde_json::Map<String, serde_json::Value>,
    expected: &ProductionReleaseFence,
) -> Result<(), String> {
    if fence
        .get("connector_id")
        .and_then(serde_json::Value::as_str)
        != Some(PRODUCTION_CONNECTOR_ID)
        || fence
            .get("contract_version")
            .and_then(serde_json::Value::as_str)
            != Some(expected.contract_version.as_str())
        || fence
            .get("release_revision")
            .and_then(serde_json::Value::as_str)
            != Some(expected.release_revision.as_str())
        || fence
            .get("endpoint_generation")
            .and_then(serde_json::Value::as_u64)
            != Some(expected.endpoint_generation)
        || fence.get("workspace").and_then(serde_json::Value::as_str)
            != Some(expected.workspace.as_str())
        || fence
            .get("tunnel_profile_name")
            .and_then(serde_json::Value::as_str)
            != Some(expected.tunnel_profile_name.as_str())
    {
        return Err("Host production release fence does not match Bridge release fence".into());
    }
    Ok(())
}

fn backend_for_run_command(state: &BridgeIngress) -> &BackendTarget {
    state
        .inner
        .process_backend
        .as_ref()
        .unwrap_or(&state.inner.default_backend)
}

fn validate_backend_url(value: &str) -> Result<Url, String> {
    let url = Url::parse(value).map_err(|error| format!("invalid backend URL: {error}"))?;
    if url.scheme() != "http" {
        return Err("backend URL must use loopback http".into());
    }
    if !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err("backend URL must not contain credentials, query, or fragment".into());
    }
    let host = url
        .host_str()
        .ok_or_else(|| "backend URL host is required".to_string())?;
    let ip = host
        .parse::<IpAddr>()
        .map_err(|_| "backend URL host must be a numeric loopback IP".to_string())?;
    if !ip.is_loopback() {
        return Err("backend URL must be loopback-only".into());
    }
    Ok(url)
}

fn validate_backend_key(value: &str) -> Result<(), String> {
    if value.as_bytes().len() < 32 {
        return Err("backend authentication key must be at least 32 bytes".into());
    }
    Ok(())
}

fn backend_client() -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|error| format!("backend HTTP client creation failed: {error}"))
}

fn valid_ingress_key(headers: &HeaderMap, expected: &[u8; 32]) -> bool {
    let Some(value) = headers
        .get(INGRESS_KEY_HEADER)
        .and_then(|value| value.to_str().ok())
    else {
        return false;
    };
    let digest = Sha256::digest(value.as_bytes());
    digest.as_slice().ct_eq(expected).into()
}

fn header_text<'a>(headers: &'a HeaderMap, name: &str) -> Option<&'a str> {
    headers.get(name).and_then(|value| value.to_str().ok())
}

fn is_reconnectable_discovery_method(method: &str, capability: &Capability) -> bool {
    *capability == Capability::Discovery
        && (matches!(
            method,
            "initialize"
                | "server/discover"
                | "tools/list"
                | "resources/list"
                | "resources/templates/list"
                | "ping"
                | "notifications/initialized"
        ))
}

pub fn required_capability(method: &str, tool_name: &str) -> Result<Capability, String> {
    ToolPolicy::catdesk_default().required_capability(method, tool_name)
}

fn text_response(status: StatusCode, message: &str) -> Response<Body> {
    // Tunnel-client treats text/plain 502 as malformed_json / Connection failed.
    // Keep HTTP status for production tests; always return JSON-RPC.
    let code = if status.is_server_error() {
        -32002
    } else if status == StatusCode::UNAUTHORIZED || status == StatusCode::FORBIDDEN {
        -32001
    } else {
        -32600
    };
    let body = serde_json::json!({
        "jsonrpc": "2.0",
        "id": serde_json::Value::Null,
        "error": {"code": code, "message": message}
    });
    Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(body.to_string()))
        .expect("static response")
}
