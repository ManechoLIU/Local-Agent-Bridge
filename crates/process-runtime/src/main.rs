use process_runtime::{ProcessRuntime, ProcessSandboxPolicy};
use std::{
    env,
    net::{IpAddr, SocketAddr},
    os::unix::fs::{MetadataExt, PermissionsExt},
    path::{Path, PathBuf},
};
use tokio::net::TcpListener;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let bind = env::var("PROCESS_RUNTIME_BIND").unwrap_or_else(|_| "127.0.0.1:33442".into());
    let address: SocketAddr = bind.parse()?;
    if !is_numeric_loopback(address.ip()) {
        return Err("PROCESS_RUNTIME_BIND must be a numeric loopback address".into());
    }
    let key_file = env::var("PROCESS_RUNTIME_BACKEND_KEY_FILE")?;
    let backend_key = read_private_key(Path::new(&key_file))?;
    let workspace = PathBuf::from(env::var("PROCESS_RUNTIME_WORKSPACE")?);
    let workspace_id = env::var("PROCESS_RUNTIME_WORKSPACE_ID")?;
    let state_dir = env::var("PROCESS_RUNTIME_STATE_DIR")
        .unwrap_or_else(|_| "/tmp/lab-process-runtime".into());
    let scratch = PathBuf::from(
        env::var("PROCESS_RUNTIME_SCRATCH").unwrap_or_else(|_| format!("{state_dir}/scratch")),
    );
    let cache = PathBuf::from(
        env::var("PROCESS_RUNTIME_CACHE").unwrap_or_else(|_| format!("{state_dir}/cache")),
    );
    let policy = ProcessSandboxPolicy::new(
        workspace,
        scratch,
        cache,
        vec![
            PathBuf::from("/opt/homebrew"),
            PathBuf::from("/usr/local"),
            PathBuf::from("/usr"),
            PathBuf::from("/bin"),
            PathBuf::from("/sbin"),
        ],
    )?;
    let runtime = ProcessRuntime::new(backend_key.as_bytes(), policy, workspace_id)?;
    let listener = TcpListener::bind(address).await?;
    eprintln!("process-runtime listening on {}", listener.local_addr()?);
    axum::serve(listener, runtime.router()).await?;
    Ok(())
}

fn is_numeric_loopback(ip: IpAddr) -> bool {
    ip.is_loopback()
}

fn read_private_key(path: &Path) -> Result<String, Box<dyn std::error::Error>> {
    let metadata = std::fs::symlink_metadata(path)?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err("Process Runtime key must be a regular file, not a symlink".into());
    }
    let current_uid = unsafe { libc::geteuid() };
    if metadata.uid() != current_uid {
        return Err("Process Runtime key owner does not match current euid".into());
    }
    if metadata.permissions().mode() & 0o077 != 0 {
        return Err("Process Runtime key permissions are too broad; require 0600".into());
    }
    let value = std::fs::read_to_string(path)?.trim().to_string();
    if value.len() < 32 {
        return Err("Process Runtime key must contain at least 32 bytes".into());
    }
    Ok(value)
}
