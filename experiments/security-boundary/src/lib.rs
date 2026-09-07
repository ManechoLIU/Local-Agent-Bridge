use hmac::{Hmac, Mac};
use rand::{RngCore, rngs::OsRng};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::ffi::{CString, OsString};
use std::fs::{File, OpenOptions};
use std::io::{self, Read, Write};
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Output};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum Capability {
    FileRead,
    FileWrite,
    FileMove,
    Shell,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionContext {
    pub principal_id: String,
    pub execution_session_id: String,
    pub capability_id: String,
    pub workspace_id: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CapabilityGrant {
    pub principal_id: String,
    pub execution_session_id: String,
    pub capability_id: String,
    pub workspace_id: String,
    pub capabilities: Vec<Capability>,
}

type HmacSha256 = Hmac<Sha256>;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CapabilityLease {
    pub grant: CapabilityGrant,
    pub issued_at_ms: u64,
    pub expires_at_ms: u64,
    pub session_generation: u64,
}

#[derive(Clone, Eq, PartialEq)]
pub struct IssuedCapability {
    pub capability_id: String,
    signing_secret: [u8; 32],
}

impl std::fmt::Debug for IssuedCapability {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("IssuedCapability")
            .field("capability_id", &self.capability_id)
            .field("signing_secret", &"<redacted>")
            .finish()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RequestProof {
    pub capability_id: String,
    pub principal_id: String,
    pub execution_session_id: String,
    pub workspace_id: String,
    pub request_id: String,
    pub nonce: String,
    pub method: String,
    pub tool_name: String,
    pub body_sha256: String,
    pub provenance: String,
    pub mac_sha256: String,
}

impl RequestProof {
    pub fn to_headers(&self) -> HashMap<String, String> {
        HashMap::from([
            ("x-lab-capability-id".into(), self.capability_id.clone()),
            ("x-lab-principal-id".into(), self.principal_id.clone()),
            (
                "x-lab-execution-session-id".into(),
                self.execution_session_id.clone(),
            ),
            ("x-lab-workspace-id".into(), self.workspace_id.clone()),
            ("x-lab-request-id".into(), self.request_id.clone()),
            ("x-lab-request-nonce".into(), self.nonce.clone()),
            ("x-lab-body-sha256".into(), self.body_sha256.clone()),
            ("x-lab-ingress-provenance".into(), self.provenance.clone()),
            ("x-lab-request-mac".into(), self.mac_sha256.clone()),
        ])
    }

    pub fn from_headers(
        headers: &HashMap<String, String>,
        observed_method: impl Into<String>,
        observed_tool_name: impl Into<String>,
    ) -> Result<Self, String> {
        let required = |name: &str| {
            headers
                .get(name)
                .cloned()
                .filter(|value| !value.trim().is_empty())
                .ok_or_else(|| format!("missing signed ingress header: {name}"))
        };
        Ok(Self {
            capability_id: required("x-lab-capability-id")?,
            principal_id: required("x-lab-principal-id")?,
            execution_session_id: required("x-lab-execution-session-id")?,
            workspace_id: required("x-lab-workspace-id")?,
            request_id: required("x-lab-request-id")?,
            nonce: required("x-lab-request-nonce")?,
            method: observed_method.into(),
            tool_name: observed_tool_name.into(),
            body_sha256: required("x-lab-body-sha256")?,
            provenance: required("x-lab-ingress-provenance")?,
            mac_sha256: required("x-lab-request-mac")?,
        })
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CorrelationObservation {
    pub subject_fingerprint: Option<String>,
    pub session_fingerprint: Option<String>,
    pub provenance: String,
}

impl CorrelationObservation {
    pub fn openai_tunnel(
        subject_fingerprint: Option<impl Into<String>>,
        session_fingerprint: Option<impl Into<String>>,
    ) -> Self {
        Self {
            subject_fingerprint: subject_fingerprint.map(Into::into),
            session_fingerprint: session_fingerprint.map(Into::into),
            provenance: "openai_tunnel_correlation_unverified_semantics".into(),
        }
    }

    pub fn is_authorization_source(&self) -> bool {
        false
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct CapabilityRecord {
    lease: CapabilityLease,
    revoked: bool,
    used_nonces: HashSet<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct CapabilityAuthoritySnapshot {
    schema_version: u32,
    records: HashMap<String, CapabilityRecord>,
    session_generations: HashMap<String, u64>,
}

#[derive(Debug)]
pub struct CapabilityAuthority {
    master_key: [u8; 32],
    records: HashMap<String, CapabilityRecord>,
    session_generations: HashMap<String, u64>,
}

impl CapabilityAuthority {
    pub fn new_random() -> Self {
        let mut master_key = [0_u8; 32];
        OsRng.fill_bytes(&mut master_key);
        Self::new_for_test(master_key)
    }

    pub fn new_for_test(master_key: [u8; 32]) -> Self {
        Self {
            master_key,
            records: HashMap::new(),
            session_generations: HashMap::new(),
        }
    }

    pub fn load_or_create_private(state_dir: impl AsRef<Path>) -> Result<Self, String> {
        let state_dir = state_dir.as_ref();
        std::fs::create_dir_all(state_dir)
            .map_err(|error| format!("capability state directory create failed: {error}"))?;
        let key_path = state_dir.join("authority.key");
        let state_path = state_dir.join("authority-state.json");
        let master_key = if key_path.exists() {
            let raw = std::fs::read_to_string(&key_path)
                .map_err(|error| format!("capability authority key read failed: {error}"))?;
            decode_hex_32(raw.trim())?
        } else {
            let mut key = [0_u8; 32];
            OsRng.fill_bytes(&mut key);
            write_private_atomic(&key_path, hex_encode(&key).as_bytes())?;
            key
        };
        if !state_path.exists() {
            return Ok(Self::new_for_test(master_key));
        }
        let raw = std::fs::read(&state_path)
            .map_err(|error| format!("capability authority state read failed: {error}"))?;
        let snapshot: CapabilityAuthoritySnapshot = serde_json::from_slice(&raw)
            .map_err(|error| format!("capability authority state parse failed: {error}"))?;
        if snapshot.schema_version != 1 {
            return Err("unsupported capability authority state schema".into());
        }
        Ok(Self {
            master_key,
            records: snapshot.records,
            session_generations: snapshot.session_generations,
        })
    }

    pub fn save_private(&self, state_dir: impl AsRef<Path>) -> Result<(), String> {
        let state_dir = state_dir.as_ref();
        std::fs::create_dir_all(state_dir)
            .map_err(|error| format!("capability state directory create failed: {error}"))?;
        let key_path = state_dir.join("authority.key");
        if !key_path.exists() {
            write_private_atomic(&key_path, hex_encode(&self.master_key).as_bytes())?;
        }
        let snapshot = CapabilityAuthoritySnapshot {
            schema_version: 1,
            records: self.records.clone(),
            session_generations: self.session_generations.clone(),
        };
        let encoded = serde_json::to_vec_pretty(&snapshot)
            .map_err(|error| format!("capability authority state encode failed: {error}"))?;
        write_private_atomic(&state_dir.join("authority-state.json"), &encoded)
    }

    pub fn issue(
        &mut self,
        grant: CapabilityGrant,
        issued_at_ms: u64,
        ttl_ms: u64,
        session_generation: u64,
    ) -> Result<IssuedCapability, String> {
        if grant.capability_id.trim().is_empty() {
            return Err("capability id must not be empty".into());
        }
        if ttl_ms == 0 {
            return Err("capability ttl must be positive".into());
        }
        if self.records.contains_key(&grant.capability_id) {
            return Err("capability id already issued".into());
        }
        let expires_at_ms = issued_at_ms
            .checked_add(ttl_ms)
            .ok_or_else(|| "capability expiry overflow".to_string())?;
        let current_generation = self
            .session_generations
            .entry(grant.execution_session_id.clone())
            .or_insert(session_generation);
        if *current_generation != session_generation {
            return Err("session generation does not match current authority generation".into());
        }
        let lease = CapabilityLease {
            grant: grant.clone(),
            issued_at_ms,
            expires_at_ms,
            session_generation,
        };
        let signing_secret = derive_capability_secret(&self.master_key, &lease)?;
        self.records.insert(
            grant.capability_id.clone(),
            CapabilityRecord {
                lease,
                revoked: false,
                used_nonces: HashSet::new(),
            },
        );
        Ok(IssuedCapability {
            capability_id: grant.capability_id,
            signing_secret,
        })
    }

    pub fn revoke(&mut self, capability_id: &str) -> bool {
        match self.records.get_mut(capability_id) {
            Some(record) => {
                record.revoked = true;
                true
            }
            None => false,
        }
    }

    pub fn rotate_session_generation(
        &mut self,
        execution_session_id: &str,
        expected_generation: u64,
    ) -> Result<u64, String> {
        let current = self
            .session_generations
            .entry(execution_session_id.to_string())
            .or_insert(expected_generation);
        if *current != expected_generation {
            return Err("session generation fence mismatch".into());
        }
        *current = current
            .checked_add(1)
            .ok_or_else(|| "session generation overflow".to_string())?;
        Ok(*current)
    }

    #[allow(clippy::too_many_arguments)]
    pub fn verify_request(
        &mut self,
        proof: &RequestProof,
        body: &[u8],
        observed_method: &str,
        observed_tool_name: &str,
        required_capability: Capability,
        now_ms: u64,
    ) -> Result<ExecutionContext, String> {
        let record = self
            .records
            .get_mut(&proof.capability_id)
            .ok_or_else(|| "unknown capability".to_string())?;
        if proof.method != observed_method {
            return Err("request method does not match signed proof".into());
        }
        if proof.tool_name != observed_tool_name {
            return Err("request tool name does not match signed proof".into());
        }
        if record.revoked {
            return Err("capability revoked".into());
        }
        if now_ms < record.lease.issued_at_ms || now_ms > record.lease.expires_at_ms {
            return Err("capability expired or not yet valid".into());
        }
        let current_generation = self
            .session_generations
            .get(&record.lease.grant.execution_session_id)
            .copied()
            .unwrap_or(record.lease.session_generation);
        if current_generation != record.lease.session_generation {
            return Err("capability belongs to a stale execution session generation".into());
        }
        if proof.request_id.trim().is_empty() || proof.nonce.trim().is_empty() {
            return Err("request id and nonce are required".into());
        }
        if record.used_nonces.contains(&proof.nonce) {
            return Err("request replay detected".into());
        }
        let body_hash = sha256_hex(body);
        if body_hash != proof.body_sha256 {
            return Err("request body hash mismatch".into());
        }
        let signing_secret = derive_capability_secret(&self.master_key, &record.lease)?;
        verify_request_mac(&signing_secret, proof)?;

        if proof.provenance != "local_ingress_hmac_v1" {
            return Err("request proof provenance is not an authorized local ingress".into());
        }
        let context = ExecutionContext {
            principal_id: proof.principal_id.clone(),
            execution_session_id: proof.execution_session_id.clone(),
            capability_id: proof.capability_id.clone(),
            workspace_id: proof.workspace_id.clone(),
        };
        record
            .lease
            .grant
            .authorize(&context, required_capability)?;
        record.used_nonces.insert(proof.nonce.clone());
        Ok(context)
    }
}

pub fn sign_request(
    issued: &IssuedCapability,
    context: &ExecutionContext,
    request_id: &str,
    nonce: &str,
    method: &str,
    tool_name: &str,
    body: &[u8],
) -> Result<RequestProof, String> {
    if request_id.trim().is_empty() || nonce.trim().is_empty() {
        return Err("request id and nonce are required".into());
    }
    let body_sha256 = sha256_hex(body);
    if context.capability_id != issued.capability_id {
        return Err("signing context capability does not match issued handle".into());
    }
    let mut proof = RequestProof {
        capability_id: issued.capability_id.clone(),
        principal_id: context.principal_id.clone(),
        execution_session_id: context.execution_session_id.clone(),
        workspace_id: context.workspace_id.clone(),
        request_id: request_id.to_string(),
        nonce: nonce.to_string(),
        method: method.to_string(),
        tool_name: tool_name.to_string(),
        body_sha256,
        provenance: "local_ingress_hmac_v1".into(),
        mac_sha256: String::new(),
    };
    proof.mac_sha256 = hmac_hex(&issued.signing_secret, &request_proof_message(&proof))?;
    Ok(proof)
}

fn derive_capability_secret(
    master_key: &[u8; 32],
    lease: &CapabilityLease,
) -> Result<[u8; 32], String> {
    let message = format!(
        "v1\n{}\n{}\n{}\n{}\n{}\n{}\n{}",
        lease.grant.principal_id,
        lease.grant.execution_session_id,
        lease.grant.capability_id,
        lease.grant.workspace_id,
        lease.issued_at_ms,
        lease.expires_at_ms,
        lease.session_generation,
    );
    let mut mac = HmacSha256::new_from_slice(master_key)
        .map_err(|_| "invalid capability authority key".to_string())?;
    mac.update(message.as_bytes());
    let bytes = mac.finalize().into_bytes();
    let mut output = [0_u8; 32];
    output.copy_from_slice(&bytes);
    Ok(output)
}

fn request_proof_message(proof: &RequestProof) -> Vec<u8> {
    format!(
        "v1\n{}\n{}\n{}\n{}\n{}\n{}\n{}\n{}\n{}\n{}",
        proof.capability_id,
        proof.principal_id,
        proof.execution_session_id,
        proof.workspace_id,
        proof.request_id,
        proof.nonce,
        proof.method,
        proof.tool_name,
        proof.body_sha256,
        proof.provenance,
    )
    .into_bytes()
}

fn verify_request_mac(secret: &[u8; 32], proof: &RequestProof) -> Result<(), String> {
    let expected = decode_hex_32(&proof.mac_sha256)?;
    let mut mac = HmacSha256::new_from_slice(secret)
        .map_err(|_| "invalid capability request key".to_string())?;
    mac.update(&request_proof_message(proof));
    mac.verify_slice(&expected)
        .map_err(|_| "request MAC mismatch".to_string())
}

fn hmac_hex(key: &[u8], message: &[u8]) -> Result<String, String> {
    let mut mac = HmacSha256::new_from_slice(key).map_err(|_| "invalid HMAC key".to_string())?;
    mac.update(message);
    Ok(hex_encode(&mac.finalize().into_bytes()))
}

fn sha256_hex(value: &[u8]) -> String {
    hex_encode(&Sha256::digest(value))
}

fn hex_encode(value: &[u8]) -> String {
    let mut output = String::with_capacity(value.len() * 2);
    for byte in value {
        use std::fmt::Write as _;
        let _ = write!(&mut output, "{byte:02x}");
    }
    output
}

fn decode_hex_32(value: &str) -> Result<[u8; 32], String> {
    if value.len() != 64 {
        return Err("request MAC must be 32-byte hex".into());
    }
    let mut output = [0_u8; 32];
    for (index, slot) in output.iter_mut().enumerate() {
        let offset = index * 2;
        *slot = u8::from_str_radix(&value[offset..offset + 2], 16)
            .map_err(|_| "request MAC is not valid hex".to_string())?;
    }
    Ok(output)
}

impl CapabilityGrant {
    pub fn authorize(
        &self,
        context: &ExecutionContext,
        capability: Capability,
    ) -> Result<(), String> {
        if self.principal_id != context.principal_id {
            return Err("principal mismatch".into());
        }
        if self.execution_session_id != context.execution_session_id {
            return Err("execution session mismatch".into());
        }
        if self.capability_id != context.capability_id {
            return Err("capability id mismatch".into());
        }
        if self.workspace_id != context.workspace_id {
            return Err("workspace mismatch".into());
        }
        if !self.capabilities.contains(&capability) {
            return Err("capability not granted".into());
        }
        Ok(())
    }
}

#[derive(Debug)]
pub struct Workspace {
    root: PathBuf,
    root_fd: OwnedFd,
}

impl Workspace {
    pub fn open(root: impl AsRef<Path>) -> Result<Self, String> {
        let root = root
            .as_ref()
            .canonicalize()
            .map_err(|error| format!("workspace canonicalize failed: {error}"))?;
        let c_root = cstring(root.as_os_str().as_bytes())?;
        let fd = unsafe {
            libc::open(
                c_root.as_ptr(),
                libc::O_RDONLY | libc::O_DIRECTORY | libc::O_CLOEXEC | libc::O_NOFOLLOW,
            )
        };
        if fd < 0 {
            return Err(format!(
                "workspace open failed: {}",
                io::Error::last_os_error()
            ));
        }
        let root_fd = unsafe { OwnedFd::from_raw_fd(fd) };
        Ok(Self { root, root_fd })
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn read_text(&self, relative: impl AsRef<Path>) -> Result<String, String> {
        let (parent, name) = self.open_parent(relative.as_ref())?;
        let fd = openat(
            parent.as_raw_fd(),
            &name,
            libc::O_RDONLY | libc::O_CLOEXEC | libc::O_NOFOLLOW,
            0,
        )
        .map_err(|error| format!("read target open failed: {error}"))?;
        ensure_safe_regular_file(&fd, "read")?;
        let mut file = File::from(fd);
        let mut output = String::new();
        file.read_to_string(&mut output)
            .map_err(|error| format!("read failed: {error}"))?;
        Ok(output)
    }

    pub fn write_text(&self, relative: impl AsRef<Path>, content: &str) -> Result<(), String> {
        let (parent, name) = self.open_parent(relative.as_ref())?;
        match openat(
            parent.as_raw_fd(),
            &name,
            libc::O_WRONLY | libc::O_CLOEXEC | libc::O_NOFOLLOW,
            0,
        ) {
            Ok(fd) => {
                ensure_safe_regular_file(&fd, "write")?;
                let rc = unsafe { libc::ftruncate(fd.as_raw_fd(), 0) };
                if rc != 0 {
                    return Err(format!("truncate failed: {}", io::Error::last_os_error()));
                }
                let mut file = File::from(fd);
                file.write_all(content.as_bytes())
                    .map_err(|error| format!("write failed: {error}"))?;
                file.sync_all()
                    .map_err(|error| format!("sync failed: {error}"))?;
                Ok(())
            }
            Err(error) if error.raw_os_error() == Some(libc::ENOENT) => {
                let fd = openat(
                    parent.as_raw_fd(),
                    &name,
                    libc::O_WRONLY
                        | libc::O_CREAT
                        | libc::O_EXCL
                        | libc::O_CLOEXEC
                        | libc::O_NOFOLLOW,
                    0o600,
                )
                .map_err(|error| format!("create target open failed: {error}"))?;
                ensure_safe_regular_file(&fd, "create")?;
                let mut file = File::from(fd);
                file.write_all(content.as_bytes())
                    .map_err(|error| format!("create write failed: {error}"))?;
                file.sync_all()
                    .map_err(|error| format!("sync failed: {error}"))?;
                Ok(())
            }
            Err(error) => Err(format!("write target open failed: {error}")),
        }
    }

    pub fn move_path(
        &self,
        from: impl AsRef<Path>,
        to: impl AsRef<Path>,
        overwrite: bool,
    ) -> Result<(), String> {
        let (src_parent, src_name) = self.open_parent(from.as_ref())?;
        let (dst_parent, dst_name) = self.open_parent(to.as_ref())?;

        let src_stat = statat_no_follow(src_parent.as_raw_fd(), &src_name)
            .map_err(|error| format!("source inspect failed: {error}"))?;
        ensure_safe_move_entry(&src_stat, "source")?;

        match statat_no_follow(dst_parent.as_raw_fd(), &dst_name) {
            Ok(dst_stat) => {
                if !overwrite {
                    return Err("destination exists".into());
                }
                ensure_safe_move_entry(&dst_stat, "destination")?;
            }
            Err(error) if error.raw_os_error() == Some(libc::ENOENT) => {}
            Err(error) => return Err(format!("destination inspect failed: {error}")),
        }

        let src = cstring(src_name.as_bytes())?;
        let dst = cstring(dst_name.as_bytes())?;
        let rc = unsafe {
            libc::renameat(
                src_parent.as_raw_fd(),
                src.as_ptr(),
                dst_parent.as_raw_fd(),
                dst.as_ptr(),
            )
        };
        if rc != 0 {
            let error = io::Error::last_os_error();
            if error.raw_os_error() == Some(libc::EXDEV) {
                return Err("cross-device move fails closed".into());
            }
            return Err(format!("rename failed: {error}"));
        }
        Ok(())
    }

    fn open_parent(&self, relative: &Path) -> Result<(OwnedFd, OsString), String> {
        let components = checked_components(relative)?;
        if components.is_empty() {
            return Err("path must name a child of the workspace".into());
        }
        let final_name = components.last().cloned().expect("checked non-empty");
        let root_dup = unsafe { libc::dup(self.root_fd.as_raw_fd()) };
        if root_dup < 0 {
            return Err(format!(
                "workspace fd duplication failed: {}",
                io::Error::last_os_error()
            ));
        }
        let mut current = unsafe { OwnedFd::from_raw_fd(root_dup) };
        for component in &components[..components.len() - 1] {
            current = openat(
                current.as_raw_fd(),
                component,
                libc::O_RDONLY | libc::O_DIRECTORY | libc::O_CLOEXEC | libc::O_NOFOLLOW,
                0,
            )
            .map_err(|error| {
                format!(
                    "unsafe or missing parent component {:?}: {error}",
                    component
                )
            })?;
        }
        Ok((current, final_name))
    }
}

fn checked_components(path: &Path) -> Result<Vec<OsString>, String> {
    if path.is_absolute() {
        return Err("absolute paths are not allowed".into());
    }
    let mut output = Vec::new();
    for component in path.components() {
        match component {
            Component::Normal(value) => output.push(value.to_os_string()),
            Component::CurDir => {}
            Component::ParentDir => return Err("parent traversal is not allowed".into()),
            Component::RootDir | Component::Prefix(_) => {
                return Err("rooted paths are not allowed".into());
            }
        }
    }
    Ok(output)
}

fn write_private_atomic(path: &Path, bytes: &[u8]) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "private state path has no parent".to_string())?;
    std::fs::create_dir_all(parent)
        .map_err(|error| format!("private state parent create failed: {error}"))?;
    let temporary = parent.join(format!(
        ".{}.{}.tmp",
        path.file_name()
            .and_then(|value| value.to_str())
            .unwrap_or("state"),
        std::process::id(),
    ));
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&temporary)
        .map_err(|error| format!("private state temp open failed: {error}"))?;
    file.write_all(bytes)
        .map_err(|error| format!("private state write failed: {error}"))?;
    file.write_all(b"\n")
        .map_err(|error| format!("private state newline failed: {error}"))?;
    file.sync_all()
        .map_err(|error| format!("private state sync failed: {error}"))?;
    std::fs::rename(&temporary, path)
        .map_err(|error| format!("private state atomic replace failed: {error}"))?;
    Ok(())
}

fn cstring(bytes: &[u8]) -> Result<CString, String> {
    CString::new(bytes).map_err(|_| "path contains NUL".into())
}

fn openat(dirfd: i32, name: &OsString, flags: i32, mode: u32) -> Result<OwnedFd, io::Error> {
    let name = CString::new(name.as_bytes())
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "path contains NUL"))?;
    let fd = unsafe { libc::openat(dirfd, name.as_ptr(), flags, mode as libc::c_uint) };
    if fd < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(unsafe { OwnedFd::from_raw_fd(fd) })
    }
}

fn fstat(fd: &OwnedFd) -> Result<libc::stat, String> {
    let mut stat = unsafe { std::mem::zeroed::<libc::stat>() };
    let rc = unsafe { libc::fstat(fd.as_raw_fd(), &mut stat) };
    if rc != 0 {
        return Err(format!("fstat failed: {}", io::Error::last_os_error()));
    }
    Ok(stat)
}

fn statat_no_follow(dirfd: i32, name: &OsString) -> Result<libc::stat, io::Error> {
    let name = CString::new(name.as_bytes())
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "path contains NUL"))?;
    let mut stat = unsafe { std::mem::zeroed::<libc::stat>() };
    let rc = unsafe { libc::fstatat(dirfd, name.as_ptr(), &mut stat, libc::AT_SYMLINK_NOFOLLOW) };
    if rc != 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(stat)
    }
}

fn file_kind(stat: &libc::stat) -> libc::mode_t {
    stat.st_mode & libc::S_IFMT
}

fn ensure_safe_regular_file(fd: &OwnedFd, operation: &str) -> Result<(), String> {
    let stat = fstat(fd)?;
    if file_kind(&stat) != libc::S_IFREG {
        return Err(format!("{operation} target must be a regular file"));
    }
    if stat.st_nlink != 1 {
        return Err(format!("{operation} target has multiple hard links"));
    }
    Ok(())
}

fn ensure_safe_move_entry(stat: &libc::stat, label: &str) -> Result<(), String> {
    let kind = file_kind(stat);
    if kind == libc::S_IFLNK {
        return Err(format!("{label} symlink is not allowed"));
    }
    if kind == libc::S_IFREG && stat.st_nlink != 1 {
        return Err(format!("{label} hard-linked file is not allowed"));
    }
    if kind != libc::S_IFREG && kind != libc::S_IFDIR {
        return Err(format!("{label} must be a regular file or directory"));
    }
    Ok(())
}

pub fn run_home_confined_shell(
    workspace: impl AsRef<Path>,
    command: &str,
) -> Result<Output, String> {
    let workspace = workspace
        .as_ref()
        .canonicalize()
        .map_err(|error| format!("workspace canonicalize failed: {error}"))?;
    let denied_home = std::env::var("HOME").map_err(|_| "HOME is unavailable")?;
    let runtime_home = workspace.join(".lab-home");
    let runtime_tmp = workspace.join(".lab-tmp");
    std::fs::create_dir_all(&runtime_home)
        .map_err(|error| format!("sandbox HOME create failed: {error}"))?;
    std::fs::create_dir_all(&runtime_tmp)
        .map_err(|error| format!("sandbox TMPDIR create failed: {error}"))?;

    let profile = r#"(version 1)
(deny default)
(allow process*)
(allow file-read-metadata
  (require-all
    (require-not (subpath (param "DENIED_HOME")))
    (require-not (subpath "/Users"))
    (require-not (subpath "/Volumes"))
    (require-not (subpath "/private/tmp"))
    (require-not (subpath "/tmp"))
    (require-not (subpath "/private/var/folders"))))
(allow file-read-metadata (subpath (param "WORKSPACE")))
(allow file-read-data
  (require-all
    (require-not (subpath (param "DENIED_HOME")))
    (require-not (subpath "/Users"))
    (require-not (subpath "/Volumes"))
    (require-not (subpath "/private/tmp"))
    (require-not (subpath "/tmp"))
    (require-not (subpath "/private/var/folders"))))
(allow file-read-data (subpath (param "WORKSPACE")))
(allow file-write* (subpath (param "WORKSPACE")))
(allow sysctl-read)
(allow mach-lookup)
(allow ipc-posix-shm)
(allow signal)
"#;

    let mut process = Command::new("/usr/bin/sandbox-exec");
    process
        .arg("-D")
        .arg(format!("DENIED_HOME={denied_home}"))
        .arg("-D")
        .arg(format!("WORKSPACE={}", workspace.display()))
        .arg("-p")
        .arg(profile)
        .arg("/bin/sh")
        .arg("-c")
        .arg(command)
        .current_dir(&workspace)
        .env_clear()
        .env("HOME", &runtime_home)
        .env("TMPDIR", &runtime_tmp)
        .env("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
        .env("LANG", "C.UTF-8");
    process
        .output()
        .map_err(|error| format!("sandboxed shell failed to start: {error}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::os::unix::fs::symlink;
    use tempfile::tempdir;

    fn grant() -> (ExecutionContext, CapabilityGrant) {
        let context = ExecutionContext {
            principal_id: "principal-a".into(),
            execution_session_id: "session-a".into(),
            capability_id: "cap-a".into(),
            workspace_id: "workspace-a".into(),
        };
        let grant = CapabilityGrant {
            principal_id: context.principal_id.clone(),
            execution_session_id: context.execution_session_id.clone(),
            capability_id: context.capability_id.clone(),
            workspace_id: context.workspace_id.clone(),
            capabilities: vec![
                Capability::FileRead,
                Capability::FileWrite,
                Capability::FileMove,
                Capability::Shell,
            ],
        };
        (context, grant)
    }

    #[test]
    fn authorization_binds_principal_session_capability_and_workspace() {
        let (context, grant) = grant();
        grant.authorize(&context, Capability::FileWrite).unwrap();
        let mut wrong = context.clone();
        wrong.execution_session_id = "other-session".into();
        assert!(grant.authorize(&wrong, Capability::FileWrite).is_err());
        let mut wrong_workspace = context;
        wrong_workspace.workspace_id = "other-workspace".into();
        assert!(
            grant
                .authorize(&wrong_workspace, Capability::FileWrite)
                .is_err()
        );
    }

    #[test]
    fn capability_issuance_expiry_revocation_and_replay_fail_closed() {
        let (context, grant) = grant();
        let mut authority = CapabilityAuthority::new_for_test([7_u8; 32]);
        let issued = authority.issue(grant, 1_000, 5_000, 3).unwrap();
        let body = br#"{"jsonrpc":"2.0","id":"r1","method":"tools/call"}"#;
        let proof = sign_request(
            &issued,
            &context,
            "r1",
            "nonce-1",
            "tools/call",
            "write",
            body,
        )
        .unwrap();
        let context = authority
            .verify_request(
                &proof,
                body,
                "tools/call",
                "write",
                Capability::FileWrite,
                2_000,
            )
            .unwrap();
        assert_eq!(context.capability_id, "cap-a");

        let replay = authority.verify_request(
            &proof,
            body,
            "tools/call",
            "write",
            Capability::FileWrite,
            2_001,
        );
        assert!(replay.unwrap_err().contains("replay"));

        let expired = sign_request(
            &issued,
            &context,
            "r2",
            "nonce-2",
            "tools/call",
            "write",
            body,
        )
        .unwrap();
        assert!(
            authority
                .verify_request(
                    &expired,
                    body,
                    "tools/call",
                    "write",
                    Capability::FileWrite,
                    6_001,
                )
                .unwrap_err()
                .contains("expired")
        );

        assert!(authority.revoke("cap-a"));
        let revoked = sign_request(
            &issued,
            &context,
            "r3",
            "nonce-3",
            "tools/call",
            "write",
            body,
        )
        .unwrap();
        assert!(
            authority
                .verify_request(
                    &revoked,
                    body,
                    "tools/call",
                    "write",
                    Capability::FileWrite,
                    2_500,
                )
                .unwrap_err()
                .contains("revoked")
        );
    }

    #[test]
    fn request_mac_binds_body_method_tool_and_observed_context() {
        let (context, grant) = grant();
        let mut authority = CapabilityAuthority::new_for_test([9_u8; 32]);
        let issued = authority.issue(grant, 10, 10_000, 1).unwrap();
        let body = b"canonical-body";
        let proof = sign_request(
            &issued,
            &context,
            "request-1",
            "nonce-a",
            "tools/call",
            "run_command",
            body,
        )
        .unwrap();

        let mut tampered = proof.clone();
        tampered.mac_sha256.replace_range(0..2, "00");
        assert!(
            authority
                .verify_request(
                    &tampered,
                    body,
                    "tools/call",
                    "run_command",
                    Capability::Shell,
                    100,
                )
                .unwrap_err()
                .contains("MAC")
        );

        let proof = sign_request(
            &issued,
            &context,
            "request-2",
            "nonce-b",
            "tools/call",
            "run_command",
            body,
        )
        .unwrap();
        assert!(
            authority
                .verify_request(
                    &proof,
                    b"changed-body",
                    "tools/call",
                    "run_command",
                    Capability::Shell,
                    100,
                )
                .unwrap_err()
                .contains("body hash")
        );

        let wrong_context = ExecutionContext {
            principal_id: "other-principal".into(),
            execution_session_id: context.execution_session_id.clone(),
            capability_id: context.capability_id.clone(),
            workspace_id: context.workspace_id.clone(),
        };
        let proof = sign_request(
            &issued,
            &wrong_context,
            "request-3",
            "nonce-c",
            "tools/call",
            "run_command",
            body,
        )
        .unwrap();
        assert!(
            authority
                .verify_request(
                    &proof,
                    body,
                    "tools/call",
                    "run_command",
                    Capability::Shell,
                    100,
                )
                .unwrap_err()
                .contains("principal mismatch")
        );
    }

    #[test]
    fn session_generation_rotation_invalidates_older_capabilities() {
        let (context, grant) = grant();
        let mut authority = CapabilityAuthority::new_for_test([11_u8; 32]);
        let issued = authority.issue(grant, 100, 10_000, 4).unwrap();
        assert_eq!(
            authority.rotate_session_generation("session-a", 4).unwrap(),
            5
        );
        let body = b"body";
        let proof = sign_request(
            &issued,
            &context,
            "request-4",
            "nonce-generation",
            "tools/call",
            "read",
            body,
        )
        .unwrap();
        assert!(
            authority
                .verify_request(
                    &proof,
                    body,
                    "tools/call",
                    "read",
                    Capability::FileRead,
                    200,
                )
                .unwrap_err()
                .contains("stale execution session generation")
        );
    }

    #[test]
    fn signed_ingress_headers_round_trip_without_exposing_signing_secret() {
        let (context, grant) = grant();
        let mut authority = CapabilityAuthority::new_for_test([12_u8; 32]);
        let issued = authority.issue(grant, 100, 10_000, 1).unwrap();
        let body = b"body";
        let proof = sign_request(
            &issued,
            &context,
            "request-headers",
            "nonce-headers",
            "tools/call",
            "read",
            body,
        )
        .unwrap();
        let headers = proof.to_headers();
        assert!(!headers.keys().any(|key| key.contains("secret")));
        assert!(!headers.values().any(|value| value.contains("<redacted>")));
        let restored = RequestProof::from_headers(&headers, "tools/call", "read").unwrap();
        assert_eq!(restored, proof);
        authority
            .verify_request(
                &restored,
                body,
                "tools/call",
                "read",
                Capability::FileRead,
                200,
            )
            .unwrap();
    }

    #[test]
    fn openai_tunnel_correlation_facts_are_diagnostic_not_authorization() {
        let observation = CorrelationObservation::openai_tunnel(
            Some("subject-fingerprint"),
            Some("session-fingerprint"),
        );
        assert!(!observation.is_authorization_source());
        assert_eq!(
            observation.provenance,
            "openai_tunnel_correlation_unverified_semantics"
        );
    }

    #[test]
    fn forged_ingress_context_without_capability_secret_fails_mac_verification() {
        let (context, grant) = grant();
        let mut authority = CapabilityAuthority::new_for_test([13_u8; 32]);
        let issued = authority.issue(grant, 100, 10_000, 1).unwrap();
        let body = b"body";
        let mut proof = sign_request(
            &issued,
            &context,
            "request-forged",
            "nonce-forged",
            "tools/call",
            "read",
            body,
        )
        .unwrap();
        proof.principal_id = "forged-principal".into();
        let error = authority
            .verify_request(
                &proof,
                body,
                "tools/call",
                "read",
                Capability::FileRead,
                200,
            )
            .unwrap_err();
        assert!(error.contains("MAC"), "{error}");
    }

    #[test]
    fn ingress_compares_actual_method_and_tool_to_signed_proof() {
        let (context, grant) = grant();
        let mut authority = CapabilityAuthority::new_for_test([15_u8; 32]);
        let issued = authority.issue(grant, 100, 10_000, 1).unwrap();
        let body = b"body";
        let proof = sign_request(
            &issued,
            &context,
            "request-method",
            "nonce-method",
            "tools/call",
            "read",
            body,
        )
        .unwrap();
        assert!(
            authority
                .verify_request(
                    &proof,
                    body,
                    "resources/read",
                    "read",
                    Capability::FileRead,
                    200,
                )
                .unwrap_err()
                .contains("method")
        );
        assert!(
            authority
                .verify_request(
                    &proof,
                    body,
                    "tools/call",
                    "run_command",
                    Capability::FileRead,
                    200,
                )
                .unwrap_err()
                .contains("tool name")
        );
    }

    #[test]
    fn revocation_and_replay_state_survive_private_restart() {
        use std::os::unix::fs::PermissionsExt;

        let state_dir = tempdir().unwrap();
        let (context, grant) = grant();
        let mut authority = CapabilityAuthority::new_for_test([17_u8; 32]);
        let issued = authority.issue(grant, 100, 10_000, 1).unwrap();
        let body = b"body";
        let first = sign_request(
            &issued,
            &context,
            "request-persisted",
            "nonce-persisted",
            "tools/call",
            "read",
            body,
        )
        .unwrap();
        authority
            .verify_request(
                &first,
                body,
                "tools/call",
                "read",
                Capability::FileRead,
                200,
            )
            .unwrap();
        authority.save_private(state_dir.path()).unwrap();

        let key_mode = fs::metadata(state_dir.path().join("authority.key"))
            .unwrap()
            .permissions()
            .mode()
            & 0o777;
        let state_mode = fs::metadata(state_dir.path().join("authority-state.json"))
            .unwrap()
            .permissions()
            .mode()
            & 0o777;
        assert_eq!(key_mode, 0o600);
        assert_eq!(state_mode, 0o600);

        let mut restarted = CapabilityAuthority::load_or_create_private(state_dir.path()).unwrap();
        assert!(
            restarted
                .verify_request(
                    &first,
                    body,
                    "tools/call",
                    "read",
                    Capability::FileRead,
                    201,
                )
                .unwrap_err()
                .contains("replay")
        );
        assert!(restarted.revoke("cap-a"));
        restarted.save_private(state_dir.path()).unwrap();
        let mut restarted_again =
            CapabilityAuthority::load_or_create_private(state_dir.path()).unwrap();
        let second = sign_request(
            &issued,
            &context,
            "request-after-revoke",
            "nonce-after-revoke",
            "tools/call",
            "read",
            body,
        )
        .unwrap();
        assert!(
            restarted_again
                .verify_request(
                    &second,
                    body,
                    "tools/call",
                    "read",
                    Capability::FileRead,
                    300,
                )
                .unwrap_err()
                .contains("revoked")
        );
    }

    #[test]
    fn hardlink_read_escape_is_rejected() {
        let outside_dir = tempdir().unwrap();
        let workspace_dir = tempdir().unwrap();
        let outside = outside_dir.path().join("outside-secret.txt");
        fs::write(&outside, "outside-secret").unwrap();
        fs::hard_link(&outside, workspace_dir.path().join("alias.txt")).unwrap();
        let workspace = Workspace::open(workspace_dir.path()).unwrap();
        let error = workspace.read_text("alias.txt").unwrap_err();
        assert!(error.contains("multiple hard links"), "{error}");
    }

    #[test]
    fn nonexistent_write_under_symlink_parent_is_rejected() {
        let outside_dir = tempdir().unwrap();
        let workspace_dir = tempdir().unwrap();
        symlink(outside_dir.path(), workspace_dir.path().join("escape")).unwrap();
        let workspace = Workspace::open(workspace_dir.path()).unwrap();
        let error = workspace.write_text("escape/new.txt", "nope").unwrap_err();
        assert!(
            error.contains("unsafe or missing parent component"),
            "{error}"
        );
        assert!(!outside_dir.path().join("new.txt").exists());
    }

    #[test]
    fn existing_symlink_write_is_rejected_without_touching_target() {
        let outside_dir = tempdir().unwrap();
        let workspace_dir = tempdir().unwrap();
        let outside = outside_dir.path().join("outside.txt");
        fs::write(&outside, "unchanged").unwrap();
        symlink(&outside, workspace_dir.path().join("alias.txt")).unwrap();
        let workspace = Workspace::open(workspace_dir.path()).unwrap();
        assert!(workspace.write_text("alias.txt", "mutated").is_err());
        assert_eq!(fs::read_to_string(outside).unwrap(), "unchanged");
    }

    #[test]
    fn nonexistent_move_destination_under_symlink_parent_is_rejected() {
        let outside_dir = tempdir().unwrap();
        let workspace_dir = tempdir().unwrap();
        fs::write(workspace_dir.path().join("source.txt"), "inside").unwrap();
        symlink(outside_dir.path(), workspace_dir.path().join("escape")).unwrap();
        let workspace = Workspace::open(workspace_dir.path()).unwrap();
        let error = workspace
            .move_path("source.txt", "escape/moved.txt", false)
            .unwrap_err();
        assert!(
            error.contains("unsafe or missing parent component"),
            "{error}"
        );
        assert!(!outside_dir.path().join("moved.txt").exists());
        assert!(workspace_dir.path().join("source.txt").exists());
    }

    #[test]
    fn normal_write_and_move_stay_inside_workspace() {
        let workspace_dir = tempdir().unwrap();
        fs::create_dir(workspace_dir.path().join("sub")).unwrap();
        let workspace = Workspace::open(workspace_dir.path()).unwrap();
        workspace.write_text("sub/a.txt", "hello").unwrap();
        assert_eq!(workspace.read_text("sub/a.txt").unwrap(), "hello");
        workspace
            .move_path("sub/a.txt", "sub/b.txt", false)
            .unwrap();
        assert_eq!(workspace.read_text("sub/b.txt").unwrap(), "hello");
        assert!(!workspace_dir.path().join("sub/a.txt").exists());
    }

    #[test]
    fn macos_shell_blocks_sensitive_fs_network_and_env_but_allows_workspace_io() {
        if !Path::new("/usr/bin/sandbox-exec").exists() {
            return;
        }
        use std::net::TcpListener;

        let home = PathBuf::from(std::env::var("HOME").unwrap());
        let workspace_dir = tempfile::Builder::new()
            .prefix("lab-security-workspace-")
            .tempdir_in(&home)
            .unwrap();
        let outside_home = tempfile::Builder::new()
            .prefix("lab-security-outside-")
            .tempfile_in(&home)
            .unwrap();
        let outside_tmp = tempfile::Builder::new()
            .prefix("lab-security-tmp-")
            .tempfile_in("/tmp")
            .unwrap();
        fs::write(outside_home.path(), "outside-home-secret").unwrap();
        fs::write(outside_tmp.path(), "outside-tmp-secret").unwrap();
        fs::write(workspace_dir.path().join("inside.txt"), "inside-ok").unwrap();

        let allowed = run_home_confined_shell(
            workspace_dir.path(),
            "/bin/cat inside.txt && printf shell-ok > shell-out.txt",
        )
        .unwrap();
        assert!(
            allowed.status.success(),
            "{}",
            String::from_utf8_lossy(&allowed.stderr)
        );
        assert!(String::from_utf8_lossy(&allowed.stdout).contains("inside-ok"));
        assert_eq!(
            fs::read_to_string(workspace_dir.path().join("shell-out.txt")).unwrap(),
            "shell-ok"
        );

        for outside in [outside_home.path(), outside_tmp.path()] {
            let escaped = run_home_confined_shell(
                workspace_dir.path(),
                &format!("/bin/cat '{}'", outside.display()),
            )
            .unwrap();
            assert!(
                !escaped.status.success(),
                "outside read unexpectedly succeeded"
            );
            assert!(!String::from_utf8_lossy(&escaped.stdout).contains("secret"));
        }

        let metadata = run_home_confined_shell(
            workspace_dir.path(),
            &format!("/bin/ls '{}'", home.display()),
        )
        .unwrap();
        assert!(
            !metadata.status.success(),
            "outside user-data metadata unexpectedly remained enumerable"
        );

        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let network = run_home_confined_shell(
            workspace_dir.path(),
            &format!("/usr/bin/nc -z 127.0.0.1 {port}"),
        )
        .unwrap();
        assert!(
            !network.status.success(),
            "network escape unexpectedly succeeded"
        );

        unsafe { std::env::set_var("LAB_SECURITY_SECRET", "must-not-leak") };
        let env = run_home_confined_shell(workspace_dir.path(), "/usr/bin/env").unwrap();
        unsafe { std::env::remove_var("LAB_SECURITY_SECRET") };
        let env_text = String::from_utf8_lossy(&env.stdout);
        assert!(
            env.status.success(),
            "{}",
            String::from_utf8_lossy(&env.stderr)
        );
        assert!(!env_text.contains("LAB_SECURITY_SECRET"));
        assert!(!env_text.contains("must-not-leak"));
        assert!(env_text.contains("HOME="));
        assert!(env_text.contains("TMPDIR="));
    }

    #[test]
    fn traversal_and_hardlink_writes_fail_closed() {
        let outside_dir = tempdir().unwrap();
        let workspace_dir = tempdir().unwrap();
        let outside = outside_dir.path().join("outside.txt");
        fs::write(&outside, "unchanged").unwrap();
        fs::hard_link(&outside, workspace_dir.path().join("hard.txt")).unwrap();
        let workspace = Workspace::open(workspace_dir.path()).unwrap();
        assert!(workspace.write_text("../escape.txt", "nope").is_err());
        let error = workspace.write_text("hard.txt", "mutated").unwrap_err();
        assert!(error.contains("multiple hard links"), "{error}");
        assert_eq!(fs::read_to_string(&outside).unwrap(), "unchanged");
    }
}
