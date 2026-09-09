import importlib.util
import unittest
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


if __name__ == "__main__":
    unittest.main()
