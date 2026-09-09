#!/usr/bin/env python3
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import signal
import shlex
import socket
import stat
import subprocess
import sys
import time
import tomllib
import uuid
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PREVIEW_SOCKET = Path(os.environ.get("LAB_M0_BRIDGE_SOCKET", "/tmp/lab-real-ingress-e2e/bridge.sock"))
CATDESK_ROOT = Path(os.environ.get("LAB_M0_CATDESK_ROOT", "/tmp/lab-catdesk-candidate-e2e"))
CATDESK_HOME = Path(os.environ.get("LAB_M0_CATDESK_HOME", str(CATDESK_ROOT / "home")))
CATDESK_CONFIG = CATDESK_HOME / ".catdesk" / "config.toml"
CATDESK_BACKEND_KEY = CATDESK_HOME / ".catdesk" / "backend.key"
CATDESK_PORT = int(os.environ.get("LAB_M0_CATDESK_PORT", "33311"))
CATDESK_RUNNER = Path(os.environ.get("LAB_M0_CATDESK_RUNNER", str(CATDESK_ROOT / "run.py")))
BRIDGE_BIN = Path(
    os.environ.get(
        "LAB_M0_BRIDGE_BIN",
        str(PROJECT_ROOT / "crates" / "local-tool-bridge" / "target" / "debug" / "local-tool-bridge"),
    )
)
RUNTIME_ROOT = Path(
    os.environ.get("LAB_M0_RUNTIME_ROOT", str(Path.home() / ".local-agent-bridge" / "m0-preview"))
)
BRIDGE_STATE_DIR = RUNTIME_ROOT / "bridge-state"
BRIDGE_LOG_DIR = RUNTIME_ROOT / "logs"
RUNTIME_META = RUNTIME_ROOT / "runtime.json"
BRIDGE_TTL_MS = int(os.environ.get("LAB_M0_BRIDGE_TTL_MS", "86400000"))
MIN_REMAINING_MS = int(os.environ.get("LAB_M0_MIN_REMAINING_MS", "3600000"))

TUNNEL_RUNTIME_DIR = Path(
    os.environ.get("LAB_M0_TUNNEL_RUNTIME_DIR", str(RUNTIME_ROOT / "tunnel"))
)
TUNNEL_PROFILE_DIR = Path(
    os.environ.get("LAB_M0_TUNNEL_PROFILE_DIR", str(TUNNEL_RUNTIME_DIR))
)
TUNNEL_PROFILE_NAME = "local-agent-bridge-poc"
TUNNEL_PID_FILE = RUNTIME_ROOT / "tunnel.pid"
TUNNEL_META = RUNTIME_ROOT / "tunnel.json"

MCP_VERSION = "2026-07-28"
M0_REQUIRED_TOOLS = {
    "catdesk_instruction",
    "read",
    "search",
    "write",
    "edit",
    "delete",
    "run_command",
    "start_command",
    "poll_command",
    "cancel_command",
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError(f"private runtime path is not a real directory: {path}")
    if path.stat().st_uid != os.geteuid():
        raise RuntimeError(f"private runtime path owner mismatch: {path}")
    path.chmod(0o700)


def _private_file(path: Path, label: str) -> None:
    st = path.lstat()
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise RuntimeError(f"{label} must be a regular non-symlink file")
    if st.st_uid != os.geteuid():
        raise RuntimeError(f"{label} owner mismatch")
    if stat.S_IMODE(st.st_mode) & 0o077:
        raise RuntimeError(f"{label} permissions must be 0600 or stricter")


def catdesk_slug(config_path: Path = CATDESK_CONFIG) -> str:
    _private_file(config_path, "CatDesk config")
    with config_path.open("rb") as handle:
        value = tomllib.load(handle).get("mcpSlug")
    if not isinstance(value, str) or not value.strip() or "/" in value:
        raise RuntimeError("CatDesk mcpSlug is missing or invalid")
    return value.strip()


def _tcp_ready(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pid_command(pid: int) -> str:
    if not _pid_alive(pid):
        return ""
    cp = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        capture_output=True,
        text=True,
        check=False,
    )
    return cp.stdout.strip() if cp.returncode == 0 else ""


def _read_pid(path: Path) -> int | None:
    try:
        value = int(path.read_text().strip())
    except (OSError, ValueError):
        return None
    return value if value > 0 else None


def _write_json_private(path: Path, value: dict[str, Any]) -> None:
    _private_dir(path.parent)
    temp = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    with temp.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temp.chmod(0o600)
    os.replace(temp, path)
    path.chmod(0o600)


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _tunnel_profile_path(profile_dir: Path) -> Path:
    return profile_dir / f"{TUNNEL_PROFILE_NAME}.yaml"


def _validate_tunnel_profile_dir(profile_dir: Path) -> dict[str, Any]:
    profile = _tunnel_profile_path(profile_dir)
    _private_file(profile, "tunnel profile")
    try:
        value = json.loads(profile.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise RuntimeError("tunnel profile is not valid JSON") from error
    control = value.get("control_plane")
    mcp = value.get("mcp")
    if not isinstance(control, dict) or not isinstance(mcp, dict):
        raise RuntimeError("tunnel profile is missing control_plane or mcp")
    tunnel_id = control.get("tunnel_id")
    if not isinstance(tunnel_id, str) or not tunnel_id.strip():
        raise RuntimeError("tunnel profile tunnel_id is missing")
    server_urls = mcp.get("server_urls")
    if not isinstance(server_urls, list):
        raise RuntimeError("tunnel profile mcp.server_urls is missing")
    sockets = {
        str(item.get("unix_socket"))
        for item in server_urls
        if isinstance(item, dict) and item.get("unix_socket")
    }
    if str(PREVIEW_SOCKET) not in sockets:
        raise RuntimeError("tunnel profile is not bound to the current M0 bridge socket")
    return {
        "profile_dir": str(profile_dir),
        "profile_path": str(profile),
        "tunnel_id": tunnel_id.strip(),
        "profile_sha256": hashlib.sha256(profile.read_bytes()).hexdigest(),
    }


def _parse_tunnel_process(pid: int, command: str) -> dict[str, Any] | None:
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    if len(argv) < 2 or Path(argv[0]).name != "tunnel-client" or argv[1] != "run":
        return None
    try:
        profile_index = argv.index("--profile")
        profile_dir_index = argv.index("--profile-dir")
    except ValueError:
        return None
    if profile_index + 1 >= len(argv) or profile_dir_index + 1 >= len(argv):
        return None
    if argv[profile_index + 1] != TUNNEL_PROFILE_NAME:
        return None
    profile_dir = Path(argv[profile_dir_index + 1]).expanduser()
    try:
        profile = _validate_tunnel_profile_dir(profile_dir)
    except (OSError, RuntimeError):
        return None
    return {
        "pid": pid,
        "command": command,
        **profile,
    }


def _discover_running_tunnels() -> list[dict[str, Any]]:
    cp = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        capture_output=True,
        text=True,
        check=False,
    )
    if cp.returncode != 0:
        return []
    found: list[dict[str, Any]] = []
    for raw in cp.stdout.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        pieces = raw.split(None, 1)
        if len(pieces) != 2:
            continue
        try:
            pid = int(pieces[0])
        except ValueError:
            continue
        parsed = _parse_tunnel_process(pid, pieces[1])
        if parsed is not None and _pid_alive(pid):
            found.append(parsed)
    return found


def _tunnel_runtime_meta() -> dict[str, Any]:
    return _read_json_object(TUNNEL_META)


def _tunnel_status() -> dict[str, Any]:
    discovered = _discover_running_tunnels()
    by_pid = {int(item["pid"]): item for item in discovered}
    if len(by_pid) > 1:
        return {
            "ready": False,
            "pid": None,
            "source": "ambiguous_live_processes",
            "reason": "multiple_active_tunnels",
            "active_pids": sorted(by_pid),
            "generation": None,
            "tunnel_id": None,
            "profile_dir": None,
        }
    if not by_pid:
        return {
            "ready": False,
            "pid": None,
            "source": "none",
            "reason": "tunnel_not_running",
            "active_pids": [],
            "generation": None,
            "tunnel_id": None,
            "profile_dir": None,
        }

    item = next(iter(by_pid.values()))
    meta = _tunnel_runtime_meta()
    canonical = (
        meta.get("pid") == item["pid"]
        and meta.get("tunnel_id") == item["tunnel_id"]
        and isinstance(meta.get("endpoint_generation"), int)
        and not isinstance(meta.get("endpoint_generation"), bool)
        and meta.get("endpoint_generation") >= 1
    )
    return {
        "ready": True,
        "pid": item["pid"],
        "source": "canonical_runtime_meta" if canonical else "live_process_discovery",
        "reason": None,
        "active_pids": [item["pid"]],
        "generation": meta.get("endpoint_generation") if canonical else None,
        "tunnel_id": item["tunnel_id"],
        "profile_dir": item["profile_dir"],
        "profile_sha256": item["profile_sha256"],
    }


def _write_tunnel_runtime_state(
    *,
    pid: int,
    tunnel_id: str,
    active_profile_dir: Path,
    endpoint_generation: int,
) -> dict[str, Any]:
    _private_dir(RUNTIME_ROOT)
    _private_dir(TUNNEL_PROFILE_DIR)
    source_info = _validate_tunnel_profile_dir(active_profile_dir)
    source_value = json.loads(Path(source_info["profile_path"]).read_text(encoding="utf-8"))
    canonical_profile = _tunnel_profile_path(TUNNEL_PROFILE_DIR)
    _write_json_private(canonical_profile, source_value)
    canonical_info = _validate_tunnel_profile_dir(TUNNEL_PROFILE_DIR)
    value = {
        "schema_version": 1,
        "pid": pid,
        "profile_name": TUNNEL_PROFILE_NAME,
        "active_profile_dir": str(active_profile_dir),
        "canonical_profile_dir": str(TUNNEL_PROFILE_DIR),
        "tunnel_id": tunnel_id,
        "profile_sha256": canonical_info["profile_sha256"],
        "endpoint_generation": endpoint_generation,
        "updated_at_ms": _now_ms(),
    }
    _write_json_private(TUNNEL_META, value)
    TUNNEL_PID_FILE.write_text(str(pid), encoding="utf-8")
    TUNNEL_PID_FILE.chmod(0o600)
    return value


def _adopt_live_tunnel(status_value: dict[str, Any]) -> dict[str, Any]:
    if status_value.get("ready") is not True or not isinstance(status_value.get("pid"), int):
        raise RuntimeError("cannot adopt an unavailable tunnel")
    prior = _tunnel_runtime_meta()
    prior_generation = prior.get("endpoint_generation")
    generation = (
        prior_generation
        if isinstance(prior_generation, int)
        and not isinstance(prior_generation, bool)
        and prior_generation >= 1
        and prior.get("tunnel_id") == status_value.get("tunnel_id")
        else 1
    )
    return _write_tunnel_runtime_state(
        pid=int(status_value["pid"]),
        tunnel_id=str(status_value["tunnel_id"]),
        active_profile_dir=Path(str(status_value["profile_dir"])),
        endpoint_generation=generation,
    )


def _stop_verified_bridge_pid(pid: int | None) -> None:
    if not pid or not _pid_alive(pid):
        return
    command = _pid_command(pid)
    if "local-tool-bridge" not in command:
        raise RuntimeError(f"refusing to stop pid {pid}: not local-tool-bridge")
    os.kill(pid, signal.SIGTERM)
    for _ in range(30):
        if not _pid_alive(pid):
            return
        time.sleep(0.1)
    raise RuntimeError(f"local-tool-bridge pid {pid} did not stop after SIGTERM")


def _known_bridge_pids() -> list[int]:
    paths = [
        RUNTIME_ROOT / "bridge.pid",
        Path("/tmp/lab-real-ingress-e2e/bridge-m0.pid"),
        Path("/tmp/lab-real-ingress-e2e/bridge-v3.pid"),
        Path("/tmp/lab-real-ingress-e2e/bridge.pid"),
    ]
    seen: list[int] = []
    for path in paths:
        pid = _read_pid(path)
        if pid and pid not in seen:
            seen.append(pid)
    return seen


def ensure_catdesk() -> None:
    _private_file(CATDESK_CONFIG, "CatDesk config")
    _private_file(CATDESK_BACKEND_KEY, "CatDesk backend key")
    if _tcp_ready("127.0.0.1", CATDESK_PORT):
        return
    if not CATDESK_RUNNER.is_file():
        raise RuntimeError(
            f"hardened CatDesk preview backend is not running and runner is missing: {CATDESK_RUNNER}"
        )
    subprocess.Popen(
        [sys.executable, str(CATDESK_RUNNER)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    for _ in range(80):
        if _tcp_ready("127.0.0.1", CATDESK_PORT):
            return
        time.sleep(0.1)
    raise RuntimeError("hardened CatDesk preview backend did not become ready")


def _find_tunnel_client() -> Path:
    explicit = os.environ.get("LAB_M0_TUNNEL_CLIENT")
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.is_file():
            return candidate
        raise RuntimeError(f"LAB_M0_TUNNEL_CLIENT does not exist: {candidate}")
    candidates = [
        Path(value)
        for value in glob.glob(str(Path.home() / ".local" / "share" / "aibridge-tunnel" / "*" / "tunnel-client"))
    ]
    candidates = [path for path in candidates if path.is_file()]
    if not candidates:
        raise RuntimeError(
            "OpenAI tunnel-client is not installed; set LAB_M0_TUNNEL_CLIENT to the standalone tunnel-client binary"
        )
    return sorted(candidates)[-1]


def ensure_tunnel() -> int:
    current = _tunnel_status()
    if current.get("ready") is True:
        if current.get("source") != "canonical_runtime_meta":
            _adopt_live_tunnel(current)
        return int(current["pid"])
    if current.get("reason") == "multiple_active_tunnels":
        raise RuntimeError("multiple active M0 tunnel processes detected; refusing implicit selection")

    # New starts require the canonical private profile. Existing legacy source-tree
    # profiles are adopted only while their verified process is still running.
    profile = _validate_tunnel_profile_dir(TUNNEL_PROFILE_DIR)
    tunnel_client = _find_tunnel_client()
    out = BRIDGE_LOG_DIR / "tunnel.out"
    err = BRIDGE_LOG_DIR / "tunnel.err"
    _private_dir(BRIDGE_LOG_DIR)
    with out.open("ab") as stdout, err.open("ab") as stderr:
        process = subprocess.Popen(
            [
                str(tunnel_client),
                "run",
                "--profile-dir",
                str(TUNNEL_PROFILE_DIR),
                "--profile",
                TUNNEL_PROFILE_NAME,
            ],
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            cwd=PROJECT_ROOT,
            start_new_session=True,
            close_fds=True,
        )
    for _ in range(30):
        if not _pid_alive(process.pid):
            raise RuntimeError("OpenAI tunnel-client exited during startup")
        discovered = _tunnel_status()
        if discovered.get("ready") is True and discovered.get("pid") == process.pid:
            break
        time.sleep(0.1)
    else:
        raise RuntimeError("OpenAI tunnel-client did not become observable after startup")

    prior = _tunnel_runtime_meta()
    previous_generation = prior.get("endpoint_generation")
    generation = (
        previous_generation + 1
        if isinstance(previous_generation, int)
        and not isinstance(previous_generation, bool)
        and previous_generation >= 1
        else 1
    )
    _write_tunnel_runtime_state(
        pid=process.pid,
        tunnel_id=str(profile["tunnel_id"]),
        active_profile_dir=TUNNEL_PROFILE_DIR,
        endpoint_generation=generation,
    )
    return process.pid


def _bridge_env(session_id: str, capability_id: str) -> dict[str, str]:
    slug = catdesk_slug()
    return {
        "HOME": str(Path.home()),
        "PATH": os.environ.get("PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"),
        "LAB_BRIDGE_SOCKET": str(PREVIEW_SOCKET),
        "LAB_BRIDGE_BACKEND_URL": f"http://127.0.0.1:{CATDESK_PORT}/{slug}/mcp",
        "LAB_BRIDGE_BACKEND_KEY_FILE": str(CATDESK_BACKEND_KEY),
        "LAB_BRIDGE_STATE_DIR": str(BRIDGE_STATE_DIR),
        "LAB_BRIDGE_WORKSPACE_ID": "catdesk-secure-workspace-m0-preview",
        "LAB_BRIDGE_PRINCIPAL_ID": "tunnel-client-local-uds",
        "LAB_BRIDGE_EXECUTION_SESSION_ID": session_id,
        "LAB_BRIDGE_CAPABILITY_ID": capability_id,
        "LAB_BRIDGE_CAPABILITY_TTL_MS": str(BRIDGE_TTL_MS),
        "LAB_BRIDGE_CAPABILITIES": "discovery,file_read,file_write,file_move,shell,process_jobs",
    }


def _runtime_meta() -> dict[str, Any]:
    try:
        value = json.loads(RUNTIME_META.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _lease_remaining_ms(meta: dict[str, Any] | None = None) -> int:
    meta = meta or _runtime_meta()
    expires = meta.get("expires_at_ms")
    if not isinstance(expires, int):
        return 0
    return max(0, expires - _now_ms())


def call_mcp(method: str, params: dict[str, Any]) -> dict[str, Any]:
    meta = {
        "io.modelcontextprotocol/protocolVersion": MCP_VERSION,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "local-agent-bridge-m0-preview", "version": "1"},
    }
    payload = {
        "jsonrpc": "2.0",
        "id": f"m0-{uuid.uuid4().hex}",
        "method": method,
        "params": {**params, "_meta": meta},
    }
    command = [
        "curl",
        "--silent",
        "--show-error",
        "--fail-with-body",
        "--unix-socket",
        str(PREVIEW_SOCKET),
        "-H",
        "content-type: application/json",
        "-H",
        f"mcp-method: {method}",
        "-H",
        f"mcp-protocol-version: {MCP_VERSION}",
        "--data",
        json.dumps(payload, separators=(",", ":")),
        "http://localhost/mcp",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=8)
    if completed.returncode != 0:
        detail = (completed.stdout or completed.stderr).strip()
        raise RuntimeError(f"M0 Bridge MCP call failed: {detail[:600]}")
    value = json.loads(completed.stdout)
    if "error" in value:
        raise RuntimeError(f"M0 Bridge MCP error: {json.dumps(value['error'], ensure_ascii=False)}")
    return value


def smoke_tools() -> list[str]:
    response = call_mcp("tools/list", {})
    tools = ((response.get("result") or {}).get("tools") or [])
    names = [tool.get("name") for tool in tools if isinstance(tool, dict)]
    missing = sorted(M0_REQUIRED_TOOLS - set(names))
    unexpected_computer = sorted(name for name in names if isinstance(name, str) and name.startswith("computer_"))
    if missing:
        raise RuntimeError(f"M0 tool surface is missing: {', '.join(missing)}")
    if unexpected_computer:
        raise RuntimeError(f"M0 tool surface unexpectedly exposes Computer tools: {', '.join(unexpected_computer)}")
    return sorted(name for name in names if isinstance(name, str))


def bridge_healthy() -> bool:
    meta = _runtime_meta()
    pid = meta.get("bridge_pid")
    if not isinstance(pid, int) or not _pid_alive(pid):
        return False
    if "local-tool-bridge" not in _pid_command(pid):
        return False
    if _lease_remaining_ms(meta) < MIN_REMAINING_MS:
        return False
    if not PREVIEW_SOCKET.exists():
        return False
    try:
        smoke_tools()
    except Exception:
        return False
    return True


def start_bridge(force: bool = False) -> dict[str, Any]:
    ensure_catdesk()
    _private_dir(RUNTIME_ROOT)
    _private_dir(BRIDGE_STATE_DIR)
    _private_dir(BRIDGE_LOG_DIR)
    if not BRIDGE_BIN.is_file():
        subprocess.run(
            ["cargo", "build", "--quiet"],
            cwd=PROJECT_ROOT / "crates" / "local-tool-bridge",
            check=True,
            timeout=120,
        )
    if not force and bridge_healthy():
        return _runtime_meta()

    for pid in _known_bridge_pids():
        _stop_verified_bridge_pid(pid)

    session_id = f"bridge-session-m0-{uuid.uuid4()}"
    capability_id = f"bridge-cap-m0-{uuid.uuid4()}"
    out_path = BRIDGE_LOG_DIR / "bridge.out"
    err_path = BRIDGE_LOG_DIR / "bridge.err"
    with out_path.open("ab") as stdout, err_path.open("ab") as stderr:
        process = subprocess.Popen(
            [str(BRIDGE_BIN)],
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            cwd=PROJECT_ROOT,
            env=_bridge_env(session_id, capability_id),
            start_new_session=True,
            close_fds=True,
        )

    for _ in range(50):
        if process.poll() is not None:
            raise RuntimeError(f"local-tool-bridge exited during M0 startup; see {err_path}")
        if PREVIEW_SOCKET.exists():
            try:
                tools = smoke_tools()
                break
            except Exception:
                pass
        time.sleep(0.1)
    else:
        raise RuntimeError("M0 local-tool-bridge did not become ready")

    now = _now_ms()
    value = {
        "schema_version": 1,
        "bridge_pid": process.pid,
        "execution_session_id": session_id,
        "capability_id": capability_id,
        "issued_at_ms": now,
        "expires_at_ms": now + BRIDGE_TTL_MS,
        "socket": str(PREVIEW_SOCKET),
        "tools": tools,
    }
    _write_json_private(RUNTIME_META, value)
    (RUNTIME_ROOT / "bridge.pid").write_text(str(process.pid), encoding="utf-8")
    (RUNTIME_ROOT / "bridge.pid").chmod(0o600)
    return value


def status() -> dict[str, Any]:
    meta = _runtime_meta()
    bridge_ok = bridge_healthy()
    tunnel = _tunnel_status()
    tunnel_ok = tunnel.get("ready") is True
    return {
        "m0_preview_ready": bool(bridge_ok and tunnel_ok and _tcp_ready("127.0.0.1", CATDESK_PORT)),
        "bridge_ready": bridge_ok,
        "bridge_pid": meta.get("bridge_pid"),
        "lease_remaining_ms": _lease_remaining_ms(meta),
        "backend_ready": _tcp_ready("127.0.0.1", CATDESK_PORT),
        "tunnel_ready": tunnel_ok,
        "tunnel_pid": tunnel.get("pid"),
        "tunnel_id": tunnel.get("tunnel_id"),
        "tunnel_endpoint_generation": tunnel.get("generation"),
        "tunnel_state_source": tunnel.get("source"),
        "tunnel_state_reason": tunnel.get("reason"),
        "tunnel_profile_dir": tunnel.get("profile_dir"),
        "socket": str(PREVIEW_SOCKET),
        "tools": meta.get("tools") or [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local Agent Bridge M0 Migration Preview runtime")
    parser.add_argument("action", choices=("start", "restart", "status", "smoke"))
    args = parser.parse_args(argv)

    if args.action in {"start", "restart"}:
        ensure_tunnel()
        start_bridge(force=args.action == "restart")
        result = status()
    elif args.action == "smoke":
        result = {"tools": smoke_tools(), "ok": True}
    else:
        result = status()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("m0_preview_ready", result.get("ok", False)) else 2


if __name__ == "__main__":
    raise SystemExit(main())
