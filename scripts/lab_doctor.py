#!/usr/bin/env python3
"""Layered, read-only health diagnostics for Local Agent Bridge."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
M0_PREVIEW_PATH = PROJECT_ROOT / "scripts" / "m0_preview.py"

HEALTHY = "HEALTHY"
DEGRADED = "DEGRADED"
BROKEN = "BROKEN"
UNINSTRUMENTED = "UNINSTRUMENTED"

M0_REQUIRED_LAYERS = (
    "backend",
    "bridge_process",
    "tunnel",
    "mcp",
    "auth_session",
    "tool_registry",
)

FULL_LAYER_ORDER = (
    "backend",
    "bridge_process",
    "tunnel",
    "connector",
    "mcp",
    "auth_session",
    "tool_registry",
    "workspace",
    "execution",
    "jobs",
    "browser",
    "computer_runtime",
    "web_executor",
    "runtime_adapter",
)


def _layer(
    state: str,
    detail: str,
    *,
    repair_state: str = "none",
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "state": state,
        "detail": detail,
        "repair_state": repair_state,
        "evidence": evidence or {},
    }


def classify_overall(
    layers: dict[str, dict[str, Any]], required_layers: Iterable[str]
) -> str:
    states = [
        str(layers.get(name, {}).get("state") or UNINSTRUMENTED)
        for name in required_layers
    ]
    if any(state == BROKEN for state in states):
        return BROKEN
    if any(state in {DEGRADED, UNINSTRUMENTED} for state in states):
        return DEGRADED
    return HEALTHY


def _m0_contract() -> tuple[set[str], int]:
    spec = importlib.util.spec_from_file_location(
        "lab_m0_preview_contract", M0_PREVIEW_PATH
    )
    if spec is None or spec.loader is None:
        return set(), 0
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return (
        set(getattr(module, "M0_REQUIRED_TOOLS", ())),
        int(getattr(module, "MIN_REMAINING_MS", 0)),
    )


def project_m0_layers(status: dict[str, Any]) -> dict[str, dict[str, Any]]:
    bridge_ready = status.get("bridge_ready") is True
    backend_ready = status.get("backend_ready") is True
    tunnel_ready = status.get("tunnel_ready") is True
    tunnel_state_source = status.get("tunnel_state_source")
    tunnel_generation = status.get("tunnel_endpoint_generation")
    tunnel_canonical = (
        tunnel_ready
        and tunnel_state_source == "canonical_runtime_meta"
        and isinstance(tunnel_generation, int)
        and not isinstance(tunnel_generation, bool)
        and tunnel_generation >= 1
    )
    lease_remaining_ms = status.get("lease_remaining_ms")
    tools = status.get("tools")
    tools = (
        [value for value in tools if isinstance(value, str)]
        if isinstance(tools, list)
        else []
    )
    required_tools, min_remaining_ms = _m0_contract()
    missing_tools = sorted(required_tools.difference(tools))
    mcp_ready = bridge_ready and not missing_tools and bool(tools)
    lease_ready = (
        bridge_ready
        and isinstance(lease_remaining_ms, int)
        and not isinstance(lease_remaining_ms, bool)
        and lease_remaining_ms >= min_remaining_ms
    )
    jobs_present = {"start_command", "poll_command", "cancel_command"}.issubset(tools)

    return {
        "backend": _layer(
            HEALTHY if backend_ready else BROKEN,
            "hardened local backend reachable"
            if backend_ready
            else "hardened local backend is not reachable",
            repair_state="restart_backend_candidate" if not backend_ready else "none",
            evidence={"backend_ready": backend_ready},
        ),
        "bridge_process": _layer(
            HEALTHY if bridge_ready else BROKEN,
            "Local Tool Bridge process/socket/smoke are healthy"
            if bridge_ready
            else "Local Tool Bridge process/socket/smoke failed",
            repair_state="restart_bridge_safe_candidate" if not bridge_ready else "none",
            evidence={
                "bridge_ready": bridge_ready,
                "bridge_pid": status.get("bridge_pid"),
                "socket": status.get("socket"),
            },
        ),
        "tunnel": _layer(
            HEALTHY if tunnel_canonical else (DEGRADED if tunnel_ready else BROKEN),
            (
                "tunnel-client is healthy and bound to canonical runtime metadata"
                if tunnel_canonical
                else "tunnel-client is healthy but runtime metadata still needs canonical adoption"
                if tunnel_ready
                else "tunnel-client process is not healthy"
            ),
            repair_state=(
                "none"
                if tunnel_canonical
                else "adopt_tunnel_runtime_state_candidate"
                if tunnel_ready
                else "restart_tunnel_safe_candidate"
            ),
            evidence={
                "tunnel_ready": tunnel_ready,
                "tunnel_pid": status.get("tunnel_pid"),
                "tunnel_id": status.get("tunnel_id"),
                "endpoint_generation": tunnel_generation,
                "state_source": tunnel_state_source,
            },
        ),
        "connector": _layer(
            UNINSTRUMENTED,
            "ChatGPT connector binding/endpoint generation is not yet machine-observable by LAB",
            repair_state="connector_probe_required",
        ),
        "mcp": _layer(
            HEALTHY if mcp_ready else BROKEN,
            "MCP discovery/smoke matches the M0 surface"
            if mcp_ready
            else "MCP discovery/smoke is incomplete",
            repair_state="rediscover_after_bridge_recovery" if not mcp_ready else "none",
            evidence={
                "tool_count": len(tools),
                "missing_required_tools": missing_tools,
            },
        ),
        "auth_session": _layer(
            HEALTHY if lease_ready else BROKEN,
            "execution-session capability lease is current"
            if lease_ready
            else "execution-session capability lease is missing, stale, or below the safe renewal window",
            repair_state="rotate_execution_session_candidate" if not lease_ready else "none",
            evidence={"lease_remaining_ms": lease_remaining_ms},
        ),
        "tool_registry": _layer(
            HEALTHY if mcp_ready else DEGRADED,
            "current capability/tool surface is discoverable"
            if mcp_ready
            else "tool registry must be rediscovered after transport recovery",
            repair_state="rediscover_capabilities" if not mcp_ready else "none",
            evidence={"tools": sorted(tools)},
        ),
        "workspace": _layer(
            DEGRADED if backend_ready and bridge_ready else BROKEN,
            "workspace isolation exists, but doctor lacks a dedicated workspace probe"
            if backend_ready and bridge_ready
            else "workspace path cannot be trusted while backend/bridge health is broken",
            repair_state="workspace_probe_required",
        ),
        "execution": _layer(
            UNINSTRUMENTED,
            "command execution semantics are not yet aggregated into doctor",
            repair_state="execution_probe_required",
        ),
        "jobs": _layer(
            DEGRADED if jobs_present else BROKEN,
            "job lifecycle tools are exposed; active recovery health is not yet probed"
            if jobs_present
            else "job lifecycle tool surface is incomplete",
            repair_state="job_probe_required",
        ),
        "browser": _layer(
            UNINSTRUMENTED,
            "browser adapter health is outside the M0 preview surface",
            repair_state="browser_probe_required",
        ),
        "computer_runtime": _layer(
            UNINSTRUMENTED,
            "Computer Runtime health is separately permission-gated",
            repair_state="computer_probe_required",
        ),
        "web_executor": _layer(
            UNINSTRUMENTED,
            "Web ChatGPT Executor canonical Runtime integration is not yet complete",
            repair_state="wex_probe_required",
        ),
        "runtime_adapter": _layer(
            UNINSTRUMENTED,
            "Adaptive Agent Runtime adapter is independent of the M0 product path",
            repair_state="runtime_adapter_probe_required",
        ),
    }


def collect_m0_status() -> dict[str, Any]:
    spec = importlib.util.spec_from_file_location("lab_m0_preview_doctor", M0_PREVIEW_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load M0 preview status provider")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.status()
    if not isinstance(result, dict):
        raise RuntimeError("M0 preview status provider returned a non-object")
    return result


def build_report(status: dict[str, Any], *, scope: str) -> dict[str, Any]:
    layers = project_m0_layers(status)
    required = M0_REQUIRED_LAYERS if scope == "m0" else FULL_LAYER_ORDER
    return {
        "schema_version": 1,
        "operation": "lab_doctor",
        "scope": scope,
        "overall": classify_overall(layers, required),
        "repair_mode": "diagnostic_only",
        "silent_fallback_allowed": False,
        "layers": {name: layers[name] for name in FULL_LAYER_ORDER},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Layered Local Agent Bridge diagnostics")
    parser.add_argument("--scope", choices=("m0", "full"), default="full")
    parser.add_argument(
        "--status-json",
        help="read an existing M0 status JSON instead of probing live state",
    )
    args = parser.parse_args(argv)
    status = (
        json.loads(Path(args.status_json).read_text(encoding="utf-8"))
        if args.status_json
        else collect_m0_status()
    )
    report = build_report(status, scope=args.scope)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["overall"] == HEALTHY else 2


if __name__ == "__main__":
    raise SystemExit(main())
