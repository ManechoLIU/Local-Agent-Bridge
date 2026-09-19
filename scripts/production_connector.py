#!/usr/bin/env python3
"""Canonical Local Agent Bridge production connector contract.

This module is intentionally fail-closed: production publication is valid only
when Host, Bridge, tunnel profile, release revision, endpoint generation, tool
surface, and canonical workspace all agree on one immutable release contract.
"""
from __future__ import annotations

import argparse
import ctypes
import fcntl
import glob
import hashlib
import ipaddress
import json
import math
import os
import re
import secrets
import shlex
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

CANONICAL_PROJECT_ROOT = Path("/Users/echoman/Documents/ChatGPT/Local-Agent-Bridge")
CONTRACT_VERSION = "lab-production-connector-v1"
CONNECTOR_ID = "local-agent-bridge-production"
CONNECTOR_DISPLAY_NAME = "Local Agent Bridge"
TUNNEL_PROFILE_NAME = "local-agent-bridge-production"
HISTORICAL_TUNNEL_PROFILE_NAMES = ("local-agent-bridge-poc",)
HISTORICAL_CONNECTOR_NAMES = ("Local Bridge Identity Probe",)
HOST_EXTENSION_ID = "ikidefdlcddfomimpckflbajjccnmfna"
MIN_HOST_EXTENSION_VERSION = "0.1.2"
PRODUCTION_RUNTIME_ROOT = Path.home() / ".local-agent-bridge" / "production-connector"
PRODUCTION_BRIDGE_SOCKET = Path("/tmp/local-agent-bridge-production/bridge.sock")
PRODUCTION_CATDESK_PORT = 33331
PRODUCTION_HOST_MCP_PORT = 33321
PRODUCTION_PROCESS_RUNTIME_PORT = 33442
PRODUCTION_BRIDGE_CAPABILITY_TTL_MS = 86_400_000
PRODUCTION_BRIDGE_CAPABILITY_RENEW_BEFORE_MS = 3_600_000
AI_BRIDGE_EXECUTABLE = Path("/Applications/AI-Bridge.app/Contents/MacOS/ai-bridge")
COMPUTE_HELPER_EXECUTABLE = Path("/Applications/Compute Helper.app/Contents/MacOS/compute-helper")
COMPUTE_HELPER_SOCKET = (
    Path.home()
    / "Library"
    / "Application Support"
    / "ai.originone.gpt-bridge"
    / "computer-helper"
    / "helper.sock"
)
RUNTIME_HOST_TOOL_HOOK_PROTOCOL = "runtime_host_tool_hook_v1"
RUNTIME_HOST_TOOL_HOOK_RUNTIME_REVISION = "b2e9cf54583e17d7cc60919433b72bc9d6ac979a"
RUNTIME_HOST_TOOL_HOOK_SOURCE_ROOT = Path("/Users/echoman/Documents/Codex/adaptive-delivery")
RUNTIME_HOST_TOOL_HOOK_BUNDLE_RELATIVE_PATHS = (
    "scripts/agent_target_resolution.py",
    "scripts/assignment_runtime.py",
    "scripts/control_event_guard.py",
    "scripts/controller_health.py",
    "scripts/controller_self_check.py",
    "scripts/controller_state.py",
    "scripts/controller_target_guard.py",
    "scripts/event_scope_guard.py",
    "scripts/goal_display_sync.py",
    "scripts/ledger_consistency_guard.py",
    "scripts/lifecycle_hook.py",
    "scripts/lint_governance.py",
    "scripts/preblock_guard.py",
    "scripts/project_context_guard.py",
    "scripts/project_state.py",
    "scripts/reviewer_supervisor.py",
    "scripts/route_contract.py",
    "scripts/rule_handshake.py",
    "scripts/runtime_host_tool_hook.py",
    "scripts/terminal_continuation.py",
    "scripts/web_agent_events.py",
    "scripts/web_agent_execution.py",
    "scripts/web_agent_health_supervisor.py",
    "scripts/web_lifecycle_bridge.py",
    "scripts/web_reentry_adapter.py",
)
RUNTIME_HOST_TOOL_HOOK_ENTRYPOINT_RELATIVE_PATH = "scripts/runtime_host_tool_hook.py"
HISTORICAL_M0_BRIDGE_SOCKET = Path("/tmp/lab-real-ingress-e2e/bridge.sock")
HISTORICAL_M0_RUNTIME_STATE = Path.home() / ".local-agent-bridge" / "m0-preview" / "runtime.json"
AI_BRIDGE_FALLBACK_HEALTH_URL_FILE = (
    Path.home()
    / "Library"
    / "Application Support"
    / "ai.originone.gpt-bridge"
    / "managed-tunnel"
    / "health-url"
)
MACOS_CLT_PYTHON_CURRENT = Path(
    "/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/"
    "Versions/Current/Resources/Python.app/Contents/MacOS/Python"
)
DEFAULT_NATIVE_USER_DATA_DIR_NAMES = (
    "chrome-profile",
    "chrome-for-testing-profile",
    "chrome-profile-current-entry-v2",
)
CANONICAL_CONTROL_PLANE_BASE_URL = "https://api.openai.com"
ALLOWED_BUILD_UNTRACKED = frozenset(
    {
        "experiments/web-host-attestation/chrome-host/host-mcp-adapter.mjs.orig",
        "experiments/web-host-attestation/chrome-host/host-mcp-adapter.mjs.rej",
    }
)
LEGACY_CANDIDATE_MARKERS = (
    "/tmp/lab-catdesk-candidate-e2e",
    "/private/tmp/lab-catdesk-candidate-e2e",
)
REQUIRED_TOOLS = frozenset(
    {
        "discover_current_entry",
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
)
FEISHU_REQUIRED_TOOLS = frozenset(
    {
        "feishu_status",
        "feishu_base_list_tables",
        "feishu_base_list_fields",
        "feishu_base_list_records",
        "feishu_chat_search",
        "feishu_message_list",
        "feishu_base_create_record",
        "feishu_base_update_record",
        "feishu_message_send",
        "feishu_message_reply",
    }
)
FEISHU_ADMIN_TOOLS = frozenset(
    {
        "feishu_setup_start",
        "feishu_setup_poll",
        "feishu_auth_begin",
        "feishu_auth_complete",
    }
)
RUNTIME_VERIFIER_BUNDLE_RELATIVE_PATHS = (
    "chrome-host/runtime-verifier-cli.mjs",
    "chrome-host/integration.mjs",
    "chrome-host/client.mjs",
    "chrome-host/extension-id.mjs",
    "provider.mjs",
    "attestation.mjs",
    "chrome-host/native-host.mjs",
    "chrome-host/native-host-core.mjs",
    "chrome-host/native-framing.mjs",
    "chrome-host/extension/timeout-budget.js",
    "chrome-host/extension/manifest.json",
    "chrome-host/extension/background.js",
    "chrome-host/extension/current-entry-registry.js",
    "chrome-host/extension/debugger-session.js",
    "chrome-host/extension/observation.js",
    "chrome-host/extension/browser-submit.js",
    "chrome-host/extension/submit-page.js",
    "chrome-host/extension/submit-journal.js",
    "chrome-host/extension/trusted-input.js",
    "chrome-host/extension/submit-transaction-queue.js",
)
RUNTIME_EXTENSION_EXECUTION_RELATIVE_PATHS = (
    "timeout-budget.js",
    "manifest.json",
    "background.js",
    "current-entry-registry.js",
    "debugger-session.js",
    "observation.js",
    "browser-submit.js",
    "submit-page.js",
    "submit-journal.js",
    "trusted-input.js",
    "submit-transaction-queue.js",
)


def canonical_workspace() -> Path:
    return validate_workspace(CANONICAL_PROJECT_ROOT)


def production_paths() -> dict[str, Any]:
    return {
        "runtime_root": PRODUCTION_RUNTIME_ROOT,
        "bridge_socket": PRODUCTION_BRIDGE_SOCKET,
        "tunnel_profile_name": TUNNEL_PROFILE_NAME,
    }


def default_release_sources(repo_root: Path = CANONICAL_PROJECT_ROOT) -> dict[str, Path]:
    """Return only explicit source paths; callers still validate each source.

    The CatDesk inputs are copied into an immutable release and are never used as
    its runtime workspace or socket.  Operators can point at a newer hardened
    candidate with the corresponding LAB_PRODUCTION_* environment variables.
    """
    root = Path(repo_root).expanduser()
    catdesk_home = Path(
        os.environ.get("LAB_PRODUCTION_CATDESK_HOME", "/tmp/lab-catdesk-candidate-e2e/home")
    ).expanduser()
    return {
        "bridge_binary": root / "crates" / "local-tool-bridge" / "target" / "release" / "local-tool-bridge",
        "process_runtime_binary": root
        / "crates"
        / "process-runtime"
        / "target"
        / "release"
        / "process-runtime",
        "catdesk_binary": Path(
            os.environ.get(
                "LAB_PRODUCTION_CATDESK_BINARY",
                "/Users/echoman/CatDesk-PoC-20260907/worktrees/lab-safe-01/target/debug/catdesk",
            )
        ).expanduser(),
        "catdesk_config": catdesk_home / ".catdesk" / "config.toml",
        "catdesk_backend_key": catdesk_home / ".catdesk" / "backend.key",
        "host_attestation_key": Path(
            os.environ.get(
                "LAB_PRODUCTION_HOST_ATTESTATION_KEY",
                str(Path.home() / ".local-agent-bridge" / "web-host" / "attestation.key"),
            )
        ).expanduser(),
        "host_source_dir": root / "experiments" / "web-host-attestation",
        "tunnel_profile": Path(
            os.environ.get(
                "LAB_PRODUCTION_TUNNEL_SOURCE_PROFILE",
                str(root / "experiments" / "web-local-poc" / "tunnel-runtime" / "local-agent-bridge-poc.yaml"),
            )
        ).expanduser(),
    }


def _git_head(repo_root: Path = CANONICAL_PROJECT_ROOT) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("cannot resolve canonical Local Agent Bridge Git HEAD")
    return _release_revision(completed.stdout.strip())


def require_exact_git_head(release_revision: str, repo_root: Path = CANONICAL_PROJECT_ROOT) -> str:
    expected = _release_revision(release_revision)
    actual = _git_head(Path(repo_root))
    if actual != expected:
        raise RuntimeError(f"canonical repository HEAD {actual} does not match requested release revision {expected}")
    return actual


def _validate_manifest_identity(manifest: dict[str, Any]) -> None:
    if not isinstance(manifest, dict):
        raise RuntimeError("production release manifest must be an object")
    if manifest.get("connector_id") != CONNECTOR_ID:
        raise RuntimeError("production release manifest connector identity mismatch")
    if manifest.get("contract_version") != CONTRACT_VERSION or manifest.get("fail_closed") is not True:
        raise RuntimeError("production release manifest is not the canonical fail-closed contract")
    _release_revision(str(manifest.get("release_revision") or ""))
    _endpoint_generation(manifest.get("endpoint_generation"))
    if manifest.get("workspace") != str(canonical_workspace()):
        raise RuntimeError("production release manifest workspace mismatch")
    if (manifest.get("tunnel") or {}).get("profile_name") != TUNNEL_PROFILE_NAME:
        raise RuntimeError("production release manifest tunnel profile mismatch")


def production_layout(
    manifest: dict[str, Any],
    *,
    runtime_root: Path = PRODUCTION_RUNTIME_ROOT,
    bridge_socket: Path = PRODUCTION_BRIDGE_SOCKET,
) -> dict[str, Path]:
    _validate_manifest_identity(manifest)
    root = Path(runtime_root).expanduser()
    revision = str(manifest["release_revision"])
    generation = int(manifest["endpoint_generation"])
    release_dir = root / "releases" / revision / f"generation-{generation}"
    secrets_dir = release_dir / "secrets"
    release_catdesk_home = release_dir / "catdesk-home"
    runtime_generation_dir = root / "runtime-state" / revision / f"generation-{generation}"
    catdesk_home = runtime_generation_dir / "catdesk-home"
    host_socket_dir = Path(bridge_socket).parent / f"{revision}-g{generation}"
    tunnel_dir = root / "tunnel"
    return {
        "runtime_root": root,
        "release_dir": release_dir,
        "release_manifest": release_dir / "release.json",
        "bridge_binary": release_dir / "bin" / "local-tool-bridge",
        "process_runtime_binary": release_dir / "bin" / "process-runtime",
        "catdesk_binary": release_dir / "bin" / "catdesk",
        "node_binary": release_dir / "bin" / "node-runtime",
        "catdesk_runner": release_dir / "bin" / "run-catdesk.py",
        "release_catdesk_config": release_catdesk_home / ".catdesk" / "config.toml",
        "release_catdesk_backend_key": release_catdesk_home / ".catdesk" / "backend.key",
        "runtime_generation_dir": runtime_generation_dir,
        "catdesk_home": catdesk_home,
        "catdesk_config": catdesk_home / ".catdesk" / "config.toml",
        "catdesk_backend_key": catdesk_home / ".catdesk" / "backend.key",
        "host_dir": release_dir / "host",
        "host_mcp_adapter": release_dir / "host" / "chrome-host" / "host-mcp-adapter.mjs",
        "native_host": release_dir / "host" / "chrome-host" / "native-host.mjs",
        "native_host_wrapper": release_dir / "bin" / "native-host-production",
        "runtime_verifier_cli": release_dir / "host" / "chrome-host" / "runtime-verifier-cli.mjs",
        "runtime_verifier_wrapper": release_dir / "bin" / "runtime-verifier-production",
        "runtime_hook_dir": release_dir / "runtime-host-tool-hook",
        "runtime_hook_entrypoint": release_dir / "runtime-host-tool-hook" / RUNTIME_HOST_TOOL_HOOK_ENTRYPOINT_RELATIVE_PATH,
        "host_backend_key": secrets_dir / "host-backend.key",
        "process_runtime_backend_key": secrets_dir / "process-runtime-backend.key",
        "host_attestation_key": secrets_dir / "host-attestation.key",
        "host_socket_dir": host_socket_dir,
        "host_socket": host_socket_dir / "host.sock",
        "host_verifier_state": runtime_generation_dir / "host" / "runtime-verifier-state.json",
        "host_reentry_state": runtime_generation_dir / "host" / "runtime-reentry-state.json",
        "host_extension_binding": runtime_generation_dir / "host" / "runtime-extension-binding.json",
        "host_tool_receipt_state": runtime_generation_dir / "host" / "tool-receipts.json",
        "bridge_state_dir": runtime_generation_dir / "bridge",
        "bridge_authority_state": runtime_generation_dir / "bridge" / "authority-state.json",
        "bridge_receipt_journal": runtime_generation_dir / "bridge" / "tool-receipt-journal.json",
        "runtime_hook_socket_dir": host_socket_dir,
        "runtime_hook_socket": host_socket_dir / "rh.sock",
        "bridge_socket": Path(bridge_socket),
        "release_tunnel_profile": release_dir / "tunnel" / f"{TUNNEL_PROFILE_NAME}.yaml",
        "tunnel_dir": tunnel_dir,
        "tunnel_profile": tunnel_dir / f"{TUNNEL_PROFILE_NAME}.yaml",
        "tunnel_health_url": tunnel_dir / "health" / f"{TUNNEL_PROFILE_NAME}.url",
        "tunnel_log": tunnel_dir / "logs" / f"{TUNNEL_PROFILE_NAME}.log",
        "runtime_state": root / "runtime.json",
        "rollback_receipt": root / "activation-rollback.json",
        "logs_dir": root / "logs",
        "catdesk_child_pid": runtime_generation_dir / "catdesk-child.pid",
        "process_runtime_state_dir": runtime_generation_dir / "process-runtime",
    }


def production_component_env(
    manifest: dict[str, Any],
    layout: dict[str, Path],
    *,
    catdesk_slug: str,
) -> dict[str, dict[str, str]]:
    _validate_manifest_contract(manifest)
    slug = str(catdesk_slug or "").strip()
    if not slug or "/" in slug:
        raise RuntimeError("production CatDesk mcpSlug is missing or invalid")
    fence = {
        "LAB_PRODUCTION_RELEASE_MANIFEST_FILE": str(layout["release_manifest"]),
        "LAB_PRODUCTION_CONNECTOR_ID": CONNECTOR_ID,
        "LAB_PRODUCTION_CONTRACT_VERSION": CONTRACT_VERSION,
        "LAB_PRODUCTION_RELEASE_REVISION": str(manifest["release_revision"]),
        "LAB_PRODUCTION_ENDPOINT_GENERATION": str(manifest["endpoint_generation"]),
        "LAB_PRODUCTION_WORKSPACE": str(manifest["workspace"]),
        "LAB_PRODUCTION_TUNNEL_PROFILE_NAME": TUNNEL_PROFILE_NAME,
    }
    host = {
        **fence,
        "LAB_HOST_MCP_BACKEND_KEY_FILE": str(layout["host_backend_key"]),
        "LAB_HOST_ATTESTATION_KEY_FILE": str(layout["host_attestation_key"]),
        "LAB_HOST_ADAPTER_SOCKET": str(layout["host_socket"]),
        "LAB_HOST_TOOL_RECEIPT_STATE_FILE": str(layout["host_tool_receipt_state"]),
        "LAB_HOST_VERIFIER_STATE_FILE": str(layout["host_verifier_state"]),
        "LAB_HOST_REENTRY_STATE_FILE": str(layout["host_reentry_state"]),
        "LAB_HOST_MCP_BIND": "127.0.0.1",
        "LAB_HOST_MCP_PORT": str(PRODUCTION_HOST_MCP_PORT),
    }
    bridge = {
        **fence,
        "LAB_BRIDGE_SOCKET": str(layout["bridge_socket"]),
        "LAB_BRIDGE_BACKEND_URL": f"http://127.0.0.1:{PRODUCTION_CATDESK_PORT}/{slug}/mcp",
        "LAB_BRIDGE_BACKEND_KEY_FILE": str(layout["catdesk_backend_key"]),
        "LAB_BRIDGE_STATE_DIR": str(layout["bridge_state_dir"]),
        "LAB_BRIDGE_WORKSPACE_ID": CONNECTOR_ID,
        "LAB_BRIDGE_PRINCIPAL_ID": "tunnel-client-production-uds",
        "LAB_BRIDGE_CAPABILITIES": (
            "discovery,host_attestation,file_read,file_write,file_move,shell,process_jobs"
        ),
        "LAB_BRIDGE_CAPABILITY_TTL_MS": str(
            manifest["bridge"]["capability_ttl_ms"]
        ),
        "LAB_BRIDGE_CAPABILITY_RENEW_BEFORE_MS": str(
            manifest["bridge"]["capability_renew_before_ms"]
        ),
        "LAB_HOST_BACKEND_URL": f"http://127.0.0.1:{PRODUCTION_HOST_MCP_PORT}/mcp",
        "LAB_HOST_BACKEND_KEY_FILE": str(layout["host_backend_key"]),
        "LAB_HOST_ADAPTER_SOCKET": str(layout["host_socket"]),
        "LAB_RUNTIME_HOST_TOOL_HOOK_SOCKET": str(layout["runtime_hook_socket"]),
    }
    process_bind = _numeric_loopback_bind(f"127.0.0.1:{PRODUCTION_PROCESS_RUNTIME_PORT}")
    process_backend_url = f"http://{process_bind}/mcp"
    process_runtime = {
        **fence,
        "PROCESS_RUNTIME_BIND": process_bind,
        "PROCESS_RUNTIME_BACKEND_KEY_FILE": str(layout["process_runtime_backend_key"]),
        "PROCESS_RUNTIME_WORKSPACE": str(manifest["workspace"]),
        "PROCESS_RUNTIME_WORKSPACE_ID": CONNECTOR_ID,
        "PROCESS_RUNTIME_STATE_DIR": str(layout["process_runtime_state_dir"]),
    }
    bridge["LAB_PROCESS_BACKEND_URL"] = process_backend_url
    bridge["LAB_PROCESS_BACKEND_KEY_FILE"] = str(layout["process_runtime_backend_key"])
    _require_paired_backend_env(
        bridge,
        url_key="LAB_PROCESS_BACKEND_URL",
        key_file_key="LAB_PROCESS_BACKEND_KEY_FILE",
        label="process",
    )
    dependencies = _validate_runtime_dependencies(manifest)
    feishu_admin_enabled = manifest.get("feishu_admin_enabled") is True
    feishu = dependencies.get("feishu_cli")
    if feishu_admin_enabled:
        bridge["LAB_BRIDGE_CAPABILITIES"] += ",provider_admin"
        bridge["LAB_FEISHU_NPM"] = "/opt/homebrew/bin/npm"
    if feishu is not None:
        bridge["LAB_BRIDGE_CAPABILITIES"] += ",collaboration_read,collaboration_write"
        bridge["LAB_FEISHU_CLI"] = str(feishu["path"])
        bridge["LAB_FEISHU_TIMEOUT_MS"] = "30000"
        bridge["LAB_FEISHU_OUTPUT_LIMIT"] = str(1024 * 1024)
    return {"host": host, "bridge": bridge, "process_runtime": process_runtime}


def production_tunnel_profile(
    source: dict[str, Any],
    manifest: dict[str, Any],
    layout: dict[str, Path],
) -> dict[str, Any]:
    _validate_manifest_contract(manifest)
    if not isinstance(source, dict):
        raise RuntimeError("source tunnel profile must be an object")
    control = source.get("control_plane")
    if not isinstance(control, dict):
        raise RuntimeError("source tunnel profile is missing control_plane")
    if not isinstance(control.get("tunnel_id"), str) or not control["tunnel_id"].strip():
        raise RuntimeError("source tunnel profile tunnel_id is missing")
    if control.get("base_url") != "https://api.openai.com":
        raise RuntimeError("source tunnel profile must use the OpenAI control plane")
    api_key = control.get("api_key")
    if not isinstance(api_key, str) or not api_key.strip():
        raise RuntimeError("source tunnel profile control-plane key reference is missing")
    return {
        "config_version": 1,
        "control_plane": json.loads(json.dumps(control)),
        "mcp": {
            "server_urls": [
                {
                    "channel": "main",
                    "url": "http://localhost/mcp",
                    "unix_socket": str(layout["bridge_socket"]),
                }
            ]
        },
        "health": {
            "listen_addr": "127.0.0.1:0",
            "url_file": str(layout["tunnel_health_url"]),
        },
        "log": {
            "file": str(layout["tunnel_log"]),
            "format": "json",
            "level": "info",
        },
        "admin_ui": {"open_browser": False},
    }


def install_release_bundle(
    manifest: dict[str, Any],
    layout: dict[str, Path],
    sources: dict[str, Path],
) -> dict[str, Path]:
    _validate_manifest_contract(manifest)
    release_dir = layout["release_dir"]
    required = {
        "bridge_binary",
        "process_runtime_binary",
        "catdesk_binary",
        "catdesk_config",
        "catdesk_backend_key",
        "host_attestation_key",
        "host_source_dir",
        "tunnel_profile",
    }
    missing = sorted(required.difference(sources))
    if missing:
        raise RuntimeError("production release sources missing: " + ", ".join(missing))

    resolved_sources: dict[str, Path] = {}
    try:
        resolved_sources = {
            name: Path(sources[name]).expanduser() for name in required
        }
    except (TypeError, ValueError) as error:
        raise RuntimeError("production release source path is invalid") from error
    for name in (
        "bridge_binary",
        "process_runtime_binary",
        "catdesk_binary",
        "catdesk_config",
        "catdesk_backend_key",
        "host_attestation_key",
    ):
        _validate_owned_file_source(
            resolved_sources[name], f"production release source {name}"
        )
    _validate_owned_tree_source(
        resolved_sources["host_source_dir"], "production Host source tree"
    )
    source_profile_path = resolved_sources["tunnel_profile"]
    if not source_profile_path.is_absolute():
        raise RuntimeError(
            "source tunnel profile must be an explicit absolute path; set "
            "LAB_PRODUCTION_TUNNEL_SOURCE_PROFILE"
        )
    try:
        _validate_private_source(source_profile_path, "source tunnel profile")
    except RuntimeError as error:
        raise RuntimeError(
            f"{error}; provide an explicit verified private profile with "
            "LAB_PRODUCTION_TUNNEL_SOURCE_PROFILE"
        ) from error
    try:
        source_profile = json.loads(source_profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("source tunnel profile is not valid UTF-8 JSON") from error
    profile = production_tunnel_profile(source_profile, manifest, layout)
    source_node = _validate_runtime_dependencies(manifest)["node"]

    install_lock = _production_install_lock(layout["runtime_root"])
    install_lock.__enter__()
    published_profile = layout["tunnel_profile"]
    previous_profile: tuple[str, int] | None = None
    published_profile_changed = False
    release_dir_created = False
    try:
        try:
            published_profile.lstat()
        except FileNotFoundError:
            pass
        else:
            _validate_private_source(
                published_profile, "existing production tunnel profile"
            )
            try:
                previous_profile = (
                    published_profile.read_text(encoding="utf-8"),
                    stat.S_IMODE(published_profile.stat().st_mode),
                )
            except (OSError, UnicodeError) as error:
                raise RuntimeError(
                    "existing production tunnel profile is not valid UTF-8"
                ) from error

        _private_dir(release_dir.parent)
        if release_dir.exists():
            raise RuntimeError(
                f"immutable production release already exists: {release_dir}"
            )
        release_dir.mkdir(mode=0o700)
        release_dir_created = True
        _copy_owned_file(resolved_sources["bridge_binary"], layout["bridge_binary"], 0o700)
        _copy_owned_file(
            resolved_sources["process_runtime_binary"],
            layout["process_runtime_binary"],
            0o700,
        )
        _copy_owned_file(resolved_sources["catdesk_binary"], layout["catdesk_binary"], 0o700)
        _copy_owned_file(Path(source_node["path"]), layout["node_binary"], 0o700)
        copied_node_digest = _sha256_file(layout["node_binary"])
        if copied_node_digest != source_node["sha256"]:
            raise RuntimeError(
                "production Node.js executable changed during immutable release installation"
            )
        node = {
            "path": str(layout["node_binary"].resolve(strict=True)),
            "sha256": copied_node_digest,
        }
        _copy_owned_file(
            resolved_sources["catdesk_config"], layout["release_catdesk_config"], 0o600
        )
        _normalize_production_catdesk_config(layout["release_catdesk_config"])
        _copy_owned_file(
            resolved_sources["catdesk_backend_key"],
            layout["release_catdesk_backend_key"],
            0o600,
        )
        _copy_owned_file(resolved_sources["host_attestation_key"], layout["host_attestation_key"], 0o600)
        _copy_owned_tree(resolved_sources["host_source_dir"], layout["host_dir"])
        _materialize_runtime_host_tool_hook(layout)
        _private_dir(layout["host_backend_key"].parent)
        layout["host_backend_key"].write_bytes(secrets.token_bytes(32).hex().encode("ascii"))
        layout["host_backend_key"].chmod(0o600)
        layout["process_runtime_backend_key"].write_bytes(
            secrets.token_bytes(32).hex().encode("ascii")
        )
        layout["process_runtime_backend_key"].chmod(0o600)

        _write_json_private(layout["release_tunnel_profile"], profile)

        wrapper = "\n".join(
            [
                "#!/bin/sh",
                f"export LAB_HOST_ADAPTER_SOCKET={shlex.quote(str(layout['host_socket']))}",
                f"export LAB_HOST_ATTESTATION_KEY_FILE={shlex.quote(str(layout['host_attestation_key']))}",
                f"export LAB_HOST_TOOL_RECEIPT_STATE_FILE={shlex.quote(str(layout['host_tool_receipt_state']))}",
                f"export LAB_HOST_EXTENSION_BINDING_FILE={shlex.quote(str(layout['host_extension_binding']))}",
                f"exec {shlex.quote(node['path'])} {shlex.quote(str(layout['native_host']))} \"$@\"",
                "",
            ]
        )
        _write_text_private(layout["native_host_wrapper"], wrapper, 0o700)
        verifier_wrapper = "\n".join(
            [
                "#!/bin/sh",
                f"export LAB_HOST_ADAPTER_SOCKET={shlex.quote(str(layout['host_socket']))}",
                f"export LAB_HOST_ATTESTATION_KEY_FILE={shlex.quote(str(layout['host_attestation_key']))}",
                f"export LAB_HOST_VERIFIER_STATE_FILE={shlex.quote(str(layout['host_verifier_state']))}",
                f"export LAB_HOST_REENTRY_STATE_FILE={shlex.quote(str(layout['host_reentry_state']))}",
                f"export LAB_HOST_EXTENSION_BINDING_FILE={shlex.quote(str(layout['host_extension_binding']))}",
                (
                    f"exec {shlex.quote(node['path'])} "
                    f"{shlex.quote(str(layout['runtime_verifier_cli']))} \"$@\""
                ),
                "",
            ]
        )
        _write_text_private(layout["runtime_verifier_wrapper"], verifier_wrapper, 0o700)
        _validate_runtime_verifier_release_files(layout)
        _write_text_private(
            layout["catdesk_runner"],
            _catdesk_runner_source(manifest, layout),
            0o700,
        )

        artifacts: dict[str, str] = {}
        for path in sorted(
            (item for item in release_dir.rglob("*") if item.is_file()),
            key=lambda item: str(item.relative_to(release_dir)),
        ):
            artifacts[str(path.relative_to(release_dir))] = _sha256_file(path)
        installed_manifest = json.loads(json.dumps(manifest))
        installed_manifest["runtime_dependencies"]["node"] = node
        installed_manifest["artifacts_sha256"] = artifacts
        installed_manifest["runtime_host_tool_hook"] = _runtime_host_tool_hook_record(
            layout, installed_manifest
        )
        _write_json_private(layout["release_manifest"], installed_manifest)

        _private_dir(layout["tunnel_dir"])
        published_profile_changed = True
        _write_json_private(published_profile, profile)
        validate_installed_release(layout)
    except Exception as error:
        cleanup_errors: list[str] = []
        try:
            if release_dir_created and release_dir.exists():
                shutil.rmtree(release_dir)
            if release_dir_created and release_dir.exists():
                raise RuntimeError("partial production release directory still exists")
        except Exception as cleanup_error:
            cleanup_errors.append(
                f"incomplete release cleanup failed: {cleanup_error}"
            )
        if published_profile_changed:
            try:
                if previous_profile is None:
                    _validate_private_source(
                        published_profile,
                        "failed production tunnel profile publication",
                    )
                    published_profile.unlink()
                else:
                    previous_text, previous_mode = previous_profile
                    _write_text_private(
                        published_profile, previous_text, previous_mode
                    )
            except Exception as profile_cleanup_error:
                cleanup_errors.append(
                    "production tunnel profile rollback failed: "
                    f"{profile_cleanup_error}"
                )
        if cleanup_errors:
            raise RuntimeError(
                f"production release install failed: {error}; "
                + "; ".join(cleanup_errors)
            ) from error
        raise
    finally:
        install_lock.__exit__(None, None, None)
    return layout


def _normalize_production_catdesk_config(path: Path) -> None:
    """Pin the immutable production bundle to CatDesk's no-widget mode.

    Only the copied release artifact is changed.  Requiring one canonical
    top-level assignment keeps this build-time policy explicit and prevents a
    malformed, nested, or duplicate TOML key from silently changing behavior.
    """
    _validate_private_source(path, "production CatDesk config")
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise RuntimeError(
            "production CatDesk showDetailMode config is not valid UTF-8"
        ) from error

    assignment = re.compile(
        r'^showDetailMode[ \t]*=[ \t]*"(?P<value>expanded|collapsed|disable)"'
        r"[ \t]*(?:#[^\r\n]*)?$",
        re.MULTILINE,
    )
    matches = list(assignment.finditer(source))
    if len(matches) != 1:
        raise RuntimeError(
            "production CatDesk config must contain exactly one canonical "
            'top-level showDetailMode = "expanded"|"collapsed"|"disable" assignment'
        )
    try:
        parsed = tomllib.loads(source)
    except tomllib.TOMLDecodeError as error:
        raise RuntimeError("production CatDesk showDetailMode config is invalid TOML") from error
    if parsed.get("showDetailMode") not in {"expanded", "collapsed", "disable"}:
        raise RuntimeError(
            "production CatDesk showDetailMode must be a supported top-level string"
        )

    match = matches[0]
    start, end = match.span("value")
    normalized = source[:start] + "disable" + source[end:]
    try:
        normalized_parsed = tomllib.loads(normalized)
    except tomllib.TOMLDecodeError as error:
        raise RuntimeError(
            "normalized production CatDesk showDetailMode config is invalid TOML"
        ) from error
    if normalized_parsed.get("showDetailMode") != "disable":
        raise RuntimeError("production CatDesk showDetailMode normalization failed")
    _write_text_private(path, normalized, 0o600)


def validate_installed_release(layout: dict[str, Path]) -> dict[str, Any]:
    manifest_path = layout["release_manifest"]
    _validate_private_source(manifest_path, "production release manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _validate_manifest_contract(manifest)
    artifacts = manifest.get("artifacts_sha256")
    if not isinstance(artifacts, dict) or not artifacts:
        raise RuntimeError("production release manifest has no artifact hashes")
    _validate_runtime_dependencies(manifest)
    _validate_release_local_node(layout, manifest)
    release_dir = layout["release_dir"]
    expected_files = {str(relative) for relative in artifacts}
    if "release.json" in expected_files:
        raise RuntimeError("production release manifest must not hash itself as an artifact")
    actual_files: set[str] = set()
    for item in release_dir.rglob("*"):
        if item.is_symlink():
            raise RuntimeError(f"production release contains unsafe symlink: {item.relative_to(release_dir)}")
        if item.is_file():
            actual_files.add(str(item.relative_to(release_dir)))
    extras = sorted(actual_files.difference(expected_files | {"release.json"}))
    missing = sorted(expected_files.difference(actual_files))
    if extras:
        raise RuntimeError("production release contains unlisted extra artifacts: " + ", ".join(extras))
    if missing:
        raise RuntimeError("production release artifacts are missing: " + ", ".join(missing))
    for relative, expected in artifacts.items():
        relative_path = Path(str(relative))
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise RuntimeError(f"production release artifact path is unsafe: {relative}")
        path = release_dir / relative_path
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"production release artifact missing or unsafe: {relative}")
        if _sha256_file(path) != expected:
            raise RuntimeError(f"production release artifact hash mismatch: {relative}")
    if manifest.get("runtime_host_tool_hook") != _runtime_host_tool_hook_record(layout, manifest):
        raise RuntimeError("production Runtime Host tool hook pin mismatch")
    if _sha256_file(layout["tunnel_profile"]) != _sha256_file(layout["release_tunnel_profile"]):
        raise RuntimeError("production tunnel profile does not match immutable release artifact")
    return manifest


def _validate_rollback_release(
    requested_manifest: dict[str, Any], layout: dict[str, Path]
) -> dict[str, Any]:
    """Validate only immutable identity needed to recover a tunnel."""
    _validate_manifest_identity(requested_manifest)
    manifest_path = layout["release_manifest"]
    _validate_private_source(manifest_path, "production release manifest for rollback")
    try:
        installed = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise RuntimeError("production release manifest for rollback is invalid JSON") from error
    _validate_manifest_identity(installed)
    for key in (
        "connector_id", "contract_version", "release_revision", "endpoint_generation", "workspace"
    ):
        if installed.get(key) != requested_manifest.get(key):
            raise RuntimeError("rollback release identity does not match the requested manifest")
    return installed


def _install_native_messaging_manifests_unlocked(
    layout: dict[str, Path],
    manifest: dict[str, Any],
    *,
    home_dir: Path = Path.home(),
    native_user_data_dirs: Iterable[Path] = (),
    browser_executables: Iterable[Path] = (),
) -> dict[str, Path]:
    _validate_manifest_contract(manifest)
    _validate_immutable_native_wrapper(layout, manifest)
    wrapper = layout["native_host_wrapper"]
    info = wrapper.lstat()
    if wrapper.is_symlink() or not wrapper.is_file() or info.st_uid != os.geteuid():
        raise RuntimeError("production Native Messaging wrapper is unsafe")
    if not os.access(wrapper, os.X_OK):
        raise RuntimeError("production Native Messaging wrapper is not executable")
    extension_manifest = layout["host_dir"] / "chrome-host" / "extension" / "manifest.json"
    extension = json.loads(extension_manifest.read_text(encoding="utf-8"))
    version = str(extension.get("version") or "")
    if _version_tuple(version) < _version_tuple(str(manifest["host"]["minimum_extension_version"])):
        raise RuntimeError("production Host extension version is stale")

    value = {
        "name": "com.local_agent_bridge.web_host",
        "description": "Local Agent Bridge production Web Host native messaging adapter",
        "path": str(wrapper),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{HOST_EXTENSION_ID}/"],
    }
    paths = _native_messaging_paths(home_dir, native_user_data_dirs=native_user_data_dirs)
    extension_binding = _build_runtime_extension_binding(
        layout,
        manifest,
        home_dir=home_dir,
        native_user_data_dirs=native_user_data_dirs,
        browser_executables=browser_executables,
    )
    config_path = _runtime_verifier_config_path(home_dir)
    with _runtime_verifier_config_lock(home_dir):
        snapshots = {
            path: _private_file_snapshot(path, f"Native Messaging manifest for {browser}")
            for browser, path in paths.items()
        }
        snapshots[config_path] = _private_file_snapshot(
            config_path, "Runtime Host verifier config"
        )
        snapshots[layout["host_extension_binding"]] = _private_file_snapshot(
            layout["host_extension_binding"], "Runtime Host extension binding"
        )
        installed: dict[str, Path] = {}
        try:
            for browser, path in paths.items():
                root = path.parent
                root.mkdir(parents=True, exist_ok=True, mode=0o755)
                if root.is_symlink() or not root.is_dir() or root.stat().st_uid != os.geteuid():
                    raise RuntimeError(f"Native Messaging directory is unsafe for {browser}")
                _write_json_private(path, value, private_parent=False)
                installed[browser] = path
            _write_json_private(layout["host_extension_binding"], extension_binding)
            _register_runtime_host_verifier_locked(
                layout, manifest, home_dir=home_dir
            )
            _validate_runtime_host_verifier_registration(
                layout, manifest, home_dir=home_dir
            )
        except Exception as error:
            rollback_errors: list[str] = []
            for path, snapshot in reversed(tuple(snapshots.items())):
                try:
                    _restore_private_file_snapshot(
                        path,
                        snapshot,
                        private_parent=path == config_path,
                    )
                except Exception as rollback_error:
                    rollback_errors.append(f"{path}: {rollback_error}")
            if rollback_errors:
                raise RuntimeError(
                    f"Native Messaging/verifier publication failed: {error}; rollback failed: "
                    + "; ".join(rollback_errors)
                ) from error
            raise
    return installed


def _managed_native_user_data_dirs(
    home_dir: Path, native_user_data_dirs: Iterable[Path]
) -> tuple[Path, ...]:
    candidates = [
        Path(home_dir).expanduser() / ".chatgpt-web-mcp" / name
        for name in DEFAULT_NATIVE_USER_DATA_DIR_NAMES
    ]
    candidates.extend(Path(path).expanduser() for path in native_user_data_dirs)
    observed: set[Path] = set()
    result: list[Path] = []
    for candidate in candidates:
        if not candidate.is_absolute():
            raise RuntimeError("custom Chrome user-data-dir must be absolute")
        resolved = candidate.resolve()
        if resolved not in observed:
            observed.add(resolved)
            result.append(resolved)
    return tuple(result)


def _configured_extension_source(user_data_dir: Path) -> tuple[Path, Path]:
    preferences = user_data_dir / "Default" / "Secure Preferences"
    raw = _read_private_bytes_if_present(
        preferences, "Chrome Secure Preferences for Runtime Host extension"
    )
    if raw is None:
        raise RuntimeError(
            f"Chrome Secure Preferences is missing for managed profile: {user_data_dir}"
        )
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("Chrome Secure Preferences is not valid UTF-8 JSON") from error
    record = (
        (((value.get("extensions") or {}).get("settings") or {}).get(HOST_EXTENSION_ID))
        if isinstance(value, dict)
        else None
    )
    configured = record.get("path") if isinstance(record, dict) else None
    if not isinstance(configured, str) or not configured.strip():
        raise RuntimeError("managed Chrome profile has no Local Agent Bridge extension path")
    extension_dir = Path(configured).expanduser()
    if not extension_dir.is_absolute():
        raise RuntimeError("managed Chrome extension path must be absolute")
    try:
        info = extension_dir.lstat()
        resolved = extension_dir.resolve(strict=True)
    except FileNotFoundError as error:
        raise RuntimeError("managed Chrome extension path is unavailable") from error
    if extension_dir.is_symlink() or not extension_dir.is_dir() or info.st_uid != os.geteuid():
        raise RuntimeError("managed Chrome extension path is unsafe")
    return preferences, resolved


def _build_runtime_extension_binding(
    layout: dict[str, Path],
    manifest: dict[str, Any],
    *,
    home_dir: Path,
    native_user_data_dirs: Iterable[Path],
    browser_executables: Iterable[Path],
) -> dict[str, Any]:
    profiles = _managed_native_user_data_dirs(home_dir, native_user_data_dirs)
    configured = [_configured_extension_source(profile) for profile in profiles]
    for user_data_dir, (bound_preference, _) in zip(profiles, configured, strict=True):
        try:
            profile_dirs = tuple(user_data_dir.iterdir())
        except OSError as error:
            raise RuntimeError("cannot inspect managed Chrome profile directories") from error
        for profile_dir in profile_dirs:
            candidate = profile_dir / "Secure Preferences"
            if candidate == bound_preference or not candidate.exists():
                continue
            if profile_dir.is_symlink() or not profile_dir.is_dir():
                raise RuntimeError("managed Chrome profile directory is unsafe")
            raw = _read_private_bytes_if_present(
                candidate, "unbound Chrome Secure Preferences"
            )
            if raw is None:
                continue
            try:
                profile_value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise RuntimeError("unbound Chrome Secure Preferences is invalid") from error
            record = (
                (((profile_value.get("extensions") or {}).get("settings") or {}).get(HOST_EXTENSION_ID))
                if isinstance(profile_value, dict)
                else None
            )
            if isinstance(record, dict):
                raise RuntimeError(
                    "Host extension is installed in an unbound Chrome profile"
                )
    extension_dirs = {extension_dir for _, extension_dir in configured}
    if len(extension_dirs) != 1:
        raise RuntimeError("managed Chrome profiles do not use one exact Host extension source")
    extension_dir = extension_dirs.pop()
    executable_pins: list[dict[str, str]] = []
    observed_executables: set[Path] = set()
    for raw_path in browser_executables:
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            raise RuntimeError("trusted Chrome executable path must be absolute")
        try:
            info = candidate.lstat()
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError as error:
            raise RuntimeError("trusted Chrome executable is missing") from error
        if (
            candidate.is_symlink()
            or not candidate.is_file()
            or info.st_uid not in {0, os.geteuid()}
            or not os.access(candidate, os.X_OK)
            or not str(resolved).endswith(
                (
                    ".app/Contents/MacOS/Google Chrome",
                    ".app/Contents/MacOS/Google Chrome for Testing",
                )
            )
        ):
            raise RuntimeError("trusted Chrome executable is unsafe or unsupported")
        if resolved in observed_executables:
            continue
        observed_executables.add(resolved)
        executable_pins.append({"path": str(resolved), "sha256": _sha256_file(resolved)})
    if not executable_pins or len(executable_pins) > 8:
        raise RuntimeError("at least one trusted Chrome executable pin is required")
    release_extension_dir = layout["host_dir"] / "chrome-host" / "extension"
    bundle_sha256: dict[str, str] = {}
    for relative in RUNTIME_EXTENSION_EXECUTION_RELATIVE_PATHS:
        actual = extension_dir / relative
        release = release_extension_dir / relative
        for path, label in (
            (actual, "loaded Chrome extension artifact"),
            (release, "production release extension artifact"),
        ):
            try:
                path_info = path.lstat()
            except FileNotFoundError as error:
                raise RuntimeError(f"{label} is missing: {relative}") from error
            if (
                path.is_symlink()
                or not path.is_file()
                or path_info.st_uid != os.geteuid()
                or stat.S_IMODE(path_info.st_mode) & 0o022
            ):
                raise RuntimeError(f"{label} is unsafe: {relative}")
        actual_digest = _sha256_file(actual)
        if actual_digest != _sha256_file(release):
            raise RuntimeError(
                f"loaded Chrome extension does not match production release: {relative}"
            )
        bundle_sha256[str(actual)] = actual_digest
    return {
        "schema_version": "runtime_host_extension_binding_v1",
        "extension_id": HOST_EXTENSION_ID,
        "release_revision": manifest["release_revision"],
        "created_at_unix_ms": int(time.time() * 1000),
        "extension_dir": str(extension_dir),
        "preference_paths": [str(path) for path, _ in configured],
        "browser_executables": executable_pins,
        "bundle_sha256": bundle_sha256,
    }


def _validate_runtime_extension_binding(
    layout: dict[str, Path], installed: dict[str, Any]
) -> dict[str, Any]:
    path = layout["host_extension_binding"]
    raw = _read_private_bytes_if_present(path, "Runtime Host extension binding")
    if raw is None:
        raise RuntimeError("Runtime Host extension binding is missing")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("Runtime Host extension binding is not valid UTF-8 JSON") from error
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "runtime_host_extension_binding_v1"
        or value.get("extension_id") != HOST_EXTENSION_ID
        or value.get("release_revision") != installed.get("release_revision")
        or not isinstance(value.get("created_at_unix_ms"), int)
        or value["created_at_unix_ms"] < 1
    ):
        raise RuntimeError("Runtime Host extension binding identity mismatch")
    extension_dir_raw = value.get("extension_dir")
    preferences = value.get("preference_paths")
    bundle = value.get("bundle_sha256")
    browser_executables = value.get("browser_executables")
    if (
        not isinstance(extension_dir_raw, str)
        or not Path(extension_dir_raw).is_absolute()
        or not isinstance(preferences, list)
        or not preferences
        or not isinstance(bundle, dict)
        or len(bundle) != len(RUNTIME_EXTENSION_EXECUTION_RELATIVE_PATHS)
        or not isinstance(browser_executables, list)
        or not browser_executables
        or len(browser_executables) > 8
    ):
        raise RuntimeError("Runtime Host extension binding structure mismatch")
    extension_dir = Path(extension_dir_raw).resolve(strict=True)
    for preference_raw in preferences:
        if not isinstance(preference_raw, str) or not Path(preference_raw).is_absolute():
            raise RuntimeError("Runtime Host extension preference path is invalid")
        _, configured_dir = _configured_extension_source(Path(preference_raw).parents[1])
        if configured_dir != extension_dir:
            raise RuntimeError("Runtime Host extension profile binding drifted")
    observed_browser_paths: set[Path] = set()
    for record in browser_executables:
        if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
            raise RuntimeError("Runtime Host browser executable pin is invalid")
        raw_path = record.get("path")
        digest = record.get("sha256")
        if not isinstance(raw_path, str) or not Path(raw_path).is_absolute():
            raise RuntimeError("Runtime Host browser executable path is invalid")
        path = Path(raw_path)
        try:
            info = path.lstat()
            resolved = path.resolve(strict=True)
        except FileNotFoundError as error:
            raise RuntimeError("Runtime Host browser executable is missing") from error
        if (
            path.is_symlink()
            or not path.is_file()
            or info.st_uid not in {0, os.geteuid()}
            or not os.access(path, os.X_OK)
            or resolved in observed_browser_paths
            or not str(resolved).endswith(
                (
                    ".app/Contents/MacOS/Google Chrome",
                    ".app/Contents/MacOS/Google Chrome for Testing",
                )
            )
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or _sha256_file(path) != digest
        ):
            raise RuntimeError("Runtime Host browser executable pin mismatch")
        observed_browser_paths.add(resolved)
    release_extension_dir = layout["host_dir"] / "chrome-host" / "extension"
    expected_paths = {
        str(extension_dir / relative) for relative in RUNTIME_EXTENSION_EXECUTION_RELATIVE_PATHS
    }
    if set(bundle) != expected_paths:
        raise RuntimeError("Runtime Host extension bundle path mismatch")
    for relative in RUNTIME_EXTENSION_EXECUTION_RELATIVE_PATHS:
        actual = extension_dir / relative
        expected = bundle.get(str(actual))
        if (
            not isinstance(expected, str)
            or _sha256_file(actual) != expected
            or _sha256_file(release_extension_dir / relative) != expected
        ):
            raise RuntimeError(f"Runtime Host extension bundle hash mismatch: {relative}")
    return value


def _runtime_verifier_bundle_paths(layout: dict[str, Path]) -> tuple[Path, ...]:
    return (
        layout["runtime_verifier_wrapper"],
        *(layout["host_dir"] / relative for relative in RUNTIME_VERIFIER_BUNDLE_RELATIVE_PATHS),
    )


def _validate_runtime_verifier_release_files(layout: dict[str, Path]) -> None:
    release_dir = layout["release_dir"].resolve(strict=True)
    bundle = _runtime_verifier_bundle_paths(layout)
    if len(bundle) != len(set(bundle)):
        raise RuntimeError("production Runtime Host verifier bundle contains duplicates")
    for path in bundle:
        try:
            resolved = path.resolve(strict=True)
            info = path.lstat()
        except FileNotFoundError as error:
            raise RuntimeError(
                f"production Runtime Host verifier artifact is missing: {path}"
            ) from error
        if (
            path.is_symlink()
            or not path.is_file()
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or not resolved.is_relative_to(release_dir)
        ):
            raise RuntimeError(
                f"production Runtime Host verifier artifact is unsafe: {path}"
            )
    for path in (layout["runtime_verifier_wrapper"], layout["runtime_verifier_cli"]):
        if not os.access(path, os.X_OK):
            raise RuntimeError(
                f"production Runtime Host verifier executable is not executable: {path}"
            )


def _runtime_verifier_config_path(home_dir: Path) -> Path:
    return Path(home_dir).expanduser() / ".codex" / "adaptive-delivery-host-attestation-verifiers.json"


@contextmanager
def _runtime_verifier_config_lock(home_dir: Path):
    config_path = _runtime_verifier_config_path(home_dir)
    _private_dir(config_path.parent)
    lock_path = config_path.with_name(config_path.name + ".lock")
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(lock_path, flags, 0o600)
    except FileExistsError as error:
        raise RuntimeError(
            "Runtime Host verifier publication lock is already held"
        ) from error
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise RuntimeError("Runtime Host verifier config lock is unsafe")
        os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        os.fsync(fd)
        yield
    finally:
        try:
            lock_info = lock_path.lstat()
            open_info = os.fstat(fd)
            if (
                stat.S_ISREG(lock_info.st_mode)
                and lock_info.st_dev == open_info.st_dev
                and lock_info.st_ino == open_info.st_ino
            ):
                lock_path.unlink()
        finally:
            os.close(fd)


def _runtime_verifier_record(
    layout: dict[str, Path], installed: dict[str, Any]
) -> dict[str, Any]:
    _validate_runtime_verifier_release_files(layout)
    artifacts = installed.get("artifacts_sha256")
    if not isinstance(artifacts, dict):
        raise RuntimeError("production release manifest has no Runtime verifier artifact hashes")
    bundle_sha256: dict[str, str] = {}
    for path in _runtime_verifier_bundle_paths(layout):
        relative = str(path.relative_to(layout["release_dir"]))
        expected = artifacts.get(relative)
        actual = _sha256_file(path)
        if not isinstance(expected, str) or expected != actual:
            raise RuntimeError(
                f"Runtime Host verifier artifact is not pinned to the immutable release: {relative}"
            )
        bundle_sha256[str(path.resolve(strict=True))] = actual
    node_record = _validate_release_local_node(layout, installed)
    node_path = Path(node_record["path"])
    node_digest = node_record["sha256"]
    bundle_sha256[str(node_path)] = node_digest
    extension_binding = _validate_runtime_extension_binding(layout, installed)
    binding_path = layout["host_extension_binding"]
    bundle_sha256[str(binding_path.resolve(strict=True))] = _sha256_file(binding_path)
    for actual_path, actual_digest in extension_binding["bundle_sha256"].items():
        existing = bundle_sha256.get(actual_path)
        if existing is not None and existing != actual_digest:
            raise RuntimeError("Runtime Host extension pin conflicts with release bundle")
        bundle_sha256[actual_path] = actual_digest
    if len(bundle_sha256) > 64:
        raise RuntimeError("production Runtime Host verifier bundle exceeds Runtime limit")
    wrapper = layout["runtime_verifier_wrapper"].resolve(strict=True)
    return {
        "protocol": "runtime_host_verifier_cli_v2",
        "executable": str(wrapper),
        "sha256": _sha256_file(wrapper),
        "bundle_sha256": bundle_sha256,
    }


def _validate_release_local_node(
    layout: dict[str, Path], installed: dict[str, Any]
) -> dict[str, str]:
    artifacts = installed.get("artifacts_sha256")
    if not isinstance(artifacts, dict):
        raise RuntimeError("production release manifest has no Node artifact hashes")
    node_record = (installed.get("runtime_dependencies") or {}).get("node")
    if not isinstance(node_record, dict):
        raise RuntimeError("production release Node pin is missing")
    node_path_raw = node_record.get("path")
    node_digest = node_record.get("sha256")
    if not isinstance(node_path_raw, str) or not isinstance(node_digest, str):
        raise RuntimeError("production release Node pin is invalid")
    node_path = Path(node_path_raw)
    try:
        node_info = node_path.lstat()
        expected_node_path = layout["node_binary"].resolve(strict=True)
    except FileNotFoundError as error:
        raise RuntimeError("production release Node executable is missing") from error
    node_relative = str(
        expected_node_path.relative_to(layout["release_dir"].resolve(strict=True))
    )
    if (
        node_path.resolve(strict=True) != expected_node_path
        or artifacts.get(node_relative) != node_digest
    ):
        raise RuntimeError(
            "production release Node executable is not pinned to the immutable release"
        )
    if (
        not node_path.is_absolute()
        or node_path.is_symlink()
        or not node_path.is_file()
        or node_info.st_uid != os.geteuid()
        or stat.S_IMODE(node_info.st_mode) & 0o077
        or not os.access(node_path, os.X_OK)
        or _sha256_file(node_path) != node_digest
    ):
        raise RuntimeError("production release Node executable pin mismatch")
    return {"path": str(expected_node_path), "sha256": node_digest}


def _read_runtime_verifier_config(path: Path) -> tuple[dict[str, Any], bytes | None]:
    raw = _read_private_bytes_if_present(path, "Runtime Host verifier config")
    if raw is None:
        return {"schema_version": 1, "verifiers": {}}, None
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("Runtime Host verifier config is not valid UTF-8 JSON") from error
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or not isinstance(value.get("verifiers"), dict)
    ):
        raise RuntimeError("Runtime Host verifier config schema is invalid")
    return value, raw


def _register_runtime_host_verifier(
    layout: dict[str, Path], manifest: dict[str, Any], *, home_dir: Path
) -> Path:
    with _runtime_verifier_config_lock(home_dir):
        return _register_runtime_host_verifier_locked(
            layout, manifest, home_dir=home_dir
        )


def _register_runtime_host_verifier_locked(
    layout: dict[str, Path], manifest: dict[str, Any], *, home_dir: Path
) -> Path:
    installed = _validate_immutable_native_wrapper(layout, manifest)
    record = _runtime_verifier_record(layout, installed)
    config_path = _runtime_verifier_config_path(home_dir)
    value, original = _read_runtime_verifier_config(config_path)
    updated = json.loads(json.dumps(value))
    updated["verifiers"]["web"] = record
    current = _read_private_bytes_if_present(
        config_path, "Runtime Host verifier config"
    )
    if current != original:
        raise RuntimeError(
            "Runtime Host verifier config changed concurrently before publication"
        )
    _write_json_private(config_path, updated)
    return config_path


def _validate_runtime_host_verifier_registration(
    layout: dict[str, Path], manifest: dict[str, Any], *, home_dir: Path
) -> Path:
    installed = _validate_immutable_native_wrapper(layout, manifest)
    expected = _runtime_verifier_record(layout, installed)
    config_path = _runtime_verifier_config_path(home_dir)
    value, _ = _read_runtime_verifier_config(config_path)
    if value["verifiers"].get("web") != expected:
        raise RuntimeError("Runtime Host verifier registration pin mismatch")
    return config_path


def _native_messaging_paths(
    home_dir: Path,
    *,
    native_user_data_dirs: Iterable[Path] = (),
) -> dict[str, Path]:
    roots = {
        "chrome": Path(home_dir)
        / "Library"
        / "Application Support"
        / "Google"
        / "Chrome"
        / "NativeMessagingHosts",
        "chrome-for-testing": Path(home_dir)
        / "Library"
        / "Application Support"
        / "Google"
        / "ChromeForTesting"
        / "NativeMessagingHosts",
    }
    paths = {browser: root / "com.local_agent_bridge.web_host.json" for browser, root in roots.items()}
    for name in DEFAULT_NATIVE_USER_DATA_DIR_NAMES:
        user_data_dir = (Path(home_dir) / ".chatgpt-web-mcp" / name).resolve()
        paths[f"custom:{user_data_dir}"] = (
            user_data_dir / "NativeMessagingHosts" / "com.local_agent_bridge.web_host.json"
        )
    seen = {path.parent.resolve() for path in paths.values()}
    for raw in native_user_data_dirs:
        expanded_user_data_dir = Path(raw).expanduser()
        if not expanded_user_data_dir.is_absolute():
            raise RuntimeError("custom Chrome user-data-dir must be absolute")
        user_data_dir = expanded_user_data_dir.resolve()
        root = user_data_dir / "NativeMessagingHosts"
        resolved_root = root.resolve()
        if resolved_root in seen:
            continue
        seen.add(resolved_root)
        paths[f"custom:{user_data_dir}"] = root / "com.local_agent_bridge.web_host.json"
    return paths


def _validate_immutable_native_wrapper(
    layout: dict[str, Path], manifest: dict[str, Any]
) -> dict[str, Any]:
    installed = validate_installed_release(layout)
    if (
        installed.get("release_revision") != manifest.get("release_revision")
        or installed.get("endpoint_generation") != manifest.get("endpoint_generation")
    ):
        raise RuntimeError("Native Messaging wrapper release pin does not match requested manifest")
    relative = str(layout["native_host_wrapper"].relative_to(layout["release_dir"]))
    expected = (installed.get("artifacts_sha256") or {}).get(relative)
    if not isinstance(expected, str) or _sha256_file(layout["native_host_wrapper"]) != expected:
        raise RuntimeError("Native Messaging wrapper is not pinned to an immutable release artifact")
    return installed


def validate_native_messaging_manifests(
    layout: dict[str, Path],
    manifest: dict[str, Any],
    *,
    home_dir: Path = Path.home(),
    native_user_data_dirs: Iterable[Path] = (),
) -> dict[str, Path]:
    """Require both supported Chrome installations to pin the same immutable wrapper."""
    _validate_manifest_contract(manifest)
    _validate_immutable_native_wrapper(layout, manifest)
    expected = {
        "name": "com.local_agent_bridge.web_host",
        "path": str(layout["native_host_wrapper"]),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{HOST_EXTENSION_ID}/"],
    }
    paths = _native_messaging_paths(home_dir, native_user_data_dirs=native_user_data_dirs)
    for browser, path in paths.items():
        _validate_private_source(path, f"Native Messaging manifest for {browser}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise RuntimeError(f"Native Messaging manifest for {browser} is not valid JSON") from error
        if not isinstance(value, dict) or any(value.get(key) != wanted for key, wanted in expected.items()):
            raise RuntimeError(f"Native Messaging manifest pin mismatch for {browser}")
    _validate_runtime_host_verifier_registration(layout, manifest, home_dir=home_dir)
    return paths


def _read_loopback_url_file(path: Path, label: str) -> tuple[str, urllib.parse.SplitResult]:
    _validate_private_source(path, label)
    value = path.read_text(encoding="utf-8").strip()
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "http" or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError(f"{label} must contain a private loopback HTTP URL")
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
        port = parsed.port
    except ValueError as error:
        raise RuntimeError(f"{label} must contain a private loopback HTTP URL") from error
    if not address.is_loopback or port is None:
        raise RuntimeError(f"{label} must contain a private loopback HTTP URL with an explicit port")
    return value, parsed


def _decode_json_or_sse(raw: bytes, label: str) -> dict[str, Any]:
    text = raw.decode("utf-8")
    candidates = [text]
    candidates.extend(
        line[5:].strip()
        for line in text.splitlines()
        if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]")
    )
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise RuntimeError(f"{label} did not return a JSON object")


def _request_json(request: urllib.request.Request, label: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(request, timeout=3.0) as response:
            if getattr(response, "status", None) != 200:
                raise RuntimeError(f"{label} returned HTTP {getattr(response, 'status', 'unknown')}")
            return _decode_json_or_sse(response.read(), label)
    except RuntimeError:
        raise
    except Exception as error:
        raise RuntimeError(f"{label} request failed") from error


MAX_TUNNEL_HEALTH_BODY_BYTES = 1024 * 1024
TUNNEL_HEALTH_FUTURE_TOLERANCE_SECONDS = 2.0
TUNNEL_POLL_STATUS_MAX_AGE_SECONDS = 60.0
# The control-plane long poll is 30s in tunnel-client 0.0.14.  Readiness must
# permit one complete fresh poll plus bounded scheduling/network overhead.
TUNNEL_POLL_STARTUP_TIMEOUT_SECONDS = 35.0
TUNNEL_POLL_RETRY_INTERVAL_SECONDS = 0.1


class _TransientTunnelReadinessError(RuntimeError):
    """A valid startup observation that can become ready without identity changes."""


class _TransientTunnelMetricsError(_TransientTunnelReadinessError):
    """A valid poller snapshot that can become ready without identity changes."""


class _TransientTunnelStartupEventError(_TransientTunnelReadinessError):
    """A valid but incomplete startup event chain."""


class _NoTunnelHealthRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> None:
        return None


TUNNEL_HEALTH_PROXY_HANDLER = urllib.request.ProxyHandler({})
TUNNEL_HEALTH_OPENER = urllib.request.build_opener(
    TUNNEL_HEALTH_PROXY_HANDLER,
    _NoTunnelHealthRedirect(),
)


def _request_tunnel_health_bytes(
    request: urllib.request.Request, label: str
) -> tuple[bytes, str | None]:
    try:
        with TUNNEL_HEALTH_OPENER.open(request, timeout=3.0) as response:
            if getattr(response, "status", None) != 200:
                raise RuntimeError(
                    f"{label} returned HTTP {getattr(response, 'status', 'unknown')}"
                )
            if response.geturl() != request.full_url:
                raise RuntimeError(f"{label} redirected away from its private loopback URL")
            raw = response.read(MAX_TUNNEL_HEALTH_BODY_BYTES + 1)
            if len(raw) > MAX_TUNNEL_HEALTH_BODY_BYTES:
                raise RuntimeError(f"{label} response body is too large")
            content_type = response.headers.get("Content-Type")
            return raw, content_type if isinstance(content_type, str) else None
    except urllib.error.HTTPError as error:
        if 300 <= error.code < 400:
            raise RuntimeError(f"{label} refused an HTTP redirect") from error
        raise RuntimeError(f"{label} returned HTTP {error.code}") from error
    except RuntimeError:
        raise
    except Exception as error:
        raise RuntimeError(f"{label} request failed") from error


def _parse_prometheus_labels(raw: str, label: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    offset = 0
    token = re.compile(r'\s*([A-Za-z_][A-Za-z0-9_]*)="((?:\\.|[^"\\])*)"\s*(,|$)')
    while offset < len(raw):
        match = token.match(raw, offset)
        if match is None:
            raise RuntimeError(f"{label} has malformed Prometheus labels")
        key, encoded, separator = match.groups()
        if key in labels:
            raise RuntimeError(f"{label} has duplicate Prometheus labels")
        try:
            value = json.loads(f'"{encoded}"')
        except json.JSONDecodeError as error:
            raise RuntimeError(f"{label} has malformed Prometheus label escaping") from error
        labels[key] = value
        offset = match.end()
        if separator == "":
            break
        if offset == len(raw):
            raise RuntimeError(f"{label} has a trailing Prometheus label separator")
    return labels


def _parse_controlplane_poll_metrics(
    raw: bytes,
    *,
    started_at: float,
    now: float,
    max_age_seconds: float = TUNNEL_POLL_STATUS_MAX_AGE_SECONDS,
) -> dict[str, float]:
    if (
        isinstance(started_at, bool)
        or not isinstance(started_at, (int, float))
        or not math.isfinite(float(started_at))
        or isinstance(now, bool)
        or not isinstance(now, (int, float))
        or not math.isfinite(float(now))
        or isinstance(max_age_seconds, bool)
        or not isinstance(max_age_seconds, (int, float))
        or not math.isfinite(float(max_age_seconds))
        or max_age_seconds <= 0
    ):
        raise RuntimeError("production tunnel metrics time bounds must be finite")
    if started_at > now + TUNNEL_HEALTH_FUTURE_TOLERANCE_SECONDS:
        raise RuntimeError("production tunnel metrics started_at is in the future")
    expected_content: dict[str, list[tuple[dict[str, str], float]]] = {
        "commands_poll_cycles_total": [],
        "commands_poll_last_successful_timestamp_seconds": [],
    }
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RuntimeError("production tunnel metrics returned non-UTF-8 data") from error
    sample = re.compile(
        r"^([A-Za-z_:][A-Za-z0-9_:]*)(?:\{(.*)\})?\s+(\S+)(?:\s+\d+)?$"
    )
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        metric_name = stripped.split("{", 1)[0].split(None, 1)[0]
        if metric_name not in expected_content:
            continue
        match = sample.fullmatch(stripped)
        if match is None or match.group(1) != metric_name:
            raise RuntimeError(f"production tunnel metrics sample {metric_name} is malformed")
        raw_labels = match.group(2)
        if raw_labels is None:
            raise RuntimeError(f"production tunnel metrics sample {metric_name} has no labels")
        labels = _parse_prometheus_labels(raw_labels, f"production tunnel metrics {metric_name}")
        if "otel_scope_name" not in labels:
            raise RuntimeError(
                f"production tunnel metrics sample {metric_name} has no otel_scope_name label"
            )
        if labels["otel_scope_name"] != "controlplane":
            continue
        if "service_name" in labels and labels["service_name"] != "tunnel-client":
            raise RuntimeError(
                f"production tunnel metrics sample {metric_name} service_name mismatch"
            )
        try:
            value = float(match.group(3))
        except ValueError as error:
            raise RuntimeError(
                f"production tunnel metrics sample {metric_name} is not numeric"
            ) from error
        if not math.isfinite(value):
            raise RuntimeError(f"production tunnel metrics sample {metric_name} is not finite")
        expected_content[metric_name].append((labels, value))
    for metric_name, values in expected_content.items():
        if not values:
            raise _TransientTunnelMetricsError(
                f"production tunnel metrics are missing controlplane sample {metric_name}"
            )
        if len(values) != 1:
            raise RuntimeError(
                f"production tunnel metrics require one unique controlplane sample for {metric_name}"
            )
    cycles_labels, cycles = expected_content["commands_poll_cycles_total"][0]
    success_labels, last_success = expected_content[
        "commands_poll_last_successful_timestamp_seconds"
    ][0]
    if cycles_labels != success_labels:
        raise RuntimeError("production tunnel controlplane poll metric labels do not match")
    if cycles == 0:
        raise _TransientTunnelMetricsError(
            "production tunnel controlplane poll cycles have not started"
        )
    if cycles < 0 or not cycles.is_integer():
        raise RuntimeError("production tunnel controlplane poll cycles are not a positive integer")
    if last_success < 0:
        raise RuntimeError("production tunnel controlplane poll success timestamp is negative")
    if last_success < math.floor(started_at):
        raise _TransientTunnelMetricsError(
            "production tunnel controlplane poll success is not fresh for this startup"
        )
    if last_success < now - max_age_seconds:
        raise _TransientTunnelMetricsError(
            "production tunnel controlplane poll success is stale"
        )
    if last_success > now + TUNNEL_HEALTH_FUTURE_TOLERANCE_SECONDS:
        raise RuntimeError("production tunnel controlplane poll success is in the future")
    return {"cycles": cycles, "last_successful_timestamp": last_success}


def _validate_prometheus_content_type(value: str | None) -> None:
    if not isinstance(value, str):
        raise RuntimeError("production tunnel metrics omitted Content-Type")
    parts = [part.strip().lower() for part in value.split(";")]
    if not parts or parts[0] != "text/plain":
        raise RuntimeError("production tunnel metrics returned an unsupported Content-Type")
    parameters: dict[str, str] = {}
    for raw in parts[1:]:
        if "=" not in raw:
            raise RuntimeError("production tunnel metrics returned an invalid Content-Type")
        key, parameter = (item.strip() for item in raw.split("=", 1))
        if not key or not parameter or key in parameters:
            raise RuntimeError("production tunnel metrics returned an invalid Content-Type")
        parameters[key] = parameter
    if parameters != {
        "version": "0.0.4",
        "charset": "utf-8",
        "escaping": "underscores",
    }:
        raise RuntimeError("production tunnel metrics returned an unsupported Content-Type")


MCP_STREAMABLE_PROTOCOL_VERSION = "2025-03-26"
MCP_STREAMABLE_ACCEPT = "application/json, text/event-stream"


def _single_jsonrpc_result(
    raw: bytes, label: str, *, expected_id: str, content_type: str
) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RuntimeError(f"{label} returned non-UTF-8 data") from error
    payloads: list[Any] = []
    if content_type == "application/json":
        try:
            value = json.loads(text)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"{label} returned invalid JSON for application/json") from error
        payloads.append(value)
    elif content_type == "text/event-stream":
        data_lines = [
            line[5:].strip()
            for line in text.splitlines()
            if line.startswith("data:") and line[5:].strip()
        ]
        for data in data_lines:
            try:
                payloads.append(json.loads(data))
            except json.JSONDecodeError as error:
                raise RuntimeError(f"{label} returned invalid SSE JSON") from error
    else:
        raise RuntimeError(f"{label} returned unsupported Content-Type {content_type or 'missing'}")
    if len(payloads) != 1 or not isinstance(payloads[0], dict):
        raise RuntimeError(f"{label} must return a single JSON-RPC result")
    payload = payloads[0]
    if payload.get("jsonrpc") != "2.0" or payload.get("id") != expected_id:
        raise RuntimeError(f"{label} returned a mismatched JSON-RPC id")
    if "error" in payload:
        raise RuntimeError(f"{label} returned a JSON-RPC error")
    if not isinstance(payload.get("result"), dict):
        raise RuntimeError(f"{label} omitted its JSON-RPC result")
    return payload


def _response_session_id(response: Any, label: str) -> str | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    get_all = getattr(headers, "get_all", None)
    values = get_all("Mcp-Session-Id") if callable(get_all) else None
    if values is None:
        value = headers.get("Mcp-Session-Id")
        values = [] if value is None else [value]
    if len(values) > 1:
        raise RuntimeError(f"{label} returned multiple MCP session ids")
    if not values:
        return None
    session_id = str(values[0])
    if not 1 <= len(session_id) <= 256 or any(
        ord(character) < 0x21 or ord(character) > 0x7E for character in session_id
    ):
        raise RuntimeError(f"{label} returned an invalid MCP session id")
    return session_id


def _open_mcp_streamable(
    request: urllib.request.Request,
    label: str,
    *,
    allowed_statuses: set[int],
    expected_session: str | None = None,
) -> tuple[bytes, str | None, str | None]:
    try:
        with urllib.request.urlopen(request, timeout=3.0) as response:
            status = getattr(response, "status", None)
            if status not in allowed_statuses:
                raise RuntimeError(f"{label} returned HTTP {status}")
            response_session = _response_session_id(response, label)
            if expected_session is not None and response_session not in (None, expected_session):
                raise RuntimeError(f"{label} returned a mismatched MCP session id")
            raw_content_type = response.headers.get("Content-Type") if getattr(response, "headers", None) else None
            content_type = (
                str(raw_content_type).split(";", 1)[0].strip().lower()
                if raw_content_type is not None
                else None
            )
            return response.read(), response_session, content_type
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"{label} returned HTTP {error.code}") from error
    except RuntimeError:
        raise
    except Exception as error:
        raise RuntimeError(f"{label} request failed") from error


def _mcp_streamable_tools_list(compute_url: str) -> dict[str, Any]:
    parsed = urllib.parse.urlsplit(compute_url)
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
        port = parsed.port
    except ValueError as error:
        raise RuntimeError("fallback MCP endpoint must be an explicit loopback HTTP URL") from error
    if (
        parsed.scheme != "http"
        or not address.is_loopback
        or port is None
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("fallback MCP endpoint must be an explicit loopback HTTP URL")

    common_headers = {
        "Accept": MCP_STREAMABLE_ACCEPT,
        "MCP-Protocol-Version": MCP_STREAMABLE_PROTOCOL_VERSION,
    }
    session_id: str | None = None
    result: dict[str, Any] | None = None
    primary_error: Exception | None = None
    try:
        initialize_id = "fallback-initialize"
        initialize = {
            "jsonrpc": "2.0",
            "id": initialize_id,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_STREAMABLE_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "local-agent-bridge-production-fallback-check",
                    "version": "1",
                },
            },
        }
        raw, session_id, content_type = _open_mcp_streamable(
            urllib.request.Request(
                compute_url,
                data=json.dumps(initialize, separators=(",", ":")).encode("utf-8"),
                method="POST",
                headers={**common_headers, "Content-Type": "application/json"},
            ),
            "AI-Bridge fallback initialize",
            allowed_statuses={200},
        )
        if session_id is None:
            raise RuntimeError("AI-Bridge fallback initialize omitted its MCP session id")
        initialize_response = _single_jsonrpc_result(
            raw,
            "AI-Bridge fallback initialize",
            expected_id=initialize_id,
            content_type=str(content_type or ""),
        )
        if initialize_response["result"].get("protocolVersion") != MCP_STREAMABLE_PROTOCOL_VERSION:
            raise RuntimeError("AI-Bridge fallback initialize protocol version mismatch")

        session_headers = {**common_headers, "Mcp-Session-Id": session_id}
        initialized = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        raw, _, content_type = _open_mcp_streamable(
            urllib.request.Request(
                compute_url,
                data=json.dumps(initialized, separators=(",", ":")).encode("utf-8"),
                method="POST",
                headers={**session_headers, "Content-Type": "application/json"},
            ),
            "AI-Bridge fallback initialized notification",
            allowed_statuses={202},
            expected_session=session_id,
        )
        if raw:
            raise RuntimeError("AI-Bridge fallback initialized notification returned an unexpected body")
        if content_type not in (None, "application/json", "text/event-stream"):
            raise RuntimeError("AI-Bridge fallback initialized notification returned unsupported Content-Type")

        tools_id = "fallback-tools-list"
        tools_request = {
            "jsonrpc": "2.0",
            "id": tools_id,
            "method": "tools/list",
            "params": {},
        }
        raw, _, content_type = _open_mcp_streamable(
            urllib.request.Request(
                compute_url,
                data=json.dumps(tools_request, separators=(",", ":")).encode("utf-8"),
                method="POST",
                headers={**session_headers, "Content-Type": "application/json"},
            ),
            "AI-Bridge fallback tools/list",
            allowed_statuses={200},
            expected_session=session_id,
        )
        result = _single_jsonrpc_result(
            raw,
            "AI-Bridge fallback tools/list",
            expected_id=tools_id,
            content_type=str(content_type or ""),
        )
    except Exception as error:
        primary_error = error
    cleanup_error: Exception | None = None
    if session_id is not None:
        try:
            raw, _, content_type = _open_mcp_streamable(
                urllib.request.Request(
                    compute_url,
                    method="DELETE",
                    headers={**common_headers, "Mcp-Session-Id": session_id},
                ),
                "AI-Bridge fallback session cleanup",
                allowed_statuses={202},
                expected_session=session_id,
            )
            if raw:
                raise RuntimeError("AI-Bridge fallback session cleanup returned an unexpected body")
            if content_type not in (None, "application/json", "text/event-stream"):
                raise RuntimeError("AI-Bridge fallback session cleanup returned unsupported Content-Type")
        except Exception as error:
            cleanup_error = error
    if primary_error is not None:
        if cleanup_error is not None:
            raise RuntimeError(f"{primary_error}; session cleanup failed: {cleanup_error}") from primary_error
        raise primary_error
    if cleanup_error is not None:
        raise RuntimeError(f"AI-Bridge fallback session cleanup failed: {cleanup_error}") from cleanup_error
    if result is None:
        raise RuntimeError("AI-Bridge fallback tools/list produced no result")
    return result


def _main_channel(status: dict[str, Any], label: str) -> dict[str, Any]:
    channels = status.get("channels")
    if isinstance(channels, dict):
        main = channels.get("main")
    elif isinstance(channels, list):
        matches = [
            item
            for item in channels
            if isinstance(item, dict) and item.get("channel", item.get("name")) == "main"
        ]
        main = matches[0] if len(matches) == 1 else None
    else:
        main = None
    if not isinstance(main, dict) or main.get("enabled") is not True or main.get("probe_status") != "ok":
        raise RuntimeError(f"{label} main channel is not enabled with probe_status=ok")
    return main


def _production_tunnel_main_channel(status: dict[str, Any]) -> dict[str, Any]:
    channels = status.get("channels")
    if isinstance(channels, dict):
        main = channels.get("main")
    elif isinstance(channels, list):
        matches = [
            item
            for item in channels
            if isinstance(item, dict) and item.get("channel", item.get("name")) == "main"
        ]
        main = matches[0] if len(matches) == 1 else None
    else:
        main = None
    if not isinstance(main, dict):
        raise RuntimeError("production tunnel status has no unique main channel")
    enabled = main.get("enabled")
    probe_status = main.get("probe_status")
    if enabled is True and probe_status == "ok":
        return main
    if enabled is True and probe_status in {"pending", "probing", "starting"}:
        raise _TransientTunnelReadinessError(
            f"production tunnel status main channel is still {probe_status}"
        )
    if enabled is False:
        raise RuntimeError("production tunnel status main channel is disabled")
    if probe_status == "failed":
        raise RuntimeError("production tunnel status main channel probe failed")
    raise RuntimeError("production tunnel status main channel readiness is unknown")


def _event_timestamp(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return float(text)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _read_tunnel_startup_event_chain(
    log_path: Path,
    *,
    expected_tunnel_id: str,
    started_at: float,
    verification_time: float,
) -> list[dict[str, Any]]:
    try:
        log_path.lstat()
    except FileNotFoundError as error:
        raise _TransientTunnelStartupEventError(
            "production tunnel log has not been created yet"
        ) from error
    _validate_private_source(log_path, "production tunnel log")
    expected_events = (
        ("mcp session initialized", "mcpclient"),
        ("tunnel metadata fetched", "controlplane"),
        ("🟢 tunnel-client started", None),
    )
    event_names = tuple(item[0] for item in expected_events)
    operational_events: list[dict[str, Any]] = []
    client_instance_id: str | None = None
    tunnel_name: str | None = None
    last_event_at: float | None = None
    for raw in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        observed_at = _event_timestamp(
            event.get("timestamp", event.get("time", event.get("ts")))
        )
        if observed_at is not None and not math.isfinite(observed_at):
            raise RuntimeError("production tunnel structured event timestamp is not finite")
        if observed_at is None or observed_at < started_at:
            continue
        if observed_at > verification_time + TUNNEL_HEALTH_FUTURE_TOLERANCE_SECONDS:
            raise RuntimeError("production tunnel structured event timestamp is in the future")
        fields = event.get("fields")
        nested = fields if isinstance(fields, dict) else {}

        def event_value(key: str) -> Any:
            return event[key] if key in event else nested.get(key)

        event_name = event_value("msg")
        if event_name is None:
            event_name = event_value("message")
        if event_name is None:
            event_name = event_value("event")
        if not isinstance(event_name, str) or event_name not in event_names:
            continue
        stage = event_names.index(event_name)
        is_alternate_chain = (
            stage == len(expected_events) - 1 and len(operational_events) == 1
        )
        if stage == 0:
            operational_events = []
            client_instance_id = None
            tunnel_name = None
            last_event_at = None
        elif is_alternate_chain:
            # v0.0.14 permits this explicit init -> started alternate chain.
            pass
        elif stage != len(operational_events):
            raise RuntimeError("production tunnel structured event chain is out of order")

        if event_value("tunnel_id") != expected_tunnel_id:
            raise RuntimeError("production tunnel structured event tunnel_id mismatch")
        if event_value("component") != expected_events[stage][1]:
            raise RuntimeError("production tunnel structured event component mismatch")
        observed_instance = event_value("client_instance_id")
        if not isinstance(observed_instance, str) or not observed_instance.strip():
            raise RuntimeError("production tunnel structured event client_instance_id is missing")
        if client_instance_id is None:
            client_instance_id = observed_instance
        elif observed_instance != client_instance_id:
            raise RuntimeError("production tunnel structured event client_instance_id mismatch")
        if last_event_at is not None and observed_at < last_event_at:
            raise RuntimeError("production tunnel structured event timestamps are out of order")
        last_event_at = observed_at
        if stage == 0:
            if event_value("server_name") != "catdesk":
                raise RuntimeError("production tunnel MCP init event server_name mismatch")
            protocol_version = event_value("protocol_version")
            if not isinstance(protocol_version, str) or not protocol_version.strip():
                raise RuntimeError("production tunnel MCP init event protocol_version is missing")
        elif stage == 1:
            observed_name = event_value("name")
            if not isinstance(observed_name, str) or not observed_name.strip():
                raise RuntimeError("production tunnel metadata event name is missing")
            tunnel_name = observed_name
        else:
            observed_name = event_value("name")
            if not isinstance(observed_name, str) or not observed_name.strip():
                raise RuntimeError("production tunnel started event name is missing")
            if tunnel_name is not None and observed_name != tunnel_name:
                raise RuntimeError("production tunnel started event name mismatch")
            observed_version = event_value("version")
            if not isinstance(observed_version, str) or not observed_version.strip():
                raise RuntimeError("production tunnel started event version is missing")
            if is_alternate_chain and re.fullmatch(
                r"0\.0\.14(?:\+[0-9A-Za-z][0-9A-Za-z.-]*)?", observed_version
            ) is None:
                raise RuntimeError(
                    "production tunnel alternate startup chain requires version 0.0.14"
                )
        operational_events.append(event)
        if stage == len(expected_events) - 1:
            return operational_events
    raise _TransientTunnelStartupEventError(
        "production tunnel log has no fresh structured operational event chain"
    )


def _verify_tunnel_operational(
    layout: dict[str, Path],
    *,
    expected_tunnel_id: str,
    started_at: float,
    retry_transient_metrics: bool = False,
    expected_process_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if (
        isinstance(started_at, bool)
        or not isinstance(started_at, (int, float))
        or not math.isfinite(float(started_at))
    ):
        raise RuntimeError("production tunnel started_at must be finite")
    deadline = (
        time.monotonic() + TUNNEL_POLL_STARTUP_TIMEOUT_SECONDS
        if retry_transient_metrics
        else None
    )
    health_url: str | None = None
    operational_events: list[dict[str, Any]] | None = None

    def wait_for_transient(error: _TransientTunnelReadinessError, label: str) -> None:
        if not retry_transient_metrics:
            raise error
        if deadline is None or time.monotonic() >= deadline:
            raise RuntimeError(
                f"production tunnel {label} retry deadline expired: {error}"
            ) from error
        time.sleep(TUNNEL_POLL_RETRY_INTERVAL_SECONDS)

    def read_health_url() -> tuple[str, urllib.parse.SplitResult]:
        try:
            layout["tunnel_health_url"].lstat()
        except FileNotFoundError as error:
            if health_url is not None:
                raise RuntimeError("production tunnel health URL disappeared after pinning") from error
            raise _TransientTunnelReadinessError(
                "production tunnel health URL has not been created yet"
            ) from error
        return _read_loopback_url_file(
            layout["tunnel_health_url"], "production tunnel health URL"
        )

    while True:
        if expected_process_identity is not None and not _process_identity_matches(
            expected_process_identity
        ):
            raise RuntimeError("production tunnel process identity drifted during startup")
        try:
            observed_health_url, parsed = read_health_url()
        except _TransientTunnelReadinessError as error:
            wait_for_transient(error, "health URL readiness")
            continue
        if health_url is None:
            health_url = observed_health_url
        elif observed_health_url != health_url:
            raise RuntimeError("production tunnel health URL drifted after pinning")
        status_url = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, "/api/status", "", "")
        )
        status_request = urllib.request.Request(
            status_url, method="GET", headers={"Accept": "application/json"}
        )
        status_raw, _ = _request_tunnel_health_bytes(
            status_request, "production tunnel status"
        )
        status = _decode_json_or_sse(status_raw, "production tunnel status")
        if status.get("control_plane_tunnel_id") != expected_tunnel_id:
            raise RuntimeError("production tunnel status control_plane_tunnel_id mismatch")
        try:
            _production_tunnel_main_channel(status)
            if operational_events is None:
                operational_events = _read_tunnel_startup_event_chain(
                    layout["tunnel_log"],
                    expected_tunnel_id=expected_tunnel_id,
                    started_at=float(started_at),
                    verification_time=time.time(),
                )
        except _TransientTunnelReadinessError as error:
            wait_for_transient(error, "startup readiness")
            continue
        metrics_url = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, "/metrics", "", "")
        )
        metrics_request = urllib.request.Request(
            metrics_url, method="GET", headers={"Accept": "text/plain"}
        )
        metrics_raw, metrics_content_type = _request_tunnel_health_bytes(
            metrics_request, "production tunnel metrics"
        )
        _validate_prometheus_content_type(metrics_content_type)
        try:
            poll_metrics = _parse_controlplane_poll_metrics(
                metrics_raw, started_at=started_at, now=time.time()
            )
        except _TransientTunnelMetricsError as error:
            wait_for_transient(error, "controlplane poll readiness")
            continue
        if expected_process_identity is not None and not _process_identity_matches(
            expected_process_identity
        ):
            raise RuntimeError("production tunnel process identity drifted before readiness")
        final_health_url, _ = read_health_url()
        if final_health_url != health_url:
            raise RuntimeError("production tunnel health URL drifted before readiness")
        break
    if health_url is None:
        raise RuntimeError("production tunnel health URL was not pinned")
    return {
        "ok": True,
        "health_url": health_url,
        "control_plane_tunnel_id": expected_tunnel_id,
        "startup_operational_events": operational_events,
        "controlplane_poll_metrics": poll_metrics,
    }


def _verify_ai_bridge_fallback(health_url_file: Path) -> dict[str, Any]:
    health_url, parsed_health = _read_loopback_url_file(
        health_url_file, "AI-Bridge fallback health URL"
    )
    status_url = urllib.parse.urlunsplit(
        (parsed_health.scheme, parsed_health.netloc, "/api/status", "", "")
    )
    status = _request_json(
        urllib.request.Request(status_url, method="GET", headers={"Accept": "application/json"}),
        "AI-Bridge fallback status",
    )
    main = _main_channel(status, "AI-Bridge fallback status")
    compute_candidates: list[str] = []
    for key in ("address", "url", "target_url", "mcp_server_url"):
        value = main.get(key)
        if isinstance(value, str) and value.strip():
            compute_candidates.append(value.strip())
    top_level_mcp_server_url = status.get("mcp_server_url")
    if isinstance(top_level_mcp_server_url, str) and top_level_mcp_server_url.strip():
        compute_candidates.append(top_level_mcp_server_url.strip())
    details = main.get("details")
    if isinstance(details, list):
        for detail in details:
            if not isinstance(detail, dict):
                continue
            key = (
                str(detail.get("key") or "")
                .strip()
                .lower()
                .replace("-", "_")
                .replace(".", "_")
            )
            value = detail.get("value")
            if key in {"address", "url", "target_url", "mcp_server_url", "server_url"} and isinstance(value, str) and value.strip():
                compute_candidates.append(value.strip())
    route_authorities: list[tuple[str, int]] = []
    routes = main.get("mcp_routes", status.get("mcp_routes"))
    if isinstance(routes, dict):
        routes = list(routes.values())
    if isinstance(routes, list):
        for route in routes:
            if not isinstance(route, dict):
                continue
            if route.get("channel", route.get("name", "main")) != "main":
                continue
            for key in ("target", "target_url", "address", "url", "mcp_server_url"):
                value = route.get(key)
                if isinstance(value, str) and value.strip():
                    route_value = value.strip()
                    parsed_route = urllib.parse.urlsplit(route_value)
                    if parsed_route.scheme:
                        compute_candidates.append(route_value)
                        continue
                    parsed_authority = urllib.parse.urlsplit(f"//{route_value}")
                    try:
                        route_address = ipaddress.ip_address(parsed_authority.hostname or "")
                        route_port = parsed_authority.port
                    except ValueError as error:
                        raise RuntimeError("AI-Bridge fallback route target is not a loopback authority") from error
                    if (
                        not route_address.is_loopback
                        or route_port is None
                        or parsed_authority.username
                        or parsed_authority.password
                        or parsed_authority.path
                        or parsed_authority.query
                        or parsed_authority.fragment
                    ):
                        raise RuntimeError("AI-Bridge fallback route target is not a loopback authority")
                    route_authorities.append((str(route_address), route_port))
    normalized_compute_urls: dict[str, str] = {}
    for candidate in compute_candidates:
        parsed_candidate = urllib.parse.urlsplit(candidate)
        try:
            candidate_address = ipaddress.ip_address(parsed_candidate.hostname or "")
            candidate_port = parsed_candidate.port
        except ValueError as error:
            raise RuntimeError("AI-Bridge fallback compute endpoint is not loopback") from error
        if (
            parsed_candidate.scheme != "http"
            or not candidate_address.is_loopback
            or candidate_port is None
            or parsed_candidate.username
            or parsed_candidate.password
            or parsed_candidate.query
            or parsed_candidate.fragment
        ):
            raise RuntimeError("AI-Bridge fallback compute endpoint is not loopback")
        host = f"[{candidate_address}]" if candidate_address.version == 6 else str(candidate_address)
        normalized = urllib.parse.urlunsplit(
            ("http", f"{host}:{candidate_port}", parsed_candidate.path or "/", "", "")
        )
        normalized_compute_urls[normalized] = normalized
    unique_compute_urls = set(normalized_compute_urls)
    if len(unique_compute_urls) != 1:
        raise RuntimeError("AI-Bridge fallback status has no main compute address")
    compute_url = unique_compute_urls.pop()
    parsed_compute = urllib.parse.urlsplit(compute_url)
    try:
        compute_address = ipaddress.ip_address(parsed_compute.hostname or "")
        compute_port = parsed_compute.port
    except ValueError as error:
        raise RuntimeError("AI-Bridge fallback compute endpoint is not loopback") from error
    if (
        parsed_compute.scheme != "http"
        or not compute_address.is_loopback
        or compute_port is None
        or parsed_compute.username
        or parsed_compute.password
        or parsed_compute.query
        or parsed_compute.fragment
    ):
        raise RuntimeError("AI-Bridge fallback compute endpoint is not loopback")
    compute_host = str(compute_address)
    if any(authority != (compute_host, compute_port) for authority in route_authorities):
        raise RuntimeError("AI-Bridge fallback route target does not match its compute endpoint")
    if not _tcp_ready(compute_host, compute_port):
        raise RuntimeError("AI-Bridge fallback compute endpoint is not listening")
    health_address = ipaddress.ip_address(parsed_health.hostname or "")
    health_port = parsed_health.port
    if not health_address.is_loopback or health_port is None:
        raise RuntimeError("AI-Bridge fallback health endpoint is not loopback")
    process_identities = _ai_bridge_fallback_process_identities(
        health_host=str(health_address),
        health_port=health_port,
        compute_host=compute_host,
        compute_port=compute_port,
    )
    tools_status = _mcp_streamable_tools_list(compute_url)
    tools = sorted(
        item.get("name")
        for item in ((tools_status.get("result") or {}).get("tools") or [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    )
    missing = sorted({"computer", "shell_command"}.difference(tools))
    if missing:
        raise RuntimeError("AI-Bridge fallback tools/list is missing: " + ", ".join(missing))
    stable_identity = _stable_ai_bridge_fallback_identity(
        status, main, compute_url, process_identities
    )
    return {
        "ready": True,
        "ai_bridge_is_trusted_receipt_source": False,
        "health_url": health_url,
        "compute_url": compute_url,
        "compute_host": compute_host,
        "compute_port": compute_port,
        "tools": tools,
        "status": status,
        "stable_identity": stable_identity,
        "status_sha256": hashlib.sha256(
            json.dumps(stable_identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }


def _stable_ai_bridge_fallback_identity(
    status: dict[str, Any], main: dict[str, Any], compute_url: str,
    process_identities: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(status, dict) or not isinstance(main, dict):
        raise RuntimeError("AI-Bridge fallback stable identity requires status and main channel objects")
    required_processes = {"ai_bridge", "tunnel", "compute_helper"}
    if set(process_identities) != required_processes:
        raise RuntimeError("AI-Bridge fallback process identity set is incomplete")
    for identity in process_identities.values():
        _validate_process_identity_record(identity)

    def stable_fields(value: object, keys: tuple[str, ...]) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        def stable_scalar(candidate: object) -> bool:
            return (
                candidate is None
                or isinstance(candidate, (str, int, bool))
                or (isinstance(candidate, float) and math.isfinite(candidate))
            )
        return {
            key: value[key]
            for key in keys
            if key in value and stable_scalar(value.get(key))
        }

    routes = status.get("mcp_routes")
    if isinstance(routes, dict):
        routes = list(routes.values())
    stable_routes = []
    if isinstance(routes, list):
        for route in routes:
            if isinstance(route, dict) and route.get("channel", route.get("name", "main")) == "main":
                stable_routes.append(stable_fields(
                    route, ("channel", "name", "target", "target_url", "address", "url", "mcp_server_url", "started_at")
                ))
    control_plane = status.get("control_plane")
    return {
        "client_instance_id": status.get("client_instance_id", status.get("instance_id")),
        "version": status.get("version", status.get("build_version")),
        "started_at": status.get("started_at"),
        "service": stable_fields(
            status,
            (
                "control_plane_tunnel_id",
                "control_plane_base_url",
                "control_plane_route",
                "mcp_server_url",
                "health_listen_addr",
            ),
        ),
        "control_plane": stable_fields(
            control_plane, ("tunnel_id", "id", "base_url", "url", "endpoint", "started_at")
        ),
        "main": stable_fields(
            main, ("name", "enabled", "probe_status", "server_kind", "transport_kind", "address", "url", "target_url", "mcp_server_url", "started_at")
        ),
        "routes": sorted(stable_routes, key=lambda item: json.dumps(item, sort_keys=True)),
        "compute_url": compute_url,
        "ai_bridge_process_identity": process_identities["ai_bridge"],
        "tunnel_process_identity": process_identities["tunnel"],
        "compute_helper_process_identity": process_identities["compute_helper"],
    }


def _ai_bridge_fallback_process_identities(
    *, health_host: str, health_port: int, compute_host: str, compute_port: int
) -> dict[str, dict[str, Any]]:
    ai_bridge = _unique_process_identity_for_executable(AI_BRIDGE_EXECUTABLE, "AI-Bridge")
    health_listener = _unique_loopback_listener_identity(health_host, health_port, "AI-Bridge health")
    tunnel = _unique_tunnel_child_identity(ai_bridge)
    if health_listener != tunnel:
        raise RuntimeError("AI-Bridge health listener identity does not match its tunnel child")
    compute_listener = _unique_loopback_listener_identity(
        compute_host, compute_port, "AI-Bridge compute route"
    )
    if compute_listener != ai_bridge:
        raise RuntimeError("AI-Bridge compute listener identity does not match AI-Bridge executable")
    compute_helper = _unique_process_identity_for_executable(
        COMPUTE_HELPER_EXECUTABLE, "Compute Helper"
    )
    _verify_process_owned_unix_socket(
        compute_helper, COMPUTE_HELPER_SOCKET, "Compute Helper"
    )
    return {"ai_bridge": ai_bridge, "tunnel": tunnel, "compute_helper": compute_helper}


def _validate_tunnel_profile(
    profile_path: Path,
    *,
    profile_name: str,
    expected_socket: Path,
) -> dict[str, Any]:
    _validate_private_source(profile_path, "tunnel profile")
    try:
        value = json.loads(profile_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise RuntimeError("tunnel profile is not valid JSON") from error
    control = value.get("control_plane")
    mcp = value.get("mcp")
    if not isinstance(control, dict) or not isinstance(mcp, dict):
        raise RuntimeError("tunnel profile is missing control_plane or mcp")
    tunnel_id = control.get("tunnel_id")
    if not isinstance(tunnel_id, str) or not tunnel_id.strip():
        raise RuntimeError("tunnel profile tunnel_id is missing")
    if control.get("base_url") != "https://api.openai.com":
        raise RuntimeError("tunnel profile control plane is not canonical")
    urls = mcp.get("server_urls")
    if not isinstance(urls, list):
        raise RuntimeError("tunnel profile mcp.server_urls is missing")
    sockets = {
        str(item.get("unix_socket"))
        for item in urls
        if isinstance(item, dict) and item.get("unix_socket")
    }
    if sockets != {str(expected_socket)}:
        raise RuntimeError(f"{profile_name} tunnel profile is not bound exactly to its expected Bridge socket")
    return {
        "profile_dir": str(profile_path.parent),
        "profile_path": str(profile_path),
        "profile_name": profile_name,
        "tunnel_id": tunnel_id.strip(),
        "profile_sha256": _sha256_file(profile_path),
    }


def _tunnel_profile_path(profile_dir: Path, profile_name: str) -> Path:
    return Path(profile_dir).expanduser() / f"{profile_name}.yaml"


def _parse_verified_tunnel_process(
    pid: int,
    command: str,
    *,
    profile_dir: Path,
    profile_name: str,
    expected_socket: Path,
) -> dict[str, Any] | None:
    parsed = parse_tunnel_process(command)
    if parsed is None:
        return None
    expected_dir = Path(profile_dir).expanduser().resolve()
    if parsed["profile_dir"].resolve() != expected_dir or parsed["profile_name"] != profile_name:
        return None
    # The complete command must be only the tunnel-client invocation for this exact profile.
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    if argv != [str(Path(argv[0])), "run", "--profile-dir", str(expected_dir), "--profile", profile_name]:
        return None
    try:
        identity = _capture_process_identity(pid, expected_argv=argv)
    except RuntimeError:
        return None
    try:
        profile = _validate_tunnel_profile(
            _tunnel_profile_path(expected_dir, profile_name),
            profile_name=profile_name,
            expected_socket=expected_socket,
        )
    except RuntimeError:
        return None
    return {
        "pid": pid,
        "command": command,
        "process_identity": identity,
        "expected_socket": str(expected_socket),
        **profile,
    }


def _discover_tunnel_processes(
    *,
    profile_dir: Path,
    profile_name: str,
    expected_socket: Path,
) -> list[dict[str, Any]]:
    completed = subprocess.run(
        ["ps", "-ww", "-axo", "pid=,comm="], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        return []
    found: list[dict[str, Any]] = []
    for raw in completed.stdout.splitlines():
        pieces = raw.strip().split(None, 1)
        if len(pieces) != 2 or Path(pieces[1]).name != "tunnel-client":
            continue
        try:
            pid = int(pieces[0])
        except ValueError:
            continue
        if not _pid_alive(pid):
            continue
        try:
            argv = _process_argv(pid)
        except RuntimeError:
            continue
        command = shlex.join(argv)
        parsed = _parse_verified_tunnel_process(
            pid,
            command,
            profile_dir=profile_dir,
            profile_name=profile_name,
            expected_socket=expected_socket,
        )
        if parsed is not None:
            found.append(parsed)
    return found


_DIRECT_TUNNEL_VALUE_FLAGS = frozenset(
    {
        "--control-plane.tunnel-id",
        "--control-plane.base-url",
        "--control-plane.api-key",
        "--mcp.server-url",
        "--cloudflared.path",
        "--health.listen-addr",
        "--health.url-file",
        "--health.unix-socket",
        "--log.level",
        "--log.file",
        "--log.format",
    }
)


def _direct_tunnel_options(argv: list[str]) -> dict[str, str] | None:
    """Parse only the two observed direct-flag schemas; ambiguity is fatal."""
    if len(argv) < 3 or Path(argv[0]).name != "tunnel-client" or argv[1] != "run":
        return None
    profile_flags = {"--config", "--profile", "--profile-dir", "--profile-file"}
    options: dict[str, str] = {}
    index = 2
    while index < len(argv):
        raw = argv[index]
        if not raw.startswith("--"):
            raise RuntimeError("cannot prove tunnel-id exclusivity for positional tunnel arguments")
        if "=" in raw:
            flag, value = raw.split("=", 1)
            index += 1
        else:
            flag = raw
            if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
                raise RuntimeError("cannot prove tunnel-id exclusivity for an empty tunnel flag")
            value = argv[index + 1]
            index += 2
        if flag in profile_flags:
            if options:
                raise RuntimeError("cannot prove tunnel-id exclusivity for mixed profile and direct flags")
            return None
        if flag not in _DIRECT_TUNNEL_VALUE_FLAGS:
            raise RuntimeError(f"cannot prove tunnel-id exclusivity for unsupported tunnel flag {flag}")
        if not value.strip() or flag in options:
            raise RuntimeError("cannot prove tunnel-id exclusivity for duplicate or empty tunnel flags")
        options[flag] = value.strip()
    if not options:
        raise RuntimeError("cannot prove tunnel-id exclusivity for an empty direct tunnel command")
    return options


def _direct_mcp_unix_socket(server_url: str) -> Path | None:
    if "=" not in server_url:
        parsed = urllib.parse.urlsplit(server_url)
        if parsed.scheme != "http":
            raise RuntimeError("cannot prove direct tunnel MCP endpoint")
        return None
    values: dict[str, str] = {}
    for raw in server_url.split(","):
        if "=" not in raw:
            raise RuntimeError("cannot prove direct tunnel MCP endpoint")
        key, value = raw.split("=", 1)
        key = key.strip().lower().replace("_", "-")
        value = value.strip()
        if key not in {"name", "url", "unix-socket"} or not value or key in values:
            raise RuntimeError("cannot prove direct tunnel MCP endpoint")
        values[key] = value
    if "unix-socket" not in values:
        url = values.get("url")
        if not url or urllib.parse.urlsplit(url).scheme != "http":
            raise RuntimeError("cannot prove direct tunnel MCP endpoint")
        return None
    raw_socket = Path(values["unix-socket"]).expanduser()
    if not raw_socket.is_absolute():
        raise RuntimeError("cannot prove direct tunnel MCP unix socket")
    return raw_socket.resolve()


def _direct_tunnel_id(options: dict[str, str]) -> str:
    explicit = options.get("--control-plane.tunnel-id")
    if explicit:
        return explicit
    raw_health_file = options.get("--health.url-file")
    if not raw_health_file:
        raise RuntimeError("cannot prove tunnel-id for a managed direct tunnel without health state")
    health_file = Path(raw_health_file).expanduser()
    if not health_file.is_absolute():
        raise RuntimeError("cannot prove tunnel-id from a relative health URL file")
    _, parsed = _read_loopback_url_file(health_file, "direct tunnel health URL")
    status_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/api/status", "", ""))
    status = _request_json(
        urllib.request.Request(status_url, method="GET", headers={"Accept": "application/json"}),
        "direct tunnel status",
    )
    observed = status.get("control_plane_tunnel_id")
    if not isinstance(observed, str) or not observed.strip():
        raise RuntimeError("cannot prove tunnel-id from direct tunnel status")
    return observed.strip()


def _discover_tunnels_by_id(
    tunnel_id: str, *, expected_socket: Path | None = None
) -> list[dict[str, Any]]:
    completed = subprocess.run(
        ["ps", "-ww", "-axo", "pid=,comm="], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise RuntimeError("cannot enumerate tunnel processes for duplicate tunnel-id fence")
    found: list[dict[str, Any]] = []
    for raw in completed.stdout.splitlines():
        pieces = raw.strip().split(None, 1)
        if len(pieces) != 2 or Path(pieces[1]).name != "tunnel-client":
            continue
        try:
            pid = int(pieces[0])
        except ValueError:
            continue
        try:
            argv = _process_argv(pid)
        except RuntimeError as error:
            raise RuntimeError("cannot prove tunnel-id exclusivity because a process argv is unavailable") from error
        command = shlex.join(argv)
        if len(argv) < 2 or Path(argv[0]).name != "tunnel-client" or argv[1] != "run":
            continue
        parsed = parse_tunnel_process(command)
        if parsed is None:
            options = _direct_tunnel_options(argv)
            if options is None:
                raise RuntimeError("cannot prove tunnel-id exclusivity for a non-canonical profile command")
            observed_id = _direct_tunnel_id(options)
            if observed_id != tunnel_id:
                continue
            server_url = options.get("--mcp.server-url")
            if server_url is None:
                raise RuntimeError("cannot prove direct tunnel ownership without one MCP endpoint")
            unix_socket = _direct_mcp_unix_socket(server_url)
            endpoint_matches = expected_socket is None or bool(
                unix_socket == Path(expected_socket).expanduser().resolve()
                and options.get("--control-plane.base-url") == CANONICAL_CONTROL_PLANE_BASE_URL
            )
            try:
                identity = _capture_process_identity(pid, expected_argv=argv)
            except RuntimeError as error:
                raise RuntimeError("cannot prove direct tunnel process identity") from error
            found.append(
                {
                    "pid": pid,
                    "command": command,
                    "tunnel_id": observed_id,
                    "process_identity": identity,
                    "direct": True,
                    "mcp_unix_socket": str(unix_socket) if unix_socket is not None else None,
                    "expected_endpoint_matches": endpoint_matches,
                }
            )
            continue
        expected_dir = parsed["profile_dir"].resolve()
        if argv != [str(Path(argv[0])), "run", "--profile-dir", str(expected_dir), "--profile", parsed["profile_name"]]:
            raise RuntimeError("cannot prove tunnel-id exclusivity for a non-canonical tunnel command")
        profile_path = _tunnel_profile_path(expected_dir, parsed["profile_name"])
        try:
            _validate_private_source(profile_path, "tunnel profile for duplicate tunnel-id fence")
            profile_value = json.loads(profile_path.read_text(encoding="utf-8"))
            observed_id = ((profile_value.get("control_plane") or {}).get("tunnel_id"))
            identity = _capture_process_identity(pid, expected_argv=argv)
        except (json.JSONDecodeError, RuntimeError) as error:
            raise RuntimeError("cannot prove tunnel-id exclusivity for an unverified tunnel process") from error
        if observed_id == tunnel_id:
            profile_socket = None
            urls = ((profile_value.get("mcp") or {}).get("server_urls") or [])
            sockets = {
                Path(str(item.get("unix_socket"))).expanduser().resolve()
                for item in urls
                if isinstance(item, dict) and item.get("unix_socket")
            }
            if len(sockets) == 1:
                profile_socket = next(iter(sockets))
            found.append({
                "pid": pid,
                "profile_dir": str(expected_dir),
                "profile_name": parsed["profile_name"],
                "profile_path": str(profile_path),
                "profile_sha256": _sha256_file(profile_path),
                "tunnel_id": observed_id,
                "process_identity": identity,
                "expected_endpoint_matches": expected_socket is None or profile_socket == Path(expected_socket).expanduser().resolve(),
            })
    return found


def _require_exact_tunnel_id_owners(
    tunnel_id: str, expected_pids: set[int], *, expected_socket: Path | None = None
) -> None:
    observed = _discover_tunnels_by_id(tunnel_id, expected_socket=expected_socket)
    observed_pids = {item.get("pid") for item in observed}
    if observed_pids != expected_pids:
        raise RuntimeError(
            "same tunnel-id ownership is not exclusive: "
            f"expected {sorted(expected_pids)}, observed {sorted(value for value in observed_pids if isinstance(value, int))}"
        )
    if expected_socket is not None and any(
        item.get("expected_endpoint_matches") is not True for item in observed
    ):
        raise RuntimeError("same tunnel-id owner does not match the exact expected MCP endpoint")


def production_tunnel_status(
    manifest: dict[str, Any],
    layout: dict[str, Path],
    *,
    retry_transient_metrics: bool = False,
) -> dict[str, Any]:
    _validate_manifest_contract(manifest)
    try:
        expected = _validate_tunnel_profile(
            layout["tunnel_profile"],
            profile_name=TUNNEL_PROFILE_NAME,
            expected_socket=layout["bridge_socket"],
        )
    except RuntimeError as error:
        return {"ready": False, "pid": None, "reason": str(error), "generation": None, "tunnel_id": None}
    discovered = _discover_tunnel_processes(
        profile_dir=layout["tunnel_dir"],
        profile_name=TUNNEL_PROFILE_NAME,
        expected_socket=layout["bridge_socket"],
    )
    if len(discovered) != 1:
        return {
            "ready": False,
            "pid": None,
            "reason": "multiple_active_tunnels" if len(discovered) > 1 else "tunnel_not_running",
            "generation": None,
            "tunnel_id": None,
            "active_pids": sorted(item["pid"] for item in discovered),
        }
    item = discovered[0]
    try:
        owners = _discover_tunnels_by_id(
            item["tunnel_id"], expected_socket=layout["bridge_socket"]
        )
    except RuntimeError as error:
        return {
            "ready": False,
            "pid": item["pid"],
            "reason": str(error),
            "generation": None,
            "tunnel_id": item["tunnel_id"],
        }
    if {owner.get("pid") for owner in owners} != {item["pid"]}:
        return {
            "ready": False,
            "pid": item["pid"],
            "reason": "same tunnel-id ownership is not globally exclusive",
            "generation": None,
            "tunnel_id": item["tunnel_id"],
        }
    state = _read_json_object(layout["runtime_state"])
    started_at = state.get("tunnel_started_at")
    recorded_identity = state.get("tunnel_process_identity")
    canonical = (
        state.get("release_revision") == manifest["release_revision"]
        and state.get("endpoint_generation") == manifest["endpoint_generation"]
        and state.get("tunnel_profile_name") == TUNNEL_PROFILE_NAME
        and state.get("tunnel_pid") == item["pid"]
        and state.get("tunnel_id") == item["tunnel_id"]
        and state.get("tunnel_profile_sha256") == expected["profile_sha256"]
        and isinstance(started_at, (int, float))
        and not isinstance(started_at, bool)
        and isinstance(recorded_identity, dict)
        and item.get("process_identity") == recorded_identity
    )
    operational: dict[str, Any] | None = None
    reason = None if canonical else "runtime_state_mismatch"
    if canonical:
        try:
            operational = _verify_tunnel_operational(
                layout,
                expected_tunnel_id=item["tunnel_id"],
                started_at=float(started_at),
                retry_transient_metrics=retry_transient_metrics,
                expected_process_identity=recorded_identity,
            )
        except RuntimeError as error:
            canonical = False
            reason = str(error)
    return {
        "ready": canonical,
        "pid": item["pid"],
        "reason": reason,
        "generation": manifest["endpoint_generation"] if canonical else None,
        "tunnel_id": item["tunnel_id"],
        "profile_dir": item["profile_dir"],
        "profile_sha256": item["profile_sha256"],
        "started_at": started_at,
        "operational": operational,
    }


def parse_tunnel_process(command: str) -> dict[str, Any] | None:
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    if len(argv) < 6 or Path(argv[0]).name != "tunnel-client" or argv[1] != "run":
        return None
    if argv.count("--profile-dir") != 1 or argv.count("--profile") != 1:
        return None
    profile_dir_index = argv.index("--profile-dir")
    profile_index = argv.index("--profile")
    if profile_dir_index + 1 >= len(argv) or profile_index + 1 >= len(argv):
        return None
    profile_dir = Path(argv[profile_dir_index + 1]).expanduser()
    profile_name = argv[profile_index + 1]
    if not profile_dir.is_absolute() or not profile_name:
        return None
    return {"profile_dir": profile_dir, "profile_name": profile_name}


def process_command_matches(
    command: str,
    executable: Path,
    required_arguments: Iterable[str] = (),
) -> bool:
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    if not argv or Path(argv[0]) != Path(executable):
        return False
    required = list(required_arguments)
    if not required:
        return True
    for index in range(0, len(argv) - len(required) + 1):
        if argv[index : index + len(required)] == required:
            return True
    return False


def find_verified_historical_m0_tunnel(
    historical_profile_dir: Path, *, profile_name: str
) -> dict[str, Any]:
    """Find exactly one live M0 tunnel bound to its live profile, never by pid file."""
    profile_dir = Path(historical_profile_dir).expanduser().resolve()
    if profile_name not in HISTORICAL_TUNNEL_PROFILE_NAMES:
        raise RuntimeError("refusing production activation: historical profile name is not explicitly allowed")
    found = _discover_tunnel_processes(
        profile_dir=profile_dir,
        profile_name=profile_name,
        expected_socket=HISTORICAL_M0_BRIDGE_SOCKET,
    )
    if len(found) != 1:
        reason = "multiple matching M0 tunnels" if len(found) > 1 else "no matching M0 tunnel"
        raise RuntimeError(f"refusing production activation: {reason} at {profile_dir}")
    return found[0]


def verified_historical_m0_bridge(runtime_state: Path) -> dict[str, Any]:
    """Preflight the old Bridge identity; activation never stops or rewrites it."""
    _validate_private_source(runtime_state, "historical M0 runtime state")
    state = _read_json_object(runtime_state)
    pid = state.get("bridge_pid")
    if not isinstance(pid, int) or pid < 1:
        raise RuntimeError("historical M0 runtime state has no valid bridge PID")
    if state.get("socket") != str(HISTORICAL_M0_BRIDGE_SOCKET):
        raise RuntimeError("historical M0 runtime state does not pin the expected old Bridge socket")
    try:
        info = HISTORICAL_M0_BRIDGE_SOCKET.lstat()
    except FileNotFoundError as error:
        raise RuntimeError("historical M0 Bridge socket is unavailable") from error
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid():
        raise RuntimeError("historical M0 Bridge socket is unsafe")
    command = _pid_command(pid)
    try:
        argv = shlex.split(command)
        if len(argv) != 1 or Path(argv[0]).name != "local-tool-bridge":
            raise RuntimeError("historical M0 Bridge command is not canonical")
        identity = _capture_process_identity(pid, expected_argv=argv)
    except (OSError, ValueError, RuntimeError) as error:
        raise RuntimeError("historical M0 Bridge PID or complete command is not verifiable")
    return {
        "pid": pid,
        "command": command,
        "socket": str(HISTORICAL_M0_BRIDGE_SOCKET),
        "process_identity": identity,
    }


def _stop_verified_tunnel(item: dict[str, Any]) -> None:
    pid = item.get("pid")
    if not isinstance(pid, int) or not _pid_alive(pid):
        raise RuntimeError("refusing to stop unavailable tunnel process")
    if not _process_identity_matches(item.get("process_identity", {})):
        raise RuntimeError("refusing to stop tunnel whose command changed after verification")
    command = shlex.join(item["process_identity"]["argv"])
    _parse_verified_tunnel_process(
        pid,
        command,
        profile_dir=Path(str(item["profile_dir"])),
        profile_name=str(item["profile_name"]),
        expected_socket=Path(str(item["expected_socket"])),
    ) if "expected_socket" in item else None
    # Always re-read the exact profile immediately before signaling.  Process
    # identity alone does not fence a concurrent profile replacement.
    expected_socket = Path(str(item.get("expected_socket") or (
        HISTORICAL_M0_BRIDGE_SOCKET
        if item.get("profile_name") in HISTORICAL_TUNNEL_PROFILE_NAMES
        else PRODUCTION_BRIDGE_SOCKET
    )))
    profile = _validate_tunnel_profile(
        Path(str(item["profile_path"])),
        profile_name=str(item["profile_name"]),
        expected_socket=expected_socket,
    )
    if profile["profile_sha256"] != item.get("profile_sha256"):
        raise RuntimeError("refusing to stop tunnel whose profile hash changed after verification")
    if profile["tunnel_id"] != item.get("tunnel_id"):
        raise RuntimeError("refusing to stop tunnel whose tunnel id changed after verification")
    _stop_verified_process_identity(item["process_identity"])


def _restart_verified_historical_tunnel(item: dict[str, Any]) -> dict[str, Any]:
    command = item.get("command")
    if not isinstance(command, str):
        raise RuntimeError("cannot roll back historical M0 tunnel without its verified command")
    argv = shlex.split(command)
    if len(argv) != 6:
        raise RuntimeError("cannot roll back historical M0 tunnel with a non-canonical command")
    profile = _validate_tunnel_profile(
        Path(str(item["profile_path"])),
        profile_name=str(item["profile_name"]),
        expected_socket=HISTORICAL_M0_BRIDGE_SOCKET,
    )
    if profile["profile_sha256"] != item.get("profile_sha256"):
        raise RuntimeError("cannot roll back historical M0 tunnel because its profile hash changed")
    if profile["tunnel_id"] != item.get("tunnel_id"):
        raise RuntimeError("cannot roll back historical M0 tunnel because its tunnel id changed")
    if _discover_tunnels_by_id(
        profile["tunnel_id"], expected_socket=HISTORICAL_M0_BRIDGE_SOCKET
    ):
        raise RuntimeError("cannot roll back historical M0 tunnel while the same tunnel id is already active")
    process = _spawn_tunnel(argv, Path(str(item["profile_dir"])), "historical-m0-tunnel")
    return _wait_for_tunnel(
        process,
        profile_dir=Path(str(item["profile_dir"])),
        profile_name=str(item["profile_name"]),
        expected_socket=HISTORICAL_M0_BRIDGE_SOCKET,
    )


def _find_tunnel_client(explicit: Path | None = None) -> Path:
    raw_explicit = explicit or os.environ.get("LAB_PRODUCTION_TUNNEL_CLIENT")
    if raw_explicit:
        candidate = Path(raw_explicit).expanduser()
        if candidate.is_file() and not candidate.is_symlink() and os.access(candidate, os.X_OK):
            return candidate.resolve(strict=True)
        raise RuntimeError("LAB_PRODUCTION_TUNNEL_CLIENT is not a regular file")
    candidates = [
        Path(value)
        for value in glob.glob(str(Path.home() / ".local" / "share" / "aibridge-tunnel" / "*" / "tunnel-client"))
    ]
    candidates = [path for path in candidates if path.is_file() and not path.is_symlink()]
    if not candidates:
        raise RuntimeError("OpenAI tunnel-client is unavailable; set LAB_PRODUCTION_TUNNEL_CLIENT")
    if len(candidates) != 1:
        raise RuntimeError("multiple tunnel-client candidates are ambiguous; pass an explicit --tunnel-client")
    return candidates[0].resolve(strict=True)


def _spawn_tunnel(command: list[str], profile_dir: Path, log_name: str) -> subprocess.Popen[Any]:
    log_dir = Path(profile_dir) / "logs"
    _private_dir(log_dir)
    return _spawn_logged(
        command,
        log_dir / f"{log_name}.out",
        log_dir / f"{log_name}.err",
        cwd=CANONICAL_PROJECT_ROOT,
        env={"HOME": str(Path.home()), "PATH": os.environ.get("PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin")},
    )


def _wait_for_tunnel(
    process: subprocess.Popen[Any],
    *,
    profile_dir: Path,
    profile_name: str,
    expected_socket: Path,
) -> dict[str, Any]:
    for _ in range(50):
        if process.poll() is not None:
            raise RuntimeError("tunnel-client exited during startup")
        found = _discover_tunnel_processes(
            profile_dir=profile_dir,
            profile_name=profile_name,
            expected_socket=expected_socket,
        )
        matching = [item for item in found if item["pid"] == process.pid]
        if len(found) == 1 and len(matching) == 1:
            return matching[0]
        time.sleep(0.1)
    raise RuntimeError("tunnel-client did not become uniquely observable with its exact profile")


def _snapshot_runtime_state(path: Path) -> tuple[bytes, int] | None:
    if not path.exists():
        return None
    _validate_private_source(path, "production runtime state")
    return path.read_bytes(), stat.S_IMODE(path.stat().st_mode)


def _restore_runtime_state(path: Path, snapshot: tuple[bytes, int] | None) -> None:
    if snapshot is None:
        if path.exists():
            _validate_private_source(path, "failed production runtime state")
            path.unlink()
        return
    try:
        value = snapshot[0].decode("utf-8")
    except UnicodeDecodeError as error:
        raise RuntimeError("production runtime state snapshot is not UTF-8 JSON text") from error
    _write_text_private(path, value, snapshot[1])


def _start_production_tunnel(
    manifest: dict[str, Any],
    layout: dict[str, Path],
    *,
    tunnel_client: Path | None = None,
) -> dict[str, Any]:
    _validate_tunnel_profile(
        layout["tunnel_profile"], profile_name=TUNNEL_PROFILE_NAME, expected_socket=layout["bridge_socket"]
    )
    resolved_client = _find_tunnel_client(tunnel_client)
    command = [
        str(resolved_client),
        "run",
        "--profile-dir",
        str(layout["tunnel_dir"]),
        "--profile",
        TUNNEL_PROFILE_NAME,
    ]
    state_snapshot = _snapshot_runtime_state(layout["runtime_state"])
    started_at = time.time()
    process = _spawn_tunnel(
        command,
        layout["tunnel_dir"],
        "production-tunnel",
    )
    identity: dict[str, Any] | None = None
    try:
        identity = _capture_process_identity(
            process.pid,
            expected_executable=resolved_client,
            expected_argv=command,
        )
        item = _wait_for_tunnel(
            process,
            profile_dir=layout["tunnel_dir"],
            profile_name=TUNNEL_PROFILE_NAME,
            expected_socket=layout["bridge_socket"],
        )
        if item.get("process_identity") != identity:
            raise RuntimeError("production tunnel identity changed during startup")
        _record_production_tunnel(manifest, layout, item, started_at=started_at)
        verified = production_tunnel_status(
            manifest, layout, retry_transient_metrics=True
        )
        if verified.get("ready") is not True or verified.get("pid") != item["pid"]:
            reason = verified.get("reason")
            if not isinstance(reason, str) or not reason:
                reason = "unknown_status_reason"
            raise RuntimeError(
                "production tunnel did not satisfy its post-start status fence: "
                f"reason={reason}; expected_pid={item['pid']}; "
                f"observed_pid={verified.get('pid')}"
            )
        return item
    except Exception as error:
        cleanup_errors: list[str] = []
        try:
            if identity is not None:
                _stop_verified_process_identity(identity, process=process)
            elif process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
        except Exception as stop_error:
            cleanup_errors.append(f"spawned production tunnel stop failed: {stop_error}")
        try:
            _restore_runtime_state(layout["runtime_state"], state_snapshot)
        except Exception as restore_error:
            cleanup_errors.append(f"production tunnel state restore failed: {restore_error}")
        if cleanup_errors:
            raise RuntimeError(f"{error}; {'; '.join(cleanup_errors)}") from error
        raise


def _record_production_tunnel(
    manifest: dict[str, Any],
    layout: dict[str, Path],
    item: dict[str, Any],
    *,
    started_at: float,
) -> None:
    state = _read_json_object(layout["runtime_state"])
    if state.get("release_revision") != manifest["release_revision"] or state.get("endpoint_generation") != manifest["endpoint_generation"]:
        raise RuntimeError("production local runtime state changed before tunnel activation")
    state.update({
        "tunnel_pid": item["pid"],
        "tunnel_id": item["tunnel_id"],
        "tunnel_profile_sha256": item["profile_sha256"],
        "tunnel_profile_name": TUNNEL_PROFILE_NAME,
        "tunnel_started_at": started_at,
        "tunnel_process_identity": item["process_identity"],
    })
    _write_json_private(layout["runtime_state"], state)


def _write_activation_rollback_receipt(
    manifest: dict[str, Any],
    layout: dict[str, Path],
    *,
    historical: dict[str, Any],
    production: dict[str, Any],
    historical_bridge: dict[str, Any],
    historical_runtime_state: Path,
) -> dict[str, Any]:
    historical_state_path = Path(historical_runtime_state).expanduser().resolve()
    _validate_private_source(historical_state_path, "historical M0 runtime state")
    receipt = {
        "schema_version": 1,
        "status": "armed",
        "connector_id": CONNECTOR_ID,
        "contract_version": CONTRACT_VERSION,
        "release_revision": manifest["release_revision"],
        "endpoint_generation": manifest["endpoint_generation"],
        "tunnel_id": production["tunnel_id"],
        "historical": historical,
        "production": production,
        "historical_bridge": historical_bridge,
        "historical_runtime_state": str(historical_state_path),
        "historical_runtime_state_sha256": _sha256_file(historical_state_path),
        "created_at": time.time(),
    }
    _write_json_private(layout["rollback_receipt"], receipt)
    receipt_sha256 = _sha256_file(layout["rollback_receipt"])
    state = _read_json_object(layout["runtime_state"])
    if (
        state.get("tunnel_pid") != production.get("pid")
        or state.get("tunnel_process_identity") != production.get("process_identity")
    ):
        raise RuntimeError("production runtime state changed before rollback receipt was armed")
    state.update(
        {
            "activation_rollback_receipt": str(layout["rollback_receipt"].resolve()),
            "activation_rollback_receipt_sha256": receipt_sha256,
        }
    )
    _write_json_private(layout["runtime_state"], state)
    return {"path": str(layout["rollback_receipt"].resolve()), "sha256": receipt_sha256}


def _decode_private_json_snapshot(snapshot: tuple[bytes, int] | None, label: str) -> dict[str, Any]:
    if snapshot is None:
        raise RuntimeError(f"{label} is missing")
    try:
        value = json.loads(snapshot[0].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{label} is not valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object")
    return value


def _completed_receipt_historical_baseline(
    receipt: dict[str, Any], manifest: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], Path, str]:
    if any(
        receipt.get(key) != expected
        for key, expected in (
            ("schema_version", 1),
            ("status", "rolled_back"),
            ("phase", "completed"),
            ("connector_id", CONNECTOR_ID),
            ("contract_version", CONTRACT_VERSION),
        )
    ):
        raise RuntimeError("canonical prior rollback receipt is not completed production rollback evidence")
    prior_revision = _release_revision(str(receipt.get("release_revision") or ""))
    prior_generation = _endpoint_generation(receipt.get("endpoint_generation"))
    current_revision = _release_revision(str(manifest.get("release_revision") or ""))
    current_generation = _endpoint_generation(manifest.get("endpoint_generation"))
    if prior_revision == current_revision:
        raise RuntimeError("canonical prior rollback receipt must identify an older production release")
    if prior_generation != current_generation - 1:
        raise RuntimeError("canonical prior rollback receipt generation is not the direct predecessor")
    historical = receipt.get("historical_restarted")
    historical_bridge = receipt.get("historical_bridge")
    if not isinstance(historical, dict) or not isinstance(historical_bridge, dict):
        raise RuntimeError("canonical prior rollback receipt lacks the historical M0 baseline")
    tunnel_id = receipt.get("tunnel_id")
    if historical.get("tunnel_id") != tunnel_id:
        raise RuntimeError("canonical prior rollback receipt historical tunnel id mismatch")
    _validate_receipt_tunnel_profile(
        historical,
        expected_socket=HISTORICAL_M0_BRIDGE_SOCKET,
        label="completed rollback historical tunnel",
    )
    historical_identity = historical.get("process_identity")
    _validate_process_identity_record(historical_identity)
    if _process_identity_matches(historical_identity):
        raise RuntimeError("historical M0 tunnel is still running; current production cannot be adopted")
    running_historical = _discover_tunnel_processes(
        profile_dir=Path(str(historical["profile_dir"])),
        profile_name=str(historical["profile_name"]),
        expected_socket=HISTORICAL_M0_BRIDGE_SOCKET,
    )
    if running_historical:
        raise RuntimeError("historical M0 tunnel is currently running; current production cannot be adopted")

    bridge_identity = historical_bridge.get("process_identity")
    _validate_process_identity_record(bridge_identity)
    try:
        bridge_argv = shlex.split(str(historical_bridge.get("command") or ""))
    except ValueError as error:
        raise RuntimeError("canonical prior rollback receipt historical Bridge command is malformed") from error
    if (
        historical_bridge.get("pid") != bridge_identity.get("pid")
        or bridge_identity.get("argv") != bridge_argv
        or len(bridge_argv) != 1
        or Path(bridge_argv[0]).name != "local-tool-bridge"
        or historical_bridge.get("socket") != str(HISTORICAL_M0_BRIDGE_SOCKET)
        or not _process_identity_matches(bridge_identity)
    ):
        raise RuntimeError("historical M0 Bridge identity is no longer live and exact")
    _verify_process_owned_unix_socket(
        bridge_identity, HISTORICAL_M0_BRIDGE_SOCKET, "historical M0 Bridge"
    )

    raw_historical_state = receipt.get("historical_runtime_state")
    if not isinstance(raw_historical_state, str) or not Path(raw_historical_state).is_absolute():
        raise RuntimeError("canonical prior rollback receipt historical runtime state path is invalid")
    historical_state_path = Path(raw_historical_state).expanduser().resolve()
    historical_state_snapshot = _private_file_snapshot(
        historical_state_path, "historical M0 runtime state"
    )
    historical_state = _decode_private_json_snapshot(
        historical_state_snapshot, "historical M0 runtime state"
    )
    if any(
        historical_state.get(state_key) != historical.get(receipt_key)
        for state_key, receipt_key in (
            ("tunnel_pid", "pid"),
            ("tunnel_id", "tunnel_id"),
            ("tunnel_profile_name", "profile_name"),
            ("tunnel_profile_sha256", "profile_sha256"),
            ("tunnel_process_identity", "process_identity"),
        )
    ):
        raise RuntimeError("historical M0 runtime state does not match the completed rollback receipt")
    historical_state_sha256 = hashlib.sha256(historical_state_snapshot[0]).hexdigest()
    return historical, historical_bridge, historical_state_path, historical_state_sha256


def _observe_current_production_for_rollback_adoption(
    manifest: dict[str, Any],
    layout: dict[str, Path],
    *,
    home_dir: Path,
    native_user_data_dirs: tuple[Path, ...],
    fallback_health_url_file: Path,
    expected_receipt_pin: tuple[str, str] | None = None,
) -> dict[str, Any]:
    installed = validate_installed_release(layout)
    installed_host = installed.get("host")
    requested_host = manifest.get("host")
    if not isinstance(installed_host, dict) or not isinstance(requested_host, dict):
        raise RuntimeError("installed production release does not match the exact requested manifest")
    installed_minimum_extension_version = installed_host.get(
        "minimum_extension_version"
    )
    if not isinstance(installed_minimum_extension_version, str):
        raise RuntimeError(
            "installed production release Host minimum extension version is invalid"
        )
    _version_tuple(installed_minimum_extension_version)
    expected_installed_host = {
        **requested_host,
        "minimum_extension_version": installed_minimum_extension_version,
    }
    if installed_host != expected_installed_host or any(
        installed.get(key) != value
        for key, value in manifest.items()
        if key != "host"
    ):
        raise RuntimeError("installed production release does not match the exact requested manifest")
    if not isinstance(installed.get("artifacts_sha256"), dict) or not installed["artifacts_sha256"]:
        raise RuntimeError("installed production release has no verified artifact pins")

    status = production_status(installed, layout)
    required_ready = (
        "release_installed",
        "runtime_state_matches",
        "runtime_verifier_config_ready",
        "backend_ready",
        "host_ready",
        "runtime_hook_ready",
        "bridge_ready",
        "local_runtime_ready",
        "manifest_has_artifacts",
        "tunnel_ready",
    )
    if any(status.get(key) is not True for key in required_ready):
        raise RuntimeError("current production status is not completely ready")
    if any(
        status.get(key) != expected
        for key, expected in (
            ("connector_id", CONNECTOR_ID),
            ("contract_version", CONTRACT_VERSION),
            ("release_revision", manifest["release_revision"]),
            ("endpoint_generation", manifest["endpoint_generation"]),
            ("workspace", manifest["workspace"]),
            ("tunnel_profile_name", TUNNEL_PROFILE_NAME),
        )
    ):
        raise RuntimeError("current production status identity drifted")

    verify_caller_conversation_id_rejected(layout)
    validate_native_messaging_manifests(
        layout,
        installed,
        home_dir=home_dir,
        native_user_data_dirs=native_user_data_dirs,
    )
    state_snapshot = _private_file_snapshot(
        layout["runtime_state"], "production runtime state for rollback adoption"
    )
    state = _decode_private_json_snapshot(
        state_snapshot, "production runtime state for rollback adoption"
    )
    observed_receipt_pin = (
        state.get("activation_rollback_receipt"),
        state.get("activation_rollback_receipt_sha256"),
    )
    if expected_receipt_pin is None:
        if observed_receipt_pin != (None, None):
            raise RuntimeError("current production already has a rollback receipt pin")
    elif observed_receipt_pin != expected_receipt_pin:
        raise RuntimeError("current production rollback receipt pin drifted after adoption")

    profile = _validate_tunnel_profile(
        layout["tunnel_profile"],
        profile_name=TUNNEL_PROFILE_NAME,
        expected_socket=layout["bridge_socket"],
    )
    discovered = _discover_tunnel_processes(
        profile_dir=layout["tunnel_dir"],
        profile_name=TUNNEL_PROFILE_NAME,
        expected_socket=layout["bridge_socket"],
    )
    if len(discovered) != 1:
        raise RuntimeError("current production tunnel is not the unique exact profile owner")
    production = discovered[0]
    _validate_receipt_tunnel_profile(
        production,
        expected_socket=layout["bridge_socket"],
        label="observed current production tunnel",
    )
    production_identity = production.get("process_identity")
    if not _process_identity_matches(production_identity):
        raise RuntimeError("current production tunnel process identity is no longer live")
    if any(
        state.get(state_key) != production.get(receipt_key)
        for state_key, receipt_key in (
            ("tunnel_pid", "pid"),
            ("tunnel_id", "tunnel_id"),
            ("tunnel_profile_sha256", "profile_sha256"),
            ("tunnel_process_identity", "process_identity"),
        )
    ) or profile.get("tunnel_id") != production.get("tunnel_id") or profile.get(
        "profile_sha256"
    ) != production.get("profile_sha256"):
        raise RuntimeError("current production tunnel identity or profile drifted from runtime state")
    if status.get("tunnel_pid") != production.get("pid") or status.get(
        "tunnel_id"
    ) != production.get("tunnel_id"):
        raise RuntimeError("current production tunnel identity drifted from ready status")
    _require_exact_tunnel_id_owners(
        str(production["tunnel_id"]),
        {int(production["pid"])},
        expected_socket=layout["bridge_socket"],
    )

    component_identities: dict[str, dict[str, Any]] = {}
    for component in ("catdesk_supervisor", "host", "runtime_hook", "bridge"):
        pid = state.get(f"{component}_pid")
        identity = state.get(f"{component}_process_identity")
        _validate_process_identity_record(identity)
        if not isinstance(pid, int) or identity.get("pid") != pid or not _process_identity_matches(identity):
            raise RuntimeError(f"current production {component} process identity drifted")
        component_identities[component] = identity
    _verify_process_owned_unix_socket(
        component_identities["bridge"], layout["bridge_socket"], "current production Bridge"
    )

    fallback = _verify_ai_bridge_fallback(fallback_health_url_file)
    fallback_identity = {
        key: fallback.get(key)
        for key in (
            "health_url",
            "compute_url",
            "compute_host",
            "compute_port",
            "tools",
            "status_sha256",
            "stable_identity",
            "ai_bridge_is_trusted_receipt_source",
        )
    }
    if fallback.get("ready") is not True or fallback.get("ai_bridge_is_trusted_receipt_source") is not False:
        raise RuntimeError("AI-Bridge fallback is not ready with the required trust boundary")
    return {
        "installed": installed,
        "status": status,
        "state": state,
        "state_snapshot": state_snapshot,
        "production": production,
        "component_identities": component_identities,
        "fallback_identity": fallback_identity,
    }


def adopt_current_production_rollback(
    manifest: dict[str, Any],
    layout: dict[str, Path],
    *,
    home_dir: Path = Path.home(),
    native_user_data_dirs: Iterable[Path] = (),
    fallback_health_url_file: Path = AI_BRIDGE_FALLBACK_HEALTH_URL_FILE,
) -> dict[str, Any]:
    """Arm rollback for an already-running exact production without inventing activation history."""
    managed_dirs = tuple(Path(path).expanduser() for path in native_user_data_dirs)
    with _production_install_lock(layout["runtime_root"]):
        canonical_receipt = layout["rollback_receipt"].expanduser().resolve()
        prior_receipt_snapshot = _private_file_snapshot(
            canonical_receipt, "canonical prior completed rollback receipt"
        )
        prior_receipt = _decode_private_json_snapshot(
            prior_receipt_snapshot, "canonical prior completed rollback receipt"
        )
        prior_receipt_sha256 = hashlib.sha256(prior_receipt_snapshot[0]).hexdigest()
        historical, historical_bridge, historical_state_path, historical_state_sha256 = (
            _completed_receipt_historical_baseline(prior_receipt, manifest)
        )
        first = _observe_current_production_for_rollback_adoption(
            manifest,
            layout,
            home_dir=home_dir,
            native_user_data_dirs=managed_dirs,
            fallback_health_url_file=fallback_health_url_file,
        )
        runtime_snapshot = first["state_snapshot"]
        runtime_before_sha256 = hashlib.sha256(runtime_snapshot[0]).hexdigest()
        if historical.get("tunnel_id") != first["production"].get("tunnel_id"):
            raise RuntimeError("historical M0 and current production tunnel ids do not match")

        second = _observe_current_production_for_rollback_adoption(
            manifest,
            layout,
            home_dir=home_dir,
            native_user_data_dirs=managed_dirs,
            fallback_health_url_file=fallback_health_url_file,
        )
        for key in (
            "installed",
            "status",
            "state",
            "production",
            "component_identities",
            "fallback_identity",
        ):
            if second[key] != first[key]:
                raise RuntimeError(f"current production {key} drifted during rollback adoption")
        historical_recheck = _completed_receipt_historical_baseline(prior_receipt, manifest)
        if historical_recheck != (
            historical,
            historical_bridge,
            historical_state_path,
            historical_state_sha256,
        ):
            raise RuntimeError("historical M0 rollback baseline drifted during adoption")
        if _read_private_bytes_if_present(
            canonical_receipt, "canonical prior completed rollback receipt CAS"
        ) != prior_receipt_snapshot[0]:
            raise RuntimeError("canonical prior completed rollback receipt changed during adoption")
        if _read_private_bytes_if_present(
            layout["runtime_state"], "production runtime state rollback adoption CAS"
        ) != runtime_snapshot[0]:
            raise RuntimeError("production runtime state changed during rollback adoption")
        historical_state_readback = _read_private_bytes_if_present(
            historical_state_path, "historical M0 runtime state rollback adoption CAS"
        )
        if (
            historical_state_readback is None
            or hashlib.sha256(historical_state_readback).hexdigest() != historical_state_sha256
        ):
            raise RuntimeError("historical M0 runtime state changed during rollback adoption")

        adopted_at = time.time()
        receipt = {
            "schema_version": 1,
            "status": "armed",
            "connector_id": CONNECTOR_ID,
            "contract_version": CONTRACT_VERSION,
            "release_revision": manifest["release_revision"],
            "endpoint_generation": manifest["endpoint_generation"],
            "tunnel_id": first["production"]["tunnel_id"],
            "historical": historical,
            "production": first["production"],
            "historical_bridge": historical_bridge,
            "historical_runtime_state": str(historical_state_path),
            "historical_runtime_state_sha256": historical_state_sha256,
            "provenance": "observed_current_production",
            "original_pre_activation_baseline": "unknown",
            "prior_canonical_completed_rollback_receipt_sha256": prior_receipt_sha256,
            "adoption_runtime_state_before_sha256": runtime_before_sha256,
            "created_at": adopted_at,
            "adopted_at": adopted_at,
        }
        receipt_write_started = False
        state_write_started = False
        try:
            receipt_write_started = True
            _write_json_private(canonical_receipt, receipt)
            receipt_sha256 = _sha256_file(canonical_receipt)
            if _read_private_bytes_if_present(
                layout["runtime_state"], "production runtime state rollback adoption pin CAS"
            ) != runtime_snapshot[0]:
                raise RuntimeError("production runtime state changed before rollback adoption pin")
            state = dict(first["state"])
            state.update(
                {
                    "activation_rollback_receipt": str(canonical_receipt),
                    "activation_rollback_receipt_sha256": receipt_sha256,
                }
            )
            state_write_started = True
            _write_json_private(layout["runtime_state"], state)
            receipt_readback = _read_private_bytes_if_present(
                canonical_receipt, "adopted rollback receipt readback"
            )
            state_readback = _read_private_bytes_if_present(
                layout["runtime_state"], "adopted production runtime state readback"
            )
            if (
                receipt_readback is None
                or hashlib.sha256(receipt_readback).hexdigest() != receipt_sha256
                or receipt_readback
                != (json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
            ):
                raise RuntimeError("adopted rollback receipt readback mismatch")
            if state_readback != (
                json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            ).encode("utf-8"):
                raise RuntimeError("adopted production runtime state pin readback mismatch")
            post_pin = _observe_current_production_for_rollback_adoption(
                manifest,
                layout,
                home_dir=home_dir,
                native_user_data_dirs=managed_dirs,
                fallback_health_url_file=fallback_health_url_file,
                expected_receipt_pin=(str(canonical_receipt), receipt_sha256),
            )
            for key in (
                "installed",
                "status",
                "production",
                "component_identities",
                "fallback_identity",
            ):
                if post_pin[key] != first[key]:
                    raise RuntimeError(
                        f"current production {key} drifted during post-pin rollback adoption verification"
                    )
        except Exception as error:
            rollback_errors: list[str] = []
            if state_write_started:
                try:
                    _restore_private_file_snapshot(
                        layout["runtime_state"], runtime_snapshot, private_parent=True
                    )
                except Exception as rollback_error:
                    rollback_errors.append(f"runtime state restore failed: {rollback_error}")
            if receipt_write_started:
                try:
                    _restore_private_file_snapshot(
                        canonical_receipt, prior_receipt_snapshot, private_parent=True
                    )
                except Exception as rollback_error:
                    rollback_errors.append(f"receipt restore failed: {rollback_error}")
            if rollback_errors:
                raise RuntimeError(f"{error}; {'; '.join(rollback_errors)}") from error
            raise
        return {
            "adopted": True,
            "release_revision": manifest["release_revision"],
            "endpoint_generation": manifest["endpoint_generation"],
            "production_tunnel_pid": first["production"]["pid"],
            "historical_bridge_pid": historical_bridge["pid"],
            "rollback_receipt": {
                "path": str(canonical_receipt),
                "sha256": receipt_sha256,
            },
        }


def _validate_receipt_tunnel_profile(
    item: dict[str, Any], *, expected_socket: Path, label: str
) -> dict[str, Any]:
    required = {
        "pid", "command", "profile_dir", "profile_name", "profile_path", "profile_sha256",
        "tunnel_id", "process_identity", "expected_socket",
    }
    if not isinstance(item, dict) or not required.issubset(item):
        raise RuntimeError(f"{label} receipt is incomplete")
    identity = _validate_process_identity_record(item["process_identity"])
    try:
        command_argv = shlex.split(str(item["command"]))
    except ValueError as error:
        raise RuntimeError(f"{label} receipt command is malformed") from error
    if identity["pid"] != item["pid"] or identity["argv"] != command_argv:
        raise RuntimeError(f"{label} receipt process identity mismatch")
    profile_dir = Path(str(item["profile_dir"])).expanduser().resolve()
    profile_path = Path(str(item["profile_path"])).expanduser().resolve()
    if profile_path != _tunnel_profile_path(profile_dir, str(item["profile_name"])).resolve():
        raise RuntimeError(f"{label} receipt profile path mismatch")
    if Path(str(item["expected_socket"])).expanduser().resolve() != expected_socket.expanduser().resolve():
        raise RuntimeError(f"{label} receipt socket mismatch")
    profile = _validate_tunnel_profile(
        profile_path,
        profile_name=str(item["profile_name"]),
        expected_socket=expected_socket,
    )
    if profile["profile_sha256"] != item["profile_sha256"] or profile["tunnel_id"] != item["tunnel_id"]:
        raise RuntimeError(f"{label} profile changed after activation")
    return profile


def _record_historical_tunnel_restart(
    layout: dict[str, Path], historical_state_path: Path, restarted: dict[str, Any]
) -> None:
    historical_state = _read_json_object(historical_state_path)
    historical_state.update(
        {
            "tunnel_pid": restarted["pid"],
            "tunnel_id": restarted["tunnel_id"],
            "tunnel_profile_name": restarted["profile_name"],
            "tunnel_profile_sha256": restarted["profile_sha256"],
            "tunnel_process_identity": restarted["process_identity"],
        }
    )
    _write_json_private(historical_state_path, historical_state)
    production_state = _read_json_object(layout["runtime_state"])
    for key in (
        "tunnel_pid", "tunnel_id", "tunnel_profile_sha256", "tunnel_started_at",
        "tunnel_process_identity",
    ):
        production_state.pop(key, None)
    production_state.update(
        {
            "rollback_completed_at": time.time(),
            "rollback_historical_tunnel_pid": restarted["pid"],
            "rollback_historical_tunnel_id": restarted["tunnel_id"],
            "rollback_historical_tunnel_process_identity": restarted["process_identity"],
        }
    )
    _write_json_private(layout["runtime_state"], production_state)


def _resume_rollback_tunnel_switch(
    historical: dict[str, Any],
    production: dict[str, Any],
    *,
    bridge_socket: Path,
) -> dict[str, Any]:
    historical_running = _discover_tunnel_processes(
        profile_dir=Path(str(historical["profile_dir"])),
        profile_name=str(historical["profile_name"]),
        expected_socket=HISTORICAL_M0_BRIDGE_SOCKET,
    )
    production_running = _process_identity_matches(production.get("process_identity", {}))
    if production_running:
        if historical_running:
            raise RuntimeError("rollback cannot proceed while production and historical tunnels both run")
        _require_exact_tunnel_id_owners(
            production["tunnel_id"], {production["pid"]}, expected_socket=bridge_socket
        )
        _stop_verified_tunnel(production)
        _require_exact_tunnel_id_owners(
            production["tunnel_id"], set(), expected_socket=bridge_socket
        )
        return _restart_verified_historical_tunnel(historical)
    if len(historical_running) > 1:
        raise RuntimeError("rollback found multiple historical tunnel processes")
    if len(historical_running) == 1:
        restarted = historical_running[0]
        if restarted.get("tunnel_id") != historical.get("tunnel_id"):
            raise RuntimeError("restarted historical tunnel id does not match rollback intent")
        _validate_receipt_tunnel_profile(
            restarted,
            expected_socket=HISTORICAL_M0_BRIDGE_SOCKET,
            label="restarted historical tunnel",
        )
        if not _process_identity_matches(restarted.get("process_identity", {})):
            raise RuntimeError("restarted historical tunnel identity cannot be verified")
        _require_exact_tunnel_id_owners(
            restarted["tunnel_id"], {restarted["pid"]}, expected_socket=HISTORICAL_M0_BRIDGE_SOCKET
        )
        return restarted
    _require_exact_tunnel_id_owners(
        historical["tunnel_id"], set(), expected_socket=HISTORICAL_M0_BRIDGE_SOCKET
    )
    return _restart_verified_historical_tunnel(historical)


def _rollback_production_activation_unlocked(
    manifest: dict[str, Any],
    layout: dict[str, Path],
    *,
    receipt_path: Path | None = None,
) -> dict[str, Any]:
    """Restore the exact pre-activation M0 tunnel from the canonical receipt."""
    _validate_rollback_release(manifest, layout)
    canonical_receipt = layout["rollback_receipt"].expanduser().resolve()
    requested_receipt = Path(receipt_path or canonical_receipt).expanduser().resolve()
    if requested_receipt != canonical_receipt:
        raise RuntimeError("rollback receipt must be the canonical production receipt")
    _validate_private_source(canonical_receipt, "production activation rollback receipt")
    receipt = _read_json_object(canonical_receipt)
    state = _read_json_object(layout["runtime_state"])
    current_receipt_sha256 = _sha256_file(canonical_receipt)
    pinned_receipt_sha256 = state.get("activation_rollback_receipt_sha256")
    valid_pins = {current_receipt_sha256}
    if receipt.get("status") == "rolling_back":
        valid_pins.update(
            value
            for value in (
                receipt.get("armed_receipt_sha256"),
                receipt.get("completed_receipt_sha256"),
            )
            if isinstance(value, str)
        )
    if (
        state.get("activation_rollback_receipt") != str(canonical_receipt)
        or pinned_receipt_sha256 not in valid_pins
    ):
        raise RuntimeError("rollback receipt is not pinned by production runtime state")
    if any(
        receipt.get(key) != expected
        for key, expected in (
            ("schema_version", 1),
            ("connector_id", CONNECTOR_ID),
            ("contract_version", CONTRACT_VERSION),
            ("release_revision", manifest["release_revision"]),
            ("endpoint_generation", manifest["endpoint_generation"]),
        )
    ):
        raise RuntimeError("rollback receipt does not match the requested production release")

    historical = receipt.get("historical")
    production = receipt.get("production")
    if not isinstance(historical, dict) or not isinstance(production, dict):
        raise RuntimeError("rollback receipt has no exact tunnel identities")
    tunnel_id = receipt.get("tunnel_id")
    if tunnel_id != historical.get("tunnel_id") or tunnel_id != production.get("tunnel_id"):
        raise RuntimeError("rollback receipt tunnel id mismatch")
    historical_state_path = Path(str(receipt.get("historical_runtime_state") or "")).expanduser()
    if not historical_state_path.is_absolute():
        raise RuntimeError("rollback receipt historical runtime state path is invalid")
    historical_state_path = historical_state_path.resolve()

    if receipt.get("status") == "rolled_back":
        restarted = receipt.get("historical_restarted")
        if not isinstance(restarted, dict) or not _process_identity_matches(restarted.get("process_identity", {})):
            raise RuntimeError("completed rollback receipt no longer identifies the historical tunnel")
        _validate_receipt_tunnel_profile(
            restarted, expected_socket=HISTORICAL_M0_BRIDGE_SOCKET, label="restarted historical tunnel"
        )
        _require_exact_tunnel_id_owners(
            str(tunnel_id), {int(restarted["pid"])}, expected_socket=HISTORICAL_M0_BRIDGE_SOCKET
        )
        historical_state = _read_json_object(historical_state_path)
        if (
            historical_state.get("tunnel_pid") != restarted.get("pid")
            or historical_state.get("tunnel_process_identity") != restarted.get("process_identity")
        ):
            raise RuntimeError("completed rollback is not reflected in historical runtime state")
        return {
            "rolled_back": True,
            "already_rolled_back": True,
            "historical_tunnel_pid": restarted["pid"],
            "production_tunnel_stopped": True,
        }
    if receipt.get("status") not in {"armed", "rolling_back"}:
        raise RuntimeError("rollback receipt is neither armed nor recoverable")

    _validate_private_source(historical_state_path, "historical M0 runtime state")
    _validate_private_source(layout["runtime_state"], "production runtime state for rollback")
    bridge = receipt.get("historical_bridge")
    if not isinstance(bridge, dict) or not _process_identity_matches(bridge.get("process_identity", {})):
        raise RuntimeError("historical M0 Bridge identity changed after activation")
    _validate_receipt_tunnel_profile(
        historical, expected_socket=HISTORICAL_M0_BRIDGE_SOCKET, label="historical tunnel"
    )
    _validate_receipt_tunnel_profile(
        production, expected_socket=layout["bridge_socket"], label="production tunnel"
    )
    if receipt.get("status") == "armed":
        if _sha256_file(historical_state_path) != receipt.get("historical_runtime_state_sha256"):
            raise RuntimeError("historical M0 runtime state changed after activation")
        receipt.update(
            {
                "status": "rolling_back",
                "phase": "intent",
                "armed_receipt_sha256": current_receipt_sha256,
                "historical_runtime_state_before_sha256": _sha256_file(historical_state_path),
                "production_runtime_state_before_sha256": _sha256_file(layout["runtime_state"]),
                "rollback_started_at": time.time(),
            }
        )
        _write_json_private(canonical_receipt, receipt)
    else:
        if receipt.get("phase") not in {"intent", "switched", "historical_written"}:
            raise RuntimeError("rollback receipt journal phase is invalid")
        for hash_key in (
            "armed_receipt_sha256",
            "historical_runtime_state_before_sha256",
            "production_runtime_state_before_sha256",
        ):
            value = receipt.get(hash_key)
            if not isinstance(value, str) or len(value) != 64:
                raise RuntimeError("rollback receipt journal is missing an expected before hash")

    if receipt["phase"] == "intent":
        restarted = _resume_rollback_tunnel_switch(
            historical, production, bridge_socket=layout["bridge_socket"]
        )
        _validate_receipt_tunnel_profile(
            restarted, expected_socket=HISTORICAL_M0_BRIDGE_SOCKET, label="restarted historical tunnel"
        )
        _require_exact_tunnel_id_owners(
            str(tunnel_id), {int(restarted["pid"])}, expected_socket=HISTORICAL_M0_BRIDGE_SOCKET
        )
        receipt.update({"phase": "switched", "historical_restarted": restarted})
        _write_json_private(canonical_receipt, receipt)
    else:
        restarted = receipt.get("historical_restarted")
        if not isinstance(restarted, dict):
            raise RuntimeError("rollback receipt journal lost the restarted historical identity")
        _validate_receipt_tunnel_profile(
            restarted, expected_socket=HISTORICAL_M0_BRIDGE_SOCKET, label="restarted historical tunnel"
        )
        if not _process_identity_matches(restarted.get("process_identity", {})):
            raise RuntimeError("rollback journal historical tunnel identity changed")
        _require_exact_tunnel_id_owners(
            str(tunnel_id), {int(restarted["pid"])}, expected_socket=HISTORICAL_M0_BRIDGE_SOCKET
        )

    historical_state = _read_json_object(historical_state_path)
    historical_is_updated = bool(
        historical_state.get("tunnel_pid") == restarted.get("pid")
        and historical_state.get("tunnel_process_identity") == restarted.get("process_identity")
        and historical_state.get("tunnel_id") == restarted.get("tunnel_id")
    )
    if not historical_is_updated:
        if _sha256_file(historical_state_path) != receipt["historical_runtime_state_before_sha256"]:
            raise RuntimeError("historical runtime state is neither before nor journaled rollback state")
        historical_state.update(
            {
                "tunnel_pid": restarted["pid"],
                "tunnel_id": restarted["tunnel_id"],
                "tunnel_profile_name": restarted["profile_name"],
                "tunnel_profile_sha256": restarted["profile_sha256"],
                "tunnel_process_identity": restarted["process_identity"],
            }
        )
        _write_json_private(historical_state_path, historical_state)
    if receipt["phase"] != "historical_written":
        receipt["phase"] = "historical_written"
        _write_json_private(canonical_receipt, receipt)

    rolled_back_at = receipt.get("rolled_back_at")
    if not isinstance(rolled_back_at, (int, float)) or isinstance(rolled_back_at, bool):
        rolled_back_at = time.time()
        receipt["rolled_back_at"] = rolled_back_at
    completed_receipt = dict(receipt)
    completed_receipt.update({"status": "rolled_back", "phase": "completed"})
    completed_receipt.pop("completed_receipt_sha256", None)
    completed_receipt_sha256 = _json_private_sha256(completed_receipt)
    if receipt.get("completed_receipt_sha256") != completed_receipt_sha256:
        receipt["completed_receipt_sha256"] = completed_receipt_sha256
        _write_json_private(canonical_receipt, receipt)

    state = _read_json_object(layout["runtime_state"])
    production_is_updated = bool(
        state.get("rollback_historical_tunnel_pid") == restarted.get("pid")
        and state.get("rollback_historical_tunnel_process_identity") == restarted.get("process_identity")
        and state.get("activation_rollback_receipt_sha256") == completed_receipt_sha256
        and "tunnel_pid" not in state
    )
    if not production_is_updated:
        if _sha256_file(layout["runtime_state"]) != receipt["production_runtime_state_before_sha256"]:
            raise RuntimeError("production runtime state is neither before nor journaled rollback state")
        for key in (
            "tunnel_pid", "tunnel_id", "tunnel_profile_sha256", "tunnel_started_at",
            "tunnel_process_identity",
        ):
            state.pop(key, None)
        state.update(
            {
                "rollback_completed_at": rolled_back_at,
                "rollback_historical_tunnel_pid": restarted["pid"],
                "rollback_historical_tunnel_id": restarted["tunnel_id"],
                "rollback_historical_tunnel_process_identity": restarted["process_identity"],
                "activation_rollback_receipt_sha256": completed_receipt_sha256,
            }
        )
        _write_json_private(layout["runtime_state"], state)
    _write_json_private(canonical_receipt, completed_receipt)
    return {
        "rolled_back": True,
        "already_rolled_back": False,
        "historical_tunnel_pid": restarted["pid"],
        "production_tunnel_stopped": True,
    }


def _activate_production_tunnel_unlocked(
    manifest: dict[str, Any],
    layout: dict[str, Path],
    *,
    historical_profile_dir: Path,
    historical_profile_name: str,
    historical_runtime_state: Path = HISTORICAL_M0_RUNTIME_STATE,
    home_dir: Path = Path.home(),
    native_user_data_dirs: Iterable[Path] = (),
    tunnel_client: Path | None = None,
    fallback_health_url_file: Path = AI_BRIDGE_FALLBACK_HEALTH_URL_FILE,
) -> dict[str, Any]:
    """Cut over only the verified M0 tunnel while preserving its Bridge."""
    validate_installed_release(layout)
    if production_status(manifest, layout).get("local_runtime_ready") is not True:
        raise RuntimeError("production local runtime is not ready; tunnel activation is fail-closed")
    verify_caller_conversation_id_rejected(layout)
    validate_native_messaging_manifests(
        layout,
        manifest,
        home_dir=home_dir,
        native_user_data_dirs=native_user_data_dirs,
    )
    fallback_before = _verify_ai_bridge_fallback(fallback_health_url_file)
    historical = find_verified_historical_m0_tunnel(
        historical_profile_dir, profile_name=historical_profile_name
    )
    old_bridge = verified_historical_m0_bridge(historical_runtime_state)
    production_profile = _validate_tunnel_profile(
        layout["tunnel_profile"], profile_name=TUNNEL_PROFILE_NAME, expected_socket=layout["bridge_socket"]
    )
    if historical["tunnel_id"] != production_profile["tunnel_id"]:
        raise RuntimeError("historical M0 tunnel and production tunnel do not address the same private connector")

    _require_exact_tunnel_id_owners(
        historical["tunnel_id"], {historical["pid"]}, expected_socket=HISTORICAL_M0_BRIDGE_SOCKET
    )
    historical_stopped = False
    production: dict[str, Any] | None = None
    try:
        _stop_verified_tunnel(historical)
        historical_stopped = True
        _require_exact_tunnel_id_owners(
            historical["tunnel_id"], set(), expected_socket=HISTORICAL_M0_BRIDGE_SOCKET
        )
        production = _start_production_tunnel(
            manifest, layout, tunnel_client=tunnel_client
        )
        _require_exact_tunnel_id_owners(
            production["tunnel_id"], {production["pid"]}, expected_socket=layout["bridge_socket"]
        )
        fallback_after = _verify_ai_bridge_fallback(fallback_health_url_file)
        for key in (
            "health_url",
            "compute_url",
            "compute_host",
            "compute_port",
            "tools",
            "status_sha256",
            "ai_bridge_is_trusted_receipt_source",
        ):
            if fallback_after.get(key) != fallback_before.get(key):
                raise RuntimeError("AI-Bridge fallback identity changed during production activation")
        rollback_receipt = _write_activation_rollback_receipt(
            manifest,
            layout,
            historical=historical,
            production=production,
            historical_bridge=old_bridge,
            historical_runtime_state=historical_runtime_state,
        )
    except Exception as error:
        rollback_errors: list[str] = []
        if production is not None:
            try:
                _stop_verified_tunnel(production)
            except Exception as stop_error:
                rollback_errors.append(f"production stop failed: {stop_error}")
        if historical_stopped:
            try:
                restarted = _restart_verified_historical_tunnel(historical)
                _require_exact_tunnel_id_owners(
                    restarted["tunnel_id"],
                    {restarted["pid"]},
                    expected_socket=HISTORICAL_M0_BRIDGE_SOCKET,
                )
                _record_historical_tunnel_restart(
                    layout, Path(historical_runtime_state).expanduser().resolve(), restarted
                )
            except Exception as rollback_error:
                rollback_errors.append(f"historical tunnel rollback failed: {rollback_error}")
        if rollback_errors:
            raise RuntimeError(f"{error}; {'; '.join(rollback_errors)}") from error
        raise
    return {
        "activated": True,
        "release_revision": manifest["release_revision"],
        "endpoint_generation": manifest["endpoint_generation"],
        "production_tunnel_pid": production["pid"],
        "historical_tunnel_pid": historical["pid"],
        "historical_bridge_pid": old_bridge["pid"],
        "historical_bridge_preserved": True,
        "personal_chat_live_e2e_required_before_historical_bridge_finalize": True,
        "ai_bridge_fallback": fallback_after,
        "rollback_receipt": rollback_receipt,
    }


def _bridge_capability_lease_status(
    layout: dict[str, Path], state: dict[str, Any], *, now_ms: int | None = None
) -> dict[str, Any]:
    capability_id = state.get("bridge_capability_id")
    session_id = state.get("bridge_execution_session_id")
    ttl_ms = state.get("bridge_capability_ttl_ms")
    renew_before_ms = state.get("bridge_capability_renew_before_ms")
    if (
        not isinstance(capability_id, str)
        or not capability_id.startswith("bridge-cap-production-")
        or not isinstance(session_id, str)
        or not session_id.startswith("bridge-session-production-")
        or ttl_ms != PRODUCTION_BRIDGE_CAPABILITY_TTL_MS
        or renew_before_ms != PRODUCTION_BRIDGE_CAPABILITY_RENEW_BEFORE_MS
    ):
        raise RuntimeError("production Bridge capability renewal state is missing or invalid")
    raw = _read_private_bytes_if_present(
        layout["bridge_authority_state"], "production Bridge capability authority state"
    )
    if raw is None:
        raise RuntimeError("production Bridge capability authority state is missing")
    try:
        authority = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("production Bridge capability authority state is invalid JSON") from error
    if not isinstance(authority, dict) or authority.get("schema_version") != 2:
        raise RuntimeError("production Bridge capability authority state schema is unsupported")
    records = authority.get("records")
    generations = authority.get("session_generations")
    if not isinstance(records, dict) or not isinstance(generations, dict):
        raise RuntimeError("production Bridge capability authority state is incomplete")
    record = records.get(capability_id)
    if not isinstance(record, dict) or record.get("revoked") is not False:
        raise RuntimeError("production Bridge capability is missing or revoked")
    lease = record.get("lease")
    grant = lease.get("grant") if isinstance(lease, dict) else None
    issued_at_ms = lease.get("issued_at_ms") if isinstance(lease, dict) else None
    expires_at_ms = lease.get("expires_at_ms") if isinstance(lease, dict) else None
    session_generation = lease.get("session_generation") if isinstance(lease, dict) else None
    expected_capabilities = {
        "Discovery",
        "HostAttestation",
        "FileRead",
        "FileWrite",
        "FileMove",
        "Shell",
        "ProcessJobs",
    }
    installed = _read_json_object(layout["release_manifest"])
    if isinstance((installed.get("runtime_dependencies") or {}).get("feishu_cli"), dict):
        expected_capabilities.update({"CollaborationRead", "CollaborationWrite"})
    if installed.get("feishu_admin_enabled") is True:
        expected_capabilities.add("ProviderAdmin")
    observed_capabilities = grant.get("capabilities") if isinstance(grant, dict) else None
    if (
        not isinstance(grant, dict)
        or grant.get("capability_id") != capability_id
        or grant.get("execution_session_id") != session_id
        or grant.get("principal_id") != "tunnel-client-production-uds"
        or grant.get("workspace_id") != CONNECTOR_ID
        or not isinstance(observed_capabilities, list)
        or not all(isinstance(value, str) for value in observed_capabilities)
        or set(observed_capabilities) != expected_capabilities
        or isinstance(issued_at_ms, bool)
        or not isinstance(issued_at_ms, int)
        or issued_at_ms < 1
        or isinstance(expires_at_ms, bool)
        or not isinstance(expires_at_ms, int)
        or expires_at_ms <= issued_at_ms
        or expires_at_ms - issued_at_ms != ttl_ms
        or isinstance(session_generation, bool)
        or not isinstance(session_generation, int)
        or session_generation < 1
        or generations.get(session_id) != session_generation
    ):
        raise RuntimeError("production Bridge capability lease identity or bounds mismatch")
    observed_now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    if isinstance(observed_now_ms, bool) or not isinstance(observed_now_ms, int):
        raise RuntimeError("production Bridge capability observation time is invalid")
    remaining_ms = max(0, expires_at_ms - observed_now_ms)
    if observed_now_ms > expires_at_ms:
        raise RuntimeError("production Bridge capability lease is expired")
    if remaining_ms <= renew_before_ms:
        raise RuntimeError("production Bridge capability lease did not renew before its safety window")
    return {
        "ready": True,
        "renewal_enabled": True,
        "remaining_ms": remaining_ms,
        "expires_at_ms": expires_at_ms,
    }


def production_status(manifest: dict[str, Any], layout: dict[str, Path]) -> dict[str, Any]:
    _validate_manifest_contract(manifest)
    try:
        installed = validate_installed_release(layout)
        release_installed = True
        release_error = None
    except Exception as error:
        installed = None
        release_installed = False
        release_error = str(error)
    runtime_verifier_config_ready = False
    if release_installed:
        try:
            _validate_runtime_host_verifier_registration(
                layout, manifest, home_dir=Path.home()
            )
            runtime_verifier_config_ready = True
        except Exception:
            pass
    state = _read_json_object(layout["runtime_state"])
    state_matches = bool(
        state.get("release_revision") == manifest["release_revision"]
        and state.get("endpoint_generation") == manifest["endpoint_generation"]
        and state.get("workspace") == manifest["workspace"]
        and state.get("tunnel_profile_name") == TUNNEL_PROFILE_NAME
    )
    catdesk_pid = state.get("catdesk_supervisor_pid") if state_matches else None
    host_pid = state.get("host_pid") if state_matches else None
    runtime_hook_pid = state.get("runtime_hook_pid") if state_matches else None
    process_runtime_pid = state.get("process_runtime_pid") if state_matches else None
    bridge_pid = state.get("bridge_pid") if state_matches else None
    node = ((installed or {}).get("runtime_dependencies") or {}).get("node", {}).get("path")
    catdesk_identity = state.get("catdesk_supervisor_process_identity") if state_matches else None
    host_identity = state.get("host_process_identity") if state_matches else None
    runtime_hook_identity = state.get("runtime_hook_process_identity") if state_matches else None
    process_runtime_identity = (
        state.get("process_runtime_process_identity") if state_matches else None
    )
    bridge_identity = state.get("bridge_process_identity") if state_matches else None
    catdesk_process = bool(
        isinstance(catdesk_pid, int)
        and isinstance(catdesk_identity, dict)
        and catdesk_identity.get("pid") == catdesk_pid
        and _process_identity_matches(catdesk_identity)
    )
    host_process = bool(
        node
        and isinstance(host_pid, int)
        and isinstance(host_identity, dict)
        and host_identity.get("pid") == host_pid
        and host_identity.get("exe_realpath")
        == str(Path(node).resolve(strict=True))
        and host_identity.get("argv")
        == [
            str(Path(node).resolve(strict=True)),
            str(_resolved_command_path(layout["host_mcp_adapter"])),
        ]
        and _process_identity_matches(host_identity)
    )
    runtime_hook_process = bool(
        isinstance(runtime_hook_pid, int)
        and isinstance(runtime_hook_identity, dict)
        and runtime_hook_identity.get("pid") == runtime_hook_pid
        and _process_identity_matches(runtime_hook_identity)
    )
    process_runtime_binary = None
    if release_installed:
        binary_path = layout.get("process_runtime_binary")
        if isinstance(binary_path, Path) and binary_path.is_file():
            try:
                process_runtime_binary = str(binary_path.resolve(strict=True))
            except OSError:
                process_runtime_binary = None
    process_runtime_process = bool(
        process_runtime_binary
        and isinstance(process_runtime_pid, int)
        and isinstance(process_runtime_identity, dict)
        and process_runtime_identity.get("pid") == process_runtime_pid
        and process_runtime_identity.get("exe_realpath") == process_runtime_binary
        and process_runtime_identity.get("argv") == [process_runtime_binary]
        and _process_identity_matches(process_runtime_identity)
    )
    bridge_process = bool(
        isinstance(bridge_pid, int)
        and isinstance(bridge_identity, dict)
        and bridge_identity.get("pid") == bridge_pid
        and _process_identity_matches(bridge_identity)
    )
    backend_ready = bool(catdesk_process and _tcp_ready("127.0.0.1", PRODUCTION_CATDESK_PORT))
    host_ready = bool(host_process and _tcp_ready("127.0.0.1", PRODUCTION_HOST_MCP_PORT))
    runtime_hook_ready = bool(
        runtime_hook_process
        and _runtime_hook_socket_is_live(
            layout["runtime_hook_socket"], state.get("runtime_hook_socket_identity")
        )
    )
    process_runtime_ready = bool(
        process_runtime_process
        and _tcp_ready("127.0.0.1", PRODUCTION_PROCESS_RUNTIME_PORT)
    )
    tools: list[str] = []
    bridge_ready = False
    bridge_lease: dict[str, Any] = {
        "ready": False,
        "renewal_enabled": False,
        "remaining_ms": None,
        "expires_at_ms": None,
    }
    bridge_lease_error = None
    if bridge_process and layout["bridge_socket"].exists():
        try:
            tools = smoke_tools(layout)
            bridge_lease = _bridge_capability_lease_status(layout, state)
            bridge_ready = bridge_lease["ready"] is True
        except Exception as error:
            bridge_lease_error = str(error)
    local_ready = bool(
        release_installed
        and state_matches
        and backend_ready
        and host_ready
        and runtime_hook_ready
        and process_runtime_ready
        and bridge_ready
    )
    tunnel = production_tunnel_status(manifest, layout)
    return {
        "connector_id": CONNECTOR_ID,
        "contract_version": CONTRACT_VERSION,
        "release_revision": manifest["release_revision"],
        "endpoint_generation": manifest["endpoint_generation"],
        "workspace": manifest["workspace"],
        "runtime_root": str(layout["runtime_root"]),
        "bridge_socket": str(layout["bridge_socket"]),
        "tunnel_profile_name": TUNNEL_PROFILE_NAME,
        "release_installed": release_installed,
        "release_error": release_error,
        "runtime_state_matches": state_matches,
        "runtime_verifier_config_ready": runtime_verifier_config_ready,
        "backend_ready": backend_ready,
        "host_ready": host_ready,
        "runtime_hook_ready": runtime_hook_ready,
        "process_runtime_ready": process_runtime_ready,
        "bridge_ready": bridge_ready,
        "bridge_capability_renewal_enabled": bridge_lease["renewal_enabled"],
        "bridge_capability_lease_remaining_ms": bridge_lease["remaining_ms"],
        "bridge_capability_lease_expires_at_ms": bridge_lease["expires_at_ms"],
        "bridge_capability_lease_error": bridge_lease_error,
        "local_runtime_ready": local_ready,
        "catdesk_supervisor_pid": catdesk_pid,
        "host_pid": host_pid,
        "runtime_hook_pid": runtime_hook_pid,
        "process_runtime_pid": process_runtime_pid,
        "bridge_pid": bridge_pid,
        "tools": tools,
        "manifest_has_artifacts": bool(installed and installed.get("artifacts_sha256")),
        "tunnel_ready": tunnel.get("ready") is True,
        "tunnel_pid": tunnel.get("pid"),
        "tunnel_id": tunnel.get("tunnel_id"),
        "tunnel_state_reason": tunnel.get("reason"),
    }


_ACTIVE_PRODUCTION_TUNNEL_STATE_KEYS = (
    "tunnel_pid",
    "tunnel_id",
    "tunnel_profile_sha256",
    "tunnel_started_at",
    "tunnel_process_identity",
)


def _completed_rollback_upgrade_gate(
    manifest: dict[str, Any], layout: dict[str, Path], prior: dict[str, Any]
) -> None:
    """Permit a new release only after the old production tunnel is provably rolled back."""
    receipt_path_value = prior.get("activation_rollback_receipt")
    receipt_hash = prior.get("activation_rollback_receipt_sha256")
    if not isinstance(receipt_path_value, str) or not isinstance(receipt_hash, str):
        raise RuntimeError("release upgrade requires a complete rollback receipt pin")
    canonical_receipt = layout["rollback_receipt"].expanduser().resolve()
    if Path(receipt_path_value).expanduser().resolve() != canonical_receipt:
        raise RuntimeError("release upgrade rollback receipt path is not canonical")
    _validate_private_source(layout["runtime_state"], "prior production runtime state")
    _validate_private_source(canonical_receipt, "completed rollback receipt")
    if _sha256_file(canonical_receipt) != receipt_hash:
        raise RuntimeError("completed rollback receipt hash does not match its runtime pin")
    receipt = _read_json_object(canonical_receipt)
    if receipt.get("status") != "rolled_back" or receipt.get("phase") != "completed":
        raise RuntimeError("release upgrade requires a completed rollback receipt")
    if any(
        receipt.get(key) != expected
        for key, expected in (
            ("schema_version", 1),
            ("connector_id", CONNECTOR_ID),
            ("contract_version", CONTRACT_VERSION),
        )
    ):
        raise RuntimeError("completed rollback receipt is not a production receipt")
    if any(
        receipt.get(key) != prior.get(key)
        for key in ("release_revision", "endpoint_generation")
    ):
        raise RuntimeError("completed rollback receipt does not match the prior production runtime")
    if any(key in prior for key in _ACTIVE_PRODUCTION_TUNNEL_STATE_KEYS):
        raise RuntimeError("prior production runtime still contains an active tunnel identity")

    restarted = receipt.get("historical_restarted")
    production = receipt.get("production")
    if not isinstance(restarted, dict) or not isinstance(production, dict):
        raise RuntimeError("completed rollback receipt lacks tunnel identities")
    production_identity = production.get("process_identity")
    _validate_process_identity_record(production_identity)
    restarted_pid = restarted.get("pid")
    restarted_identity = restarted.get("process_identity")
    if not isinstance(restarted_pid, int) or not isinstance(restarted_identity, dict):
        raise RuntimeError("completed rollback receipt historical identity is invalid")
    if (
        prior.get("rollback_historical_tunnel_pid") != restarted_pid
        or prior.get("rollback_historical_tunnel_id") != restarted.get("tunnel_id")
        or prior.get("rollback_historical_tunnel_process_identity") != restarted_identity
        or prior.get("rollback_completed_at") != receipt.get("rolled_back_at")
    ):
        raise RuntimeError("completed rollback receipt is not reflected in the prior production state")
    if not isinstance(receipt.get("rolled_back_at"), (int, float)) or isinstance(
        receipt.get("rolled_back_at"), bool
    ):
        raise RuntimeError("completed rollback receipt has no completion time")

    historical_state_value = receipt.get("historical_runtime_state")
    if not isinstance(historical_state_value, str):
        raise RuntimeError("completed rollback receipt historical runtime state is invalid")
    historical_state_path = Path(historical_state_value).expanduser()
    if not historical_state_path.is_absolute():
        raise RuntimeError("completed rollback receipt historical runtime state path is invalid")
    historical_state_path = historical_state_path.resolve()
    _validate_private_source(historical_state_path, "completed rollback historical runtime state")
    historical_state = _read_json_object(historical_state_path)
    if any(
        historical_state.get(state_key) != restarted.get(receipt_key)
        for state_key, receipt_key in (
            ("tunnel_pid", "pid"),
            ("tunnel_id", "tunnel_id"),
            ("tunnel_profile_sha256", "profile_sha256"),
            ("tunnel_process_identity", "process_identity"),
        )
    ):
        raise RuntimeError("completed rollback is not reflected in historical runtime state")
    if not _process_identity_matches(restarted_identity):
        raise RuntimeError("completed rollback historical tunnel identity is no longer running")
    tunnel_id = restarted.get("tunnel_id")
    if not isinstance(tunnel_id, str) or receipt.get("tunnel_id") != tunnel_id:
        raise RuntimeError("completed rollback receipt historical tunnel id is invalid")
    if production.get("tunnel_id") != tunnel_id:
        raise RuntimeError("completed rollback receipt production tunnel id is invalid")
    _validate_receipt_tunnel_profile(
        restarted,
        expected_socket=HISTORICAL_M0_BRIDGE_SOCKET,
        label="completed rollback historical tunnel",
    )
    _require_exact_tunnel_id_owners(
        tunnel_id, {restarted_pid}, expected_socket=HISTORICAL_M0_BRIDGE_SOCKET
    )
    if _process_identity_matches(production_identity):
        raise RuntimeError("old production tunnel identity is still running")


def _preserved_activation_state(
    manifest: dict[str, Any],
    layout: dict[str, Path],
    prior: dict[str, Any],
    *,
    tunnel_ready: bool = False,
) -> dict[str, Any]:
    receipt_path_value = prior.get("activation_rollback_receipt")
    receipt_hash = prior.get("activation_rollback_receipt_sha256")
    is_release_upgrade = (
        prior.get("release_revision") != manifest["release_revision"]
        or prior.get("endpoint_generation") != manifest["endpoint_generation"]
    )
    if receipt_path_value is None and receipt_hash is None:
        if is_release_upgrade:
            if any(key in prior for key in _ACTIVE_PRODUCTION_TUNNEL_STATE_KEYS):
                # Same live UDS: keep tunnel metadata without rollback-tunnel.
                return {
                    key: value
                    for key, value in prior.items()
                    if key.startswith("tunnel_")
                }
            return {}
        return {
            key: value for key, value in prior.items() if key.startswith("tunnel_")
        } if tunnel_ready else {}
    if not isinstance(receipt_path_value, str) or not isinstance(receipt_hash, str):
        raise RuntimeError("armed rollback receipt pin is incomplete")
    canonical_receipt = layout["rollback_receipt"].expanduser().resolve()
    if Path(receipt_path_value).expanduser().resolve() != canonical_receipt:
        raise RuntimeError("armed rollback receipt path is not canonical")
    _validate_private_source(canonical_receipt, "armed rollback receipt")
    if _sha256_file(canonical_receipt) != receipt_hash:
        raise RuntimeError("armed rollback receipt hash does not match its runtime pin")
    receipt = _read_json_object(canonical_receipt)
    if is_release_upgrade:
        production = receipt.get("production")
        expected_socket = None
        if isinstance(production, dict):
            raw_socket = production.get("expected_socket")
            if isinstance(raw_socket, str) and raw_socket.strip():
                expected_socket = Path(raw_socket).expanduser().resolve()
        if (
            receipt.get("status") == "armed"
            and expected_socket == layout["bridge_socket"].expanduser().resolve()
            and receipt.get("tunnel_id") == prior.get("tunnel_id")
        ):
            # Same live UDS: cut over local runtime without rollback-tunnel.
            return {
                key: value
                for key, value in prior.items()
                if key.startswith("tunnel_")
            }
        _completed_rollback_upgrade_gate(manifest, layout, prior)
        return {}
    if any(
        receipt.get(key) != expected
        for key, expected in (
            ("schema_version", 1),
            ("status", "armed"),
            ("connector_id", CONNECTOR_ID),
            ("contract_version", CONTRACT_VERSION),
            ("release_revision", manifest["release_revision"]),
            ("endpoint_generation", manifest["endpoint_generation"]),
        )
    ):
        raise RuntimeError("armed rollback receipt does not match the production runtime")
    production = receipt.get("production")
    if not isinstance(production, dict) or receipt.get("tunnel_id") != production.get("tunnel_id"):
        raise RuntimeError("armed rollback receipt production tunnel is incomplete")
    _validate_receipt_tunnel_profile(
        production, expected_socket=layout["bridge_socket"], label="armed production tunnel"
    )
    for state_key, item_key in (
        ("tunnel_pid", "pid"),
        ("tunnel_id", "tunnel_id"),
        ("tunnel_profile_sha256", "profile_sha256"),
        ("tunnel_process_identity", "process_identity"),
    ):
        if prior.get(state_key) != production.get(item_key):
            raise RuntimeError("armed rollback receipt does not match production tunnel state")
    if not _process_identity_matches(production["process_identity"]):
        raise RuntimeError("armed production tunnel identity is no longer running")
    _require_exact_tunnel_id_owners(
        production["tunnel_id"], {production["pid"]}, expected_socket=layout["bridge_socket"]
    )
    return {
        **{key: value for key, value in prior.items() if key.startswith("tunnel_")},
        "activation_rollback_receipt": receipt_path_value,
        "activation_rollback_receipt_sha256": receipt_hash,
    }


def _start_local_runtime_unlocked(
    manifest: dict[str, Any], layout: dict[str, Path]
) -> dict[str, Any]:
    installed = validate_installed_release(layout)
    current = production_status(manifest, layout)
    if current["local_runtime_ready"]:
        return current
    prior = _read_json_object(layout["runtime_state"])
    is_release_upgrade = bool(
        prior
        and (
            prior.get("release_revision") != manifest["release_revision"]
            or prior.get("endpoint_generation") != manifest["endpoint_generation"]
        )
    )
    preserved_activation_state = _preserved_activation_state(
        manifest,
        layout,
        prior,
        tunnel_ready=current.get("tunnel_ready") is True,
    )
    if prior:
        if is_release_upgrade:
            _stop_prior_local_runtime_for_release_upgrade(prior, layout)
        else:
            _stop_verified_local_runtime(prior, layout)
    if _tcp_ready("127.0.0.1", PRODUCTION_CATDESK_PORT):
        raise RuntimeError("production CatDesk port is occupied by an unverified process")
    if _tcp_ready("127.0.0.1", PRODUCTION_HOST_MCP_PORT):
        raise RuntimeError("production Host MCP port is occupied by an unverified process")
    if _tcp_ready("127.0.0.1", PRODUCTION_PROCESS_RUNTIME_PORT):
        raise RuntimeError("production Process Runtime port is occupied by an unverified process")
    if layout["bridge_socket"].exists():
        raise RuntimeError("production Bridge socket exists without a verified runtime owner")
    if layout["runtime_hook_socket"].exists() or layout["runtime_hook_socket"].is_symlink():
        raise RuntimeError("production Runtime Host tool hook socket exists without a verified runtime owner")

    catdesk_child_pid_was_absent = not layout["catdesk_child_pid"].exists()
    _private_dir(layout["logs_dir"])
    _materialize_catdesk_runtime(layout)
    _prepare_private_unix_socket_path(layout["host_socket"])
    _prepare_private_unix_socket_path(layout["runtime_hook_socket"])
    _private_dir(layout["host_verifier_state"].parent)
    _private_dir(layout["bridge_state_dir"])
    slug = _catdesk_slug(layout["catdesk_config"])
    component_env = production_component_env(manifest, layout, catdesk_slug=slug)
    _require_paired_backend_env(
        component_env["bridge"],
        url_key="LAB_PROCESS_BACKEND_URL",
        key_file_key="LAB_PROCESS_BACKEND_KEY_FILE",
        label="process",
    )
    if "process_runtime" not in component_env:
        raise RuntimeError("production process-runtime env is missing")
    base_env = {
        "HOME": str(Path.home()),
        "PATH": os.environ.get("PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    started: list[tuple[subprocess.Popen[Any], dict[str, Any] | None, bool]] = []
    try:
        python = _kernel_python_executable()
        catdesk_command = [str(python), str(layout["catdesk_runner"])]
        catdesk = _spawn_logged(
            catdesk_command,
            layout["logs_dir"] / "catdesk-supervisor.out",
            layout["logs_dir"] / "catdesk-supervisor.err",
            cwd=layout["release_dir"],
            env=base_env,
        )
        started.append((catdesk, None, True))
        catdesk_identity = _capture_process_identity(
            catdesk.pid, expected_executable=python, expected_argv=catdesk_command
        )
        started[-1] = (catdesk, catdesk_identity, True)
        _wait_for_tcp(catdesk, PRODUCTION_CATDESK_PORT, "production CatDesk")

        dependencies = _validate_runtime_dependencies(installed)
        node = Path(dependencies["node"]["path"])
        host_adapter = _resolved_command_path(layout["host_mcp_adapter"])
        host_command = [str(node), str(host_adapter)]
        host = _spawn_logged(
            host_command,
            layout["logs_dir"] / "host.out",
            layout["logs_dir"] / "host.err",
            cwd=layout["host_dir"],
            env={**base_env, **component_env["host"]},
        )
        started.append((host, None, False))
        host_identity = _capture_process_identity(
            host.pid, expected_executable=node, expected_argv=host_command
        )
        started[-1] = (host, host_identity, False)
        _wait_for_tcp(host, PRODUCTION_HOST_MCP_PORT, "production Host MCP")

        runtime_python = Path(dependencies["python"]["path"])
        runtime_hook_command = [
            str(runtime_python),
            str(layout["runtime_hook_entrypoint"]),
            "--serve-unix-socket",
            str(layout["runtime_hook_socket"]),
        ]
        runtime_hook = _spawn_logged(
            runtime_hook_command,
            layout["logs_dir"] / "runtime-host-tool-hook.out",
            layout["logs_dir"] / "runtime-host-tool-hook.err",
            cwd=layout["runtime_hook_dir"],
            env=base_env,
        )
        started.append((runtime_hook, None, False))
        runtime_hook_identity = _capture_process_identity(
            runtime_hook.pid,
            expected_executable=runtime_python,
            expected_argv=runtime_hook_command,
        )
        started[-1] = (runtime_hook, runtime_hook_identity, False)
        runtime_hook_socket_identity = _wait_for_runtime_hook(runtime_hook, layout)

        _private_dir(layout["process_runtime_state_dir"])
        process_runtime_binary = layout["process_runtime_binary"].resolve(strict=True)
        process_runtime_command = [str(process_runtime_binary)]
        process_runtime = _spawn_logged(
            process_runtime_command,
            layout["logs_dir"] / "process-runtime.out",
            layout["logs_dir"] / "process-runtime.err",
            cwd=layout["release_dir"],
            env={**base_env, **component_env["process_runtime"]},
        )
        started.append((process_runtime, None, False))
        process_runtime_identity = _capture_process_identity(
            process_runtime.pid,
            expected_executable=process_runtime_binary,
            expected_argv=process_runtime_command,
        )
        started[-1] = (process_runtime, process_runtime_identity, False)
        _wait_for_tcp(
            process_runtime, PRODUCTION_PROCESS_RUNTIME_PORT, "production Process Runtime"
        )

        session_id = f"bridge-session-production-{uuid.uuid4()}"
        capability_id = f"bridge-cap-production-{uuid.uuid4()}"
        bridge_env = _bridge_env_with_live_host_receipt(
            {
                **base_env,
                **component_env["bridge"],
                "LAB_BRIDGE_EXECUTION_SESSION_ID": session_id,
                "LAB_BRIDGE_CAPABILITY_ID": capability_id,
            },
            layout,
        )
        bridge_binary = layout["bridge_binary"].resolve(strict=True)
        bridge_command = [str(bridge_binary)]
        bridge = _spawn_logged(
            bridge_command,
            layout["logs_dir"] / "bridge.out",
            layout["logs_dir"] / "bridge.err",
            cwd=Path(manifest["workspace"]),
            env=bridge_env,
        )
        started.append((bridge, None, False))
        bridge_identity = _capture_process_identity(
            bridge.pid, expected_executable=bridge_binary, expected_argv=bridge_command
        )
        started[-1] = (bridge, bridge_identity, False)
        tools = _wait_for_bridge(bridge, layout)
        validate_tool_surface(tools, installed.get("required_tools") or REQUIRED_TOOLS)

        state = {
            "schema_version": 1,
            "connector_id": CONNECTOR_ID,
            "contract_version": CONTRACT_VERSION,
            "release_revision": manifest["release_revision"],
            "endpoint_generation": manifest["endpoint_generation"],
            "workspace": manifest["workspace"],
            "tunnel_profile_name": TUNNEL_PROFILE_NAME,
            "release_manifest": str(layout["release_manifest"]),
            "catdesk_supervisor_pid": catdesk.pid,
            "host_pid": host.pid,
            "runtime_hook_pid": runtime_hook.pid,
            "process_runtime_pid": process_runtime.pid,
            "bridge_pid": bridge.pid,
            "catdesk_supervisor_process_identity": catdesk_identity,
            "host_process_identity": host_identity,
            "runtime_hook_process_identity": runtime_hook_identity,
            "runtime_hook_socket_identity": runtime_hook_socket_identity,
            "process_runtime_process_identity": process_runtime_identity,
            "bridge_process_identity": bridge_identity,
            "bridge_execution_session_id": session_id,
            "bridge_capability_id": capability_id,
            "bridge_capability_ttl_ms": manifest["bridge"]["capability_ttl_ms"],
            "bridge_capability_renew_before_ms": manifest["bridge"][
                "capability_renew_before_ms"
            ],
            "tools": tools,
            **preserved_activation_state,
        }
        _write_json_private(layout["runtime_state"], state)
        result = production_status(manifest, layout)
        if not result["local_runtime_ready"]:
            raise RuntimeError("production local runtime did not satisfy its post-start status contract")
        return result
    except Exception:
        all_started_processes_stopped = True
        for process, identity, process_group in reversed(started):
            try:
                if identity is not None:
                    _stop_verified_process_identity(
                        identity, process_group=process_group, process=process
                    )
                elif process.poll() is None:
                    if process_group:
                        os.killpg(process.pid, signal.SIGTERM)
                    else:
                        process.terminate()
                    process.wait(timeout=5)
            except Exception:
                all_started_processes_stopped = False
        if all_started_processes_stopped:
            try:
                _unlink_owned_runtime_socket(layout["bridge_socket"])
            except Exception:
                pass
            try:
                _unlink_runtime_hook_socket(
                    layout["runtime_hook_socket"],
                    locals().get("runtime_hook_socket_identity"),
                )
            except Exception:
                pass
            if catdesk_child_pid_was_absent:
                try:
                    _unlink_owned_runtime_pid_file(layout["catdesk_child_pid"])
                except Exception:
                    pass
        try:
            observed_state = _read_json_object(layout["runtime_state"])
            if "state" in locals() and observed_state == state:
                if is_release_upgrade:
                    _write_json_private(layout["runtime_state"], prior)
                elif preserved_activation_state:
                    _write_json_private(
                        layout["runtime_state"],
                        {
                            "schema_version": 1,
                            "connector_id": CONNECTOR_ID,
                            "contract_version": CONTRACT_VERSION,
                            "release_revision": manifest["release_revision"],
                            "endpoint_generation": manifest["endpoint_generation"],
                            "workspace": manifest["workspace"],
                            **preserved_activation_state,
                        },
                    )
                else:
                    layout["runtime_state"].unlink(missing_ok=True)
        except Exception:
            pass
        raise


def smoke_tools(layout: dict[str, Path]) -> list[str]:
    status, response = _call_mcp(layout, "tools/list", {})
    if status != 200 or "error" in response:
        raise RuntimeError(f"production Bridge tools/list failed with HTTP {status}")
    tools = ((response.get("result") or {}).get("tools") or [])
    names = sorted(
        tool.get("name") for tool in tools if isinstance(tool, dict) and isinstance(tool.get("name"), str)
    )
    validate_tool_surface(names)
    return names


def verify_caller_conversation_id_rejected(layout: dict[str, Path]) -> None:
    status, response = _call_mcp(
        layout,
        "tools/call",
        {
            "name": "discover_current_entry",
            "arguments": {
                "target_generation": 1,
                "ownership_generation": 1,
                "conversation_id": "caller-supplied-must-be-rejected",
            },
        },
        tool_name="discover_current_entry",
    )
    message = str(((response.get("error") or {}).get("message") or ""))
    if status != 400 or "conversation_id" not in message:
        raise RuntimeError("production Host did not reject caller-supplied conversation_id")


def _call_mcp(
    layout: dict[str, Path],
    method: str,
    params: dict[str, Any],
    *,
    tool_name: str = "",
) -> tuple[int, dict[str, Any]]:
    meta = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {
            "name": "local-agent-bridge-production-smoke",
            "version": "1",
        },
    }
    payload = {
        "jsonrpc": "2.0",
        "id": f"production-{uuid.uuid4().hex}",
        "method": method,
        "params": {**params, "_meta": meta},
    }
    command = [
        "curl",
        "--silent",
        "--show-error",
        "--unix-socket",
        str(layout["bridge_socket"]),
        "-H",
        "content-type: application/json",
        "-H",
        f"mcp-method: {method}",
        "-H",
        "mcp-protocol-version: 2026-07-28",
    ]
    if tool_name:
        command.extend(["-H", f"mcp-name: {tool_name}"])
    command.extend(
        [
            "--data",
            json.dumps(payload, separators=(",", ":")),
            "--write-out",
            "\n%{http_code}",
            "http://localhost/mcp",
        ]
    )
    completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=8)
    if completed.returncode != 0:
        raise RuntimeError(f"production Bridge MCP transport failed: {completed.stderr.strip()[:400]}")
    body, separator, raw_status = completed.stdout.rpartition("\n")
    if not separator or not raw_status.isdigit():
        raise RuntimeError("production Bridge MCP response omitted HTTP status")
    value = json.loads(body)
    if not isinstance(value, dict):
        raise RuntimeError("production Bridge MCP response must be a JSON object")
    return int(raw_status), value


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


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


def _process_is_running(pid: int) -> bool:
    if not _pid_alive(pid):
        return False
    try:
        state = _ps_process_field(pid, "state")
    except RuntimeError:
        # An existing process whose state cannot be proven is still treated as
        # running; callers must not infer safe termination from missing data.
        return True
    return not state.startswith("Z")


def _process_group_has_running_members(pgid: int) -> bool:
    completed = subprocess.run(
        ["ps", "-ww", "-axo", "pid=,pgid=,state="],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("cannot enumerate process group members")
    for raw in completed.stdout.splitlines():
        pieces = raw.strip().split()
        if len(pieces) != 3:
            raise RuntimeError("cannot parse process group member enumeration")
        try:
            observed_pid = int(pieces[0])
            observed_pgid = int(pieces[1])
        except ValueError:
            raise RuntimeError("cannot parse process group member enumeration")
        if observed_pid < 1 or observed_pgid < 1:
            raise RuntimeError("cannot parse process group member enumeration")
        if observed_pgid == pgid and not pieces[2].startswith("Z"):
            return True
    return False


def _pid_command(pid: int) -> str:
    if not _pid_alive(pid):
        return ""
    return shlex.join(_process_argv(pid))


def _parse_macos_procargs2(raw: bytes) -> list[str]:
    if len(raw) < struct.calcsize("=i"):
        raise RuntimeError("macOS process argv is truncated before argc")
    (argc,) = struct.unpack_from("=i", raw)
    if argc <= 0 or argc > 4096:
        raise RuntimeError("macOS process argv argc is invalid")
    offset = struct.calcsize("=i")
    executable_end = raw.find(b"\0", offset)
    if executable_end <= offset:
        raise RuntimeError("macOS process argv executable path is malformed or missing NUL")
    offset = executable_end + 1
    while offset < len(raw) and raw[offset] == 0:
        offset += 1
    argv: list[str] = []
    for index in range(argc):
        if offset >= len(raw):
            raise RuntimeError("macOS process argv is truncated before declared argc")
        end = raw.find(b"\0", offset)
        if end < 0:
            raise RuntimeError("macOS process argv entry is missing its NUL terminator")
        value = os.fsdecode(raw[offset:end])
        if index == 0 and not value:
            raise RuntimeError("macOS process argv[0] is empty")
        argv.append(value)
        offset = end + 1
    if len(argv) != argc:
        raise RuntimeError("macOS process argv does not match declared argc")
    return argv


def _macos_process_argv(pid: int, *, libc: Any | None = None) -> list[str]:
    library = libc if libc is not None else ctypes.CDLL(None, use_errno=True)
    try:
        sysctl_fn = library.sysctl
    except AttributeError as error:
        raise RuntimeError(f"cannot read process argv via macOS sysctl for PID {pid}") from error
    try:
        sysctl_fn.argtypes = [
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_size_t),
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        sysctl_fn.restype = ctypes.c_int
    except AttributeError:
        # Test doubles and some ctypes wrappers do not expose signature slots.
        pass
    mib = (ctypes.c_int * 3)(1, 49, pid)
    size = ctypes.c_size_t(0)
    if sysctl_fn(mib, 3, None, ctypes.byref(size), None, 0) != 0:
        raise RuntimeError(f"cannot size process argv via macOS sysctl for PID {pid}")
    if size.value < struct.calcsize("=i") + 2 or size.value > 16 * 1024 * 1024:
        raise RuntimeError(f"macOS sysctl returned an invalid argv size for PID {pid}")
    buffer = ctypes.create_string_buffer(size.value)
    actual_size = ctypes.c_size_t(size.value)
    if sysctl_fn(mib, 3, buffer, ctypes.byref(actual_size), None, 0) != 0:
        raise RuntimeError(f"cannot read process argv via macOS sysctl for PID {pid}")
    if actual_size.value > size.value:
        raise RuntimeError(f"macOS sysctl process argv grew during capture for PID {pid}")
    return _parse_macos_procargs2(bytes(buffer.raw[: actual_size.value]))


def _process_argv(pid: int) -> list[str]:
    if not isinstance(pid, int) or pid < 1:
        raise RuntimeError("process argv PID is invalid")
    if sys.platform == "darwin":
        return _macos_process_argv(pid)
    if sys.platform.startswith("linux"):
        path = Path(f"/proc/{pid}/cmdline")
        try:
            raw = path.read_bytes()
        except OSError as error:
            raise RuntimeError(f"cannot read process argv for PID {pid}") from error
        if not raw or not raw.endswith(b"\0"):
            raise RuntimeError(f"process argv for PID {pid} is empty or missing final NUL")
        values = raw[:-1].split(b"\0")
        if not values or not values[0]:
            raise RuntimeError(f"process argv[0] for PID {pid} is empty")
        return [os.fsdecode(value) for value in values]
    raise RuntimeError(f"cannot capture process argv for PID {pid} on this platform")


def _validate_trusted_python_binary(path: Path, label: str) -> Path:
    path = Path(path).expanduser()
    if not path.is_absolute():
        raise RuntimeError(f"{label} path is not absolute")
    try:
        info = path.lstat()
    except OSError as error:
        raise RuntimeError(f"{label} is unavailable") from error
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RuntimeError(f"{label} is an unsafe symlink or non-regular file")
    if info.st_uid not in {0, os.geteuid()}:
        raise RuntimeError(f"{label} is not owned by a trusted uid")
    if stat.S_IMODE(info.st_mode) & 0o022:
        raise RuntimeError(f"{label} is group/world writable and not trusted")
    if not os.access(path, os.X_OK):
        raise RuntimeError(f"{label} is not executable")
    return path


def _kernel_python_executable() -> Path:
    """Choose argv[0] that the kernel reports as the Python executable."""
    if sys.platform == "darwin":
        framework_python = (
            Path(sys.prefix)
            / "Resources"
            / "Python.app"
            / "Contents"
            / "MacOS"
            / "Python"
        )
        try:
            return _validate_trusted_python_binary(
                framework_python, "macOS Framework Python app binary"
            )
        except RuntimeError as framework_error:
            try:
                clt_python = MACOS_CLT_PYTHON_CURRENT.expanduser().resolve(strict=True)
                return _validate_trusted_python_binary(
                    clt_python, "macOS CLT Python app binary"
                )
            except (OSError, RuntimeError) as clt_error:
                raise RuntimeError(
                    "no trusted macOS Python executable is available: "
                    f"Framework candidate rejected ({framework_error}); "
                    f"CLT candidate rejected ({clt_error})"
                ) from clt_error

    raw_base = getattr(sys, "_base_executable", None)
    if not isinstance(raw_base, str) or not raw_base:
        raise RuntimeError("base Python executable is unavailable")
    try:
        base_python = Path(raw_base).expanduser().resolve(strict=True)
    except OSError as error:
        raise RuntimeError("base Python executable is unavailable") from error
    return _validate_trusted_python_binary(base_python, "base Python executable")


PROCESS_IDENTITY_KEYS = frozenset(
    {
        "pid",
        "uid",
        "lstart",
        "start_timestamp",
        "pgid",
        "exe_realpath",
        "exe_inode",
        "exe_sha256",
        "argv",
    }
)


def _validate_process_identity_record(identity: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(identity, dict) or not PROCESS_IDENTITY_KEYS.issubset(identity):
        raise RuntimeError("process identity is missing required fields")
    if not isinstance(identity["pid"], int) or identity["pid"] < 1:
        raise RuntimeError("process identity PID is invalid")
    if not isinstance(identity["uid"], int) or identity["uid"] < 0:
        raise RuntimeError("process identity UID is invalid")
    if not isinstance(identity["lstart"], str) or not identity["lstart"].strip():
        raise RuntimeError("process identity lstart is invalid")
    if not isinstance(identity["start_timestamp"], (int, float)) or isinstance(
        identity["start_timestamp"], bool
    ):
        raise RuntimeError("process identity start timestamp is invalid")
    if not isinstance(identity["pgid"], int) or identity["pgid"] < 1:
        raise RuntimeError("process identity PGID is invalid")
    path = identity["exe_realpath"]
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise RuntimeError("process identity executable path is invalid")
    if not isinstance(identity["exe_inode"], int) or identity["exe_inode"] < 1:
        raise RuntimeError("process identity executable inode is invalid")
    digest = identity["exe_sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise RuntimeError("process identity executable hash is invalid")
    argv = identity["argv"]
    if not isinstance(argv, list) or not argv or any(not isinstance(value, str) for value in argv):
        raise RuntimeError("process identity complete argv is invalid")
    return identity


def _ps_process_field(pid: int, field: str) -> str:
    completed = subprocess.run(
        ["ps", "-ww", "-p", str(pid), "-o", f"{field}="],
        capture_output=True,
        text=True,
        check=False,
    )
    value = completed.stdout.strip()
    if completed.returncode != 0 or not value or "\n" in value:
        raise RuntimeError(f"cannot capture process identity field {field} for PID {pid}")
    return value


def _process_executable_realpath(pid: int) -> Path:
    proc_exe = Path(f"/proc/{pid}/exe")
    if proc_exe.exists():
        try:
            return proc_exe.resolve(strict=True)
        except OSError as error:
            raise RuntimeError(f"cannot capture process executable for PID {pid}") from error
    if sys.platform == "darwin":
        try:
            library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
            proc_pidpath = library.proc_pidpath
            proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
            proc_pidpath.restype = ctypes.c_int
            buffer = ctypes.create_string_buffer(4096)
            length = proc_pidpath(pid, buffer, len(buffer))
            if length <= 0:
                raise OSError(ctypes.get_errno(), "proc_pidpath failed")
            return Path(os.fsdecode(buffer.value)).resolve(strict=True)
        except (AttributeError, OSError) as error:
            raise RuntimeError(f"cannot capture process executable for PID {pid}") from error
    raise RuntimeError(f"cannot capture process executable for PID {pid} on this platform")


def _capture_process_identity(
    pid: int,
    *,
    expected_executable: Path | None = None,
    expected_argv: Iterable[str] | None = None,
) -> dict[str, Any]:
    if not _pid_alive(pid):
        raise RuntimeError(f"cannot capture identity for unavailable process {pid}")
    try:
        uid = int(_ps_process_field(pid, "uid"))
        pgid = int(_ps_process_field(pid, "pgid"))
    except ValueError as error:
        raise RuntimeError(f"cannot capture numeric process identity for PID {pid}") from error
    lstart = _ps_process_field(pid, "lstart")
    try:
        start_timestamp = time.mktime(time.strptime(lstart, "%a %b %d %H:%M:%S %Y"))
    except ValueError as error:
        raise RuntimeError(f"cannot parse process lstart for PID {pid}") from error
    argv = _process_argv(pid)
    executable = _process_executable_realpath(pid)
    try:
        executable_info = executable.stat()
    except OSError as error:
        raise RuntimeError(f"cannot stat process executable for PID {pid}") from error
    if uid != os.geteuid() or os.getpgid(pid) != pgid:
        raise RuntimeError(f"process identity owner or PGID mismatch for PID {pid}")
    if Path(argv[0]).expanduser().resolve(strict=True) != executable:
        raise RuntimeError(f"process identity executable does not match complete argv for PID {pid}")
    if expected_executable is not None and Path(expected_executable).expanduser().resolve(strict=True) != executable:
        raise RuntimeError(f"process executable mismatch for PID {pid}")
    expected = list(expected_argv) if expected_argv is not None else None
    if expected is not None and argv != expected:
        raise RuntimeError(f"process complete argv mismatch for PID {pid}")
    return _validate_process_identity_record(
        {
            "pid": pid,
            "uid": uid,
            "lstart": lstart,
            "start_timestamp": start_timestamp,
            "pgid": pgid,
            "exe_realpath": str(executable),
            "exe_inode": executable_info.st_ino,
            "exe_sha256": _sha256_file(executable),
            "argv": argv,
        }
    )


def _process_identity_matches(identity: dict[str, Any]) -> bool:
    try:
        expected = _validate_process_identity_record(identity)
        observed = _capture_process_identity(
            expected["pid"],
            expected_executable=Path(expected["exe_realpath"]),
            expected_argv=expected["argv"],
        )
    except (OSError, RuntimeError):
        return False
    return observed == expected


def _unique_process_identity_for_executable(executable: Path, label: str) -> dict[str, Any]:
    expected = Path(executable).expanduser().resolve(strict=True)
    completed = subprocess.run(
        ["ps", "-ww", "-axo", "pid=,comm="],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"cannot enumerate {label} processes")
    matches: list[dict[str, Any]] = []
    for raw in completed.stdout.splitlines():
        fields = raw.strip().split(maxsplit=1)
        if len(fields) != 2:
            raise RuntimeError(f"cannot parse {label} process enumeration")
        try:
            pid = int(fields[0])
        except ValueError as error:
            raise RuntimeError(f"cannot parse {label} process PID") from error
        if pid < 1 or Path(fields[1]).expanduser().resolve(strict=False) != expected:
            continue
        matches.append(_capture_process_identity(pid, expected_executable=expected))
    if len(matches) != 1:
        raise RuntimeError(f"{label} executable must have exactly one live process")
    return matches[0]


def _unique_tunnel_child_identity(ai_bridge: dict[str, Any]) -> dict[str, Any]:
    parent = _validate_process_identity_record(ai_bridge)
    completed = subprocess.run(
        ["ps", "-ww", "-axo", "pid=,ppid=,comm="],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("cannot enumerate AI-Bridge child processes")
    matches: list[dict[str, Any]] = []
    for raw in completed.stdout.splitlines():
        fields = raw.strip().split(maxsplit=2)
        if len(fields) != 3:
            raise RuntimeError("cannot parse AI-Bridge child process enumeration")
        try:
            pid, parent_pid = int(fields[0]), int(fields[1])
        except ValueError as error:
            raise RuntimeError("cannot parse AI-Bridge child process PID") from error
        if pid < 1 or parent_pid != parent["pid"] or Path(fields[2]).name != "tunnel-client":
            continue
        matches.append(_capture_process_identity(pid))
    if len(matches) != 1:
        raise RuntimeError("AI-Bridge must have exactly one direct tunnel-client child")
    return matches[0]


def _unique_loopback_listener_identity(host: str, port: int, label: str) -> dict[str, Any]:
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise RuntimeError(f"{label} listener host is invalid") from error
    if not address.is_loopback or not isinstance(port, int) or port < 1 or port > 65535:
        raise RuntimeError(f"{label} listener must be an explicit loopback TCP port")
    authority = f"[{address}]" if address.version == 6 else str(address)
    completed = subprocess.run(
        ["lsof", "-nP", "-t", f"-iTCP@{authority}:{port}", "-sTCP:LISTEN"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"cannot resolve {label} listener PID")
    try:
        pids = {int(value.strip()) for value in completed.stdout.splitlines() if value.strip()}
    except ValueError as error:
        raise RuntimeError(f"cannot parse {label} listener PID") from error
    if len(pids) != 1 or any(pid < 1 for pid in pids):
        raise RuntimeError(f"{label} listener must have exactly one PID")
    return _capture_process_identity(next(iter(pids)))


def _trusted_macos_tmp_alias() -> bool:
    if sys.platform != "darwin":
        return False
    macos_tmp = Path("/tmp")
    macos_private_tmp = Path("/private/tmp")
    try:
        tmp_info = macos_tmp.lstat()
        private_tmp_info = macos_private_tmp.lstat()
        target = os.readlink(macos_tmp)
        resolved = macos_tmp.resolve(strict=True)
    except (FileNotFoundError, OSError):
        return False
    return (
        stat.S_ISLNK(tmp_info.st_mode)
        and tmp_info.st_uid == 0
        and target == "private/tmp"
        and resolved == macos_private_tmp
        and stat.S_ISDIR(private_tmp_info.st_mode)
        and private_tmp_info.st_uid == 0
        and stat.S_IMODE(private_tmp_info.st_mode) & stat.S_ISVTX
    )


def _verify_process_owned_unix_socket(
    identity: dict[str, Any], socket_path: Path, label: str
) -> None:
    expected = _validate_process_identity_record(identity)
    path = Path(socket_path).expanduser()
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
    except FileNotFoundError as error:
        raise RuntimeError(f"{label} Unix socket is missing") from error
    canonical_path = path
    macos_tmp = Path("/tmp")
    macos_private_tmp = Path("/private/tmp")
    if (
        path.is_absolute()
        and (path == macos_tmp or macos_tmp in path.parents)
        and _trusted_macos_tmp_alias()
    ):
        canonical_path = macos_private_tmp / path.relative_to(macos_tmp)
    if (
        not path.is_absolute()
        or path.is_symlink()
        or resolved != canonical_path
        or not stat.S_ISSOCK(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o022
    ):
        raise RuntimeError(f"{label} Unix socket is unsafe")
    completed = subprocess.run(
        ["lsof", "-nP", "-a", "-p", str(expected["pid"]), "-U", "-Fn"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"cannot resolve {label} Unix socket owner")
    names = {
        line[1:]
        for line in completed.stdout.splitlines()
        if line.startswith("n") and not line.startswith("n->")
    }
    if str(path) not in names or not _process_identity_matches(expected):
        raise RuntimeError(f"{label} Unix socket owner identity mismatch")


def _stop_verified_process_identity(
    identity: dict[str, Any],
    *,
    process_group: bool = False,
    process: subprocess.Popen[Any] | None = None,
) -> None:
    expected = _validate_process_identity_record(identity)
    pid = expected["pid"]
    if process is not None and process.pid != pid:
        raise RuntimeError("spawned process handle PID does not match verified identity")
    process_exited = process is not None and process.poll() is not None
    if process_exited:
        if not process_group:
            return
        if expected["pgid"] != pid:
            raise RuntimeError(f"refusing to stop process group {pid}: identity is not group leader")
        if not _process_group_has_running_members(pid):
            return
        raise RuntimeError(
            f"refusing to stop process group {pid}: leader exited while members remain; "
            "cannot exclude process-group ID reuse"
        )
    else:
        if not _process_identity_matches(expected):
            raise RuntimeError(f"refusing to stop process {pid} whose exact identity changed")
        if process_group:
            if expected["pgid"] != pid or os.getpgid(pid) != pid:
                raise RuntimeError(f"refusing to stop process group {pid}: identity is not group leader")
            os.killpg(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        running = (
            _process_group_has_running_members(pid)
            if process_group
            else _process_is_running(pid)
        )
        if not running:
            if process is not None and not process_exited:
                process.wait(timeout=5)
            return
        time.sleep(0.1)
    raise RuntimeError(f"verified process {pid} did not stop after SIGTERM")


def _tcp_ready(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.35):
            return True
    except OSError:
        return False


def _resolved_command_path(path: Path) -> Path:
    """Return the real path used as argv[0]/argv[1] so macOS /tmp vs /private/tmp matches.

    Host MCP's adapter only starts when process.argv[1] equals import.meta.url.
    Passing a /tmp path on macOS makes Node see /private/tmp and exit 0 with no logs.
    """
    try:
        return Path(path).expanduser().resolve()
    except OSError:
        return Path(path)


def _numeric_loopback_bind(bind: str) -> str:
    text = str(bind or "").strip()
    host, separator, port = text.rpartition(":")
    if separator != ":" or not host or not port.isdigit():
        raise RuntimeError("production Process Runtime bind must be numeric-loopback host:port")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError as error:
        raise RuntimeError("production Process Runtime bind host must be a numeric IP") from error
    if not ip.is_loopback:
        raise RuntimeError("production Process Runtime bind must be numeric loopback")
    return f"{ip}:{int(port)}"


def _require_paired_backend_env(
    env: dict[str, str],
    *,
    url_key: str,
    key_file_key: str,
    label: str,
) -> None:
    if not isinstance(env, dict):
        raise RuntimeError(f"production {label} env must be an object")
    url = str(env.get(url_key) or "").strip()
    key_file = str(env.get(key_file_key) or "").strip()
    if bool(url) != bool(key_file):
        raise RuntimeError(f"{url_key} and {key_file_key} must be configured together")
    if not url or not key_file:
        raise RuntimeError(f"production {label} backend must be configured")


def _catdesk_slug(config_path: Path) -> str:
    _validate_private_source(config_path, "production CatDesk config")
    with config_path.open("rb") as handle:
        slug = tomllib.load(handle).get("mcpSlug")
    if not isinstance(slug, str) or not slug.strip() or "/" in slug:
        raise RuntimeError("production CatDesk mcpSlug is missing or invalid")
    return slug.strip()


def _spawn_logged(
    command: list[str],
    stdout_path: Path,
    stderr_path: Path,
    *,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.Popen[Any]:
    _private_dir(stdout_path.parent)
    with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            cwd=cwd,
            env=env,
            start_new_session=True,
            close_fds=True,
        )
    stdout_path.chmod(0o600)
    stderr_path.chmod(0o600)
    return process


def _wait_for_tcp(process: subprocess.Popen[Any], port: int, label: str) -> None:
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError(f"{label} exited during startup")
        if _tcp_ready("127.0.0.1", port):
            return
        time.sleep(0.1)
    raise RuntimeError(f"{label} did not become ready")


_BRIDGE_HOST_RECEIPT_ENV_KEYS = (
    "LAB_HOST_ADAPTER_SOCKET",
    "LAB_RUNTIME_HOST_TOOL_HOOK_SOCKET",
)


def _host_adapter_socket_is_listening(path: Path) -> bool:
    """True only when native-host (or an equivalent owner) is accepting on host.sock."""
    socket_path = Path(path).expanduser()
    try:
        info = socket_path.lstat()
    except FileNotFoundError:
        return False
    if (
        socket_path.is_symlink()
        or not stat.S_ISSOCK(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.35)
            probe.connect(str(socket_path))
        return True
    except OSError:
        return False


def _bridge_env_with_live_host_receipt(
    env: dict[str, str], layout: dict[str, Path]
) -> dict[str, str]:
    """Inject Host receipt env into Bridge only when host.sock is actually listening.

    Protected run_command 502s with "private receipt socket inspect failed" if
    LAB_HOST_ADAPTER_SOCKET points at a missing socket. The runtime-hook socket
    cannot be injected without the Host adapter socket.
    """
    gated = dict(env)
    if _host_adapter_socket_is_listening(layout["host_socket"]):
        return gated
    for key in _BRIDGE_HOST_RECEIPT_ENV_KEYS:
        gated.pop(key, None)
    return gated


def _wait_for_bridge(process: subprocess.Popen[Any], layout: dict[str, Path]) -> list[str]:
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError("production Bridge exited during startup")
        if layout["bridge_socket"].exists():
            try:
                return smoke_tools(layout)
            except Exception:
                pass
        time.sleep(0.1)
    raise RuntimeError("production Bridge did not become ready")


def _runtime_hook_socket_identity(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except FileNotFoundError as error:
        raise RuntimeError("production Runtime Host tool hook socket is missing") from error
    if (
        path.is_symlink()
        or not stat.S_ISSOCK(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise RuntimeError("production Runtime Host tool hook socket is unsafe")
    parent = path.parent.lstat()
    if (
        path.parent.is_symlink()
        or not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.geteuid()
        or stat.S_IMODE(parent.st_mode) != 0o700
    ):
        raise RuntimeError("production Runtime Host tool hook socket parent is unsafe")
    return {
        "path": str(path),
        "uid": info.st_uid,
        "mode": stat.S_IMODE(info.st_mode),
        "device": info.st_dev,
        "inode": info.st_ino,
    }


def _runtime_hook_socket_is_live(path: Path, identity: object) -> bool:
    if not isinstance(identity, dict):
        return False
    try:
        observed = _runtime_hook_socket_identity(path)
        if observed != identity:
            return False
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.5)
            probe.connect(str(path))
        return True
    except (OSError, RuntimeError):
        return False


def _wait_for_runtime_hook(
    process: subprocess.Popen[Any], layout: dict[str, Path]
) -> dict[str, Any]:
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError("production Runtime Host tool hook exited during startup")
        identity = _runtime_hook_socket_identity_or_none(layout["runtime_hook_socket"])
        if identity is not None and _runtime_hook_socket_is_live(layout["runtime_hook_socket"], identity):
            return identity
        time.sleep(0.1)
    raise RuntimeError("production Runtime Host tool hook did not become ready")


def _runtime_hook_socket_identity_or_none(path: Path) -> dict[str, Any] | None:
    try:
        return _runtime_hook_socket_identity(path)
    except RuntimeError:
        return None


def _unlink_runtime_hook_socket(path: Path, identity: object) -> None:
    if not isinstance(identity, dict) or _runtime_hook_socket_identity(path) != identity:
        raise RuntimeError("refusing to remove changed production Runtime Host tool hook socket")
    path.unlink()


def _stop_verified_local_runtime(state: dict[str, Any], layout: dict[str, Path]) -> None:
    is_current_release_state = state.get("release_manifest") == str(layout["release_manifest"])
    if is_current_release_state:
        hook_pid = state.get("runtime_hook_pid")
        hook_identity = state.get("runtime_hook_process_identity")
        hook_socket_identity = state.get("runtime_hook_socket_identity")
        if (
            not isinstance(hook_pid, int)
            or not isinstance(hook_identity, dict)
            or hook_identity.get("pid") != hook_pid
            or not isinstance(hook_socket_identity, dict)
        ):
            raise RuntimeError(
                "cannot stop current production runtime without exact Runtime Host tool hook state"
            )
        _validate_process_identity_record(hook_identity)
    targets: list[tuple[dict[str, Any], bool]] = []
    for pid_key, identity_key, process_group in (
        ("bridge_pid", "bridge_process_identity", False),
        ("process_runtime_pid", "process_runtime_process_identity", False),
        ("runtime_hook_pid", "runtime_hook_process_identity", False),
        ("host_pid", "host_process_identity", False),
        ("catdesk_supervisor_pid", "catdesk_supervisor_process_identity", True),
    ):
        pid = state.get(pid_key)
        if not isinstance(pid, int):
            continue
        identity = state.get(identity_key)
        if not isinstance(identity, dict) or identity.get("pid") != pid:
            raise RuntimeError(f"cannot stop production runtime without exact {identity_key}")
        _validate_process_identity_record(identity)
        if not _process_identity_matches(identity):
            raise RuntimeError(f"cannot stop production runtime because {identity_key} changed")
        targets.append((identity, process_group))
    for identity, process_group in targets:
        _stop_verified_process_identity(identity, process_group=process_group)
    if layout["bridge_socket"].exists():
        info = layout["bridge_socket"].lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid():
            raise RuntimeError("refusing to remove unsafe production Bridge socket")
        layout["bridge_socket"].unlink()
    if layout["runtime_hook_socket"].exists() or layout["runtime_hook_socket"].is_symlink():
        _unlink_runtime_hook_socket(
            layout["runtime_hook_socket"], state.get("runtime_hook_socket_identity")
        )


def _stop_prior_local_runtime_for_release_upgrade(state: dict[str, Any], layout: dict[str, Path]) -> None:
    """Stop old live components, but allow a retry after a failed new start already stopped them."""
    live_state = dict(state)
    for pid_key, identity_key in (
        ("bridge_pid", "bridge_process_identity"),
        ("process_runtime_pid", "process_runtime_process_identity"),
        ("runtime_hook_pid", "runtime_hook_process_identity"),
        ("host_pid", "host_process_identity"),
        ("catdesk_supervisor_pid", "catdesk_supervisor_process_identity"),
    ):
        pid = state.get(pid_key)
        if not isinstance(pid, int):
            continue
        identity = state.get(identity_key)
        if not isinstance(identity, dict) or identity.get("pid") != pid:
            raise RuntimeError(f"cannot stop prior production runtime without exact {identity_key}")
        _validate_process_identity_record(identity)
        if _process_identity_matches(identity):
            continue
        if _process_is_running(pid):
            raise RuntimeError(f"cannot stop prior production runtime because {identity_key} changed")
        live_state.pop(pid_key, None)
        live_state.pop(identity_key, None)
    _stop_verified_local_runtime(live_state, layout)


def _materialize_catdesk_runtime(layout: dict[str, Path]) -> None:
    runtime_generation_dir = layout["runtime_generation_dir"]
    _private_dir(runtime_generation_dir.parent.parent)
    _private_dir(runtime_generation_dir.parent)
    _private_dir(runtime_generation_dir)
    for source_key, destination_key in (
        ("release_catdesk_config", "catdesk_config"),
        ("release_catdesk_backend_key", "catdesk_backend_key"),
    ):
        source = layout[source_key]
        destination = layout[destination_key]
        _validate_private_source(source, f"immutable production {source_key}")
        try:
            value = source.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise RuntimeError(f"immutable production {source_key} is not readable UTF-8") from error
        _write_text_private(destination, value, 0o600)
        if _sha256_file(destination) != _sha256_file(source):
            raise RuntimeError(f"materialized production {destination_key} hash mismatch")


def _prepare_private_unix_socket_path(path: Path) -> None:
    path = Path(path).expanduser()
    if not path.is_absolute():
        raise RuntimeError("production Host socket path is not absolute")
    maximum_bytes = 103 if sys.platform == "darwin" else 107
    if len(os.fsencode(str(path))) > maximum_bytes:
        raise RuntimeError("production Host socket path exceeds the platform AF_UNIX limit")
    _private_dir(path.parent)


def _unlink_owned_runtime_socket(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if (
        not stat.S_ISSOCK(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise RuntimeError("refusing to remove unsafe production Bridge socket")
    path.unlink()


def _unlink_owned_runtime_pid_file(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise RuntimeError("refusing to remove unsafe production CatDesk child PID file")
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise RuntimeError("production CatDesk child PID file is invalid") from error
    if pid < 1:
        raise RuntimeError("production CatDesk child PID file is invalid")
    path.unlink()


def _validate_manifest_contract(manifest: dict[str, Any]) -> None:
    _validate_manifest_identity(manifest)
    bridge = manifest.get("bridge")
    ttl_ms = bridge.get("capability_ttl_ms") if isinstance(bridge, dict) else None
    renew_before_ms = (
        bridge.get("capability_renew_before_ms") if isinstance(bridge, dict) else None
    )
    if (
        isinstance(ttl_ms, bool)
        or not isinstance(ttl_ms, int)
        or ttl_ms != PRODUCTION_BRIDGE_CAPABILITY_TTL_MS
        or isinstance(renew_before_ms, bool)
        or not isinstance(renew_before_ms, int)
        or renew_before_ms != PRODUCTION_BRIDGE_CAPABILITY_RENEW_BEFORE_MS
        or renew_before_ms >= ttl_ms
    ):
        raise RuntimeError(
            "production Bridge capability renewal policy is invalid, unbounded, or noncanonical"
        )
    dependencies = _validate_runtime_dependencies(manifest)
    required_tools = manifest.get("required_tools")
    if not isinstance(required_tools, list) or not all(
        isinstance(value, str) and value for value in required_tools
    ):
        raise RuntimeError("production required_tools is invalid")
    feishu_admin_enabled = manifest.get("feishu_admin_enabled", False)
    if not isinstance(feishu_admin_enabled, bool):
        raise RuntimeError("production Feishu admin flag must be boolean")
    if feishu_admin_enabled and "feishu_cli" in dependencies:
        raise RuntimeError("Feishu setup release and pinned Feishu runtime are mutually exclusive")
    expected_tools = set(REQUIRED_TOOLS)
    if feishu_admin_enabled:
        expected_tools.update(FEISHU_ADMIN_TOOLS)
    if "feishu_cli" in dependencies:
        expected_tools.update(FEISHU_REQUIRED_TOOLS)
    if set(required_tools) != expected_tools:
        raise RuntimeError("production required_tools does not match runtime dependencies")


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if path.is_symlink() or not path.is_dir() or info.st_uid != os.geteuid():
        raise RuntimeError(f"private production path is unsafe: {path}")
    path.chmod(0o700)


@contextmanager
def _production_install_lock(runtime_root: Path):
    """Serialize release publication and rollback under one private lock."""
    root = Path(runtime_root)
    _private_dir(root)
    lock_path = root / ".install.lock"
    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock_path, flags, 0o600)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise RuntimeError("production install lock is unsafe")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def install_native_messaging_manifests(
    layout: dict[str, Path],
    manifest: dict[str, Any],
    *,
    home_dir: Path = Path.home(),
    native_user_data_dirs: Iterable[Path] = (),
    browser_executables: Iterable[Path] = (),
) -> dict[str, Path]:
    with _production_install_lock(layout["runtime_root"]):
        return _install_native_messaging_manifests_unlocked(
            layout,
            manifest,
            home_dir=home_dir,
            native_user_data_dirs=native_user_data_dirs,
            browser_executables=browser_executables,
        )


def rollback_production_activation(
    manifest: dict[str, Any],
    layout: dict[str, Path],
    *,
    receipt_path: Path | None = None,
) -> dict[str, Any]:
    with _production_install_lock(layout["runtime_root"]):
        return _rollback_production_activation_unlocked(
            manifest, layout, receipt_path=receipt_path
        )


def activate_production_tunnel(
    manifest: dict[str, Any],
    layout: dict[str, Path],
    *,
    historical_profile_dir: Path,
    historical_profile_name: str,
    historical_runtime_state: Path = HISTORICAL_M0_RUNTIME_STATE,
    home_dir: Path = Path.home(),
    native_user_data_dirs: Iterable[Path] = (),
    tunnel_client: Path | None = None,
    fallback_health_url_file: Path = AI_BRIDGE_FALLBACK_HEALTH_URL_FILE,
) -> dict[str, Any]:
    with _production_install_lock(layout["runtime_root"]):
        return _activate_production_tunnel_unlocked(
            manifest,
            layout,
            historical_profile_dir=historical_profile_dir,
            historical_profile_name=historical_profile_name,
            historical_runtime_state=historical_runtime_state,
            home_dir=home_dir,
            native_user_data_dirs=native_user_data_dirs,
            tunnel_client=tunnel_client,
            fallback_health_url_file=fallback_health_url_file,
        )


def start_local_runtime(
    manifest: dict[str, Any], layout: dict[str, Path]
) -> dict[str, Any]:
    with _production_install_lock(layout["runtime_root"]):
        return _start_local_runtime_unlocked(manifest, layout)


def _validate_private_source(path: Path, label: str) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as error:
        raise RuntimeError(f"{label} is missing: {path}") from error
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RuntimeError(f"{label} must be a regular non-symlink file")
    if info.st_uid != os.geteuid():
        raise RuntimeError(f"{label} owner mismatch")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise RuntimeError(f"{label} permissions must be 0600 or stricter")


def _read_private_bytes_if_present(path: Path, label: str) -> bytes | None:
    """Read an owned private regular file without following a replacement symlink."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise RuntimeError(f"{label} cannot be opened safely") from error
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise RuntimeError(f"{label} must be an owned private regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _private_file_snapshot(path: Path, label: str) -> tuple[bytes, int] | None:
    payload = _read_private_bytes_if_present(path, label)
    if payload is None:
        return None
    info = path.lstat()
    if path.is_symlink() or not path.is_file() or info.st_uid != os.geteuid():
        raise RuntimeError(f"{label} changed while taking a publication snapshot")
    return payload, stat.S_IMODE(info.st_mode)


def _restore_private_file_snapshot(
    path: Path,
    snapshot: tuple[bytes, int] | None,
    *,
    private_parent: bool,
) -> None:
    if snapshot is None:
        current = _read_private_bytes_if_present(path, "publication rollback target")
        if current is None:
            return
        path.unlink()
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_flags |= getattr(os, "O_CLOEXEC", 0)
        directory_flags |= getattr(os, "O_NOFOLLOW", 0)
        directory_fd = os.open(path.parent, directory_flags)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return
    payload, mode = snapshot
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RuntimeError("publication rollback snapshot is not UTF-8") from error
    _write_text_private(
        path,
        text,
        mode,
        private_parent=private_parent,
    )


def _validate_owned_file_source(source: Path, label: str) -> None:
    source = Path(source)
    try:
        info = source.lstat()
    except FileNotFoundError as error:
        raise RuntimeError(f"{label} is missing: {source}") from error
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RuntimeError(f"{label} must be a regular non-symlink file: {source}")
    if info.st_uid != os.geteuid():
        raise RuntimeError(f"{label} owner mismatch: {source}")


def _copy_owned_file(source: Path, destination: Path, mode: int) -> None:
    source = Path(source)
    _validate_owned_file_source(source, "release source")
    _private_dir(destination.parent)
    shutil.copyfile(source, destination)
    destination.chmod(mode)


def _validate_owned_tree_source(source: Path, label: str) -> None:
    source = Path(source)
    try:
        source_info = source.lstat()
    except FileNotFoundError as error:
        raise RuntimeError(f"{label} is missing: {source}") from error
    if (
        stat.S_ISLNK(source_info.st_mode)
        or not stat.S_ISDIR(source_info.st_mode)
        or source_info.st_uid != os.geteuid()
    ):
        raise RuntimeError(f"{label} is unsafe: {source}")
    try:
        items = list(source.rglob("*"))
    except OSError as error:
        raise RuntimeError(f"{label} cannot be enumerated: {source}") from error
    for item in items:
        try:
            info = item.lstat()
        except FileNotFoundError as error:
            raise RuntimeError(f"{label} changed during preflight: {item}") from error
        if (
            stat.S_ISLNK(info.st_mode)
            or (not stat.S_ISDIR(info.st_mode) and not stat.S_ISREG(info.st_mode))
            or info.st_uid != os.geteuid()
        ):
            raise RuntimeError(f"{label} member is unsafe: {item}")


def _copy_owned_tree(source: Path, destination: Path) -> None:
    source = Path(source)
    _validate_owned_tree_source(source, "release source tree")
    shutil.copytree(source, destination)
    for item in destination.rglob("*"):
        if item.is_dir():
            item.chmod(0o700)
        elif item.is_file():
            item.chmod(0o700 if os.access(item, os.X_OK) else 0o600)


def _write_json_private(
    path: Path, value: Any, *, private_parent: bool = True
) -> None:
    _write_text_private(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        0o600,
        private_parent=private_parent,
    )


def _json_private_sha256(value: Any) -> str:
    encoded = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_text_private(
    path: Path, value: str, mode: int, *, private_parent: bool = True
) -> None:
    path = Path(path)
    if private_parent:
        _private_dir(path.parent)
    else:
        parent = path.parent
        parent_info = parent.lstat()
        if parent.is_symlink() or not parent.is_dir() or parent_info.st_uid != os.geteuid():
            raise RuntimeError(f"private write parent is unsafe: {parent}")
    try:
        existing = path.lstat()
    except FileNotFoundError:
        existing = None
    if existing is not None and (
        stat.S_ISLNK(existing.st_mode)
        or not stat.S_ISREG(existing.st_mode)
        or existing.st_uid != os.geteuid()
    ):
        raise RuntimeError(f"private write target is unsafe: {path}")

    temp = path.parent / f".{path.name}.{secrets.token_hex(12)}.tmp"
    open_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    open_flags |= getattr(os, "O_CLOEXEC", 0)
    open_flags |= getattr(os, "O_NOFOLLOW", 0)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_CLOEXEC", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    directory_fd: int | None = None
    temp_fd: int | None = None
    handle: Any | None = None
    replaced = False
    try:
        directory_fd = os.open(path.parent, directory_flags)
        temp_fd = os.open(temp, open_flags, mode)
        try:
            handle = os.fdopen(temp_fd, "w", encoding="utf-8", newline="")
            temp_fd = None
        except Exception:
            os.close(temp_fd)
            temp_fd = None
            raise
        written = handle.write(value)
        if written != len(value):
            raise OSError("private temporary file write was incomplete")
        handle.flush()
        os.fsync(handle.fileno())
        os.fchmod(handle.fileno(), mode)
        # Persist the exact permission metadata as well as the payload before rename.
        os.fsync(handle.fileno())
        handle.close()
        handle = None
        os.replace(temp, path)
        replaced = True
        try:
            os.fsync(directory_fd)
        except OSError as error:
            raise RuntimeError(
                "private write replaced target but directory fsync failed; persistence state is unknown"
            ) from error
    finally:
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
        if temp_fd is not None:
            try:
                os.close(temp_fd)
            except OSError:
                pass
        if not replaced:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass
        if directory_fd is not None:
            try:
                os.close(directory_fd)
            except OSError:
                pass


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _runtime_host_tool_hook_source_bytes(relative: str) -> bytes:
    if relative not in RUNTIME_HOST_TOOL_HOOK_BUNDLE_RELATIVE_PATHS:
        raise RuntimeError("Runtime Host tool hook source path is not part of the pinned bundle")
    root = RUNTIME_HOST_TOOL_HOOK_SOURCE_ROOT.expanduser()
    if not root.is_absolute() or not (root / ".git").exists():
        raise RuntimeError("pinned Runtime Host tool hook source repository is unavailable")
    revision = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", f"{RUNTIME_HOST_TOOL_HOOK_RUNTIME_REVISION}^{{commit}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if revision.returncode != 0 or revision.stdout.strip() != RUNTIME_HOST_TOOL_HOOK_RUNTIME_REVISION:
        raise RuntimeError("pinned Runtime Host tool hook revision is unavailable")
    candidate = f"{RUNTIME_HOST_TOOL_HOOK_RUNTIME_REVISION}:{relative}"
    completed = subprocess.run(
        ["git", "-C", str(root), "show", candidate],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout:
        raise RuntimeError("pinned Runtime Host tool hook source is unavailable")
    return completed.stdout


def _runtime_host_tool_hook_bundle_paths(layout: dict[str, Path]) -> tuple[Path, ...]:
    return tuple(layout["runtime_hook_dir"] / relative for relative in RUNTIME_HOST_TOOL_HOOK_BUNDLE_RELATIVE_PATHS)


def _materialize_runtime_host_tool_hook(layout: dict[str, Path]) -> None:
    entrypoint = layout["runtime_hook_entrypoint"]
    if entrypoint not in _runtime_host_tool_hook_bundle_paths(layout):
        raise RuntimeError("Runtime Host tool hook entrypoint is outside its pinned bundle")
    for relative, destination in zip(
        RUNTIME_HOST_TOOL_HOOK_BUNDLE_RELATIVE_PATHS,
        _runtime_host_tool_hook_bundle_paths(layout),
        strict=True,
    ):
        payload = _runtime_host_tool_hook_source_bytes(relative)
        _write_text_private(
            destination,
            payload.decode("utf-8"),
            0o700 if destination == entrypoint else 0o600,
        )


def _validate_runtime_host_tool_hook_release_files(layout: dict[str, Path]) -> None:
    release_dir = layout["release_dir"].resolve(strict=True)
    bundle = _runtime_host_tool_hook_bundle_paths(layout)
    if len(bundle) != len(set(bundle)) or layout["runtime_hook_entrypoint"] not in bundle:
        raise RuntimeError("production Runtime Host tool hook bundle is malformed")
    for path in bundle:
        try:
            resolved = path.resolve(strict=True)
            info = path.lstat()
        except FileNotFoundError as error:
            raise RuntimeError(f"production Runtime Host tool hook artifact is missing: {path}") from error
        if (
            path.is_symlink()
            or not path.is_file()
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or not resolved.is_relative_to(release_dir)
        ):
            raise RuntimeError(f"production Runtime Host tool hook artifact is unsafe: {path}")
    if not os.access(layout["runtime_hook_entrypoint"], os.X_OK):
        raise RuntimeError("production Runtime Host tool hook entrypoint is not executable")


def _runtime_host_tool_hook_record(layout: dict[str, Path], installed: dict[str, Any]) -> dict[str, Any]:
    _validate_runtime_host_tool_hook_release_files(layout)
    artifacts = installed.get("artifacts_sha256")
    if not isinstance(artifacts, dict):
        raise RuntimeError("production release manifest has no Runtime Host tool hook hashes")
    bundle_sha256: dict[str, str] = {}
    for path in _runtime_host_tool_hook_bundle_paths(layout):
        relative = str(path.relative_to(layout["release_dir"]))
        digest = _sha256_file(path)
        if artifacts.get(relative) != digest:
            raise RuntimeError("Runtime Host tool hook artifact is not pinned to the immutable release")
        bundle_sha256[str(path.resolve(strict=True))] = digest
    runtime = (installed.get("runtime_dependencies") or {}).get("python")
    if not isinstance(runtime, dict):
        raise RuntimeError("production Runtime Host tool hook Python pin is missing")
    return {
        "protocol": RUNTIME_HOST_TOOL_HOOK_PROTOCOL,
        "runtime_revision": RUNTIME_HOST_TOOL_HOOK_RUNTIME_REVISION,
        "executable": str(layout["runtime_hook_entrypoint"].resolve(strict=True)),
        "sha256": _sha256_file(layout["runtime_hook_entrypoint"]),
        "bundle_sha256": bundle_sha256,
        "python": dict(runtime),
        "ai_bridge_is_trusted_receipt_source": False,
    }


def _node_runtime_dependency(node_executable: Path | None = None) -> dict[str, str]:
    raw = str(node_executable) if node_executable is not None else shutil.which("node")
    if not raw:
        raise RuntimeError("Node.js is required for the production release")
    try:
        path = Path(raw).expanduser().resolve(strict=True)
    except FileNotFoundError as error:
        raise RuntimeError("production Node.js executable is unavailable") from error
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise RuntimeError("production Node.js executable must resolve to a regular absolute file")
    return {"path": str(path), "sha256": _sha256_file(path)}


def _python_runtime_dependency(python_executable: Path | None = None) -> dict[str, Any]:
    path = _validate_trusted_python_binary(
        python_executable or _kernel_python_executable(), "Runtime Host tool hook Python executable"
    ).resolve(strict=True)
    info = path.stat()
    return {"path": str(path), "inode": info.st_ino, "sha256": _sha256_file(path)}


def _feishu_cli_runtime_dependency(
    feishu_cli_executable: Path | None,
    feishu_cli_version: str | None,
) -> dict[str, Any] | None:
    if feishu_cli_executable is None:
        if feishu_cli_version is not None:
            raise RuntimeError("Feishu CLI version requires an executable pin")
        return None
    version = str(feishu_cli_version or "").strip()
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None:
        raise RuntimeError("Feishu CLI version must be an exact semantic version")
    raw = Path(feishu_cli_executable).expanduser()
    if not raw.is_absolute():
        raise RuntimeError("Feishu CLI executable path must be absolute")
    try:
        path = raw.resolve(strict=True)
        info = path.stat()
    except FileNotFoundError as error:
        raise RuntimeError("Feishu CLI executable is unavailable") from error
    if (
        not path.is_file()
        or info.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(info.st_mode) & 0o022
        or not os.access(path, os.X_OK)
    ):
        raise RuntimeError("Feishu CLI executable is not a trusted executable file")
    return {
        "path": str(path),
        "inode": info.st_ino,
        "sha256": _sha256_file(path),
        "version": version,
    }


def _validate_runtime_dependencies(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    dependencies = manifest.get("runtime_dependencies")
    node = dependencies.get("node") if isinstance(dependencies, dict) else None
    if not isinstance(node, dict):
        raise RuntimeError("production release manifest is missing the Node.js runtime dependency")
    raw_path = node.get("path")
    expected_sha256 = node.get("sha256")
    if not isinstance(raw_path, str) or not Path(raw_path).is_absolute():
        raise RuntimeError("production Node.js runtime path is not absolute")
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        raise RuntimeError("production Node.js runtime hash is invalid")
    path = Path(raw_path)
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as error:
        raise RuntimeError("production Node.js runtime is unavailable") from error
    if resolved != path or path.is_symlink() or not path.is_file():
        raise RuntimeError("production Node.js runtime path is not immutable and canonical")
    if _sha256_file(path) != expected_sha256:
        raise RuntimeError("production Node.js runtime hash mismatch")
    python = dependencies.get("python") if isinstance(dependencies, dict) else None
    if not isinstance(python, dict):
        raise RuntimeError("production release manifest is missing the Runtime Host tool hook Python dependency")
    python_path = python.get("path")
    python_inode = python.get("inode")
    python_digest = python.get("sha256")
    if not isinstance(python_path, str) or not Path(python_path).is_absolute():
        raise RuntimeError("production Runtime Host tool hook Python path is not absolute")
    if not isinstance(python_inode, int) or python_inode < 1 or not isinstance(python_digest, str) or len(python_digest) != 64:
        raise RuntimeError("production Runtime Host tool hook Python pin is invalid")
    python_file = Path(python_path)
    try:
        python_resolved = python_file.resolve(strict=True)
        python_info = python_file.lstat()
    except FileNotFoundError as error:
        raise RuntimeError("production Runtime Host tool hook Python is unavailable") from error
    if (
        python_resolved != python_file
        or python_file.is_symlink()
        or not python_file.is_file()
        or python_info.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(python_info.st_mode) & 0o022
        or python_info.st_ino != python_inode
        or not os.access(python_file, os.X_OK)
        or _sha256_file(python_file) != python_digest
    ):
        raise RuntimeError("production Runtime Host tool hook Python pin mismatch")
    validated = {
        "node": {"path": raw_path, "sha256": expected_sha256},
        "python": {"path": python_path, "inode": python_inode, "sha256": python_digest},
    }
    feishu = dependencies.get("feishu_cli") if isinstance(dependencies, dict) else None
    if feishu is not None:
        if not isinstance(feishu, dict):
            raise RuntimeError("production Feishu CLI dependency must be an object")
        feishu_path = feishu.get("path")
        feishu_inode = feishu.get("inode")
        feishu_digest = feishu.get("sha256")
        feishu_version = feishu.get("version")
        if (
            not isinstance(feishu_path, str)
            or not Path(feishu_path).is_absolute()
            or not isinstance(feishu_inode, int)
            or feishu_inode < 1
            or not isinstance(feishu_digest, str)
            or len(feishu_digest) != 64
            or not isinstance(feishu_version, str)
            or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", feishu_version) is None
        ):
            raise RuntimeError("production Feishu CLI pin is invalid")
        feishu_file = Path(feishu_path)
        try:
            feishu_resolved = feishu_file.resolve(strict=True)
            feishu_info = feishu_file.lstat()
        except FileNotFoundError as error:
            raise RuntimeError("production Feishu CLI is unavailable") from error
        if (
            feishu_resolved != feishu_file
            or feishu_file.is_symlink()
            or not feishu_file.is_file()
            or feishu_info.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(feishu_info.st_mode) & 0o022
            or feishu_info.st_ino != feishu_inode
            or not os.access(feishu_file, os.X_OK)
            or _sha256_file(feishu_file) != feishu_digest
        ):
            raise RuntimeError("production Feishu CLI pin or hash mismatch")
        validated["feishu_cli"] = {
            "path": feishu_path,
            "inode": feishu_inode,
            "sha256": feishu_digest,
            "version": feishu_version,
        }
    return validated


def _catdesk_runner_source(manifest: dict[str, Any], layout: dict[str, Path]) -> str:
    return f"""#!/usr/bin/env python3
import fcntl, os, pty, select, struct, subprocess, termios, time
from pathlib import Path
binary = Path({str(layout['catdesk_binary'])!r})
home = Path({str(layout['catdesk_home'])!r})
workspace = Path({str(manifest['workspace'])!r})
log_path = Path({str(layout['logs_dir'] / 'catdesk.log')!r})
pid_path = Path({str(layout['catdesk_child_pid'])!r})
log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
master, slave = pty.openpty()
fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 120, 0, 0))
env = os.environ.copy()
env.update({{'HOME': str(home), 'WORKSPACE_ROOT': str(workspace), 'PORT': {str(PRODUCTION_CATDESK_PORT)!r}, 'CATDESK_SKIP_MACOS_TERMINAL_PROFILE': '1', 'TERM': 'xterm-256color', 'CATDESK_LOCAL_BACKEND_KEY_FILE': str(home / '.catdesk/backend.key'), 'CATDESK_REQUIRE_LOCAL_BACKEND_AUTH': '1'}})
with log_path.open('ab', buffering=0) as log:
    proc = subprocess.Popen([str(binary)], stdin=slave, stdout=slave, stderr=slave, cwd=str(workspace), env=env)
    pid_path.write_text(str(proc.pid), encoding='utf-8')
    pid_path.chmod(0o600)
    os.close(slave)
    started = time.time(); next_send = started + 3.0
    while proc.poll() is None:
        now = time.time()
        if now >= next_send and now - started < 25:
            os.write(master, b'1'); next_send = now + 1.0
        readable, _, _ = select.select([master], [], [], 0.2)
        if readable:
            try: log.write(os.read(master, 65536))
            except OSError: break
try: os.close(master)
except OSError: pass
raise SystemExit(proc.wait())
"""


def validate_workspace(path: Path) -> Path:
    raw = str(path.expanduser())
    if any(marker in raw for marker in LEGACY_CANDIDATE_MARKERS):
        raise RuntimeError("production workspace points at legacy candidate workspace")
    try:
        resolved = path.expanduser().resolve(strict=True)
    except FileNotFoundError as error:
        raise RuntimeError(f"production workspace does not exist: {path}") from error
    if not resolved.is_dir():
        raise RuntimeError(f"production workspace is not a directory: {resolved}")
    resolved_text = str(resolved)
    if any(marker in resolved_text for marker in LEGACY_CANDIDATE_MARKERS):
        raise RuntimeError("production workspace resolves into legacy candidate workspace")
    return resolved


def validate_tool_surface(
    tools: Iterable[str], required_tools: Iterable[str] | None = None
) -> set[str]:
    observed = {str(value) for value in tools if isinstance(value, str)}
    required = {
        str(value)
        for value in (required_tools if required_tools is not None else REQUIRED_TOOLS)
        if isinstance(value, str)
    }
    missing = sorted(required.difference(observed))
    if missing:
        raise RuntimeError(
            "stale or incomplete production tool surface; missing required tools: "
            + ", ".join(missing)
        )
    return observed


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = value.split(".")
    if not parts or any(not part.isdigit() for part in parts):
        raise RuntimeError(f"invalid numeric version: {value}")
    return tuple(int(part) for part in parts)


def _release_revision(value: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 40 or any(ch not in "0123456789abcdef" for ch in text):
        raise RuntimeError("production release revision must be a full 40-character Git SHA")
    return text


def _endpoint_generation(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RuntimeError("production endpoint generation must be a positive integer")
    return value


def release_manifest(
    workspace: Path | None = None,
    *,
    release_revision: str,
    endpoint_generation: int,
    node_executable: Path | None = None,
    feishu_cli_executable: Path | None = None,
    feishu_cli_version: str | None = None,
    feishu_admin_enabled: bool = False,
) -> dict[str, Any]:
    root = validate_workspace(workspace or canonical_workspace())
    revision = _release_revision(release_revision)
    generation = _endpoint_generation(endpoint_generation)
    runtime_dependencies: dict[str, Any] = {
        "node": _node_runtime_dependency(node_executable),
        "python": _python_runtime_dependency(),
    }
    if not isinstance(feishu_admin_enabled, bool):
        raise RuntimeError("Feishu admin flag must be boolean")
    required_tools = set(REQUIRED_TOOLS)
    feishu_cli = _feishu_cli_runtime_dependency(
        feishu_cli_executable, feishu_cli_version
    )
    if feishu_admin_enabled and feishu_cli is not None:
        raise RuntimeError("Feishu setup release and pinned Feishu runtime are mutually exclusive")
    if feishu_admin_enabled:
        required_tools.update(FEISHU_ADMIN_TOOLS)
    if feishu_cli is not None:
        runtime_dependencies["feishu_cli"] = feishu_cli
        required_tools.update(FEISHU_REQUIRED_TOOLS)
    return {
        "schema_version": 1,
        "connector_id": CONNECTOR_ID,
        "connector_display_name": CONNECTOR_DISPLAY_NAME,
        "lifecycle": "production",
        "contract_version": CONTRACT_VERSION,
        "release_revision": revision,
        "endpoint_generation": generation,
        "fail_closed": True,
        "silent_legacy_fallback_allowed": False,
        "feishu_admin_enabled": feishu_admin_enabled,
        "historical_connectors_forbidden": list(HISTORICAL_CONNECTOR_NAMES),
        "workspace": str(root),
        "required_tools": sorted(required_tools),
        "runtime_dependencies": runtime_dependencies,
        "tunnel": {
            "profile_name": TUNNEL_PROFILE_NAME,
            "legacy_profile_names_forbidden": list(HISTORICAL_TUNNEL_PROFILE_NAMES),
        },
        "bridge": {
            "contract_version": CONTRACT_VERSION,
            "release_revision": revision,
            "endpoint_generation": generation,
            "capability_ttl_ms": PRODUCTION_BRIDGE_CAPABILITY_TTL_MS,
            "capability_renew_before_ms": PRODUCTION_BRIDGE_CAPABILITY_RENEW_BEFORE_MS,
        },
        "host": {
            "tool": "discover_current_entry",
            "accepts_caller_conversation_id": False,
            "entry_scope": "runtime_invocation",
            "machine_source": "host_invocation_context_v1",
            "provenance": "runtime_host_current_entry_v1",
            "secure_origin": "https://chatgpt.com",
            "extension_id": HOST_EXTENSION_ID,
            "minimum_extension_version": MIN_HOST_EXTENSION_VERSION,
            "contract_version": CONTRACT_VERSION,
            "release_revision": revision,
            "endpoint_generation": generation,
        },
    }


def validate_release_handshake(manifest: dict[str, Any], observed: dict[str, Any]) -> None:
    if manifest.get("connector_id") != CONNECTOR_ID:
        raise RuntimeError("release manifest connector identity mismatch")
    if manifest.get("contract_version") != CONTRACT_VERSION or not manifest.get("fail_closed"):
        raise RuntimeError("release manifest is not the canonical fail-closed production contract")
    expected_revision = _release_revision(str(manifest.get("release_revision") or ""))
    expected_generation = _endpoint_generation(manifest.get("endpoint_generation"))

    if observed.get("connector_id") != manifest["connector_id"]:
        raise RuntimeError("production connector identity mismatch")
    if observed.get("contract_version") != manifest["contract_version"]:
        raise RuntimeError("production connector contract version mismatch; stale surface rejected")
    if observed.get("release_revision") != expected_revision:
        raise RuntimeError("production connector release revision mismatch")
    if observed.get("endpoint_generation") != expected_generation:
        raise RuntimeError("production connector endpoint generation mismatch")

    expected_workspace = validate_workspace(Path(manifest["workspace"]))
    actual_workspace = validate_workspace(Path(str(observed.get("workspace") or "")))
    if actual_workspace != expected_workspace:
        raise RuntimeError(
            f"production connector workspace mismatch: expected {expected_workspace}, got {actual_workspace}"
        )

    validate_tool_surface(
        observed.get("tools") or [], manifest.get("required_tools") or REQUIRED_TOOLS
    )

    tunnel = manifest["tunnel"]
    if observed.get("tunnel_profile_name") != tunnel["profile_name"]:
        raise RuntimeError("production tunnel profile mismatch; historical candidate rejected")
    if observed.get("tunnel_endpoint_generation") != expected_generation:
        raise RuntimeError("production tunnel endpoint generation mismatch")

    bridge = manifest["bridge"]
    if observed.get("bridge_release_revision") != bridge["release_revision"]:
        raise RuntimeError("production Bridge release revision mismatch")
    if observed.get("bridge_contract_version") != bridge["contract_version"]:
        raise RuntimeError("production Bridge contract version mismatch")
    if observed.get("bridge_endpoint_generation") != bridge["endpoint_generation"]:
        raise RuntimeError("production Bridge endpoint generation mismatch")

    host = manifest["host"]
    if observed.get("host_extension_id") != host["extension_id"]:
        raise RuntimeError("production Host extension identity mismatch")
    actual_version = str(observed.get("host_extension_version") or "")
    if _version_tuple(actual_version) < _version_tuple(host["minimum_extension_version"]):
        raise RuntimeError("production Host extension version is stale")
    if observed.get("host_native_manifest_release") != host["release_revision"]:
        raise RuntimeError("production Native Messaging manifest release mismatch")
    if observed.get("host_contract_version") != host["contract_version"]:
        raise RuntimeError("production Host contract version mismatch")
    if observed.get("host_endpoint_generation") != host["endpoint_generation"]:
        raise RuntimeError("production Host endpoint generation mismatch")


def _require_clean_build_repo(repo_root: Path) -> None:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("cannot inspect full production build repository status")
    for line in completed.stdout.splitlines():
        if len(line) < 4:
            raise RuntimeError("production build repository status is not parseable")
        status_code = line[:2]
        path = line[3:]
        if status_code != "??":
            raise RuntimeError(
                "refusing to build a production release from tracked dirty changes"
            )
        allowed = (
            path.startswith("scripts/__pycache__/")
            or path.startswith("tests/__pycache__/")
            or path in ALLOWED_BUILD_UNTRACKED
        )
        if not allowed:
            raise RuntimeError(
                f"refusing to build with unknown untracked input: {path}"
            )


def _export_git_snapshot(repo_root: Path, revision: str, destination: Path) -> Path:
    revision = _release_revision(revision)
    destination = Path(destination)
    if destination.exists():
        raise RuntimeError("Git snapshot destination already exists")
    destination.mkdir(parents=True, mode=0o700)
    archive_path = destination.parent / f".{destination.name}.{secrets.token_hex(12)}.tar"
    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "archive",
                "--format=tar",
                f"--output={archive_path}",
                revision,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError("cannot export exact production Git revision")
        archive_path.chmod(0o600)
        with tarfile.open(archive_path, mode="r:") as archive:
            archive.extractall(destination, filter="data")
    finally:
        if archive_path.exists():
            archive_path.unlink()
    return destination


def install_production_release(
    manifest: dict[str, Any],
    layout: dict[str, Path],
    *,
    build_repo: Path,
    sources: dict[str, Path] | None = None,
) -> dict[str, Path]:
    """Build a pinned Bridge then copy all components into one immutable bundle."""
    repo = Path(build_repo).expanduser().resolve()
    require_exact_git_head(str(manifest["release_revision"]), repo)
    _require_clean_build_repo(repo)
    with tempfile.TemporaryDirectory(prefix="lab-production-source-") as raw_snapshot_parent:
        snapshot = Path(raw_snapshot_parent) / "source"
        _export_git_snapshot(repo, str(manifest["release_revision"]), snapshot)
        completed = subprocess.run(
            ["cargo", "build", "--release"],
            cwd=snapshot / "crates" / "local-tool-bridge",
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"production Bridge release build failed: {completed.stderr.strip()[-600:]}")
        process_build = subprocess.run(
            ["cargo", "build", "--release"],
            cwd=snapshot / "crates" / "process-runtime",
            capture_output=True,
            text=True,
            check=False,
        )
        if process_build.returncode != 0:
            raise RuntimeError(
                "production Process Runtime release build failed: "
                + process_build.stderr.strip()[-600:]
            )
        resolved_sources = dict(sources or default_release_sources(snapshot))
        resolved_sources["bridge_binary"] = snapshot / "crates" / "local-tool-bridge" / "target" / "release" / "local-tool-bridge"
        resolved_sources["process_runtime_binary"] = (
            snapshot / "crates" / "process-runtime" / "target" / "release" / "process-runtime"
        )
        resolved_sources["host_source_dir"] = snapshot / "experiments" / "web-host-attestation"
        return install_release_bundle(manifest, layout, resolved_sources)


def _rollback_request_manifest(release_revision: str, endpoint_generation: int) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "connector_id": CONNECTOR_ID,
        "contract_version": CONTRACT_VERSION,
        "release_revision": _release_revision(release_revision),
        "endpoint_generation": _endpoint_generation(endpoint_generation),
        "fail_closed": True,
        "workspace": str(canonical_workspace()),
        "tunnel": {"profile_name": TUNNEL_PROFILE_NAME},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local Agent Bridge production connector contract")
    parser.add_argument(
        "action",
        choices=("manifest", "validate-handshake", "install", "start-local", "status", "smoke", "install-native", "activate-tunnel", "adopt-current-rollback", "rollback-tunnel"),
    )
    parser.add_argument("--release-revision", required=True)
    parser.add_argument("--endpoint-generation", required=True, type=int)
    parser.add_argument("--observed", type=Path)
    parser.add_argument("--runtime-root", type=Path, default=PRODUCTION_RUNTIME_ROOT)
    parser.add_argument("--bridge-socket", type=Path, default=PRODUCTION_BRIDGE_SOCKET)
    parser.add_argument("--build-repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--historical-profile-dir", type=Path)
    parser.add_argument("--historical-profile-name")
    parser.add_argument("--historical-runtime-state", type=Path, default=HISTORICAL_M0_RUNTIME_STATE)
    parser.add_argument("--tunnel-client", type=Path)
    parser.add_argument("--native-user-data-dir", action="append", type=Path, default=[])
    parser.add_argument("--browser-executable", action="append", type=Path, default=[])
    parser.add_argument("--fallback-health-url-file", type=Path, default=AI_BRIDGE_FALLBACK_HEALTH_URL_FILE)
    parser.add_argument("--rollback-receipt", type=Path)
    parser.add_argument(
        "--feishu-admin",
        action="store_true",
        help="build a Feishu Setup Release with ProviderAdmin tools only",
    )
    parser.add_argument(
        "--feishu-cli",
        type=Path,
        help="pin an installed Feishu lark-cli executable into the immutable release",
    )
    parser.add_argument(
        "--feishu-cli-version",
        help="exact lark-cli semantic version paired with --feishu-cli",
    )
    args = parser.parse_args(argv)

    manifest = (
        _rollback_request_manifest(args.release_revision, args.endpoint_generation)
        if args.action == "rollback-tunnel"
        else release_manifest(
            release_revision=args.release_revision,
            endpoint_generation=args.endpoint_generation,
            feishu_admin_enabled=args.feishu_admin,
            feishu_cli_executable=args.feishu_cli,
            feishu_cli_version=args.feishu_cli_version,
        )
    )
    if args.action == "manifest":
        print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.action == "validate-handshake":
        if args.observed is None:
            raise SystemExit("--observed is required for validate-handshake")
        observed = json.loads(args.observed.read_text(encoding="utf-8"))
        validate_release_handshake(manifest, observed)
        print(json.dumps({
            "ok": True,
            "connector_id": CONNECTOR_ID,
            "contract_version": CONTRACT_VERSION,
            "release_revision": manifest["release_revision"],
            "endpoint_generation": manifest["endpoint_generation"],
        }))
        return 0

    layout = production_layout(
        manifest, runtime_root=args.runtime_root.expanduser(), bridge_socket=args.bridge_socket.expanduser()
    )
    if args.action == "install":
        result: dict[str, Any] = {str(key): str(value) for key, value in install_production_release(
            manifest, layout, build_repo=args.build_repo
        ).items()}
    elif args.action == "start-local":
        result = start_local_runtime(manifest, layout)
    elif args.action == "status":
        result = production_status(manifest, layout)
    elif args.action == "smoke":
        result = {"ok": True, "tools": smoke_tools(layout)}
    elif args.action == "install-native":
        validate_installed_release(layout)
        result = {key: str(value) for key, value in install_native_messaging_manifests(
            layout,
            manifest,
            native_user_data_dirs=args.native_user_data_dir,
            browser_executables=args.browser_executable,
        ).items()}
    elif args.action == "adopt-current-rollback":
        result = adopt_current_production_rollback(
            manifest,
            layout,
            native_user_data_dirs=args.native_user_data_dir,
            fallback_health_url_file=args.fallback_health_url_file,
        )
    elif args.action == "rollback-tunnel":
        result = rollback_production_activation(
            manifest,
            layout,
            receipt_path=args.rollback_receipt,
        )
    else:
        if args.historical_profile_dir is None or not args.historical_profile_name:
            raise SystemExit("--historical-profile-dir and --historical-profile-name are required for activate-tunnel; do not guess the live M0 profile")
        result = activate_production_tunnel(
            manifest,
            layout,
            historical_profile_dir=args.historical_profile_dir,
            historical_profile_name=args.historical_profile_name,
            historical_runtime_state=args.historical_runtime_state,
            native_user_data_dirs=args.native_user_data_dir,
            tunnel_client=args.tunnel_client,
            fallback_health_url_file=args.fallback_health_url_file,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if args.action == "status":
        return 0 if (
            result.get("local_runtime_ready") is True
            and result.get("tunnel_ready") is True
        ) else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
