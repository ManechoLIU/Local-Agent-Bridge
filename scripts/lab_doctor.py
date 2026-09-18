#!/usr/bin/env python3
"""Layered, read-only health diagnostics for Local Agent Bridge."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import re
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
M0_PREVIEW_PATH = PROJECT_ROOT / "scripts" / "m0_preview.py"

HEALTHY = "HEALTHY"
DEGRADED = "DEGRADED"
BROKEN = "BROKEN"
UNINSTRUMENTED = "UNINSTRUMENTED"

EXPOSURE_CONFIRMED = "CONFIRMED"
EXPOSURE_ABSENT_REPAIRABLE = "ABSENT_REPAIRABLE"
EXPOSURE_UNAVAILABLE = "UNAVAILABLE"
EXPOSURE_UNKNOWN = "UNKNOWN"

MANAGED_WEB_EXPOSURE_SOURCES = {
    "web_chatgpt_executor",
    "managed_web_executor",
    "local_web_executor_adapter",
}
WEB_EXECUTION_ID_RE = re.compile(
    r"^wex_[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
WEB_EXECUTION_EXPOSURE_MAX_AGE_SECONDS = 300
WEB_EXECUTION_EXPOSURE_MAX_FUTURE_SKEW_SECONDS = 30

FEISHU_ADMIN_TOOLS = frozenset(
    {
        "feishu_setup_start",
        "feishu_setup_poll",
        "feishu_auth_begin",
        "feishu_auth_complete",
    }
)
FEISHU_BUSINESS_TOOLS = frozenset(
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
    "collaboration",
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


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def parse_web_tool_exposure(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    source = str(value.get("source") or "").strip()
    if source not in MANAGED_WEB_EXPOSURE_SOURCES:
        return None
    claimed_state = str(value.get("state") or EXPOSURE_UNKNOWN).upper()
    if claimed_state not in {
        EXPOSURE_CONFIRMED,
        EXPOSURE_ABSENT_REPAIRABLE,
        EXPOSURE_UNAVAILABLE,
        EXPOSURE_UNKNOWN,
    }:
        claimed_state = EXPOSURE_UNKNOWN
    required_apps = _string_list(value.get("required_apps", value.get("required_app")))
    observed_apps = _string_list(value.get("observed_apps"))
    required_keys = {item.strip().casefold() for item in required_apps if item.strip()}
    observed_keys = {item.strip().casefold() for item in observed_apps if item.strip()}
    exact_current_proof = bool(required_keys) and required_keys.issubset(observed_keys)
    if claimed_state == EXPOSURE_CONFIRMED and not exact_current_proof:
        state = EXPOSURE_ABSENT_REPAIRABLE if required_keys else EXPOSURE_UNKNOWN
    else:
        state = claimed_state
    attempts = value.get("attempts")
    attempts_n = (
        attempts
        if isinstance(attempts, int) and not isinstance(attempts, bool)
        else 0
    )
    return {
        "source": source,
        "state": state,
        "required_apps": required_apps,
        "confirmed": state == EXPOSURE_CONFIRMED and exact_current_proof,
        "attempts": attempts_n,
        "observed_apps": observed_apps,
        "action": value.get("action"),
    }



def _fresh_execution_timestamp(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        observed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    if observed.tzinfo is None:
        return False
    age = (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()
    return (
        -WEB_EXECUTION_EXPOSURE_MAX_FUTURE_SKEW_SECONDS
        <= age
        <= WEB_EXECUTION_EXPOSURE_MAX_AGE_SECONDS
    )

def load_web_tool_exposure_from_executor_state(
    state_dir: str | Path, execution_id: str
) -> dict[str, Any] | None:
    execution_id = str(execution_id or "").strip()
    if WEB_EXECUTION_ID_RE.fullmatch(execution_id) is None:
        return None
    executions = Path(state_dir) / "executions"
    if not executions.is_dir():
        return None
    path = executions / f"{execution_id}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if str(data.get("execution_id") or "").strip() != execution_id:
        return None
    # Per-message exposure is current only while this explicitly correlated
    # executor execution is live. Historical/crashed files are audit evidence.
    if str(data.get("status") or "").strip().lower() != "running":
        return None
    if data.get("terminal_at") not in (None, ""):
        return None
    if not _fresh_execution_timestamp(data.get("updated_at")):
        return None
    exposure = data.get("tool_exposure")
    if not isinstance(exposure, dict):
        return None
    return parse_web_tool_exposure(
        {
            **exposure,
            "source": str(data.get("provenance") or ""),
        }
    )


def unknown_web_tool_exposure() -> dict[str, Any]:
    return {
        "source": None,
        "state": EXPOSURE_UNKNOWN,
        "required_apps": [],
        "confirmed": False,
        "attempts": 0,
        "observed_apps": [],
        "action": None,
    }


def resolve_web_tool_exposure(status: dict[str, Any]) -> dict[str, Any]:
    # Per-message Web app exposure is current only when it is correlated to the
    # explicitly current managed Web execution. A naked status claim is audit
    # input only and must never promote connector health.
    state_dir = status.get("web_executor_state_dir")
    current_execution_id = str(status.get("web_executor_current_execution_id") or "").strip()
    if isinstance(state_dir, str) and state_dir.strip() and current_execution_id:
        loaded = load_web_tool_exposure_from_executor_state(
            state_dir, current_execution_id
        )
        if loaded is not None:
            return loaded
    return unknown_web_tool_exposure()


def project_connector_layer(exposure: dict[str, Any]) -> dict[str, Any]:
    source = exposure.get("source")
    state = str(exposure.get("state") or EXPOSURE_UNKNOWN)
    evidence = {"web_tool_exposure": exposure}
    if not source or state == EXPOSURE_UNKNOWN:
        return _layer(
            UNINSTRUMENTED,
            "ChatGPT connector/tool exposure is not machine-observed by a managed Web executor",
            repair_state="connector_probe_required",
            evidence=evidence,
        )
    if state == EXPOSURE_CONFIRMED:
        return _layer(
            HEALTHY,
            "managed Web executor confirmed required app exposure for the observed turn",
            repair_state="none",
            evidence=evidence,
        )
    if state == EXPOSURE_ABSENT_REPAIRABLE:
        return _layer(
            DEGRADED,
            "required Web tool app is absent on the current turn and can be reselected; backend/tunnel health is separate",
            repair_state="reselect_web_app_exposure",
            evidence=evidence,
        )
    return _layer(
        DEGRADED,
        "required Web tool app exposure is unavailable; backend/tunnel health is reported separately",
        repair_state="reselect_web_app_exposure",
        evidence=evidence,
    )


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


def project_feishu_layer(status: dict[str, Any], tools: Iterable[str]) -> dict[str, Any]:
    observed = {value for value in tools if isinstance(value, str)}
    admin = observed.intersection(FEISHU_ADMIN_TOOLS)
    business = observed.intersection(FEISHU_BUSINESS_TOOLS)

    if admin and business:
        return _layer(
            BROKEN,
            "Feishu setup/admin and pinned business tools are mixed in one release",
            repair_state="deploy_single_feishu_release_mode",
            evidence={
                "mode": "MIXED",
                "admin_tools": sorted(admin),
                "business_tools": sorted(business),
                "business_tools_ready": False,
                "auth_state": "UNKNOWN",
            },
        )

    if admin:
        missing = sorted(FEISHU_ADMIN_TOOLS.difference(admin))
        if missing:
            return _layer(
                BROKEN,
                "Feishu setup/admin tool surface is incomplete",
                repair_state="redeploy_feishu_setup_release",
                evidence={
                    "mode": "SETUP",
                    "missing_tools": missing,
                    "business_tools_ready": False,
                    "auth_state": "UNKNOWN",
                },
            )
        return _layer(
            DEGRADED,
            "Feishu setup release is active; install/config/auth may proceed but business tools are not pinned yet",
            repair_state="complete_feishu_setup_then_pin_release",
            evidence={
                "mode": "SETUP",
                "business_tools_ready": False,
                "auth_state": "UNKNOWN",
            },
        )

    if business:
        missing = sorted(FEISHU_BUSINESS_TOOLS.difference(business))
        if missing:
            return _layer(
                BROKEN,
                "Pinned Feishu business tool surface is incomplete",
                repair_state="redeploy_pinned_feishu_release",
                evidence={
                    "mode": "PINNED",
                    "missing_tools": missing,
                    "business_tools_ready": False,
                    "auth_state": "UNKNOWN",
                },
            )
        auth_ready = status.get("feishu_auth_ready")
        auth_state = (
            "AUTH_READY"
            if auth_ready is True
            else "AUTH_REQUIRED"
            if auth_ready is False
            else "UNKNOWN"
        )
        return _layer(
            HEALTHY if auth_ready is True else DEGRADED,
            (
                "Pinned Feishu provider and Base/IM tools are ready"
                if auth_ready is True
                else "Pinned Feishu provider is exposed but authorization is not ready"
                if auth_ready is False
                else "Pinned Feishu provider is exposed; authorization readiness is not instrumented"
            ),
            repair_state=(
                "none"
                if auth_ready is True
                else "complete_feishu_authorization"
                if auth_ready is False
                else "probe_feishu_authorization"
            ),
            evidence={
                "mode": "PINNED",
                "business_tools_ready": True,
                "auth_state": auth_state,
            },
        )

    return _layer(
        UNINSTRUMENTED,
        "Feishu collaboration provider is not enabled in this release",
        repair_state="none",
        evidence={
            "mode": "DISABLED",
            "business_tools_ready": False,
            "auth_state": "UNKNOWN",
        },
    )


def project_m0_layers(
    status: dict[str, Any], *, exposure: dict[str, Any] | None = None
) -> dict[str, dict[str, Any]]:
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
    if exposure is None:
        exposure = resolve_web_tool_exposure(status)

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
        "connector": project_connector_layer(exposure),
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
        "collaboration": project_feishu_layer(status, tools),
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
    exposure = resolve_web_tool_exposure(status)
    layers = project_m0_layers(status, exposure=exposure)
    required = M0_REQUIRED_LAYERS if scope == "m0" else FULL_LAYER_ORDER
    return {
        "schema_version": 1,
        "operation": "lab_doctor",
        "scope": scope,
        "overall": classify_overall(layers, required),
        "repair_mode": "diagnostic_only",
        "silent_fallback_allowed": False,
        "web_tool_exposure": exposure,
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
