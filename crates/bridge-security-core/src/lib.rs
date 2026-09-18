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
use std::os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt};
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Output};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum Capability {
    Discovery,
    HostAttestation,
    FileRead,
    FileWrite,
    FileMove,
    Shell,
    ProcessJobs,
    CollaborationRead,
    CollaborationWrite,
    ProviderAdmin,
    Browser,
    Computer,
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
    pub bridge_execution_id: String,
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
            (
                "x-lab-bridge-execution-id".into(),
                self.bridge_execution_id.clone(),
            ),
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
            bridge_execution_id: required("x-lab-bridge-execution-id")?,
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
    #[serde(default)]
    used_request_ids: HashSet<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct CapabilityAuthoritySnapshot {
    schema_version: u32,
    #[serde(default = "default_key_generation")]
    key_generation: u64,
    records: HashMap<String, CapabilityRecord>,
    session_generations: HashMap<String, u64>,
}

fn default_key_generation() -> u64 {
    1
}

#[derive(Clone, Debug)]
pub struct CapabilityAuthority {
    master_key: [u8; 32],
    key_generation: u64,
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
            key_generation: 1,
            records: HashMap::new(),
            session_generations: HashMap::new(),
        }
    }

    pub fn load_or_create_private(state_dir: impl AsRef<Path>) -> Result<Self, String> {
        let state_dir = state_dir.as_ref();
        prepare_private_dir(state_dir)?;
        let state_path = state_dir.join("authority-state.json");
        if !state_path.exists() {
            let has_key_artifacts = std::fs::read_dir(state_dir)
                .map_err(|error| format!("capability state directory read failed: {error}"))?
                .filter_map(Result::ok)
                .any(|entry| {
                    entry
                        .file_name()
                        .to_string_lossy()
                        .starts_with("authority.key")
                });
            if has_key_artifacts {
                return Err(
                    "capability authority state is missing while key material exists; fail closed"
                        .into(),
                );
            }
            let authority = Self::new_random();
            authority.save_private(state_dir)?;
            return Ok(authority);
        }
        let raw = read_private_file(&state_path, "capability authority state")?;
        let snapshot: CapabilityAuthoritySnapshot = serde_json::from_slice(&raw)
            .map_err(|error| format!("capability authority state parse failed: {error}"))?;
        if snapshot.schema_version != 1 && snapshot.schema_version != 2 {
            return Err("unsupported capability authority state schema".into());
        }
        let key_generation = snapshot.key_generation.max(1);
        let key_path = private_key_path(state_dir, key_generation);
        let legacy_key = state_dir.join("authority.key");
        let selected = if key_path.exists() {
            key_path
        } else if key_generation == 1 && legacy_key.exists() {
            legacy_key
        } else {
            return Err(format!(
                "capability authority key generation {key_generation} is missing"
            ));
        };
        let raw_key = read_private_file(&selected, "capability authority key")?;
        let raw_key = String::from_utf8(raw_key)
            .map_err(|_| "capability authority key is not UTF-8".to_string())?;
        let master_key = decode_hex_32(raw_key.trim())?;
        Ok(Self {
            master_key,
            key_generation,
            records: snapshot.records,
            session_generations: snapshot.session_generations,
        })
    }

    pub fn save_private(&self, state_dir: impl AsRef<Path>) -> Result<(), String> {
        let state_dir = state_dir.as_ref();
        prepare_private_dir(state_dir)?;
        let key_path = private_key_path(state_dir, self.key_generation);
        let expected_key = hex_encode(&self.master_key);
        if key_path.exists() {
            let on_disk = read_private_file(&key_path, "capability authority key")?;
            let on_disk = String::from_utf8(on_disk)
                .map_err(|_| "capability authority key is not UTF-8".to_string())?;
            if on_disk.trim() != expected_key {
                return Err(
                    "capability authority key file conflicts with in-memory generation".into(),
                );
            }
        } else {
            write_private_atomic(&key_path, expected_key.as_bytes())?;
        }
        let snapshot = CapabilityAuthoritySnapshot {
            schema_version: 2,
            key_generation: self.key_generation,
            records: self.records.clone(),
            session_generations: self.session_generations.clone(),
        };
        let encoded = serde_json::to_vec_pretty(&snapshot)
            .map_err(|error| format!("capability authority state encode failed: {error}"))?;
        write_private_atomic(&state_dir.join("authority-state.json"), &encoded)
    }

    pub fn key_generation(&self) -> u64 {
        self.key_generation
    }

    pub fn rotate_master_key_private(
        &mut self,
        state_dir: impl AsRef<Path>,
        expected_generation: u64,
    ) -> Result<u64, String> {
        if self.key_generation != expected_generation {
            return Err("master key generation fence mismatch".into());
        }
        let next = self
            .key_generation
            .checked_add(1)
            .ok_or_else(|| "master key generation overflow".to_string())?;
        let state_dir = state_dir.as_ref();
        prepare_private_dir(state_dir)?;
        // Commit ordering is deliberate: write the new key first, then atomically
        // publish state that points at it. If a prior attempt crashed after writing
        // key.N+1 but before publishing state, reuse that orphan key on retry.
        let new_key_path = private_key_path(state_dir, next);
        let new_key = if new_key_path.exists() {
            let raw = read_private_file(&new_key_path, "next capability authority key")?;
            let raw = String::from_utf8(raw)
                .map_err(|_| "next capability authority key is not UTF-8".to_string())?;
            decode_hex_32(raw.trim())?
        } else {
            let mut generated = [0_u8; 32];
            OsRng.fill_bytes(&mut generated);
            write_private_atomic(&new_key_path, hex_encode(&generated).as_bytes())?;
            generated
        };
        let previous_key = self.master_key;
        let previous_generation = self.key_generation;
        self.master_key = new_key;
        self.key_generation = next;
        if let Err(error) = self.save_private(state_dir) {
            self.master_key = previous_key;
            self.key_generation = previous_generation;
            return Err(error);
        }
        let previous_path = private_key_path(state_dir, previous_generation);
        if previous_path.exists() && std::fs::remove_file(&previous_path).is_ok() {
            let _ = sync_directory(state_dir);
        }
        // State already committed to the new generation. Cleanup failure must not
        // report rotation failure and cause callers to retry the old fence. The
        // retired key is no longer authoritative and can be removed on maintenance.
        Ok(next)
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
                used_request_ids: HashSet::new(),
            },
        );
        Ok(IssuedCapability {
            capability_id: grant.capability_id,
            signing_secret,
        })
    }

    pub fn lease(&self, capability_id: &str) -> Option<CapabilityLease> {
        self.records
            .get(capability_id)
            .map(|record| record.lease.clone())
    }

    /// Renews a durable capability only when its complete grant still matches the
    /// caller's immutable binding. Replay state is deliberately retained and the
    /// in-memory record is restored if publishing the renewed lease fails.
    pub fn renew_exact_grant_private(
        &mut self,
        state_dir: impl AsRef<Path>,
        capability_id: &str,
        expected_grant: &CapabilityGrant,
        issued_at_ms: u64,
        ttl_ms: u64,
    ) -> Result<IssuedCapability, String> {
        if ttl_ms == 0 {
            return Err("capability ttl must be positive".into());
        }
        let expires_at_ms = issued_at_ms
            .checked_add(ttl_ms)
            .ok_or_else(|| "capability expiry overflow".to_string())?;
        let prior = self
            .records
            .get(capability_id)
            .cloned()
            .ok_or_else(|| "unknown capability".to_string())?;
        if prior.revoked {
            return Err("capability revoked".into());
        }
        if &prior.lease.grant != expected_grant {
            return Err("capability grant does not match exact renewal grant".into());
        }
        let current_generation = self
            .session_generations
            .get(&prior.lease.grant.execution_session_id)
            .copied()
            .unwrap_or(prior.lease.session_generation);
        if current_generation != prior.lease.session_generation {
            return Err("capability belongs to a stale execution session generation".into());
        }

        let mut renewed = prior.clone();
        renewed.lease.issued_at_ms = issued_at_ms;
        renewed.lease.expires_at_ms = expires_at_ms;
        let signing_secret = derive_capability_secret(&self.master_key, &renewed.lease)?;
        self.records.insert(capability_id.to_string(), renewed);
        if let Err(error) = self.save_private(state_dir) {
            self.records.insert(capability_id.to_string(), prior);
            return Err(error);
        }
        Ok(IssuedCapability {
            capability_id: capability_id.to_string(),
            signing_secret,
        })
    }

    pub fn issued_capability(&self, capability_id: &str) -> Result<IssuedCapability, String> {
        let record = self
            .records
            .get(capability_id)
            .ok_or_else(|| "unknown capability".to_string())?;
        if record.revoked {
            return Err("capability revoked".into());
        }
        let signing_secret = derive_capability_secret(&self.master_key, &record.lease)?;
        Ok(IssuedCapability {
            capability_id: capability_id.to_string(),
            signing_secret,
        })
    }

    pub fn issued_capability_at(
        &self,
        capability_id: &str,
        now_ms: u64,
    ) -> Result<IssuedCapability, String> {
        let record = self
            .records
            .get(capability_id)
            .ok_or_else(|| "unknown capability".to_string())?;
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
        self.issued_capability(capability_id)
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
        if record.used_request_ids.contains(&proof.request_id) {
            return Err("request id replay detected".into());
        }
        if record.used_nonces.contains(&proof.nonce) {
            return Err("request nonce replay detected".into());
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
        record.used_request_ids.insert(proof.request_id.clone());
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
    let mut execution = [0_u8; 24];
    OsRng.fill_bytes(&mut execution);
    sign_request_for_execution(
        issued,
        context,
        &format!("be_{}", hex_encode(&execution)),
        request_id,
        nonce,
        method,
        tool_name,
        body,
    )
}

#[allow(clippy::too_many_arguments)]
pub fn sign_request_for_execution(
    issued: &IssuedCapability,
    context: &ExecutionContext,
    bridge_execution_id: &str,
    request_id: &str,
    nonce: &str,
    method: &str,
    tool_name: &str,
    body: &[u8],
) -> Result<RequestProof, String> {
    if request_id.trim().is_empty() || nonce.trim().is_empty() {
        return Err("request id and nonce are required".into());
    }
    if bridge_execution_id.trim().is_empty() {
        return Err("Bridge execution id is required".into());
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
        bridge_execution_id: bridge_execution_id.to_string(),
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
    let mut capabilities = lease
        .grant
        .capabilities
        .iter()
        .map(|capability| format!("{capability:?}"))
        .collect::<Vec<_>>();
    capabilities.sort();
    let message = format!(
        "v2\n{}\n{}\n{}\n{}\n{}\n{}\n{}\n{}",
        lease.grant.principal_id,
        lease.grant.execution_session_id,
        lease.grant.capability_id,
        lease.grant.workspace_id,
        capabilities.join(","),
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
        "v2\n{}\n{}\n{}\n{}\n{}\n{}\n{}\n{}\n{}\n{}\n{}",
        proof.capability_id,
        proof.principal_id,
        proof.execution_session_id,
        proof.workspace_id,
        proof.bridge_execution_id,
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

fn prepare_private_dir(path: &Path) -> Result<(), String> {
    if path.exists() {
        let metadata = std::fs::symlink_metadata(path)
            .map_err(|error| format!("private state directory inspect failed: {error}"))?;
        if metadata.file_type().is_symlink() || !metadata.is_dir() {
            return Err("private state directory must be a real directory, not a symlink".into());
        }
    } else {
        std::fs::create_dir_all(path)
            .map_err(|error| format!("capability state directory create failed: {error}"))?;
    }
    let metadata = std::fs::metadata(path)
        .map_err(|error| format!("private state directory metadata failed: {error}"))?;
    let current_uid = unsafe { libc::geteuid() };
    if metadata.uid() != current_uid {
        return Err("private state directory owner does not match current euid".into());
    }
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o700))
        .map_err(|error| format!("private state directory permission update failed: {error}"))?;
    Ok(())
}

fn read_private_file(path: &Path, label: &str) -> Result<Vec<u8>, String> {
    let metadata = std::fs::symlink_metadata(path)
        .map_err(|error| format!("{label} inspect failed: {error}"))?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(format!("{label} must be a regular file, not a symlink"));
    }
    if metadata.permissions().mode() & 0o077 != 0 {
        return Err(format!(
            "{label} permissions are too broad; require 0600-style privacy"
        ));
    }
    std::fs::read(path).map_err(|error| format!("{label} read failed: {error}"))
}

fn sync_directory(path: &Path) -> Result<(), String> {
    let directory =
        File::open(path).map_err(|error| format!("private state parent open failed: {error}"))?;
    directory
        .sync_all()
        .map_err(|error| format!("private state parent sync failed: {error}"))
}

fn private_key_path(state_dir: &Path, generation: u64) -> PathBuf {
    state_dir.join(format!("authority.key.{generation}"))
}

fn write_private_atomic(path: &Path, bytes: &[u8]) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "private state path has no parent".to_string())?;
    prepare_private_dir(parent)?;
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
    sync_directory(parent)?;
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
(allow process-exec)
(allow process-fork)
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
(allow signal (target self))
(allow signal (target children))
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
mod request_proof_tests {
    use super::*;

    fn fixture() -> (CapabilityAuthority, IssuedCapability, ExecutionContext) {
        let context = ExecutionContext {
            principal_id: "bridge-principal".into(),
            execution_session_id: "bridge-session".into(),
            capability_id: "bridge-capability".into(),
            workspace_id: "bridge-workspace".into(),
        };
        let grant = CapabilityGrant {
            principal_id: context.principal_id.clone(),
            execution_session_id: context.execution_session_id.clone(),
            capability_id: context.capability_id.clone(),
            workspace_id: context.workspace_id.clone(),
            capabilities: vec![Capability::Shell],
        };
        let mut authority = CapabilityAuthority::new_for_test([91; 32]);
        let issued = authority.issue(grant, 1, 1_000, 1).unwrap();
        (authority, issued, context)
    }

    fn private_test_dir() -> PathBuf {
        let path = std::env::temp_dir().join(format!(
            "bridge-security-core-renewal-{}",
            rand::random::<u64>()
        ));
        std::fs::create_dir(&path).unwrap();
        path
    }

    #[test]
    fn bridge_execution_identity_is_in_headers_and_mac() {
        let (mut authority, issued, context) = fixture();
        let body = br#"{"jsonrpc":"2.0"}"#;
        let proof = sign_request_for_execution(
            &issued,
            &context,
            "hte_machine",
            "request-1",
            "nonce-1",
            "tools/call",
            "run_command",
            body,
        )
        .unwrap();
        assert_eq!(
            proof.to_headers()["x-lab-bridge-execution-id"],
            "hte_machine"
        );
        authority
            .verify_request(
                &proof,
                body,
                "tools/call",
                "run_command",
                Capability::Shell,
                2,
            )
            .unwrap();

        let (mut fresh_authority, _, _) = fixture();
        let mut substituted = proof;
        substituted.bridge_execution_id = "hte_caller".into();
        assert_eq!(
            fresh_authority
                .verify_request(
                    &substituted,
                    body,
                    "tools/call",
                    "run_command",
                    Capability::Shell,
                    2
                )
                .unwrap_err(),
            "request MAC mismatch"
        );
    }

    #[test]
    fn exact_grant_renewal_replaces_the_signing_secret_without_clearing_replay_sets() {
        let (mut authority, issued, context) = fixture();
        let body = br#"{"jsonrpc":"2.0"}"#;
        let old_proof = sign_request(
            &issued,
            &context,
            "request-before-renewal",
            "nonce-before-renewal",
            "tools/call",
            "run_command",
            body,
        )
        .unwrap();
        authority
            .verify_request(
                &old_proof,
                body,
                "tools/call",
                "run_command",
                Capability::Shell,
                2,
            )
            .unwrap();

        let state_dir = private_test_dir();
        authority.save_private(&state_dir).unwrap();
        let grant = authority.lease(&context.capability_id).unwrap().grant;
        let renewed = authority
            .renew_exact_grant_private(&state_dir, &context.capability_id, &grant, 900, 1_000)
            .unwrap();

        assert_eq!(
            authority
                .lease(&context.capability_id)
                .unwrap()
                .issued_at_ms,
            900
        );
        assert_eq!(
            authority
                .verify_request(
                    &old_proof,
                    body,
                    "tools/call",
                    "run_command",
                    Capability::Shell,
                    900,
                )
                .unwrap_err(),
            "request id replay detected"
        );

        let stale_proof = sign_request(
            &issued,
            &context,
            "request-with-old-secret",
            "nonce-with-old-secret",
            "tools/call",
            "run_command",
            body,
        )
        .unwrap();
        assert_eq!(
            authority
                .verify_request(
                    &stale_proof,
                    body,
                    "tools/call",
                    "run_command",
                    Capability::Shell,
                    900,
                )
                .unwrap_err(),
            "request MAC mismatch"
        );

        let renewed_proof = sign_request(
            &renewed,
            &context,
            "request-after-renewal",
            "nonce-after-renewal",
            "tools/call",
            "run_command",
            body,
        )
        .unwrap();
        authority
            .verify_request(
                &renewed_proof,
                body,
                "tools/call",
                "run_command",
                Capability::Shell,
                900,
            )
            .unwrap();
        std::fs::remove_dir_all(state_dir).unwrap();
    }

    #[test]
    fn exact_grant_renewal_rejects_drift_revocation_and_save_failure_without_mutating_memory() {
        let (mut authority, _, context) = fixture();
        let state_dir = private_test_dir();
        authority.save_private(&state_dir).unwrap();
        let grant = authority.lease(&context.capability_id).unwrap().grant;
        let mut drifted = grant.clone();
        drifted.workspace_id = "other-workspace".into();
        assert_eq!(
            authority
                .renew_exact_grant_private(
                    &state_dir,
                    &context.capability_id,
                    &drifted,
                    900,
                    1_000,
                )
                .unwrap_err(),
            "capability grant does not match exact renewal grant"
        );
        assert!(authority.revoke(&context.capability_id));
        assert_eq!(
            authority
                .renew_exact_grant_private(&state_dir, &context.capability_id, &grant, 900, 1_000,)
                .unwrap_err(),
            "capability revoked"
        );

        let (mut authority, _, context) = fixture();
        authority.save_private(&state_dir).unwrap();
        let grant = authority.lease(&context.capability_id).unwrap().grant;
        let before = authority.lease(&context.capability_id).unwrap();
        std::fs::remove_file(state_dir.join("authority-state.json")).unwrap();
        std::fs::create_dir(state_dir.join("authority-state.json")).unwrap();
        assert!(
            authority
                .renew_exact_grant_private(&state_dir, &context.capability_id, &grant, 900, 1_000,)
                .is_err()
        );
        assert_eq!(authority.lease(&context.capability_id).unwrap(), before);
        std::fs::remove_dir_all(state_dir).unwrap();
    }
}
