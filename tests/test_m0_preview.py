import importlib.util
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "m0_preview.py"
SPEC = importlib.util.spec_from_file_location("m0_preview", MODULE_PATH)
m0 = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(m0)


class M0PreviewTests(unittest.TestCase):
    def test_catdesk_slug_reads_private_config_without_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text('mcpSlug = "safe-slug"\n', encoding="utf-8")
            path.chmod(0o600)
            self.assertEqual(m0.catdesk_slug(path), "safe-slug")

    def test_catdesk_slug_rejects_broad_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text('mcpSlug = "safe-slug"\n', encoding="utf-8")
            path.chmod(0o644)
            with self.assertRaisesRegex(RuntimeError, "0600"):
                m0.catdesk_slug(path)

    def test_required_tool_surface_is_m0_only(self):
        self.assertIn("cancel_command", m0.M0_REQUIRED_TOOLS)
        self.assertIn("start_command", m0.M0_REQUIRED_TOOLS)
        self.assertIn("read", m0.M0_REQUIRED_TOOLS)
        self.assertFalse(any(name.startswith("computer_") for name in m0.M0_REQUIRED_TOOLS))

    def test_lease_remaining_expires_fail_closed(self):
        now = m0._now_ms()
        self.assertEqual(m0._lease_remaining_ms({"expires_at_ms": now - 1}), 0)
        self.assertGreater(m0._lease_remaining_ms({"expires_at_ms": now + 60_000}), 0)

    def test_tunnel_profile_validation_binds_exact_m0_socket(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = root / f"{m0.TUNNEL_PROFILE_NAME}.yaml"
            profile.write_text(json.dumps({
                "control_plane": {
                    "api_key": "file:/private/key",
                    "base_url": "https://api.openai.com",
                    "tunnel_id": "tunnel-test",
                },
                "mcp": {
                    "server_urls": [{
                        "channel": "main",
                        "url": "http://localhost/mcp",
                        "unix_socket": str(m0.PREVIEW_SOCKET),
                    }]
                },
            }), encoding="utf-8")
            profile.chmod(0o600)
            result = m0._validate_tunnel_profile_dir(root)
            self.assertEqual(result["tunnel_id"], "tunnel-test")

    def test_live_tunnel_discovery_is_not_tied_to_current_worktree_pid_file(self):
        discovered = [{
            "pid": 321,
            "profile_dir": "/legacy/main/profile",
            "profile_path": "/legacy/main/profile/local-agent-bridge-poc.yaml",
            "tunnel_id": "tunnel-live",
            "profile_sha256": "a" * 64,
            "command": "tunnel-client run",
        }]
        with patch.object(m0, "_discover_running_tunnels", return_value=discovered), patch.object(
            m0, "_tunnel_runtime_meta", return_value={}
        ):
            result = m0._tunnel_status()
        self.assertTrue(result["ready"])
        self.assertEqual(result["pid"], 321)
        self.assertEqual(result["source"], "live_process_discovery")
        self.assertIsNone(result["generation"])

    def test_multiple_matching_tunnels_fail_closed_instead_of_implicit_selection(self):
        discovered = [
            {"pid": 10, "tunnel_id": "a"},
            {"pid": 11, "tunnel_id": "a"},
        ]
        with patch.object(m0, "_discover_running_tunnels", return_value=discovered):
            result = m0._tunnel_status()
        self.assertFalse(result["ready"])
        self.assertEqual(result["reason"], "multiple_active_tunnels")
        self.assertEqual(result["active_pids"], [10, 11])

    def test_canonical_tunnel_meta_supplies_endpoint_generation(self):
        discovered = [{
            "pid": 321,
            "profile_dir": "/runtime/tunnel",
            "profile_path": "/runtime/tunnel/local-agent-bridge-poc.yaml",
            "tunnel_id": "tunnel-live",
            "profile_sha256": "a" * 64,
            "command": "tunnel-client run",
        }]
        meta = {
            "pid": 321,
            "tunnel_id": "tunnel-live",
            "endpoint_generation": 7,
        }
        with patch.object(m0, "_discover_running_tunnels", return_value=discovered), patch.object(
            m0, "_tunnel_runtime_meta", return_value=meta
        ):
            result = m0._tunnel_status()
        self.assertTrue(result["ready"])
        self.assertEqual(result["source"], "canonical_runtime_meta")
        self.assertEqual(result["generation"], 7)

    def test_ensure_tunnel_adopts_healthy_legacy_process_without_restart(self):
        current = {
            "ready": True,
            "pid": 321,
            "source": "live_process_discovery",
            "reason": None,
            "tunnel_id": "tunnel-live",
            "profile_dir": "/legacy/profile",
        }
        with patch.object(m0, "_tunnel_status", return_value=current), patch.object(
            m0, "_adopt_live_tunnel", return_value={"endpoint_generation": 1}
        ) as adopt, patch.object(
            m0, "_find_tunnel_client", side_effect=AssertionError("healthy tunnel must not relaunch")
        ):
            self.assertEqual(m0.ensure_tunnel(), 321)
        adopt.assert_called_once_with(current)

    def test_write_tunnel_runtime_state_uses_private_canonical_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "legacy"
            canonical = root / "runtime" / "tunnel"
            runtime_root = root / "runtime"
            source.mkdir()
            profile = source / f"{m0.TUNNEL_PROFILE_NAME}.yaml"
            profile.write_text(json.dumps({
                "control_plane": {
                    "api_key": "file:/private/key",
                    "base_url": "https://api.openai.com",
                    "tunnel_id": "tunnel-live",
                },
                "mcp": {
                    "server_urls": [{
                        "channel": "main",
                        "url": "http://localhost/mcp",
                        "unix_socket": str(m0.PREVIEW_SOCKET),
                    }]
                },
            }), encoding="utf-8")
            profile.chmod(0o600)
            with patch.object(m0, "RUNTIME_ROOT", runtime_root), patch.object(
                m0, "TUNNEL_PROFILE_DIR", canonical
            ), patch.object(
                m0, "TUNNEL_META", runtime_root / "tunnel.json"
            ), patch.object(
                m0, "TUNNEL_PID_FILE", runtime_root / "tunnel.pid"
            ):
                meta = m0._write_tunnel_runtime_state(
                    pid=321,
                    tunnel_id="tunnel-live",
                    active_profile_dir=source,
                    endpoint_generation=4,
                )
                self.assertEqual(meta["endpoint_generation"], 4)
                for path in (
                    runtime_root / "tunnel.json",
                    runtime_root / "tunnel.pid",
                    canonical / f"{m0.TUNNEL_PROFILE_NAME}.yaml",
                ):
                    self.assertEqual(path.stat().st_mode & 0o077, 0)



if __name__ == "__main__":
    unittest.main()
