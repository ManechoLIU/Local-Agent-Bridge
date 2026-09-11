use axum::{
    Router,
    body::{Body, Bytes},
    extract::State,
    http::{HeaderMap, Response, StatusCode, header},
    routing::post,
};
use bridge_security_core::{
    Capability, CapabilityAuthority, ExecutionContext, IssuedCapability, sign_request,
};
use reqwest::Url;
use sha2::{Digest, Sha256};
use std::{
    collections::HashMap,
    net::IpAddr,
    path::PathBuf,
    sync::Arc,
    time::{SystemTime, UNIX_EPOCH},
};
use subtle::ConstantTimeEq;
use tokio::sync::Mutex;
use uuid::Uuid;

const INGRESS_KEY_HEADER: &str = "x-lab-ingress-key";
const PRODUCTION_CONNECTOR_ID: &str = "local-agent-bridge-production";
const PRODUCTION_CONTRACT_VERSION: &str = "lab-production-connector-v1";
const PRODUCTION_WORKSPACE: &str = "/Users/echoman/Documents/ChatGPT/Local-Agent-Bridge";
const PRODUCTION_TUNNEL_PROFILE: &str = "local-agent-bridge-production";
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

    Ok(ProductionReleaseFence {
        contract_version: observed_contract_version.to_string(),
        release_revision: observed_release_revision.to_string(),
        endpoint_generation: observed_endpoint_generation,
        workspace: observed_workspace.to_string(),
        tunnel_profile_name: observed_tunnel_profile_name.to_string(),
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
            .insert("computer_type", Capability::Computer)
            .insert("computer_key", Capability::Computer)
            .insert("computer_scroll", Capability::Computer)
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

#[derive(Clone)]
struct BackendTarget {
    url: Url,
    key: String,
}

struct BridgeIngressInner {
    authority: Mutex<CapabilityAuthority>,
    issued: IssuedCapability,
    context: ExecutionContext,
    local_auth: LocalIngressAuth,
    default_backend: BackendTarget,
    host_backend: Option<BackendTarget>,
    host_release_fence: Option<ProductionReleaseFence>,
    computer_backend: Option<BackendTarget>,
    client: reqwest::Client,
    policy: ToolPolicy,
    state_dir: Option<PathBuf>,
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
                issued,
                context,
                local_auth: LocalIngressAuth::SharedKey(ingress_key_sha256),
                default_backend: BackendTarget {
                    url: backend_url,
                    key: backend_key.to_string(),
                },
                host_backend: None,
                host_release_fence: None,
                computer_backend: None,
                client: backend_client()?,
                policy,
                state_dir: Some(state_dir.into()),
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
                issued,
                context,
                local_auth: LocalIngressAuth::TrustedUnixSocket,
                default_backend: BackendTarget {
                    url: backend_url,
                    key: backend_key.to_string(),
                },
                host_backend: None,
                host_release_fence: None,
                computer_backend: None,
                client: backend_client()?,
                policy,
                state_dir: Some(state_dir.into()),
            }),
        })
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
    let provided_request_id = header_text(&headers, "x-request-id")
        .filter(|value| !value.is_empty())
        .map(str::to_string);
    let request_id = provided_request_id.unwrap_or_else(|| {
        if is_reconnectable_discovery_method(method, &capability) {
            Uuid::new_v4().to_string()
        } else {
            parsed
                .get("id")
                .map(|value| value.to_string())
                .unwrap_or_else(|| Uuid::new_v4().to_string())
        }
    });
    let nonce = Uuid::new_v4().to_string();
    let proof = match sign_request(
        &state.inner.issued,
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
    let now_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX);
    {
        let mut authority = state.inner.authority.lock().await;
        if let Err(error) =
            authority.verify_request(&proof, &body, method, tool_name, capability, now_ms)
        {
            return text_response(StatusCode::FORBIDDEN, &error);
        }
        if let Some(state_dir) = &state.inner.state_dir {
            if let Err(error) = authority.save_private(state_dir) {
                return text_response(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    &format!("capability replay state persistence failed: {error}"),
                );
            }
        }
    }

    if method == "tools/list"
        && (state.inner.host_backend.is_some() || state.inner.computer_backend.is_some())
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
        Ok(response) => response,
        Err(error) => text_response(StatusCode::BAD_GATEWAY, &error),
    }
}

async fn forward_to_backend(
    state: &BridgeIngress,
    target: &BackendTarget,
    incoming: &HeaderMap,
    proof_headers: &HashMap<String, String>,
    body: Bytes,
) -> Result<Response<Body>, String> {
    let mut request = state
        .inner
        .client
        .post(target.url.clone())
        .body(body.to_vec());
    for name in FORWARDED_HEADERS {
        if let Some(value) = incoming.get(*name).and_then(|value| value.to_str().ok()) {
            request = request.header(*name, value);
        }
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
    let bytes = backend
        .bytes()
        .await
        .map_err(|error| format!("backend response read failed: {error}"))?;
    let mut response = Response::builder().status(status);
    if let Some(content_type) = content_type {
        response = response.header(header::CONTENT_TYPE, content_type);
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
            forward_to_backend(state, computer, incoming, proof_headers, body).await?,
        ));
    }
    for (kind, response) in &responses {
        if response.status() != StatusCode::OK {
            return Err(format!(
                "tools/list backend {kind} returned {}",
                response.status()
            ));
        }
    }
    let mut iter = responses.into_iter();
    let (_, first) = iter
        .next()
        .ok_or_else(|| "tools/list has no default backend".to_string())?;
    let first_bytes = axum::body::to_bytes(first.into_body(), usize::MAX)
        .await
        .map_err(|error| format!("default tools/list response read failed: {error}"))?;
    let mut merged: serde_json::Value = serde_json::from_slice(&first_bytes)
        .map_err(|error| format!("default tools/list response is not JSON: {error}"))?;
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
        tools.extend(extra_tools.iter().cloned());
    }
    Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(merged.to_string()))
        .map_err(|error| format!("merged tools/list response build failed: {error}"))
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
        ) || method.starts_with("notifications/"))
}

pub fn required_capability(method: &str, tool_name: &str) -> Result<Capability, String> {
    ToolPolicy::catdesk_default().required_capability(method, tool_name)
}

fn text_response(status: StatusCode, message: &str) -> Response<Body> {
    Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, "text/plain; charset=utf-8")
        .body(Body::from(message.to_string()))
        .expect("static response")
}
