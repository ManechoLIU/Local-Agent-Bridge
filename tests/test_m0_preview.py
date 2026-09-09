import importlib.util
import json
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
