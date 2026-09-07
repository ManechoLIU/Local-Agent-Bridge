use axum::body::Body;
use bridge_security_core::{Capability, CapabilityAuthority, CapabilityGrant, ExecutionContext};
use hyper::{server::conn::http1, service::service_fn};
use hyper_util::rt::TokioIo;
use local_tool_bridge::{BridgeIngress, ToolPolicy};
use std::{
    env,
    path::PathBuf,
    time::{SystemTime, UNIX_EPOCH},
};
use tokio::net::UnixListener;
use tower::ServiceExt;
use uuid::Uuid;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let socket_path = required_env("LAB_BRIDGE_SOCKET")?;
    let backend_url = required_env("LAB_BRIDGE_BACKEND_URL")?;
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
    let issued = if authority.lease(&capability_id).is_some() {
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
    let ingress = BridgeIngress::new_trusted_unix_socket(
        authority,
        issued,
        context,
        &backend_url,
        policy,
        state_dir.clone(),
    )?;
    let app = ingress.router();

    let socket = PathBuf::from(&socket_path);
    if socket.exists() {
        std::fs::remove_file(&socket)?;
    }
    if let Some(parent) = socket.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let listener = UnixListener::bind(&socket)?;
    set_socket_private(&socket)?;
    eprintln!(
        "local-tool-bridge ready socket={} backend={} capability_id={}",
        socket.display(),
        backend_url,
        capability_id
    );

    loop {
        let (stream, _) = listener.accept().await?;
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

#[cfg(unix)]
fn set_socket_private(path: &std::path::Path) -> std::io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))
}
