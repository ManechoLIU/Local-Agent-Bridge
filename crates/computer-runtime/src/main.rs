use computer_runtime::{ComputerRuntime, NativeMacProvider};
use std::{
    env,
    net::{IpAddr, SocketAddr},
    os::unix::fs::{MetadataExt, PermissionsExt},
    path::Path,
};
use tokio::net::TcpListener;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let bind = env::var("COMPUTER_RUNTIME_BIND").unwrap_or_else(|_| "127.0.0.1:33441".into());
    let address: SocketAddr = bind.parse()?;
    if !is_numeric_loopback(address.ip()) {
        return Err("COMPUTER_RUNTIME_BIND must be a numeric loopback address".into());
    }
    let key_file = env::var("COMPUTER_RUNTIME_BACKEND_KEY_FILE")?;
    let backend_key = read_private_key(Path::new(&key_file))?;
    let runtime = ComputerRuntime::new(backend_key.as_bytes(), NativeMacProvider)?;
    let listener = TcpListener::bind(address).await?;
    eprintln!("computer-runtime listening on {}", listener.local_addr()?);
    axum::serve(listener, runtime.router()).await?;
    Ok(())
}

fn is_numeric_loopback(ip: IpAddr) -> bool {
    ip.is_loopback()
}

fn read_private_key(path: &Path) -> Result<String, Box<dyn std::error::Error>> {
    let metadata = std::fs::symlink_metadata(path)?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err("Computer Runtime key must be a regular file, not a symlink".into());
    }
    let current_uid = unsafe { libc::geteuid() };
    if metadata.uid() != current_uid {
        return Err("Computer Runtime key owner does not match current euid".into());
    }
    if metadata.permissions().mode() & 0o077 != 0 {
        return Err("Computer Runtime key permissions are too broad; require 0600".into());
    }
    let value = std::fs::read_to_string(path)?.trim().to_string();
    if value.as_bytes().len() < 32 {
        return Err("Computer Runtime key must contain at least 32 bytes".into());
    }
    Ok(value)
}
