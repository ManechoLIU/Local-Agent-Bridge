use axum::body::Body;
use bridge_security_core::{Capability, CapabilityAuthority, CapabilityGrant, ExecutionContext};
use collaboration_adapter::{LarkCliAdminSpec, LarkCliInstallSpec, LarkCliProvider};
use hyper::{server::conn::http1, service::service_fn};
use hyper_util::rt::TokioIo;
use local_tool_bridge::{
    BridgeIngress, PRODUCTION_CAPABILITY_TTL_MS, ProductionReleaseFence, ToolPolicy,
    parse_extra_file_roots, validate_production_release_manifest,
};
use std::{
    env,
    os::fd::AsRawFd,
    os::unix::fs::{FileTypeExt, MetadataExt, PermissionsExt},
    path::{Path, PathBuf},
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tokio::net::{UnixListener, UnixStream};
use tower::ServiceExt;
use uuid::Uuid;

const PRODUCTION_BRIDGE_SOCKET: &str = "/tmp/local-agent-bridge-production/bridge.sock";
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let socket_path = required_env("LAB_BRIDGE_SOCKET")?;
    let backend_url = required_env("LAB_BRIDGE_BACKEND_URL")?;
    let host_backend_url = env::var("LAB_HOST_BACKEND_URL").ok();
    let host_backend_key_file = env::var("LAB_HOST_BACKEND_KEY_FILE").ok();
    let production_socket_identity =
        same_socket_identity(Path::new(&socket_path), Path::new(PRODUCTION_BRIDGE_SOCKET));
    let production_mode = production_socket_identity
        || env::var_os("LAB_PRODUCTION_CONNECTOR_ID").is_some()
        || host_backend_url.is_some()
        || host_backend_key_file.is_some();
    if production_mode && socket_path != PRODUCTION_BRIDGE_SOCKET {
        return Err(format!(
            "production Bridge must use canonical socket {PRODUCTION_BRIDGE_SOCKET}"
        )
        .into());
    }
    let production_fence = match (
        host_backend_url.as_deref(),
        host_backend_key_file.as_deref(),
    ) {
        (Some(_), Some(_)) => Some(required_production_release_fence()?),
        (None, None) if !production_mode => None,
        (None, None) => {
            return Err(
                "production Bridge requires LAB_HOST_BACKEND_URL and LAB_HOST_BACKEND_KEY_FILE"
                    .into(),
            );
        }
        _ => {
            return Err(
                "LAB_HOST_BACKEND_URL and LAB_HOST_BACKEND_KEY_FILE must be configured together"
                    .into(),
            );
        }
    };
    let backend_key =
        read_private_secret_file(&PathBuf::from(required_env("LAB_BRIDGE_BACKEND_KEY_FILE")?))?;
    let state_dir = PathBuf::from(required_env("LAB_BRIDGE_STATE_DIR")?);
    let workspace_id =
        env::var("LAB_BRIDGE_WORKSPACE_ID").unwrap_or_else(|_| "workspace-default".into());
    let principal_id =
        env::var("LAB_BRIDGE_PRINCIPAL_ID").unwrap_or_else(|_| "tunnel-client-local-uds".into());
    let execution_session_id = env::var("LAB_BRIDGE_EXECUTION_SESSION_ID")
        .unwrap_or_else(|_| format!("bridge-session-{}", Uuid::new_v4()));
    let capability_id = env::var("LAB_BRIDGE_CAPABILITY_ID")
        .unwrap_or_else(|_| format!("bridge-cap-{}", Uuid::new_v4()));
    let ttl_ms = match env::var("LAB_BRIDGE_CAPABILITY_TTL_MS") {
        Ok(value) => value
            .parse::<u64>()
            .map_err(|_| "LAB_BRIDGE_CAPABILITY_TTL_MS must be a positive integer")?,
        Err(env::VarError::NotPresent) => 3_600_000,
        Err(error) => {
            return Err(format!("LAB_BRIDGE_CAPABILITY_TTL_MS is unreadable: {error}").into());
        }
    };
    let renew_before_ms = match env::var("LAB_BRIDGE_CAPABILITY_RENEW_BEFORE_MS") {
        Ok(value) => Some(
            value
                .parse::<u64>()
                .map_err(|_| "LAB_BRIDGE_CAPABILITY_RENEW_BEFORE_MS must be a positive integer")?,
        ),
        Err(env::VarError::NotPresent) if production_mode => {
            return Err("production Bridge requires LAB_BRIDGE_CAPABILITY_RENEW_BEFORE_MS".into());
        }
        Err(env::VarError::NotPresent) => None,
        Err(error) => {
            return Err(
                format!("LAB_BRIDGE_CAPABILITY_RENEW_BEFORE_MS is unreadable: {error}").into(),
            );
        }
    };
    if ttl_ms == 0 || (production_mode && ttl_ms > PRODUCTION_CAPABILITY_TTL_MS) {
        return Err(format!(
            "production capability ttl must be positive and at most {PRODUCTION_CAPABILITY_TTL_MS}ms"
        )
        .into());
    }
    if let Some(renew_before_ms) = renew_before_ms {
        if renew_before_ms == 0 || renew_before_ms >= ttl_ms {
            return Err(
                "LAB_BRIDGE_CAPABILITY_RENEW_BEFORE_MS must be positive and less than LAB_BRIDGE_CAPABILITY_TTL_MS"
                    .into(),
            );
        }
    }
    if let Some(fence) = &production_fence {
        if ttl_ms != fence.capability_ttl_ms
            || renew_before_ms != Some(fence.capability_renew_before_ms)
        {
            return Err(
                "production capability lease environment does not match immutable release manifest"
                    .into(),
            );
        }
    }

    let capabilities = capability_set_from_env()?;
    let provider_admin_enabled = capabilities.contains(&Capability::ProviderAdmin);
    let policy = ToolPolicy::catdesk_default();
    let context = ExecutionContext {
        principal_id: principal_id.clone(),
        execution_session_id: execution_session_id.clone(),
        capability_id: capability_id.clone(),
        workspace_id: workspace_id.clone(),
    };
    let grant = CapabilityGrant {
        principal_id,
        execution_session_id,
        capability_id: capability_id.clone(),
        workspace_id,
        capabilities,
    };

    let now_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)?
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX);
    let mut authority = CapabilityAuthority::load_or_create_private(&state_dir)?;
    if env_flag("LAB_BRIDGE_ROTATE_MASTER_KEY") {
        let expected = required_env("LAB_BRIDGE_EXPECTED_KEY_GENERATION")?
            .parse::<u64>()
            .map_err(|_| "LAB_BRIDGE_EXPECTED_KEY_GENERATION must be an integer")?;
        authority.rotate_master_key_private(&state_dir, expected)?;
    }
    let issued = if let Some(existing) = authority.lease(&capability_id) {
        if existing.grant != grant {
            return Err(format!(
                "existing capability {capability_id} grant does not match current configured principal/session/workspace/capabilities; rotate capability identity explicitly"
            )
            .into());
        }
        match authority.issued_capability_at(&capability_id, now_ms) {
            Ok(issued) => issued,
            Err(_) if renew_before_ms.is_some() && existing.expires_at_ms < now_ms => {
                // The Bridge renews under its authority mutex before signing the
                // first request, so no expired proof is ever accepted.
                authority.issued_capability(&capability_id)?
            }
            Err(error) => {
                return Err(format!(
                    "existing capability {capability_id} is not reusable: {error}; rotate the capability/session identity explicitly"
                )
                .into());
            }
        }
    } else {
        authority.issue(grant, now_ms, ttl_ms, 1)?
    };
    authority.save_private(&state_dir)?;

    // UDS ownership/permissions are the local ingress authentication boundary.
    // Capability replay state is persisted before any backend side effect.
    let mut ingress = BridgeIngress::new_trusted_unix_socket(
        authority,
        issued,
        context,
        &backend_url,
        &backend_key,
        policy,
        state_dir.clone(),
    )?;
    if let Some(renew_before_ms) = renew_before_ms {
        ingress = ingress.with_capability_renewal(ttl_ms, renew_before_ms)?;
    }
    match (
        host_backend_url.as_deref(),
        host_backend_key_file.as_deref(),
    ) {
        (Some(url), Some(key_file)) => {
            let host_key = read_private_secret_file(Path::new(key_file))?;
            ingress = ingress.with_host_backend(
                url,
                &host_key,
                production_fence
                    .clone()
                    .ok_or("production Host backend is missing its release fence")?,
            )?;
        }
        (None, None) => {}
        _ => unreachable!("Host backend pair was validated before release fence loading"),
    }
    let host_receipt_socket = env::var("LAB_HOST_ADAPTER_SOCKET").ok();
    let runtime_hook_socket = env::var("LAB_RUNTIME_HOST_TOOL_HOOK_SOCKET").ok();
    match (
        host_receipt_socket.as_deref(),
        runtime_hook_socket.as_deref(),
    ) {
        (Some(host_socket), runtime_socket) => {
            ingress = ingress.with_tool_receipt_chain(
                Path::new(host_socket),
                runtime_socket.map(Path::new),
                production_fence
                    .clone()
                    .ok_or("tool receipt chain requires the production release fence")?,
            )?;
        }
        (None, Some(_)) => {
            return Err(
                "LAB_RUNTIME_HOST_TOOL_HOOK_SOCKET requires LAB_HOST_ADAPTER_SOCKET".into(),
            );
        }
        (None, None) => {}
    }
    let computer_backend_url = env::var("LAB_COMPUTER_BACKEND_URL").ok();
    let computer_backend_key_file = env::var("LAB_COMPUTER_BACKEND_KEY_FILE").ok();
    match (
        computer_backend_url.as_deref(),
        computer_backend_key_file.as_deref(),
    ) {
        (Some(url), Some(key_file)) => {
            let computer_key = read_private_secret_file(Path::new(key_file))?;
            ingress = ingress.with_computer_backend(url, &computer_key)?;
        }
        (None, None) => {}
        _ => {
            return Err(
                "LAB_COMPUTER_BACKEND_URL and LAB_COMPUTER_BACKEND_KEY_FILE must be configured together"
                    .into(),
            );
        }
    }
    let process_backend_url = env::var("LAB_PROCESS_BACKEND_URL").ok();
    let process_backend_key_file = env::var("LAB_PROCESS_BACKEND_KEY_FILE").ok();
    match (
        process_backend_url.as_deref(),
        process_backend_key_file.as_deref(),
    ) {
        (Some(url), Some(key_file)) => {
            let process_key = read_private_secret_file(Path::new(key_file))?;
            ingress = ingress.with_process_backend(url, &process_key)?;
        }
        (None, None) => {}
        _ => {
            return Err(
                "LAB_PROCESS_BACKEND_URL and LAB_PROCESS_BACKEND_KEY_FILE must be configured together"
                    .into(),
            );
        }
    }
    if provider_admin_enabled {
        let home = PathBuf::from(
            env::var("HOME").map_err(|_| "HOME is required for Feishu admin provider state")?,
        );
        let provider_root = feishu_provider_root(&home);
        let (runtime_home, config_dir) = feishu_private_paths(&home);
        let npm_program = env::var("LAB_FEISHU_NPM")
            .unwrap_or_else(|_| "/opt/homebrew/bin/npm".into());
        let install = LarkCliInstallSpec::new(
            Path::new(&npm_program),
            provider_root.join("npm"),
        )
        .map_err(|error| format!("Feishu installer configuration rejected: {}", error.message))?;
        let admin = LarkCliAdminSpec::new(install, runtime_home, config_dir)
            .map_err(|error| format!("Feishu admin configuration rejected: {}", error.message))?;
        ingress = ingress.with_feishu_admin(
            admin,
            Duration::from_secs(15 * 60),
            1024 * 1024,
        )?;
    }

    let feishu_settings = feishu_settings_from_values(
        env::var("LAB_FEISHU_CLI").ok(),
        env::var("LAB_FEISHU_PROFILE").ok(),
        env::var("LAB_FEISHU_TIMEOUT_MS").ok(),
        env::var("LAB_FEISHU_OUTPUT_LIMIT").ok(),
    )?;
    if let Some(settings) = &feishu_settings {
        let home = PathBuf::from(
            env::var("HOME").map_err(|_| "HOME is required for Feishu private provider state")?,
        );
        let (runtime_home, config_dir) = feishu_private_paths(&home);
        let provider = LarkCliProvider::new_isolated(
            Path::new(&settings.cli_path),
            settings.profile.clone(),
            runtime_home,
            config_dir,
        )
        .map_err(|error| format!("Feishu provider configuration rejected: {}", error.message))?;
        ingress = ingress.with_feishu_provider(
            provider,
            Duration::from_millis(settings.timeout_ms),
            settings.output_limit,
        )?;
    }
    let extra_file_roots = extra_file_roots_from_env(production_mode)?;
    ingress = ingress.with_extra_file_roots(extra_file_roots)?;
    ingress = ingress.with_extra_file_production(production_mode)?;
    let app = ingress.router();

    let socket = PathBuf::from(&socket_path);
    let parent = socket
        .parent()
        .ok_or("LAB_BRIDGE_SOCKET must have a parent directory")?;
    prepare_private_socket_parent(parent)?;
    if socket.exists() {
        let metadata = std::fs::symlink_metadata(&socket)?;
        let current_uid = unsafe { libc::geteuid() };
        if metadata.file_type().is_symlink()
            || !metadata.file_type().is_socket()
            || metadata.uid() != current_uid
        {
            return Err("existing bridge socket path is not an owned Unix socket".into());
        }
        std::fs::remove_file(&socket)?;
    }
    let listener = UnixListener::bind(&socket)?;
    set_socket_private(&socket)?;
    eprintln!(
        "local-tool-bridge ready socket={} backend={} host_backend={} computer_backend={} process_backend={} feishu_provider={} host_receipts={} runtime_hook={} capability_id={} production_release={} production_generation={}",
        socket.display(),
        backend_url,
        host_backend_url.as_deref().unwrap_or("disabled"),
        computer_backend_url.as_deref().unwrap_or("disabled"),
        process_backend_url.as_deref().unwrap_or("disabled"),
        if feishu_settings.is_some() { "enabled" } else { "disabled" },
        host_receipt_socket.as_deref().unwrap_or("disabled"),
        runtime_hook_socket.as_deref().unwrap_or("disabled"),
        capability_id,
        production_fence
            .as_ref()
            .map(|fence| fence.release_revision.as_str())
            .unwrap_or("disabled"),
        production_fence
            .as_ref()
            .map(|fence| fence.endpoint_generation.to_string())
            .unwrap_or_else(|| "disabled".to_string()),
    );

    loop {
        let (stream, _) = listener.accept().await?;
        if let Err(error) = require_current_uid_peer(&stream) {
            eprintln!("rejected UDS peer: {error}");
            drop(stream);
            continue;
        }
        let app = app.clone();
        tokio::spawn(async move {
            let io = TokioIo::new(stream);
            let service = service_fn(move |request| {
                let app = app.clone();
                async move {
                    let request = request.map(Body::new);
                    let response = app.oneshot(request).await;
                    match response {
                        Ok(response) => Ok::<_, std::convert::Infallible>(response),
                        Err(error) => match error {},
                    }
                }
            });
            if let Err(error) = http1::Builder::new().serve_connection(io, service).await {
                eprintln!("UDS connection failed: {error}");
            }
        });
    }
}

fn env_flag(name: &str) -> bool {
    matches!(
        env::var(name).ok().as_deref(),
        Some("1") | Some("true") | Some("TRUE") | Some("yes") | Some("YES")
    )
}

fn required_env(name: &str) -> Result<String, Box<dyn std::error::Error>> {
    env::var(name).map_err(|_| format!("{name} is required").into())
}

fn same_socket_identity(left: &Path, right: &Path) -> bool {
    normalized_future_path(left)
        .zip(normalized_future_path(right))
        .is_some_and(|(left, right)| left == right)
}

fn normalized_future_path(path: &Path) -> Option<PathBuf> {
    if !path.is_absolute() {
        return None;
    }
    let mut cursor = path;
    let mut missing = Vec::new();
    while !cursor.exists() {
        missing.push(cursor.file_name()?.to_owned());
        cursor = cursor.parent()?;
    }
    let mut normalized = std::fs::canonicalize(cursor).ok()?;
    for component in missing.into_iter().rev() {
        normalized.push(component);
    }
    Some(normalized)
}

fn required_production_release_fence() -> Result<ProductionReleaseFence, Box<dyn std::error::Error>>
{
    let connector_id = required_env("LAB_PRODUCTION_CONNECTOR_ID")?;
    if connector_id != "local-agent-bridge-production" {
        return Err("LAB_PRODUCTION_CONNECTOR_ID must be local-agent-bridge-production".into());
    }
    let manifest_path = PathBuf::from(required_env("LAB_PRODUCTION_RELEASE_MANIFEST_FILE")?);
    let manifest_json = read_private_release_manifest(&manifest_path)?;
    let contract_version = required_env("LAB_PRODUCTION_CONTRACT_VERSION")?;
    let release_revision = required_env("LAB_PRODUCTION_RELEASE_REVISION")?;
    let endpoint_generation = required_env("LAB_PRODUCTION_ENDPOINT_GENERATION")?
        .parse::<u64>()
        .map_err(|_| "LAB_PRODUCTION_ENDPOINT_GENERATION must be a positive integer")?;
    let workspace = required_env("LAB_PRODUCTION_WORKSPACE")?;
    let tunnel_profile_name = required_env("LAB_PRODUCTION_TUNNEL_PROFILE_NAME")?;
    validate_production_release_manifest(
        &manifest_json,
        &contract_version,
        &release_revision,
        endpoint_generation,
        &workspace,
        &tunnel_profile_name,
    )
    .map_err(|error| format!("production Bridge release fence rejected: {error}").into())
}

fn capability_set_from_env() -> Result<Vec<Capability>, Box<dyn std::error::Error>> {
    let raw = env::var("LAB_BRIDGE_CAPABILITIES")
        .unwrap_or_else(|_| "discovery,file_read,file_write,file_move,shell,process_jobs".into());
    parse_capability_set(&raw).map_err(Into::into)
}

fn parse_capability_set(raw: &str) -> Result<Vec<Capability>, String> {
    raw.split(',')
        .filter(|value| !value.trim().is_empty())
        .map(|value| match value.trim() {
            "discovery" => Ok(Capability::Discovery),
            "host_attestation" => Ok(Capability::HostAttestation),
            "file_read" => Ok(Capability::FileRead),
            "file_write" => Ok(Capability::FileWrite),
            "file_move" => Ok(Capability::FileMove),
            "shell" => Ok(Capability::Shell),
            "process_jobs" => Ok(Capability::ProcessJobs),
            "browser" => Ok(Capability::Browser),
            "computer" => Ok(Capability::Computer),
            "collaboration_read" => Ok(Capability::CollaborationRead),
            "collaboration_write" => Ok(Capability::CollaborationWrite),
            "provider_admin" => Ok(Capability::ProviderAdmin),
            other => Err(format!("unknown LAB_BRIDGE_CAPABILITIES value: {other}")),
        })
        .collect()
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct FeishuSettings {
    cli_path: String,
    profile: Option<String>,
    timeout_ms: u64,
    output_limit: usize,
}

fn extra_file_roots_from_env(production: bool) -> Result<Vec<PathBuf>, String> {
    parse_extra_file_roots(env::var("LAB_EXTRA_FILE_ROOTS").ok().as_deref(), production)
}

fn feishu_settings_from_values(
    cli_path: Option<String>,
    profile: Option<String>,
    timeout_ms: Option<String>,
    output_limit: Option<String>,
) -> Result<Option<FeishuSettings>, String> {
    let Some(cli_path) = cli_path else {
        if profile.is_some() || timeout_ms.is_some() || output_limit.is_some() {
            return Err("LAB_FEISHU_CLI is required when any Feishu setting is configured".into());
        }
        return Ok(None);
    };
    let cli_path = cli_path.trim().to_string();
    if cli_path.is_empty() {
        return Err("LAB_FEISHU_CLI must not be empty".into());
    }
    let profile = match profile {
        Some(profile) => {
            let profile = profile.trim().to_string();
            if profile.is_empty() {
                return Err("LAB_FEISHU_PROFILE must not be empty when configured".into());
            }
            Some(profile)
        }
        None => None,
    };
    let timeout_ms = match timeout_ms {
        Some(value) => value
            .parse::<u64>()
            .map_err(|_| "LAB_FEISHU_TIMEOUT_MS must be an integer".to_string())?,
        None => 30_000,
    };
    if timeout_ms == 0 || timeout_ms > 120_000 {
        return Err("LAB_FEISHU_TIMEOUT_MS must be between 1 and 120000".into());
    }
    let output_limit = match output_limit {
        Some(value) => value
            .parse::<usize>()
            .map_err(|_| "LAB_FEISHU_OUTPUT_LIMIT must be an integer".to_string())?,
        None => 1024 * 1024,
    };
    if output_limit == 0 || output_limit > 4 * 1024 * 1024 {
        return Err("LAB_FEISHU_OUTPUT_LIMIT must be between 1 and 4194304".into());
    }
    Ok(Some(FeishuSettings {
        cli_path,
        profile,
        timeout_ms,
        output_limit,
    }))
}

fn feishu_provider_root(home: &Path) -> PathBuf {
    home.join(".local-agent-bridge")
        .join("providers")
        .join("feishu")
}

fn feishu_private_paths(home: &Path) -> (PathBuf, PathBuf) {
    let root = feishu_provider_root(home);
    (root.join("home"), root.join("config"))
}

fn current_euid() -> u32 {
    unsafe { libc::geteuid() as u32 }
}

fn prepare_private_socket_parent(path: &Path) -> std::io::Result<()> {
    if path.exists() {
        let metadata = std::fs::symlink_metadata(path)?;
        if metadata.file_type().is_symlink() || !metadata.is_dir() {
            return Err(std::io::Error::other(
                "bridge socket parent must be a real directory",
            ));
        }
    } else {
        std::fs::create_dir_all(path)?;
    }
    let metadata = std::fs::metadata(path)?;
    if metadata.uid() != current_euid() {
        return Err(std::io::Error::other(
            "bridge socket parent owner does not match current euid",
        ));
    }
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o700))
}

fn read_private_secret_file(path: &Path) -> Result<String, Box<dyn std::error::Error>> {
    let metadata = std::fs::symlink_metadata(path)?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err("backend key must be a regular file, not a symlink".into());
    }
    if metadata.uid() != current_euid() {
        return Err("backend key owner does not match current euid".into());
    }
    if metadata.permissions().mode() & 0o077 != 0 {
        return Err("backend key permissions are too broad; require 0600".into());
    }
    let value = std::fs::read_to_string(path)?;
    let value = value.trim().to_string();
    if value.as_bytes().len() < 32 {
        return Err("backend key must contain at least 32 bytes".into());
    }
    Ok(value)
}

fn read_private_release_manifest(path: &Path) -> Result<String, Box<dyn std::error::Error>> {
    let metadata = std::fs::symlink_metadata(path)?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err("production release manifest must be a regular file, not a symlink".into());
    }
    if metadata.uid() != current_euid() {
        return Err("production release manifest owner does not match current euid".into());
    }
    if metadata.permissions().mode() & 0o077 != 0 {
        return Err("production release manifest permissions are too broad; require 0600".into());
    }
    if metadata.len() > 1_048_576 {
        return Err("production release manifest exceeds 1 MiB".into());
    }
    Ok(std::fs::read_to_string(path)?)
}

#[cfg(target_os = "macos")]
fn require_current_uid_peer(stream: &UnixStream) -> std::io::Result<()> {
    let mut peer_uid: libc::uid_t = 0;
    let mut peer_gid: libc::gid_t = 0;
    let rc = unsafe { libc::getpeereid(stream.as_raw_fd(), &mut peer_uid, &mut peer_gid) };
    if rc != 0 {
        return Err(std::io::Error::last_os_error());
    }
    if peer_uid as u32 != current_euid() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::PermissionDenied,
            "UDS peer uid does not match bridge uid",
        ));
    }
    Ok(())
}

#[cfg(target_os = "linux")]
fn require_current_uid_peer(stream: &UnixStream) -> std::io::Result<()> {
    let mut credentials = unsafe { std::mem::zeroed::<libc::ucred>() };
    let mut length = std::mem::size_of::<libc::ucred>() as libc::socklen_t;
    let rc = unsafe {
        libc::getsockopt(
            stream.as_raw_fd(),
            libc::SOL_SOCKET,
            libc::SO_PEERCRED,
            &mut credentials as *mut _ as *mut libc::c_void,
            &mut length,
        )
    };
    if rc != 0 {
        return Err(std::io::Error::last_os_error());
    }
    if credentials.uid != current_euid() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::PermissionDenied,
            "UDS peer uid does not match bridge uid",
        ));
    }
    Ok(())
}

#[cfg(not(any(target_os = "macos", target_os = "linux")))]
fn require_current_uid_peer(_stream: &UnixStream) -> std::io::Result<()> {
    Err(std::io::Error::new(
        std::io::ErrorKind::Unsupported,
        "UDS peer credential verification is unsupported on this platform",
    ))
}

fn set_socket_private(path: &Path) -> std::io::Result<()> {
    let metadata = std::fs::symlink_metadata(path)?;
    if !metadata.file_type().is_socket() || metadata.uid() != current_euid() {
        return Err(std::io::Error::other(
            "new bridge socket is not owned by current euid",
        ));
    }
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))
}

#[cfg(test)]
mod feishu_config_tests {
    use super::*;

    #[test]
    fn collaboration_capabilities_are_explicit_and_separate() {
        let capabilities = parse_capability_set(
            "discovery,collaboration_read,collaboration_write,provider_admin",
        )
        .unwrap();
        assert_eq!(
            capabilities,
            vec![
                Capability::Discovery,
                Capability::CollaborationRead,
                Capability::CollaborationWrite,
                Capability::ProviderAdmin,
            ]
        );
        assert!(parse_capability_set("discovery,feishu_raw").is_err());
    }

    #[test]
    fn feishu_config_is_optional_but_partial_configuration_fails_closed() {
        assert!(feishu_settings_from_values(None, None, None, None).unwrap().is_none());
        assert!(
            feishu_settings_from_values(
                None,
                Some("lab-feishu".into()),
                None,
                None,
            )
            .is_err()
        );
        let settings = feishu_settings_from_values(
            Some("/opt/homebrew/bin/lark-cli".into()),
            Some("lab-feishu".into()),
            Some("45000".into()),
            Some("131072".into()),
        )
        .unwrap()
        .unwrap();
        assert_eq!(settings.cli_path, "/opt/homebrew/bin/lark-cli");
        assert_eq!(settings.profile.as_deref(), Some("lab-feishu"));
        assert_eq!(settings.timeout_ms, 45_000);
        assert_eq!(settings.output_limit, 131_072);
    }

    #[test]
    fn feishu_private_paths_stay_under_lab_provider_root() {
        let (runtime_home, config_dir) =
            feishu_private_paths(Path::new("/Users/tester"));
        assert_eq!(
            runtime_home,
            PathBuf::from("/Users/tester/.local-agent-bridge/providers/feishu/home")
        );
        assert_eq!(
            config_dir,
            PathBuf::from("/Users/tester/.local-agent-bridge/providers/feishu/config")
        );
    }

    #[test]
    fn feishu_config_bounds_timeout_and_output() {
        assert!(
            feishu_settings_from_values(
                Some("/opt/homebrew/bin/lark-cli".into()),
                None,
                Some("0".into()),
                None,
            )
            .is_err()
        );
        assert!(
            feishu_settings_from_values(
                Some("/opt/homebrew/bin/lark-cli".into()),
                None,
                Some("120001".into()),
                None,
            )
            .is_err()
        );
        assert!(
            feishu_settings_from_values(
                Some("/opt/homebrew/bin/lark-cli".into()),
                None,
                None,
                Some("4194305".into()),
            )
            .is_err()
        );
    }
}
