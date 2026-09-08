use axum::body::Body;
use bridge_security_core::{Capability, CapabilityAuthority, CapabilityGrant, ExecutionContext};
use hyper::{server::conn::http1, service::service_fn};
use hyper_util::rt::TokioIo;
use local_tool_bridge::{BridgeIngress, ToolPolicy};
use std::{
    env,
    os::fd::AsRawFd,
    os::unix::fs::{FileTypeExt, MetadataExt, PermissionsExt},
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};
use tokio::net::{UnixListener, UnixStream};
use tower::ServiceExt;
use uuid::Uuid;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let socket_path = required_env("LAB_BRIDGE_SOCKET")?;
    let backend_url = required_env("LAB_BRIDGE_BACKEND_URL")?;
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
    let ttl_ms = env::var("LAB_BRIDGE_CAPABILITY_TTL_MS")
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .unwrap_or(3_600_000);

    let capabilities = capability_set_from_env()?;
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
        authority.issued_capability_at(&capability_id, now_ms).map_err(|error| {
            format!(
                "existing capability {capability_id} is not reusable: {error}; rotate the capability/session identity explicitly"
            )
        })?
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
        "local-tool-bridge ready socket={} backend={} computer_backend={} capability_id={}",
        socket.display(),
        backend_url,
        computer_backend_url.as_deref().unwrap_or("disabled"),
        capability_id
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

fn capability_set_from_env() -> Result<Vec<Capability>, Box<dyn std::error::Error>> {
    let raw = env::var("LAB_BRIDGE_CAPABILITIES")
        .unwrap_or_else(|_| "discovery,file_read,file_write,file_move,shell,process_jobs".into());
    raw.split(',')
        .filter(|value| !value.trim().is_empty())
        .map(|value| match value.trim() {
            "discovery" => Ok(Capability::Discovery),
            "file_read" => Ok(Capability::FileRead),
            "file_write" => Ok(Capability::FileWrite),
            "file_move" => Ok(Capability::FileMove),
            "shell" => Ok(Capability::Shell),
            "process_jobs" => Ok(Capability::ProcessJobs),
            "browser" => Ok(Capability::Browser),
            "computer" => Ok(Capability::Computer),
            other => Err(format!("unknown LAB_BRIDGE_CAPABILITIES value: {other}").into()),
        })
        .collect()
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
