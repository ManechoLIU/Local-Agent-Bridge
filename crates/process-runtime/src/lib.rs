use axum::{
    Router,
    body::{Body, Bytes},
    extract::State,
    http::{HeaderMap, Response, StatusCode, header},
    routing::post,
};
use serde::Serialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs,
    io::Read,
    os::unix::{
        fs::{MetadataExt, PermissionsExt},
        process::{CommandExt, ExitStatusExt},
    },
    path::{Path, PathBuf},
    process::{Child, ChildStderr, ChildStdout, Command, Stdio},
    sync::Arc,
    thread,
    time::{Duration, Instant},
};
use subtle::ConstantTimeEq;

mod jobs;

use jobs::{JobSnapshot, JobTable};

const MAX_COMMAND_BYTES: usize = 65_536;
const MAX_RUN_TIMEOUT_MS: u64 = 120_000;
const MAX_STREAM_BYTES: usize = 1_048_576;
const SAFE_PATH: &str = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin";
const PROVENANCE: &str = "lab_process_runtime_v1";
const BACKEND_KEY_HEADER: &str = "x-lab-backend-key";
const SIGNED_PROVENANCE: &str = "local_ingress_hmac_v1";

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct BridgeBinding {
    pub principal_id: String,
    pub execution_session_id: String,
}

pub(crate) struct SpawnedProcess {
    pub child: Child,
    pub stdout: ChildStdout,
    pub stderr: ChildStderr,
    pub timeout: Duration,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RunCommandRequest {
    pub command: String,
    pub cwd: Option<String>,
    pub timeout_ms: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PreparedCommand {
    pub program: PathBuf,
    pub args: Vec<String>,
    pub env: BTreeMap<String, String>,
    pub cwd: PathBuf,
    pub timeout: Duration,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct RunCommandResult {
    pub stdout: String,
    pub stderr: String,
    pub exit_code: i32,
    pub success: bool,
    pub command_success: bool,
    pub timed_out: bool,
    pub provenance: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ProcessSandboxPolicy {
    workspace: PathBuf,
    scratch: PathBuf,
    cache: PathBuf,
    trusted_toolchain_roots: Vec<PathBuf>,
    profile: String,
}

impl ProcessSandboxPolicy {
    pub fn new(
        workspace: impl AsRef<Path>,
        scratch: impl AsRef<Path>,
        cache: impl AsRef<Path>,
        trusted_toolchain_roots: Vec<PathBuf>,
    ) -> Result<Self, String> {
        let workspace = validated_absolute_dir(workspace.as_ref(), "workspace")?;
        let scratch = prepare_private_dir(scratch.as_ref(), "scratch")?;
        let cache = prepare_private_dir(cache.as_ref(), "cache")?;

        reject_root_overlap(&workspace, &scratch, "scratch")?;
        reject_root_overlap(&workspace, &cache, "cache")?;
        reject_root_overlap(&scratch, &cache, "cache")?;

        let mut trusted = Vec::new();
        for root in trusted_toolchain_roots {
            if !root.is_absolute() {
                return Err("trusted toolchain root must be absolute".into());
            }
            match fs::symlink_metadata(&root) {
                Ok(meta) if !meta.file_type().is_symlink() && meta.is_dir() => {
                    let normalized = lexical_absolute(&root, "trusted toolchain root")?;
                    if !trusted.contains(&normalized) {
                        trusted.push(normalized);
                    }
                }
                Ok(_) => {
                    return Err(format!(
                        "trusted toolchain root is not a real directory: {}",
                        root.display()
                    ));
                }
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
                Err(error) => {
                    return Err(format!(
                        "trusted toolchain root inspect failed for {}: {error}",
                        root.display()
                    ));
                }
            }
        }
        trusted.sort();

        for dir in [
            scratch.join("tmp"),
            cache.join("cargo"),
            cache.join("npm"),
        ] {
            prepare_private_dir(&dir, "runtime subdirectory")?;
        }

        let profile = build_profile(&workspace, &scratch, &cache, &trusted, &[])?;
        Ok(Self {
            workspace,
            scratch,
            cache,
            trusted_toolchain_roots: trusted,
            profile,
        })
    }

    pub fn profile(&self) -> &str {
        &self.profile
    }

    pub fn workspace(&self) -> &Path {
        &self.workspace
    }

    pub fn scratch(&self) -> &Path {
        &self.scratch
    }

    pub fn cache(&self) -> &Path {
        &self.cache
    }

    pub fn trusted_toolchain_roots(&self) -> &[PathBuf] {
        &self.trusted_toolchain_roots
    }

    pub fn prepare(&self, request: RunCommandRequest) -> Result<PreparedCommand, String> {
        if request.command.trim().is_empty() {
            return Err("command must not be empty".into());
        }
        if request.command.as_bytes().len() > MAX_COMMAND_BYTES {
            return Err(format!("command exceeds {MAX_COMMAND_BYTES} bytes"));
        }
        if request.command.as_bytes().contains(&0) {
            return Err("command contains NUL".into());
        }
        if request.timeout_ms == 0 || request.timeout_ms > MAX_RUN_TIMEOUT_MS {
            return Err(format!(
                "timeout_ms must be between 1 and {MAX_RUN_TIMEOUT_MS}"
            ));
        }

        let extra_roots = load_authorized_cwd_roots();
        let cwd = resolve_cwd(&self.workspace, &extra_roots, request.cwd.as_deref())?;
        let profile = build_profile(
            &self.workspace,
            &self.scratch,
            &self.cache,
            &self.trusted_toolchain_roots,
            &extra_roots,
        )?;
        let mut env = BTreeMap::new();
        env.insert("HOME".into(), self.scratch.display().to_string());
        env.insert(
            "TMPDIR".into(),
            self.scratch.join("tmp").display().to_string(),
        );
        env.insert(
            "CARGO_HOME".into(),
            self.cache.join("cargo").display().to_string(),
        );
        env.insert(
            "npm_config_cache".into(),
            self.cache.join("npm").display().to_string(),
        );
        env.insert("GIT_CONFIG_GLOBAL".into(), "/dev/null".into());
        env.insert("PATH".into(), SAFE_PATH.into());
        env.insert("LANG".into(), "C.UTF-8".into());

        let args = vec![
            "-p".into(),
            profile,
            "/bin/sh".into(),
            "-c".into(),
            request.command,
        ];

        Ok(PreparedCommand {
            program: PathBuf::from("/usr/bin/sandbox-exec"),
            args,
            env,
            cwd,
            timeout: Duration::from_millis(request.timeout_ms),
        })
    }

    pub fn run(&self, request: RunCommandRequest) -> Result<RunCommandResult, String> {
        let prepared = self.prepare(request)?;
        execute_prepared(prepared)
    }
}

pub(crate) fn spawn_sandbox(prepared: PreparedCommand) -> Result<SpawnedProcess, String> {
    if prepared.program.as_os_str() != "/usr/bin/sandbox-exec" {
        return Err("Process Runtime only launches sandbox-exec".into());
    }
    let meta = fs::symlink_metadata(&prepared.program)
        .map_err(|error| format!("sandbox-exec inspect failed: {error}"))?;
    if meta.file_type().is_symlink() || !meta.is_file() {
        return Err("sandbox-exec must be a real binary".into());
    }

    let mut child = Command::new(&prepared.program)
        .args(&prepared.args)
        .env_clear()
        .envs(&prepared.env)
        .current_dir(&prepared.cwd)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .process_group(0)
        .spawn()
        .map_err(|error| format!("sandbox-exec spawn failed: {error}"))?;

    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "sandbox-exec stdout unavailable".to_string())?;
    let stderr = child
        .stderr
        .take()
        .ok_or_else(|| "sandbox-exec stderr unavailable".to_string())?;
    Ok(SpawnedProcess {
        child,
        stdout,
        stderr,
        timeout: prepared.timeout,
    })
}

fn execute_prepared(prepared: PreparedCommand) -> Result<RunCommandResult, String> {
    let mut spawned = spawn_sandbox(prepared)?;
    let stdout_thread = thread::spawn(move || read_bounded(spawned.stdout));
    let stderr_thread = thread::spawn(move || read_bounded(spawned.stderr));

    let started = Instant::now();
    let mut timed_out = false;
    let status = loop {
        match spawned.child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if started.elapsed() >= spawned.timeout => {
                timed_out = true;
                let pid = spawned.child.id() as i32;
                unsafe {
                    libc::killpg(pid, libc::SIGKILL);
                }
                break spawned.child.wait().map_err(|error| {
                    format!("sandbox-exec wait after timeout failed: {error}")
                })?;
            }
            Ok(None) => thread::sleep(Duration::from_millis(10)),
            Err(error) => return Err(format!("sandbox-exec wait failed: {error}")),
        }
    };

    let stdout = stdout_thread
        .join()
        .map_err(|_| "stdout reader panicked".to_string())?;
    let stderr = stderr_thread
        .join()
        .map_err(|_| "stderr reader panicked".to_string())?;

    let exit_code = if timed_out {
        137
    } else {
        status
            .code()
            .unwrap_or_else(|| 128 + status.signal().unwrap_or(1))
    };
    let success = !timed_out && exit_code == 0;
    Ok(RunCommandResult {
        stdout,
        stderr,
        exit_code,
        success,
        command_success: success,
        timed_out,
        provenance: PROVENANCE.to_string(),
    })
}

fn read_bounded(mut reader: impl Read) -> String {
    let mut collected = Vec::new();
    let mut buf = [0_u8; 8192];
    loop {
        match reader.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => {
                let room = MAX_STREAM_BYTES.saturating_sub(collected.len());
                collected.extend_from_slice(&buf[..n.min(room)]);
                if collected.len() >= MAX_STREAM_BYTES {
                    let mut drain = [0_u8; 8192];
                    while reader.read(&mut drain).ok().unwrap_or(0) > 0 {}
                    break;
                }
            }
            Err(_) => break,
        }
    }
    String::from_utf8_lossy(&collected).into_owned()
}

#[derive(Clone)]
pub struct ProcessRuntime {
    inner: Arc<ProcessRuntimeInner>,
}

struct ProcessRuntimeInner {
    backend_key_sha256: [u8; 32],
    policy: ProcessSandboxPolicy,
    workspace_id: String,
    jobs: JobTable,
}

impl ProcessRuntime {
    pub fn new(
        backend_key: &[u8],
        policy: ProcessSandboxPolicy,
        workspace_id: impl Into<String>,
    ) -> Result<Self, String> {
        if backend_key.len() < 32 {
            return Err("Process Runtime backend key must be at least 32 bytes".into());
        }
        let workspace_id = workspace_id.into();
        if workspace_id.trim().is_empty() {
            return Err("Process Runtime workspace_id must not be empty".into());
        }
        let mut backend_key_sha256 = [0_u8; 32];
        backend_key_sha256.copy_from_slice(&Sha256::digest(backend_key));
        Ok(Self {
            inner: Arc::new(ProcessRuntimeInner {
                backend_key_sha256,
                policy,
                workspace_id,
                jobs: JobTable::new(),
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
    State(runtime): State<ProcessRuntime>,
    headers: HeaderMap,
    body: Bytes,
) -> Response<Body> {
    if !valid_backend_key(&headers, &runtime.inner.backend_key_sha256) {
        return text_response(
            StatusCode::UNAUTHORIZED,
            "Process Runtime backend authentication failed",
        );
    }

    let parsed: Value = match serde_json::from_slice(&body) {
        Ok(value) => value,
        Err(_) => return text_response(StatusCode::BAD_REQUEST, "invalid JSON-RPC body"),
    };
    let method = match parsed.get("method").and_then(Value::as_str) {
        Some(value) => value,
        None => return text_response(StatusCode::BAD_REQUEST, "JSON-RPC method is required"),
    };
    let id = parsed.get("id").cloned().unwrap_or(Value::Null);

    match method {
        "server/discover" | "initialize" => json_response(
            StatusCode::OK,
            json!({
                "jsonrpc": "2.0",
                "id": id,
                "result": {
                    "serverInfo": {"name": "process-runtime", "version": "0.1.0"},
                    "capabilities": {"tools": {}}
                }
            }),
        ),
        "tools/list" => json_response(
            StatusCode::OK,
            json!({"jsonrpc":"2.0","id":id,"result":{"tools":tool_specs()}}),
        ),
        "tools/call" => {
            let binding = match signed_bridge_binding(
                &headers,
                &body,
                &runtime.inner.workspace_id,
            ) {
                Ok(value) => value,
                Err(error) => return text_response(StatusCode::FORBIDDEN, &error),
            };
            let params = parsed.get("params").cloned().unwrap_or_else(|| json!({}));
            let name = match params.get("name").and_then(Value::as_str) {
                Some(value) => value,
                None => {
                    return text_response(
                        StatusCode::BAD_REQUEST,
                        "tools/call requires params.name",
                    );
                }
            };
            let arguments = params
                .get("arguments")
                .cloned()
                .unwrap_or_else(|| json!({}));
            match name {
                "run_command" => {
                    let request = match parse_command_arguments("run_command", &arguments) {
                        Ok(value) => value,
                        Err(error) => return text_response(StatusCode::BAD_REQUEST, &error),
                    };
                    let policy = runtime.inner.policy.clone();
                    match tokio::task::spawn_blocking(move || policy.run(request)).await {
                        Ok(Ok(result)) => tool_result(id, json!(result)),
                        Ok(Err(error)) => text_response(StatusCode::FORBIDDEN, &error),
                        Err(error) => text_response(
                            StatusCode::INTERNAL_SERVER_ERROR,
                            &format!("Process Runtime worker failed: {error}"),
                        ),
                    }
                }
                "start_command" => {
                    let request = match parse_command_arguments("start_command", &arguments) {
                        Ok(value) => value,
                        Err(error) => return text_response(StatusCode::BAD_REQUEST, &error),
                    };
                    dispatch_job(id, move || {
                        runtime.inner.jobs.start(
                            &runtime.inner.policy,
                            &runtime.inner.workspace_id,
                            &binding,
                            request,
                        )
                    })
                    .await
                }
                "poll_command" => {
                    let job_id = match parse_job_id_arguments("poll_command", &arguments) {
                        Ok(value) => value,
                        Err(error) => return text_response(StatusCode::BAD_REQUEST, &error),
                    };
                    job_result(id, runtime.inner.jobs.poll(&binding, &job_id))
                }
                "cancel_command" => {
                    let job_id = match parse_job_id_arguments("cancel_command", &arguments) {
                        Ok(value) => value,
                        Err(error) => return text_response(StatusCode::BAD_REQUEST, &error),
                    };
                    job_result(id, runtime.inner.jobs.cancel(&binding, &job_id))
                }
                _ => text_response(
                    StatusCode::FORBIDDEN,
                    "Process Runtime v1 exposes only run_command/start_command/poll_command/cancel_command",
                ),
            }
        }
        method if method.starts_with("notifications/") => Response::builder()
            .status(StatusCode::NO_CONTENT)
            .body(Body::empty())
            .expect("static response"),
        _ => text_response(
            StatusCode::FORBIDDEN,
            "unsupported Process Runtime MCP method",
        ),
    }
}

fn parse_command_arguments(tool: &str, arguments: &Value) -> Result<RunCommandRequest, String> {
    let command = arguments
        .get("command")
        .and_then(Value::as_str)
        .ok_or_else(|| format!("{tool} requires arguments.command"))?
        .to_string();
    let cwd = match arguments.get("cwd") {
        None | Some(Value::Null) => None,
        Some(Value::String(value)) => Some(value.clone()),
        Some(_) => return Err(format!("{tool} cwd must be a string")),
    };
    let timeout_ms = match arguments.get("timeout_ms") {
        None | Some(Value::Null) => 30_000,
        Some(value) => value
            .as_u64()
            .ok_or_else(|| format!("{tool} timeout_ms must be a positive integer"))?,
    };
    Ok(RunCommandRequest {
        command,
        cwd,
        timeout_ms,
    })
}

fn parse_job_id_arguments(tool: &str, arguments: &Value) -> Result<String, String> {
    let job_id = arguments
        .get("job_id")
        .and_then(Value::as_str)
        .ok_or_else(|| format!("{tool} requires arguments.job_id"))?;
    if job_id.is_empty() || job_id.len() > 128 {
        return Err(format!("{tool} job_id is invalid"));
    }
    if job_id.as_bytes().contains(&0) {
        return Err(format!("{tool} job_id contains NUL"));
    }
    Ok(job_id.to_string())
}

fn job_result(id: Value, result: Result<JobSnapshot, String>) -> Response<Body> {
    match result {
        Ok(snapshot) => tool_result(id, json!(snapshot)),
        Err(error) => text_response(StatusCode::FORBIDDEN, &error),
    }
}

async fn dispatch_job(
    id: Value,
    work: impl FnOnce() -> Result<JobSnapshot, String> + Send + 'static,
) -> Response<Body> {
    match tokio::task::spawn_blocking(work).await {
        Ok(Ok(snapshot)) => tool_result(id, json!(snapshot)),
        Ok(Err(error)) => text_response(StatusCode::FORBIDDEN, &error),
        Err(error) => text_response(
            StatusCode::INTERNAL_SERVER_ERROR,
            &format!("Process Runtime worker failed: {error}"),
        ),
    }
}

fn signed_bridge_binding(
    headers: &HeaderMap,
    body: &[u8],
    expected_workspace_id: &str,
) -> Result<BridgeBinding, String> {
    let required = |name: &str| {
        headers
            .get(name)
            .and_then(|value| value.to_str().ok())
            .filter(|value| !value.trim().is_empty())
            .map(str::to_string)
            .ok_or_else(|| format!("missing signed Bridge header: {name}"))
    };
    if required("x-lab-ingress-provenance")? != SIGNED_PROVENANCE {
        return Err("Process Runtime requires signed Local Tool Bridge provenance".into());
    }
    let expected_body = required("x-lab-body-sha256")?;
    let actual_body = hex_sha256(body);
    if expected_body != actual_body {
        return Err("signed Bridge body hash does not match Process Runtime request".into());
    }
    let _request_id = required("x-lab-request-id")?;
    let _nonce = required("x-lab-request-nonce")?;
    let _mac = required("x-lab-request-mac")?;
    let principal_id = required("x-lab-principal-id")?;
    let execution_session_id = required("x-lab-execution-session-id")?;
    let _capability = required("x-lab-capability-id")?;
    let workspace_id = required("x-lab-workspace-id")?;
    if workspace_id != expected_workspace_id {
        return Err("signed Bridge workspace_id does not match Process Runtime workspace".into());
    }
    Ok(BridgeBinding {
        principal_id,
        execution_session_id,
    })
}

fn valid_backend_key(headers: &HeaderMap, expected: &[u8; 32]) -> bool {
    let Some(value) = headers
        .get(BACKEND_KEY_HEADER)
        .and_then(|value| value.to_str().ok())
    else {
        return false;
    };
    let digest = Sha256::digest(value.as_bytes());
    digest.as_slice().ct_eq(expected).into()
}

fn hex_sha256(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn command_input_schema() -> Value {
    json!({
        "type": "object",
        "required": ["command"],
        "properties": {
            "command": {"type": "string", "minLength": 1, "maxLength": 65536},
            "cwd": {"type": "string"},
            "timeout_ms": {"type": "integer", "minimum": 1, "maximum": 120000}
        }
    })
}

fn job_id_input_schema() -> Value {
    json!({
        "type": "object",
        "required": ["job_id"],
        "properties": {
            "job_id": {"type": "string", "minLength": 1, "maxLength": 128}
        }
    })
}

fn tool_specs() -> Vec<Value> {
    vec![
        json!({
            "name": "run_command",
            "description": "LAB Process Runtime v1 bounded command",
            "inputSchema": command_input_schema()
        }),
        json!({
            "name": "start_command",
            "description": "LAB Process Runtime v1 start a bounded background job",
            "inputSchema": command_input_schema()
        }),
        json!({
            "name": "poll_command",
            "description": "LAB Process Runtime v1 poll a bounded background job",
            "inputSchema": job_id_input_schema()
        }),
        json!({
            "name": "cancel_command",
            "description": "LAB Process Runtime v1 cancel a bounded background job",
            "inputSchema": job_id_input_schema()
        }),
    ]
}

fn tool_result(id: Value, value: Value) -> Response<Body> {
    json_response(
        StatusCode::OK,
        json!({
            "jsonrpc":"2.0",
            "id":id,
            "result":{
                "content":[{"type":"text","text":value.to_string()}],
                "structuredContent":value
            }
        }),
    )
}

fn json_response(status: StatusCode, value: Value) -> Response<Body> {
    Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(value.to_string()))
        .expect("static response")
}

fn text_response(status: StatusCode, message: &str) -> Response<Body> {
    Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, "text/plain; charset=utf-8")
        .body(Body::from(message.to_string()))
        .expect("static response")
}

fn validated_absolute_dir(path: &Path, label: &str) -> Result<PathBuf, String> {
    let normalized = lexical_absolute(path, label)?;
    let meta = fs::symlink_metadata(&normalized)
        .map_err(|error| format!("{label} inspect failed: {error}"))?;
    if meta.file_type().is_symlink() || !meta.is_dir() {
        return Err(format!("{label} must be a real directory"));
    }
    Ok(normalized)
}

fn lexical_absolute(path: &Path, label: &str) -> Result<PathBuf, String> {
    use std::path::Component;
    if !path.is_absolute() {
        return Err(format!("{label} must be absolute"));
    }
    let mut out = PathBuf::new();
    for component in path.components() {
        match component {
            Component::RootDir => out.push("/"),
            Component::Normal(value) => out.push(value),
            Component::CurDir => {}
            Component::ParentDir => {
                return Err(format!("{label} must not contain parent traversal"));
            }
            Component::Prefix(_) => return Err(format!("{label} has an unsupported prefix")),
        }
    }
    Ok(out)
}

fn prepare_private_dir(path: &Path, label: &str) -> Result<PathBuf, String> {
    if !path.is_absolute() {
        return Err(format!("{label} must be absolute"));
    }
    fs::create_dir_all(path).map_err(|error| format!("{label} create failed: {error}"))?;
    let raw = fs::symlink_metadata(path)
        .map_err(|error| format!("{label} inspect failed: {error}"))?;
    if raw.file_type().is_symlink() || !raw.is_dir() {
        return Err(format!("{label} must be a real directory"));
    }
    let euid = unsafe { libc::geteuid() };
    if raw.uid() != euid {
        return Err(format!("{label} owner does not match current euid"));
    }
    fs::set_permissions(path, fs::Permissions::from_mode(0o700))
        .map_err(|error| format!("{label} chmod failed: {error}"))?;
    lexical_absolute(path, label)
}

fn reject_root_overlap(left: &Path, right: &Path, label: &str) -> Result<(), String> {
    if left == right || left.starts_with(right) || right.starts_with(left) {
        return Err(format!(
            "{label} must not overlap another Process Runtime root"
        ));
    }
    Ok(())
}

fn load_authorized_cwd_roots() -> Vec<PathBuf> {
    let Ok(path) = std::env::var("PROCESS_RUNTIME_AUTHORIZED_ROOTS_FILE") else {
        return Vec::new();
    };
    let Ok(raw) = fs::read_to_string(path) else {
        return Vec::new();
    };
    let mut roots = Vec::new();
    for line in raw.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let Ok(canonical) = PathBuf::from(line).canonicalize() else {
            continue;
        };
        if canonical.is_dir() && !roots.contains(&canonical) {
            roots.push(canonical);
        }
    }
    roots
}

fn path_is_under(path: &Path, root: &Path) -> bool {
    path == root || path.starts_with(root)
}

fn resolve_cwd(workspace: &Path, extra: &[PathBuf], raw: Option<&str>) -> Result<PathBuf, String> {
    use std::path::Component;

    let relative = match raw {
        None => PathBuf::new(),
        Some(value) if value.trim().is_empty() => {
            return Err("cwd must not be empty when provided".into());
        }
        Some(value) => {
            let path = PathBuf::from(value);
            if path.is_absolute() {
                let canonical = path.canonicalize().map_err(|error| {
                    format!("cwd inspect failed: {error}")
                })?;
                if path_is_under(&canonical, workspace)
                    || extra.iter().any(|root| path_is_under(&canonical, root))
                {
                    if !canonical.is_dir() {
                        return Err("cwd must be a directory".into());
                    }
                    return Ok(canonical);
                }
                return Err(
                    "cwd must resolve inside the workspace or an authorized directory".into(),
                );
            } else {
                path
            }
        }
    };

    let mut current = workspace.to_path_buf();
    for component in relative.components() {
        match component {
            Component::CurDir => continue,
            Component::Normal(value) => current.push(value),
            Component::ParentDir | Component::RootDir | Component::Prefix(_) => {
                return Err("cwd must not escape the Process Runtime workspace".into());
            }
        }
        let meta = fs::symlink_metadata(&current)
            .map_err(|error| format!("cwd inspect failed: {error}"))?;
        if meta.file_type().is_symlink() {
            return Err("cwd must not traverse symlinks".into());
        }
    }
    let meta =
        fs::metadata(&current).map_err(|error| format!("cwd inspect failed: {error}"))?;
    if !meta.is_dir() {
        return Err("cwd must be a directory inside the Process Runtime workspace".into());
    }
    Ok(current)
}

fn seatbelt_escape(path: &Path) -> Result<String, String> {
    let raw = path
        .to_str()
        .ok_or_else(|| "sandbox path must be valid UTF-8".to_string())?;
    if raw.chars().any(|value| matches!(value, '"' | '\n' | '\r')) {
        return Err("sandbox path contains unsupported characters".into());
    }
    Ok(raw.replace('\\', "\\\\").replace('"', "\\\""))
}

fn build_profile(
    workspace: &Path,
    scratch: &Path,
    cache: &Path,
    trusted: &[PathBuf],
    extra: &[PathBuf],
) -> Result<String, String> {
    let workspace = seatbelt_escape(workspace)?;
    let scratch = seatbelt_escape(scratch)?;
    let cache = seatbelt_escape(cache)?;

    let mut toolchains = String::new();
    for root in trusted {
        let root = seatbelt_escape(root)?;
        toolchains.push_str(&format!(
            "(allow file-read-metadata (subpath \"{root}\"))\n(allow file-read-data (subpath \"{root}\"))\n"
        ));
    }

    for root in extra {
        let root = seatbelt_escape(root)?;
        toolchains.push_str(&format!(
            "(allow file-read-metadata (subpath \"{root}\"))\n(allow file-read-data (subpath \"{root}\"))\n(allow file-write* (subpath \"{root}\"))\n"
        ));
    }

    Ok(format!(
        r#"(version 1)
(deny default)
(allow process-exec)
(allow process-fork)
(allow sysctl-read)
(allow signal (target self))
(allow signal (target children))
(allow file-read-metadata
  (require-all
    (require-not (subpath "/Users"))
    (require-not (subpath "/Volumes"))
    (require-not (subpath "/private/tmp"))
    (require-not (subpath "/tmp"))
    (require-not (subpath "/private/var/folders"))))
(allow file-read-data
  (require-all
    (require-not (subpath "/Users"))
    (require-not (subpath "/Volumes"))
    (require-not (subpath "/private/tmp"))
    (require-not (subpath "/tmp"))
    (require-not (subpath "/private/var/folders"))))
(allow file-read-metadata (literal "/dev/null"))
(allow file-read-data (literal "/dev/null"))
(allow file-write* (literal "/dev/null"))
(allow file-read-metadata (subpath "{workspace}"))
(allow file-read-data (subpath "{workspace}"))
(allow file-write* (subpath "{workspace}"))
(allow file-read-metadata (subpath "{scratch}"))
(allow file-read-data (subpath "{scratch}"))
(allow file-write* (subpath "{scratch}"))
(allow file-read-metadata (subpath "{cache}"))
(allow file-read-data (subpath "{cache}"))
(allow file-write* (subpath "{cache}"))
{toolchains}"#
    ))
}
