mod feishu;

pub use feishu::{
    Identity, LarkCliAdminAction, LarkCliAdminSpec, LarkCliFixedCommand, LarkCliInstallSpec,
    LarkCliProvider, LARK_CLI_PINNED_VERSION,
};

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CollaborationErrorKind {
    CliUnavailable,
    AuthRequired,
    PermissionDenied,
    InvalidArgument,
    ProviderRateLimited,
    ProviderRejected,
    TimeoutBeforeDispatch,
    ProviderTimeoutAfterDispatch,
    ResultUnknownAfterDispatch,
    InvalidProviderOutput,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CollaborationError {
    pub kind: CollaborationErrorKind,
    pub message: String,
    pub dispatch_attempted: bool,
}

impl CollaborationError {
    pub fn invalid_argument(message: impl Into<String>) -> Self {
        Self {
            kind: CollaborationErrorKind::InvalidArgument,
            message: message.into(),
            dispatch_attempted: false,
        }
    }

    fn new(
        kind: CollaborationErrorKind,
        message: impl Into<String>,
        dispatch_attempted: bool,
    ) -> Self {
        Self {
            kind,
            message: message.into(),
            dispatch_attempted,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ProcessOutput {
    pub exit_code: i32,
    pub stdout: String,
    pub stderr: String,
    pub dispatch_attempted: bool,
}

pub trait ProviderRunner {
    fn run(
        &self,
        program: &str,
        args: &[String],
        timeout: std::time::Duration,
        output_limit: usize,
        write_operation: bool,
    ) -> Result<ProcessOutput, CollaborationError>;

    fn run_with_env(
        &self,
        program: &str,
        args: &[String],
        env: &[(String, String)],
        timeout: std::time::Duration,
        output_limit: usize,
        write_operation: bool,
    ) -> Result<ProcessOutput, CollaborationError> {
        let _ = env;
        self.run(program, args, timeout, output_limit, write_operation)
    }
}

#[derive(Clone, Copy, Debug, Default)]
pub struct ProcessRunner;

impl ProviderRunner for ProcessRunner {
    fn run(
        &self,
        program: &str,
        args: &[String],
        timeout: std::time::Duration,
        output_limit: usize,
        write_operation: bool,
    ) -> Result<ProcessOutput, CollaborationError> {
        ProcessRunner::run(self, program, args, timeout, output_limit, write_operation)
    }

    fn run_with_env(
        &self,
        program: &str,
        args: &[String],
        env: &[(String, String)],
        timeout: std::time::Duration,
        output_limit: usize,
        write_operation: bool,
    ) -> Result<ProcessOutput, CollaborationError> {
        ProcessRunner::run_with_env(
            self,
            program,
            args,
            env,
            timeout,
            output_limit,
            write_operation,
        )
    }
}

impl ProcessRunner {
    pub fn run(
        &self,
        program: &str,
        args: &[String],
        timeout: std::time::Duration,
        output_limit: usize,
        write_operation: bool,
    ) -> Result<ProcessOutput, CollaborationError> {
        self.run_internal(program, args, &[], timeout, output_limit, write_operation)
    }

    pub fn run_with_env(
        &self,
        program: &str,
        args: &[String],
        env: &[(String, String)],
        timeout: std::time::Duration,
        output_limit: usize,
        write_operation: bool,
    ) -> Result<ProcessOutput, CollaborationError> {
        self.run_internal(program, args, env, timeout, output_limit, write_operation)
    }

    fn run_internal(
        &self,
        program: &str,
        args: &[String],
        env: &[(String, String)],
        timeout: std::time::Duration,
        output_limit: usize,
        write_operation: bool,
    ) -> Result<ProcessOutput, CollaborationError> {
        use std::io::Read;
        use std::process::{Command, Stdio};
        use std::thread;
        use std::time::{Duration, Instant};

        if output_limit == 0 {
            return Err(CollaborationError::invalid_argument(
                "output_limit must be positive",
            ));
        }

        let mut command = Command::new(program);
        let mut child = command
            .args(args)
            .env_clear()
            .envs(env.iter().map(|(key, value)| (key, value)))
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|error| {
                CollaborationError::new(
                    CollaborationErrorKind::CliUnavailable,
                    format!("provider process launch failed: {error}"),
                    false,
                )
            })?;

        drop(child.stdin.take());
        let stdout = child.stdout.take().ok_or_else(|| {
            CollaborationError::new(
                CollaborationErrorKind::InvalidProviderOutput,
                "provider stdout pipe is unavailable",
                true,
            )
        })?;
        let stderr = child.stderr.take().ok_or_else(|| {
            CollaborationError::new(
                CollaborationErrorKind::InvalidProviderOutput,
                "provider stderr pipe is unavailable",
                true,
            )
        })?;

        fn collect<R: Read + Send + 'static>(
            mut reader: R,
            limit: usize,
        ) -> thread::JoinHandle<Result<(Vec<u8>, bool), String>> {
            thread::spawn(move || {
                let mut bytes = Vec::with_capacity(limit.min(8192));
                let mut overflow = false;
                let mut buffer = [0u8; 4096];
                loop {
                    let read = reader
                        .read(&mut buffer)
                        .map_err(|error| format!("provider pipe read failed: {error}"))?;
                    if read == 0 {
                        break;
                    }
                    let remaining = limit.saturating_sub(bytes.len());
                    if read > remaining {
                        if remaining > 0 {
                            bytes.extend_from_slice(&buffer[..remaining]);
                        }
                        overflow = true;
                    } else {
                        bytes.extend_from_slice(&buffer[..read]);
                    }
                }
                Ok((bytes, overflow))
            })
        }

        let stdout_reader = collect(stdout, output_limit);
        let stderr_reader = collect(stderr, output_limit);
        let deadline = Instant::now() + timeout;
        let status = loop {
            match child.try_wait() {
                Ok(Some(status)) => break status,
                Ok(None) if Instant::now() < deadline => {
                    thread::sleep(Duration::from_millis(5));
                }
                Ok(None) => {
                    let _ = child.kill();
                    let _ = child.wait();
                    let _ = stdout_reader.join();
                    let _ = stderr_reader.join();
                    let kind = if write_operation {
                        CollaborationErrorKind::ResultUnknownAfterDispatch
                    } else {
                        CollaborationErrorKind::ProviderTimeoutAfterDispatch
                    };
                    return Err(CollaborationError::new(
                        kind,
                        "provider process timed out after dispatch",
                        true,
                    ));
                }
                Err(error) => {
                    let _ = child.kill();
                    let _ = child.wait();
                    let _ = stdout_reader.join();
                    let _ = stderr_reader.join();
                    return Err(CollaborationError::new(
                        CollaborationErrorKind::ProviderRejected,
                        format!("provider process wait failed: {error}"),
                        true,
                    ));
                }
            }
        };

        let (stdout, stdout_overflow) = stdout_reader
            .join()
            .map_err(|_| {
                CollaborationError::new(
                    CollaborationErrorKind::InvalidProviderOutput,
                    "provider stdout reader panicked",
                    true,
                )
            })?
            .map_err(|error| {
                CollaborationError::new(
                    CollaborationErrorKind::InvalidProviderOutput,
                    error,
                    true,
                )
            })?;
        let (stderr, stderr_overflow) = stderr_reader
            .join()
            .map_err(|_| {
                CollaborationError::new(
                    CollaborationErrorKind::InvalidProviderOutput,
                    "provider stderr reader panicked",
                    true,
                )
            })?
            .map_err(|error| {
                CollaborationError::new(
                    CollaborationErrorKind::InvalidProviderOutput,
                    error,
                    true,
                )
            })?;

        if stdout_overflow || stderr_overflow {
            return Err(CollaborationError::new(
                CollaborationErrorKind::InvalidProviderOutput,
                "provider output exceeded configured limit",
                true,
            ));
        }

        let stdout = String::from_utf8(stdout).map_err(|_| {
            CollaborationError::new(
                CollaborationErrorKind::InvalidProviderOutput,
                "provider stdout is not UTF-8",
                true,
            )
        })?;
        let stderr = String::from_utf8(stderr).map_err(|_| {
            CollaborationError::new(
                CollaborationErrorKind::InvalidProviderOutput,
                "provider stderr is not UTF-8",
                true,
            )
        })?;

        if !status.success() {
            let stdout_trim = stdout.trim();
            let stderr_trim = stderr.trim();
            let detail = if !stdout_trim.is_empty() {
                stdout_trim
            } else if !stderr_trim.is_empty() {
                stderr_trim
            } else {
                ""
            };
            let no_change = detail.contains("no document changes")
                || detail.contains("degrade_code=1011");
            let message = if no_change {
                "str_replace produced no document changes. Fetch the current document and use an exact existing substring as pattern. Do not treat this as Feishu being unavailable.".to_string()
            } else if detail.is_empty() {
                format!("provider exited with status {status}")
            } else {
                detail.chars().take(1200).collect()
            };
            let kind = if no_change {
                CollaborationErrorKind::InvalidArgument
            } else {
                CollaborationErrorKind::ProviderRejected
            };
            return Err(CollaborationError::new(kind, message, true));
        }

        Ok(ProcessOutput {
            exit_code: status.code().unwrap_or_default(),
            stdout,
            stderr,
            dispatch_attempted: true,
        })
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CollaborationOperation {
    Status,
    BaseListTables {
        base_token: String,
    },
    BaseListFields {
        base_token: String,
        table_id: String,
    },
    BaseListRecords {
        base_token: String,
        table_id: String,
        view_id: Option<String>,
        page_size: Option<u16>,
        page_token: Option<String>,
    },
    BaseCreate {
        name: String,
        folder_token: Option<String>,
        table_name: Option<String>,
        fields_json: Option<String>,
        time_zone: Option<String>,
    },
    BaseCreateView {
        base_token: String,
        table_id: String,
        view_json: String,
    },
    BaseCreateRecord {
        base_token: String,
        table_id: String,
        fields_json: String,
    },
    BaseUpdateRecord {
        base_token: String,
        table_id: String,
        record_id: String,
        fields_json: String,
    },
    ChatSearch {
        query: String,
        page_size: Option<u16>,
    },
    MessageList {
        chat_id: String,
        page_size: Option<u16>,
        page_token: Option<String>,
    },
    MessageSend {
        chat_id: String,
        text: String,
        identity: Identity,
        idempotency_key: String,
    },
    MessageReply {
        message_id: String,
        text: String,
        identity: Identity,
        idempotency_key: String,
    },
    WikiNodeGet {
        node_token: String,
    },
    DocsSearch {
        query: String,
        page_size: Option<u16>,
    },
    DocsFetch {
        doc: String,
        doc_format: Option<String>,
    },
    DocsUpdate {
        doc: String,
        command: String,
        pattern: Option<String>,
        content: Option<String>,
        doc_format: Option<String>,
        block_id: Option<String>,
        src_block_ids: Option<String>,
    },
    DocsMediaInsert {
        doc: String,
        file: String,
        width: Option<u16>,
        caption: Option<String>,
        align: Option<String>,
    },
}

impl CollaborationOperation {
    pub fn is_write(&self) -> bool {
        matches!(
            self,
            Self::BaseCreate { .. }
                | Self::BaseCreateView { .. }
                | Self::BaseCreateRecord { .. }
                | Self::BaseUpdateRecord { .. }
                | Self::MessageSend { .. }
                | Self::MessageReply { .. }
                | Self::DocsUpdate { .. }
                | Self::DocsMediaInsert { .. }
        )
    }
}
