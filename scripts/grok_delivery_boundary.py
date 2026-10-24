"""Local dispatch boundary: preserve streams and accept validated delivery only."""
import json
import os
import subprocess
from pathlib import Path


def execute_preserving_streams(argv, *, prompt, cwd, base, assignment_id, timeout, env):
    paths = {name: Path(base) / (assignment_id + '-' + name + '.log') for name in ('stdout', 'stderr')}
    # Reserve both paths before any provider starts. Never truncate previous evidence.
    handles = []
    try:
        for name in ('stdout', 'stderr'):
            fd = os.open(paths[name], os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            handles.append(os.fdopen(fd, 'wb'))
        done = subprocess.run(argv, input=prompt.encode(), stdout=handles[0], stderr=handles[1], cwd=str(cwd), timeout=timeout, env=env, check=False)
        for f in handles:
            f.flush()
            os.fsync(f.fileno())
        return {'exit_code': done.returncode, 'stdout_path': str(paths['stdout']), 'stderr_path': str(paths['stderr'])}
    finally:
        for f in handles:
            f.close()


def classify_delivery(*, execution, terminal, lease, delivery, assignment_id):
    expected = assignment_id
    if not terminal or not lease or terminal.get('assignment_id') != expected or lease.get('assignment_id') != expected:
        return 'RESULT_UNKNOWN'
    if not lease.get('terminal_state') or terminal.get('result_unknown') is True:
        return 'RESULT_UNKNOWN'
    if (execution['exit_code'] == 0 and terminal.get('exit_code') == 0
            and lease.get('terminal_state') == 'completed'
            and terminal.get('outcome_code') == 'SUCCESS'
            and terminal.get('delivery_outcome') == 'pass'
            and 'FINAL_RESULT' in terminal.get('phase_history', [])
            and delivery and delivery.get('delivery_outcome') == 'pass'
            and delivery.get('evidence') and delivery.get('artifacts')):
        return 'DELIVERED_PENDING_PARENT_ACCEPTANCE'
    return 'DELIVERY_FAILED'
