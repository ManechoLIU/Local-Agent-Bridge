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
    sync::Arc,
    time::{SystemTime, UNIX_EPOCH},
};
use subtle::ConstantTimeEq;
use tokio::sync::Mutex;
use uuid::Uuid;

const INGRESS_KEY_HEADER: &str = "x-lab-ingress-key";
const FORWARDED_HEADERS: &[&str] = &[
    "content-type",
    "accept",
    "mcp-protocol-version",
    "mcp-method",
    "mcp-name",
    "mcp-session-id",
];

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
            .insert("read", Capability::FileRead)
            .insert("search", Capability::FileRead)
            .insert("write", Capability::FileWrite)
            .insert("edit", Capability::FileWrite)
            .insert("delete", Capability::FileWrite)
            .insert("run_command", Capability::Shell)
            .insert("start_command", Capability::ProcessJobs)
            .insert("poll_command", Capability::ProcessJobs)
            .insert("cancel_command", Capability::ProcessJobs)
    }

    pub fn required_capability(&self, method: &str, tool_name: &str) -> Result<Capability, String> {
        match method {
            "server/discover"
            | "initialize"
            | "tools/list"
            | "resources/list"
            | "resources/read"
            | "resources/templates/list"
            | "ping" => Ok(Capability::Discovery),
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

#[derive(Clone)]
pub struct BridgeIngress {
    inner: Arc<BridgeIngressInner>,
}

struct BridgeIngressInner {
    authority: Mutex<CapabilityAuthority>,
    issued: IssuedCapability,
    context: ExecutionContext,
    ingress_key_sha256: [u8; 32],
    backend_url: Url,
    client: reqwest::Client,
    policy: ToolPolicy,
}

impl BridgeIngress {
    pub fn new(
        authority: CapabilityAuthority,
        issued: IssuedCapability,
        context: ExecutionContext,
        ingress_key: &[u8],
        backend_url: &str,
    ) -> Result<Self, String> {
        Self::new_with_policy(
            authority,
            issued,
            context,
            ingress_key,
            backend_url,
            ToolPolicy::catdesk_default(),
        )
    }

    pub fn new_with_policy(
        authority: CapabilityAuthority,
        issued: IssuedCapability,
        context: ExecutionContext,
        ingress_key: &[u8],
        backend_url: &str,
        policy: ToolPolicy,
    ) -> Result<Self, String> {
        if ingress_key.len() < 32 {
            return Err("local ingress key must be at least 32 bytes".into());
        }
        if issued.capability_id != context.capability_id {
            return Err("issued capability does not match ingress execution context".into());
        }
        let backend_url =
            Url::parse(backend_url).map_err(|error| format!("invalid backend URL: {error}"))?;
        if backend_url.scheme() != "http" {
            return Err("backend URL must use loopback http".into());
        }
        let host = backend_url.host_str().unwrap_or_default();
        if !matches!(host, "127.0.0.1" | "localhost" | "::1") {
            return Err("backend URL must be loopback-only".into());
        }
        let mut ingress_key_sha256 = [0_u8; 32];
        ingress_key_sha256.copy_from_slice(&Sha256::digest(ingress_key));
        Ok(Self {
            inner: Arc::new(BridgeIngressInner {
                authority: Mutex::new(authority),
                issued,
                context,
                ingress_key_sha256,
                backend_url,
                client: reqwest::Client::new(),
                policy,
            }),
        })
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
    if !valid_ingress_key(&headers, &state.inner.ingress_key_sha256) {
        return text_response(
            StatusCode::UNAUTHORIZED,
            "local ingress authentication failed",
        );
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
    let request_id = header_text(&headers, "x-request-id")
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .or_else(|| parsed.get("id").map(|value| value.to_string()))
        .unwrap_or_else(|| Uuid::new_v4().to_string());
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
    if let Err(error) = state
        .inner
        .authority
        .lock()
        .await
        .verify_request(&proof, &body, method, tool_name, capability, now_ms)
    {
        return text_response(StatusCode::FORBIDDEN, &error);
    }

    match forward_to_backend(&state, &headers, &proof.to_headers(), body).await {
        Ok(response) => response,
        Err(error) => text_response(StatusCode::BAD_GATEWAY, &error),
    }
}

async fn forward_to_backend(
    state: &BridgeIngress,
    incoming: &HeaderMap,
    proof_headers: &HashMap<String, String>,
    body: Bytes,
) -> Result<Response<Body>, String> {
    let mut request = state
        .inner
        .client
        .post(state.inner.backend_url.clone())
        .body(body.to_vec());
    for name in FORWARDED_HEADERS {
        if let Some(value) = incoming.get(*name).and_then(|value| value.to_str().ok()) {
            request = request.header(*name, value);
        }
    }
    for (name, value) in proof_headers {
        request = request.header(name, value);
    }
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
