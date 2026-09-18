import importlib.util
from datetime import datetime, timezone
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "lab_doctor.py"
SPEC = importlib.util.spec_from_file_location("lab_doctor", MODULE_PATH)
doctor = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(doctor)


def healthy_status():
    return {
        "m0_preview_ready": True,
        "bridge_ready": True,
        "bridge_pid": 123,
        "lease_remaining_ms": 86_400_000,
        "backend_ready": True,
        "tunnel_ready": True,
        "tunnel_pid": 456,
        "tunnel_id": "tunnel-test",
        "tunnel_endpoint_generation": 3,
        "tunnel_state_source": "canonical_runtime_meta",
        "socket": "/tmp/bridge.sock",
        "tools": [
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
        ],
    }


class LabDoctorTests(unittest.TestCase):
    def test_m0_scope_is_healthy_when_required_layers_are_healthy(self):
        report = doctor.build_report(healthy_status(), scope="m0")
        self.assertEqual(report["overall"], doctor.HEALTHY)
        self.assertFalse(report["silent_fallback_allowed"])
        self.assertEqual(
            report["layers"]["bridge_process"]["state"], doctor.HEALTHY
        )
        self.assertEqual(
            report["layers"]["connector"]["state"], doctor.UNINSTRUMENTED
        )

    def test_full_scope_is_degraded_until_product_layers_are_instrumented(self):
        report = doctor.build_report(healthy_status(), scope="full")
        self.assertEqual(report["overall"], doctor.DEGRADED)
        self.assertEqual(
            report["layers"]["web_executor"]["state"], doctor.UNINSTRUMENTED
        )


    def test_feishu_setup_surface_is_reported_as_setup_not_business_ready(self):
        status = healthy_status()
        status["tools"] += [
            "feishu_setup_start",
            "feishu_setup_poll",
            "feishu_auth_begin",
            "feishu_auth_complete",
        ]
        report = doctor.build_report(status, scope="full")
        layer = report["layers"]["collaboration"]
        self.assertEqual(layer["state"], doctor.DEGRADED)
        self.assertEqual(layer["evidence"]["mode"], "SETUP")
        self.assertFalse(layer["evidence"]["business_tools_ready"])

    def test_feishu_pinned_surface_with_auth_is_healthy(self):
        status = healthy_status()
        status["tools"] += [
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
        ]
        status["feishu_auth_ready"] = True
        report = doctor.build_report(status, scope="full")
        layer = report["layers"]["collaboration"]
        self.assertEqual(layer["state"], doctor.HEALTHY)
        self.assertEqual(layer["evidence"]["mode"], "PINNED")
        self.assertEqual(layer["evidence"]["auth_state"], "AUTH_READY")

    def test_feishu_setup_and_business_tools_mixed_is_broken(self):
        status = healthy_status()
        status["tools"] += [
            "feishu_setup_start",
            "feishu_status",
        ]
        report = doctor.build_report(status, scope="full")
        layer = report["layers"]["collaboration"]
        self.assertEqual(layer["state"], doctor.BROKEN)
        self.assertEqual(layer["repair_state"], "deploy_single_feishu_release_mode")

    def test_bridge_failure_does_not_overwrite_other_layer_health(self):
        status = healthy_status()
        status["bridge_ready"] = False
        report = doctor.build_report(status, scope="m0")
        self.assertEqual(report["overall"], doctor.BROKEN)
        self.assertEqual(
            report["layers"]["bridge_process"]["state"], doctor.BROKEN
        )
        self.assertEqual(report["layers"]["tunnel"]["state"], doctor.HEALTHY)
        self.assertEqual(report["layers"]["backend"]["state"], doctor.HEALTHY)


    def test_live_discovered_tunnel_is_degraded_until_runtime_state_is_adopted(self):
        status = healthy_status()
        status["tunnel_state_source"] = "live_process_discovery"
        status["tunnel_endpoint_generation"] = None
        report = doctor.build_report(status, scope="m0")
        self.assertEqual(report["overall"], doctor.DEGRADED)
        self.assertEqual(report["layers"]["tunnel"]["state"], doctor.DEGRADED)
        self.assertEqual(
            report["layers"]["tunnel"]["repair_state"],
            "adopt_tunnel_runtime_state_candidate",
        )

    def test_tunnel_failure_has_explicit_repair_state(self):
        status = healthy_status()
        status["tunnel_ready"] = False
        report = doctor.build_report(status, scope="m0")
        self.assertEqual(report["layers"]["tunnel"]["state"], doctor.BROKEN)
        self.assertEqual(
            report["layers"]["tunnel"]["repair_state"],
            "restart_tunnel_safe_candidate",
        )

    def test_stale_lease_is_auth_failure_not_bridge_wide_boolean(self):
        status = healthy_status()
        status["lease_remaining_ms"] = 0
        report = doctor.build_report(status, scope="m0")
        self.assertEqual(
            report["layers"]["auth_session"]["state"], doctor.BROKEN
        )
        self.assertEqual(report["layers"]["tunnel"]["state"], doctor.HEALTHY)

    def test_missing_job_tool_is_reported_without_global_tool_disable(self):
        status = healthy_status()
        status["tools"].remove("cancel_command")
        report = doctor.build_report(status, scope="full")
        self.assertEqual(report["layers"]["jobs"]["state"], doctor.BROKEN)
        self.assertIn(
            "cancel_command",
            report["layers"]["mcp"]["evidence"]["missing_required_tools"],
        )

    def test_tools_list_alone_does_not_mark_connector_or_web_exposure_healthy(self):
        report = doctor.build_report(healthy_status(), scope="full")
        self.assertEqual(report["layers"]["backend"]["state"], doctor.HEALTHY)
        self.assertEqual(report["layers"]["tunnel"]["state"], doctor.HEALTHY)
        self.assertEqual(report["layers"]["tool_registry"]["state"], doctor.HEALTHY)
        self.assertEqual(report["layers"]["connector"]["state"], doctor.UNINSTRUMENTED)
        self.assertEqual(
            report["web_tool_exposure"]["state"], doctor.EXPOSURE_UNKNOWN
        )
        self.assertFalse(report["web_tool_exposure"]["confirmed"])

    def test_direct_status_web_exposure_cannot_bypass_current_execution_correlation(self):
        status = healthy_status()
        status["web_tool_exposure"] = {
            "source": "web_chatgpt_executor",
            "state": doctor.EXPOSURE_ABSENT_REPAIRABLE,
            "required_apps": ["Local Agent Bridge"],
            "observed_apps": [],
            "confirmed": False,
            "attempts": 1,
        }
        report = doctor.build_report(status, scope="full")
        self.assertEqual(report["layers"]["backend"]["state"], doctor.HEALTHY)
        self.assertEqual(report["layers"]["tunnel"]["state"], doctor.HEALTHY)
        self.assertEqual(report["layers"]["mcp"]["state"], doctor.HEALTHY)
        self.assertEqual(report["layers"]["connector"]["state"], doctor.UNINSTRUMENTED)
        self.assertEqual(report["web_tool_exposure"]["state"], doctor.EXPOSURE_UNKNOWN)

    def test_direct_confirmed_managed_web_exposure_is_not_current_proof(self):
        status = healthy_status()
        status["web_tool_exposure"] = {
            "source": "local_web_executor_adapter",
            "state": doctor.EXPOSURE_CONFIRMED,
            "required_apps": ["Local Agent Bridge"],
            "observed_apps": ["Local Agent Bridge"],
            "confirmed": True,
            "attempts": 0,
        }
        report = doctor.build_report(status, scope="full")
        self.assertEqual(report["layers"]["connector"]["state"], doctor.UNINSTRUMENTED)
        self.assertEqual(report["web_tool_exposure"]["state"], doctor.EXPOSURE_UNKNOWN)
        self.assertFalse(report["web_tool_exposure"]["confirmed"])


    def test_claimed_confirmed_without_exact_observed_app_is_not_healthy(self):
        status = healthy_status()
        status["web_tool_exposure"] = {
            "source": "local_web_executor_adapter",
            "state": doctor.EXPOSURE_CONFIRMED,
            "required_apps": ["Local Agent Bridge"],
            "observed_apps": [],
            "confirmed": True,
        }
        report = doctor.build_report(status, scope="full")
        self.assertNotEqual(report["layers"]["connector"]["state"], doctor.HEALTHY)
        self.assertNotEqual(report["web_tool_exposure"]["state"], doctor.EXPOSURE_CONFIRMED)

    def test_historical_confirmed_execution_without_explicit_current_correlation_is_not_healthy(self):
        status = healthy_status()
        with tempfile.TemporaryDirectory() as tmp:
            executions = Path(tmp) / "executions"
            executions.mkdir()
            record = {
                "updated_at": "2026-09-15T00:00:00.000Z",
                "tool_exposure": {
                    "state": doctor.EXPOSURE_CONFIRMED,
                    "required_apps": ["Local Agent Bridge"],
                    "observed_apps": ["Local Agent Bridge"],
                    "confirmed": True,
                },
            }
            (executions / "old_execution.json").write_text(json.dumps(record), encoding="utf-8")
            status["web_executor_state_dir"] = tmp
            report = doctor.build_report(status, scope="full")
        self.assertNotEqual(report["layers"]["connector"]["state"], doctor.HEALTHY)
        self.assertNotEqual(report["web_tool_exposure"]["state"], doctor.EXPOSURE_CONFIRMED)

    def test_completed_managed_execution_cannot_promote_current_connector_health(self):
        status = healthy_status()
        with tempfile.TemporaryDirectory() as tmp:
            executions = Path(tmp) / "executions"
            executions.mkdir()
            record = {
                "provenance": "local_web_executor_adapter",
                "status": "completed",
                "terminal_at": "2026-09-15T00:00:01.000Z",
                "updated_at": "2026-09-15T00:00:01.000Z",
                "tool_exposure": {
                    "state": doctor.EXPOSURE_CONFIRMED,
                    "required_apps": ["Local Agent Bridge"],
                    "observed_apps": ["Local Agent Bridge"],
                    "confirmed": True,
                },
            }
            (executions / "completed_execution.json").write_text(json.dumps(record), encoding="utf-8")
            status["web_executor_state_dir"] = tmp
            report = doctor.build_report(status, scope="full")
        self.assertEqual(report["layers"]["connector"]["state"], doctor.UNINSTRUMENTED)
        self.assertEqual(report["web_tool_exposure"]["state"], doctor.EXPOSURE_UNKNOWN)

    def test_tools_list_claimed_exposure_is_ignored(self):
        status = healthy_status()
        status["web_tool_exposure"] = {
            "source": "tools_list",
            "state": doctor.EXPOSURE_CONFIRMED,
            "required_apps": ["Local Agent Bridge"],
            "confirmed": True,
        }
        report = doctor.build_report(status, scope="full")
        self.assertEqual(report["layers"]["connector"]["state"], doctor.UNINSTRUMENTED)
        self.assertEqual(
            report["web_tool_exposure"]["state"], doctor.EXPOSURE_UNKNOWN
        )

    def test_running_executor_state_without_explicit_current_execution_id_is_not_current_proof(self):
        status = healthy_status()
        with tempfile.TemporaryDirectory() as tmp:
            executions = Path(tmp) / "executions"
            executions.mkdir()
            execution_id = "wex_22222222-2222-2222-2222-222222222222"
            record = {
                "execution_id": execution_id,
                "provenance": "local_web_executor_adapter",
                "status": "running",
                "updated_at": "2026-09-15T00:00:00.000Z",
                "tool_exposure": {
                    "state": doctor.EXPOSURE_CONFIRMED,
                    "required_apps": ["Local Agent Bridge"],
                    "observed_apps": ["Local Agent Bridge"],
                    "confirmed": True,
                },
            }
            (executions / f"{execution_id}.json").write_text(json.dumps(record), encoding="utf-8")
            status["web_executor_state_dir"] = tmp
            report = doctor.build_report(status, scope="full")
        self.assertEqual(report["layers"]["connector"]["state"], doctor.UNINSTRUMENTED)
        self.assertEqual(report["web_tool_exposure"]["state"], doctor.EXPOSURE_UNKNOWN)

    def test_current_execution_id_does_not_fall_back_to_another_running_record(self):
        status = healthy_status()
        with tempfile.TemporaryDirectory() as tmp:
            executions = Path(tmp) / "executions"
            executions.mkdir()
            old_id = "wex_33333333-3333-3333-3333-333333333333"
            current_id = "wex_44444444-4444-4444-4444-444444444444"
            old_record = {
                "execution_id": old_id,
                "provenance": "local_web_executor_adapter",
                "status": "running",
                "tool_exposure": {
                    "state": doctor.EXPOSURE_CONFIRMED,
                    "required_apps": ["Local Agent Bridge"],
                    "observed_apps": ["Local Agent Bridge"],
                },
            }
            (executions / f"{old_id}.json").write_text(json.dumps(old_record), encoding="utf-8")
            status["web_executor_state_dir"] = tmp
            status["web_executor_current_execution_id"] = current_id
            report = doctor.build_report(status, scope="full")
        self.assertEqual(report["layers"]["connector"]["state"], doctor.UNINSTRUMENTED)
        self.assertEqual(report["web_tool_exposure"]["state"], doctor.EXPOSURE_UNKNOWN)

    def test_build_report_uses_one_exposure_snapshot(self):
        status = healthy_status()
        confirmed = {
            "source": "local_web_executor_adapter",
            "state": doctor.EXPOSURE_CONFIRMED,
            "required_apps": ["Local Agent Bridge"],
            "confirmed": True,
            "attempts": 0,
            "observed_apps": ["Local Agent Bridge"],
            "action": "already_exposed",
        }
        unknown = doctor.unknown_web_tool_exposure()
        with patch.object(
            doctor, "resolve_web_tool_exposure", side_effect=[confirmed, unknown]
        ) as resolver:
            report = doctor.build_report(status, scope="full")
        self.assertEqual(resolver.call_count, 1)
        self.assertEqual(report["web_tool_exposure"], confirmed)
        self.assertEqual(
            report["layers"]["connector"]["evidence"]["web_tool_exposure"],
            confirmed,
        )

    def test_stale_running_current_execution_cannot_promote_connector_health(self):
        status = healthy_status()
        with tempfile.TemporaryDirectory() as tmp:
            executions = Path(tmp) / "executions"
            executions.mkdir()
            execution_id = "wex_55555555-5555-5555-5555-555555555555"
            record = {
                "execution_id": execution_id,
                "provenance": "local_web_executor_adapter",
                "status": "running",
                "updated_at": "2000-01-01T00:00:00.000Z",
                "tool_exposure": {
                    "state": doctor.EXPOSURE_CONFIRMED,
                    "required_apps": ["Local Agent Bridge"],
                    "observed_apps": ["Local Agent Bridge"],
                    "confirmed": True,
                },
            }
            (executions / f"{execution_id}.json").write_text(json.dumps(record), encoding="utf-8")
            status["web_executor_state_dir"] = tmp
            status["web_executor_current_execution_id"] = execution_id
            report = doctor.build_report(status, scope="full")
        self.assertEqual(report["layers"]["connector"]["state"], doctor.UNINSTRUMENTED)
        self.assertEqual(report["web_tool_exposure"]["state"], doctor.EXPOSURE_UNKNOWN)

    def test_web_executor_state_dir_supplies_exposure_without_backend_conflation(self):
        status = healthy_status()
        with tempfile.TemporaryDirectory() as tmp:
            executions = Path(tmp) / "executions"
            executions.mkdir()
            execution_id = "wex_11111111-1111-1111-1111-111111111111"
            record = {
                "execution_id": execution_id,
                "provenance": "local_web_executor_adapter",
                "status": "running",
                "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "tool_exposure": {
                    "state": "UNAVAILABLE",
                    "required_apps": ["Local Agent Bridge"],
                    "confirmed": False,
                    "attempts": 1,
                },
            }
            (executions / f"{execution_id}.json").write_text(
                json.dumps(record), encoding="utf-8"
            )
            status["web_executor_state_dir"] = tmp
            status["web_executor_current_execution_id"] = execution_id
            report = doctor.build_report(status, scope="full")
        self.assertEqual(report["layers"]["backend"]["state"], doctor.HEALTHY)
        self.assertEqual(report["layers"]["connector"]["state"], doctor.DEGRADED)
        self.assertEqual(
            report["web_tool_exposure"]["state"], doctor.EXPOSURE_UNAVAILABLE
        )


if __name__ == "__main__":
    unittest.main()
