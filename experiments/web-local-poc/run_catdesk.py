import fcntl
import os
import pty
import select
import struct
import subprocess
import termios
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BIN = Path('/Users/echoman/CatDesk-PoC-20260907/install/node_modules/catdesk/npm/bin/catdesk')
HOME = ROOT / 'home'
WORKSPACE = ROOT / 'workspace'
LOG = ROOT / 'logs' / 'catdesk.log'
PORT = '33300'

master, slave = pty.openpty()
fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 120, 0, 0))
env = os.environ.copy()
env.update({
    'HOME': str(HOME),
    'WORKSPACE_ROOT': str(WORKSPACE),
    'PORT': PORT,
    'CATDESK_SKIP_MACOS_TERMINAL_PROFILE': '1',
    'TERM': 'xterm-256color',
})
with LOG.open('ab', buffering=0) as log:
    proc = subprocess.Popen(
        [str(BIN)], stdin=slave, stdout=slave, stderr=slave,
        cwd=str(WORKSPACE), env=env, start_new_session=True,
    )
    os.close(slave)
    started = time.time()
    sent = False
    while proc.poll() is None:
        if not sent and time.time() - started > 2.2:
            os.write(master, b'1')
            sent = True
        readable, _, _ = select.select([master], [], [], 0.2)
        if readable:
            try:
                log.write(os.read(master, 65536))
            except OSError:
                break
try:
    os.close(master)
except OSError:
    pass
raise SystemExit(proc.wait())
