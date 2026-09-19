use crate::{
    BridgeBinding, ProcessSandboxPolicy, RunCommandRequest, SpawnedProcess, PROVENANCE,
    spawn_sandbox, MAX_STREAM_BYTES,
};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::{
    collections::HashMap,
    io::Read,
    os::unix::process::ExitStatusExt,
    sync::{
        atomic::{AtomicU64, AtomicUsize, Ordering},
        Arc, Mutex,
    },
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

const MAX_ACTIVE_JOBS: usize = 8;
const MAX_RETAINED_JOBS: usize = 32;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum JobState {
    Running,
    Succeeded,
    Failed,
    Cancelled,
    TimedOut,
    Lost,
}

impl JobState {
    fn as_str(self) -> &'static str {
        match self {
            Self::Running => "running",
            Self::Succeeded => "succeeded",
            Self::Failed => "failed",
            Self::Cancelled => "cancelled",
            Self::TimedOut => "timed_out",
            Self::Lost => "lost",
        }
    }
}

struct JobRecord {
    job_id: String,
    owner_principal: String,
    owner_session: String,
    state: JobState,
    stdout: Vec<u8>,
    stderr: Vec<u8>,
    exit_code: i32,
    timed_out: bool,
    pid: i32,
    cancel_requested: bool,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct JobSnapshot {
    pub job_id: String,
    pub state: String,
    pub stdout: String,
    pub stderr: String,
    pub exit_code: i32,
    pub success: bool,
    pub timed_out: bool,
    pub provenance: String,
}

pub struct JobTable {
    jobs: Mutex<HashMap<String, Arc<Mutex<JobRecord>>>>,
    seq: AtomicU64,
    active: Arc<AtomicUsize>,
}

impl JobTable {
    pub fn new() -> Self {
        Self {
            jobs: Mutex::new(HashMap::new()),
            seq: AtomicU64::new(1),
            active: Arc::new(AtomicUsize::new(0)),
        }
    }

    pub fn start(
        &self,
        policy: &ProcessSandboxPolicy,
        workspace_id: &str,
        binding: &BridgeBinding,
        request: RunCommandRequest,
    ) -> Result<JobSnapshot, String> {
        let prepared = policy.prepare(request)?;
        let previous = self.active.fetch_add(1, Ordering::SeqCst);
        if previous >= MAX_ACTIVE_JOBS {
            self.active.fetch_sub(1, Ordering::SeqCst);
            return Err("Process Runtime active job limit reached".into());
        }

        let spawned = match spawn_sandbox(prepared) {
            Ok(value) => value,
            Err(error) => {
                self.active.fetch_sub(1, Ordering::SeqCst);
                return Err(error);
            }
        };

        let job_id = self.new_job_id(workspace_id);
        let record = Arc::new(Mutex::new(JobRecord {
            job_id: job_id.clone(),
            owner_principal: binding.principal_id.clone(),
            owner_session: binding.execution_session_id.clone(),
            state: JobState::Running,
            stdout: Vec::new(),
            stderr: Vec::new(),
            exit_code: -1,
            timed_out: false,
            pid: spawned.child.id() as i32,
            cancel_requested: false,
        }));

        {
            let mut jobs = lock_map(&self.jobs);
            evict_terminal_if_needed(&mut jobs);
            jobs.insert(job_id.clone(), Arc::clone(&record));
        }

        spawn_waiter(spawned, Arc::clone(&record), Arc::clone(&self.active));

        Ok(snapshot(&lock_job(&record)))
    }

    pub fn poll(&self, binding: &BridgeBinding, job_id: &str) -> Result<JobSnapshot, String> {
        let record = self.lookup(binding, job_id)?;
        Ok(snapshot(&lock_job(&record)))
    }

    pub fn cancel(&self, binding: &BridgeBinding, job_id: &str) -> Result<JobSnapshot, String> {
        let record = self.lookup(binding, job_id)?;
        {
            let mut job = lock_job(&record);
            if job.state == JobState::Running {
                job.cancel_requested = true;
                kill_process_group(job.pid);
            }
        }
        Ok(snapshot(&lock_job(&record)))
    }

    fn lookup(
        &self,
        binding: &BridgeBinding,
        job_id: &str,
    ) -> Result<Arc<Mutex<JobRecord>>, String> {
        let jobs = lock_map(&self.jobs);
        let Some(record) = jobs.get(job_id).cloned() else {
            return Err("unknown job_id".into());
        };
        let job = lock_job(&record);
        if job.owner_principal != binding.principal_id
            || job.owner_session != binding.execution_session_id
        {
            return Err("job owner does not match signed Bridge execution context".into());
        }
        drop(job);
        Ok(record)
    }

    fn new_job_id(&self, workspace_id: &str) -> String {
        let seq = self.seq.fetch_add(1, Ordering::Relaxed);
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        let digest = Sha256::digest(format!("{seq}:{nanos}:{workspace_id}").as_bytes());
        digest.iter().take(16).map(|byte| format!("{byte:02x}")).collect()
    }
}

fn spawn_waiter(
    mut spawned: SpawnedProcess,
    record: Arc<Mutex<JobRecord>>,
    active: Arc<AtomicUsize>,
) {
    thread::spawn(move || {
        let stdout = spawned.stdout;
        let stderr = spawned.stderr;
        let stdout_job = Arc::clone(&record);
        let stderr_job = Arc::clone(&record);
        let stdout_thread = thread::spawn(move || stream_into_job(stdout, stdout_job, false));
        let stderr_thread = thread::spawn(move || stream_into_job(stderr, stderr_job, true));

        let started = Instant::now();
        let timeout = spawned.timeout;
        let status = loop {
            let (cancel_requested, timed_out_already) = {
                let job = lock_job(&record);
                (job.cancel_requested, job.timed_out)
            };
            match spawned.child.try_wait() {
                Ok(Some(status)) => break Some(status),
                Ok(None) if cancel_requested => {
                    let pid = spawned.child.id() as i32;
                    kill_process_group(pid);
                    match spawned.child.wait() {
                        Ok(status) => break Some(status),
                        Err(_) => break None,
                    }
                }
                Ok(None) if !timed_out_already && started.elapsed() >= timeout => {
                    {
                        let mut job = lock_job(&record);
                        job.timed_out = true;
                    }
                    let pid = spawned.child.id() as i32;
                    kill_process_group(pid);
                    match spawned.child.wait() {
                        Ok(status) => break Some(status),
                        Err(_) => break None,
                    }
                }
                Ok(None) => thread::sleep(Duration::from_millis(10)),
                Err(_) => break None,
            }
        };

        let _ = stdout_thread.join();
        let _ = stderr_thread.join();

        let mut job = lock_job(&record);
        if job.state == JobState::Running {
            if status.is_none() {
                job.state = JobState::Lost;
                job.exit_code = -1;
            } else if job.cancel_requested {
                job.state = JobState::Cancelled;
                job.exit_code = 137;
            } else if job.timed_out {
                job.state = JobState::TimedOut;
                job.exit_code = 137;
            } else {
                let status = status.expect("checked");
                let exit_code = status
                    .code()
                    .unwrap_or_else(|| 128 + status.signal().unwrap_or(1));
                job.exit_code = exit_code;
                job.state = if exit_code == 0 {
                    JobState::Succeeded
                } else {
                    JobState::Failed
                };
            }
            active.fetch_sub(1, Ordering::SeqCst);
        }
    });
}

fn stream_into_job(mut reader: impl Read, job: Arc<Mutex<JobRecord>>, stderr: bool) {
    let mut buf = [0_u8; 8192];
    loop {
        match reader.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => {
                let mut record = lock_job(&job);
                let target = if stderr {
                    &mut record.stderr
                } else {
                    &mut record.stdout
                };
                let room = MAX_STREAM_BYTES.saturating_sub(target.len());
                target.extend_from_slice(&buf[..n.min(room)]);
                let full = target.len() >= MAX_STREAM_BYTES;
                drop(record);
                if full {
                    let mut drain = [0_u8; 8192];
                    while reader.read(&mut drain).ok().unwrap_or(0) > 0 {}
                    break;
                }
            }
            Err(_) => break,
        }
    }
}

fn snapshot(job: &JobRecord) -> JobSnapshot {
    JobSnapshot {
        job_id: job.job_id.clone(),
        state: job.state.as_str().to_string(),
        stdout: String::from_utf8_lossy(&job.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&job.stderr).into_owned(),
        exit_code: job.exit_code,
        success: job.state == JobState::Succeeded,
        timed_out: job.timed_out || job.state == JobState::TimedOut,
        provenance: PROVENANCE.to_string(),
    }
}

fn evict_terminal_if_needed(jobs: &mut HashMap<String, Arc<Mutex<JobRecord>>>) {
    if jobs.len() < MAX_RETAINED_JOBS {
        return;
    }
    let victim = jobs.iter().find_map(|(id, job)| {
        let state = lock_job(job).state;
        if state != JobState::Running {
            Some(id.clone())
        } else {
            None
        }
    });
    if let Some(id) = victim {
        jobs.remove(&id);
    }
}

fn lock_map(
    jobs: &Mutex<HashMap<String, Arc<Mutex<JobRecord>>>>,
) -> std::sync::MutexGuard<'_, HashMap<String, Arc<Mutex<JobRecord>>>> {
    jobs.lock().unwrap_or_else(|error| error.into_inner())
}

fn lock_job(job: &Mutex<JobRecord>) -> std::sync::MutexGuard<'_, JobRecord> {
    job.lock().unwrap_or_else(|error| error.into_inner())
}

fn kill_process_group(pid: i32) {
    if pid > 0 {
        unsafe {
            libc::killpg(pid, libc::SIGKILL);
        }
    }
}
