import json
import os
import shutil
import socket
import stat
import struct
import subprocess
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from scripts import production_connector as pc


REVISION = "a" * 40


@contextmanager
def fake_mcp_streamable_server(mode="ok"):
    events = []
    session_id = "session-test-123"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def _respond(self, status, body=b"", *, response_session=None, content_type=None):
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            if body:
                self.send_header("Content-Type", content_type or "text/event-stream")
            if response_session is not None:
                self.send_header("Mcp-Session-Id", response_session)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _payload(self):
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            return json.loads(raw) if raw else None

        def do_POST(self):
            payload = self._payload()
            events.append(("POST", payload, dict(self.headers.items())))
            method = (payload or {}).get("method")
            if method == "initialize":
                if mode == "initialize_422":
                    self._respond(422, b"Unexpected message")
                    return
                response_id = "wrong-id" if mode == "wrong_initialize_id" else payload["id"]
                body = (
                    "data: "
                    + json.dumps({
                        "jsonrpc": "2.0",
                        "id": response_id,
                        "result": {
                            "protocolVersion": "2025-03-26",
                            "capabilities": {},
                            "serverInfo": {"name": "ai-bridge-desktop", "version": "1.0.0"},
                        },
                    })
                    + "\n\n"
                ).encode()
                content_type = None
                if mode == "text_plain_sse":
                    content_type = "text/plain"
                elif mode == "json_content_sse_body":
                    content_type = "application/json"
                elif mode == "sse_content_json_body":
                    body = json.dumps({
                        "jsonrpc": "2.0",
                        "id": response_id,
                        "result": {
                            "protocolVersion": "2025-03-26",
                            "capabilities": {},
                            "serverInfo": {"name": "ai-bridge-desktop", "version": "1.0.0"},
                        },
                    }).encode()
                self._respond(
                    200,
                    body,
                    response_session=None if mode == "missing_session" else session_id,
                    content_type=content_type,
                )
                return
            if method == "notifications/initialized":
                self._respond(202, response_session=session_id)
                return
            if method == "tools/list":
                if mode == "tools_422":
                    self._respond(422, b"Unexpected message", response_session=session_id)
                    return
                response_id = "wrong-id" if mode == "wrong_tools_id" else payload["id"]
                value = {
                    "jsonrpc": "2.0",
                    "id": response_id,
                    "result": {"tools": [{"name": "computer"}, {"name": "shell_command"}]},
                }
                if mode == "tools_error":
                    value = {
                        "jsonrpc": "2.0",
                        "id": payload["id"],
                        "error": {"code": -32000, "message": "failed"},
                    }
                body = ("data: " + json.dumps(value) + "\n\n").encode()
                if mode == "multiple_tools_results":
                    body += ("data: " + json.dumps(value) + "\n\n").encode()
                self._respond(
                    200,
                    body,
                    response_session="different-session" if mode == "mismatched_session" else session_id,
                )
                return
            self._respond(400)

        def do_DELETE(self):
            events.append(("DELETE", None, dict(self.headers.items())))
            self._respond(500 if mode == "cleanup_500" else 202, response_session=session_id)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/mcp/private", events
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class ProductionConnectorContractTests(unittest.TestCase):
    def _manifest(self, generation: int = 7):
        return pc.release_manifest(
            pc.canonical_workspace(),
            release_revision=REVISION,
            endpoint_generation=generation,
        )

    def _browser_executable(self, root: Path) -> Path:
        path = (
            root
            / "Google Chrome for Testing.app"
            / "Contents"
            / "MacOS"
            / "Google Chrome for Testing"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test-browser-executable")
        path.chmod(0o700)
        return path

    def _release_sources(self, root: Path) -> dict[str, Path]:
        sources_root = root / "sources"
        host = sources_root / "host"
        chrome_host = host / "chrome-host"
        (chrome_host / "extension").mkdir(parents=True)
        (chrome_host / "native-host.mjs").write_text("#!/usr/bin/env node\n", encoding="utf-8")
        (chrome_host / "host-mcp-adapter.mjs").write_text("#!/usr/bin/env node\n", encoding="utf-8")
        (chrome_host / "extension" / "manifest.json").write_text(
            json.dumps({"version": pc.MIN_HOST_EXTENSION_VERSION}), encoding="utf-8"
        )
        for relative in pc.RUNTIME_VERIFIER_BUNDLE_RELATIVE_PATHS:
            path = host / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text(f"// {relative}\n", encoding="utf-8")
            path.chmod(0o700 if relative == "chrome-host/runtime-verifier-cli.mjs" else 0o600)
        for name in ("bridge", "catdesk", "process-runtime"):
            path = sources_root / name
            path.write_bytes(name.encode() * 32)
            path.chmod(0o700)
        for name, content in (
            (
                "catdesk-config",
                b"mcpSlug = 'private-slug'\nshowDetailMode = \"expanded\"\n",
            ),
            ("catdesk-key", b"k" * 64),
            ("attestation-key", b"a" * 32),
        ):
            path = sources_root / name
            path.write_bytes(content)
            path.chmod(0o600)
        tunnel = sources_root / "m0-profile.json"
        tunnel.write_text(json.dumps({
            "config_version": 1,
            "control_plane": {
                "tunnel_id": "tunnel_existing_private_connector",
                "base_url": "https://api.openai.com",
                "api_key": "file:/private/key",
            },
            "mcp": {"server_urls": [{"url": "http://localhost/mcp", "unix_socket": "/tmp/lab-real-ingress-e2e/bridge.sock"}]},
        }), encoding="utf-8")
        tunnel.chmod(0o600)
        return {
            "bridge_binary": sources_root / "bridge",
            "process_runtime_binary": sources_root / "process-runtime",
            "catdesk_binary": sources_root / "catdesk",
            "catdesk_config": sources_root / "catdesk-config",
            "catdesk_backend_key": sources_root / "catdesk-key",
            "host_attestation_key": sources_root / "attestation-key",
            "host_source_dir": host,
            "tunnel_profile": tunnel,
        }

    def _write_managed_extension_preferences(
        self,
        home: Path,
        extension_dir: Path,
        native_user_data_dirs=(),
    ) -> None:
        for user_data_dir in pc._managed_native_user_data_dirs(
            home, native_user_data_dirs
        ):
            default_dir = user_data_dir / "Default"
            default_dir.mkdir(parents=True, mode=0o700)
            preferences = default_dir / "Secure Preferences"
            preferences.write_text(
                json.dumps(
                    {
                        "extensions": {
                            "settings": {
                                pc.HOST_EXTENSION_ID: {"path": str(extension_dir)}
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            preferences.chmod(0o600)

    def _healthy(self, manifest):
        return {
            "connector_id": manifest["connector_id"],
            "contract_version": manifest["contract_version"],
            "release_revision": manifest["release_revision"],
            "endpoint_generation": manifest["endpoint_generation"],
            "workspace": manifest["workspace"],
            "tools": manifest["required_tools"],
            "tunnel_profile_name": manifest["tunnel"]["profile_name"],
            "tunnel_endpoint_generation": manifest["endpoint_generation"],
            "bridge_release_revision": manifest["release_revision"],
            "bridge_contract_version": manifest["contract_version"],
            "bridge_endpoint_generation": manifest["endpoint_generation"],
            "host_extension_id": manifest["host"]["extension_id"],
            "host_extension_version": manifest["host"]["minimum_extension_version"],
            "host_native_manifest_release": manifest["release_revision"],
            "host_contract_version": manifest["contract_version"],
            "host_endpoint_generation": manifest["endpoint_generation"],
        }

    def _identity(self, pid, argv):
        return {
            "pid": pid,
            "uid": os.geteuid(),
            "lstart": "Thu Sep 11 12:00:00 2026",
            "start_timestamp": 1789099200.0,
            "pgid": pid,
            "exe_realpath": argv[0],
            "exe_inode": 1000 + pid,
            "exe_sha256": f"{pid:064x}"[-64:],
            "argv": argv,
        }

    def _rollback_adoption_fixture(self, root, manifest, layout):
        historical_identity = self._identity(
            101,
            [
                "/opt/tunnel-client",
                "run",
                "--profile-dir",
                str(root / "m0-profile"),
                "--profile",
                "local-agent-bridge-poc",
            ],
        )
        historical = {
            "pid": 101,
            "command": " ".join(historical_identity["argv"]),
            "profile_dir": str(root / "m0-profile"),
            "profile_name": "local-agent-bridge-poc",
            "profile_path": str(root / "m0-profile" / "local-agent-bridge-poc.yaml"),
            "profile_sha256": "a" * 64,
            "tunnel_id": "tunnel-existing",
            "process_identity": historical_identity,
            "expected_socket": str(pc.HISTORICAL_M0_BRIDGE_SOCKET),
        }
        historical_state_path = root / "m0-runtime.json"
        pc._write_json_private(
            historical_state_path,
            {
                "bridge_pid": 202,
                "tunnel_pid": historical["pid"],
                "tunnel_id": historical["tunnel_id"],
                "tunnel_profile_name": historical["profile_name"],
                "tunnel_profile_sha256": historical["profile_sha256"],
                "tunnel_process_identity": historical["process_identity"],
            },
        )
        bridge_identity = self._identity(202, ["/opt/local-tool-bridge"])
        prior_receipt = {
            "schema_version": 1,
            "status": "rolled_back",
            "phase": "completed",
            "connector_id": pc.CONNECTOR_ID,
            "contract_version": pc.CONTRACT_VERSION,
            "release_revision": "9" * 40,
            "endpoint_generation": manifest["endpoint_generation"] - 1,
            "tunnel_id": historical["tunnel_id"],
            "historical": {**historical, "pid": 91},
            "historical_restarted": historical,
            "production": {
                "pid": 93,
                "tunnel_id": historical["tunnel_id"],
                "process_identity": self._identity(93, ["/opt/tunnel-client", "run"]),
            },
            "historical_bridge": {
                "pid": 202,
                "command": "/opt/local-tool-bridge",
                "socket": str(pc.HISTORICAL_M0_BRIDGE_SOCKET),
                "process_identity": bridge_identity,
            },
            "historical_runtime_state": str(historical_state_path.resolve()),
            "historical_runtime_state_sha256": "0" * 64,
            "created_at": 1.0,
            "rolled_back_at": 2.0,
        }
        pc._write_json_private(layout["rollback_receipt"], prior_receipt)

        production_identity = self._identity(
            303,
            [
                "/opt/tunnel-client",
                "run",
                "--profile-dir",
                str(layout["tunnel_dir"]),
                "--profile",
                pc.TUNNEL_PROFILE_NAME,
            ],
        )
        production = {
            "pid": 303,
            "command": " ".join(production_identity["argv"]),
            "profile_dir": str(layout["tunnel_dir"]),
            "profile_name": pc.TUNNEL_PROFILE_NAME,
            "profile_path": str(layout["tunnel_profile"]),
            "profile_sha256": "b" * 64,
            "tunnel_id": historical["tunnel_id"],
            "process_identity": production_identity,
            "expected_socket": str(layout["bridge_socket"]),
        }
        state = {
            "schema_version": 1,
            "connector_id": pc.CONNECTOR_ID,
            "contract_version": pc.CONTRACT_VERSION,
            "release_revision": manifest["release_revision"],
            "endpoint_generation": manifest["endpoint_generation"],
            "workspace": manifest["workspace"],
            "tunnel_profile_name": pc.TUNNEL_PROFILE_NAME,
            "tunnel_pid": production["pid"],
            "tunnel_id": production["tunnel_id"],
            "tunnel_profile_sha256": production["profile_sha256"],
            "tunnel_started_at": 3.0,
            "tunnel_process_identity": production["process_identity"],
        }
        for name, pid in (
            ("catdesk_supervisor", 401),
            ("host", 402),
            ("runtime_hook", 403),
            ("bridge", 404),
        ):
            state[f"{name}_pid"] = pid
            state[f"{name}_process_identity"] = self._identity(pid, [f"/opt/{name}"])
        pc._write_json_private(layout["runtime_state"], state)
        ready = {
            "connector_id": pc.CONNECTOR_ID,
            "contract_version": pc.CONTRACT_VERSION,
            "release_revision": manifest["release_revision"],
            "endpoint_generation": manifest["endpoint_generation"],
            "workspace": manifest["workspace"],
            "tunnel_profile_name": pc.TUNNEL_PROFILE_NAME,
            "release_installed": True,
            "runtime_state_matches": True,
            "runtime_verifier_config_ready": True,
            "backend_ready": True,
            "host_ready": True,
            "runtime_hook_ready": True,
            "bridge_ready": True,
            "local_runtime_ready": True,
            "manifest_has_artifacts": True,
            "tunnel_ready": True,
            "tunnel_pid": production["pid"],
            "tunnel_id": production["tunnel_id"],
        }
        return prior_receipt, historical, production, state, ready

    def test_canonical_workspace_is_exact_real_project_root_not_repair_worktree(self):
        root = pc.canonical_workspace()
        expected = Path("/Users/echoman/Documents/ChatGPT/Local-Agent-Bridge").resolve()
        self.assertTrue(root.is_dir())
        self.assertEqual(root, expected)
        self.assertNotIn(".worktrees/lab-production-connector-p0", str(root))
        self.assertNotIn("lab-catdesk-candidate-e2e", str(root))

    def test_production_runtime_paths_are_isolated_from_historical_m0(self):
        paths = pc.production_paths()
        self.assertEqual(paths["runtime_root"], Path.home() / ".local-agent-bridge" / "production-connector")
        self.assertEqual(paths["bridge_socket"], Path("/tmp/local-agent-bridge-production/bridge.sock"))
        self.assertEqual(paths["tunnel_profile_name"], "local-agent-bridge-production")
        self.assertNotEqual(paths["bridge_socket"], Path("/tmp/lab-real-ingress-e2e/bridge.sock"))
        self.assertNotEqual(paths["tunnel_profile_name"], "local-agent-bridge-poc")

    def test_required_surface_rejects_old_poc_tools_and_accepts_host_plus_files_shell(self):
        old = {
            "catdesk_instruction", "read", "search", "write", "edit", "delete",
            "run_command", "start_command", "poll_command", "cancel_command",
        }
        with self.assertRaisesRegex(RuntimeError, "discover_current_entry|stale"):
            pc.validate_tool_surface(old)
        pc.validate_tool_surface(old | {"discover_current_entry"})

    def test_workspace_validation_rejects_candidate_tmp_and_nonexistent_legacy_path(self):
        with self.assertRaisesRegex(RuntimeError, "candidate"):
            pc.validate_workspace(Path("/private/tmp/lab-catdesk-candidate-e2e/workspace"))
        with self.assertRaisesRegex(RuntimeError, "does not exist"):
            pc.validate_workspace(Path("/Users/echoman/Documents/ChatGPT/Local Agent Bridge"))

    def test_release_manifest_pins_unique_production_contract_revision_and_generation(self):
        manifest = self._manifest()
        self.assertEqual(manifest["connector_id"], "local-agent-bridge-production")
        self.assertEqual(manifest["connector_display_name"], "Local Agent Bridge")
        self.assertEqual(manifest["lifecycle"], "production")
        self.assertEqual(manifest["contract_version"], "lab-production-connector-v1")
        self.assertEqual(manifest["release_revision"], REVISION)
        self.assertEqual(manifest["endpoint_generation"], 7)
        self.assertTrue(manifest["fail_closed"])
        self.assertFalse(manifest["silent_legacy_fallback_allowed"])
        self.assertEqual(manifest["workspace"], str(pc.canonical_workspace()))
        self.assertEqual(manifest["host"]["tool"], "discover_current_entry")
        self.assertFalse(manifest["host"]["accepts_caller_conversation_id"])
        self.assertEqual(manifest["host"]["secure_origin"], "https://chatgpt.com")
        self.assertEqual(manifest["host"]["minimum_extension_version"], "0.1.2")
        self.assertIn("read", manifest["required_tools"])
        self.assertIn("run_command", manifest["required_tools"])
        self.assertIn("discover_current_entry", manifest["required_tools"])
        self.assertEqual(manifest["tunnel"]["profile_name"], "local-agent-bridge-production")
        self.assertEqual(
            manifest["bridge"]["capability_ttl_ms"],
            pc.PRODUCTION_BRIDGE_CAPABILITY_TTL_MS,
        )
        self.assertEqual(
            manifest["bridge"]["capability_renew_before_ms"],
            pc.PRODUCTION_BRIDGE_CAPABILITY_RENEW_BEFORE_MS,
        )
        self.assertIn("local-agent-bridge-poc", manifest["tunnel"]["legacy_profile_names_forbidden"])
        self.assertIn("Local Bridge Identity Probe", manifest["historical_connectors_forbidden"])


    def test_feishu_setup_release_exposes_only_admin_tools_and_provider_admin(self):
        manifest = pc.release_manifest(
            pc.canonical_workspace(),
            release_revision=REVISION,
            endpoint_generation=7,
            feishu_admin_enabled=True,
        )
        self.assertNotIn("feishu_cli", manifest["runtime_dependencies"])
        for tool in pc.FEISHU_ADMIN_TOOLS:
            self.assertIn(tool, manifest["required_tools"])
        for tool in pc.FEISHU_REQUIRED_TOOLS:
            self.assertNotIn(tool, manifest["required_tools"])

        layout = pc.production_layout(
            manifest,
            runtime_root=Path("/tmp/lab-feishu-setup-runtime"),
            bridge_socket=Path("/tmp/lab-feishu-setup.sock"),
        )
        bridge_env = pc.production_component_env(
            manifest, layout, catdesk_slug="private-slug"
        )["bridge"]
        self.assertIn("provider_admin", bridge_env["LAB_BRIDGE_CAPABILITIES"])
        self.assertNotIn("collaboration_read", bridge_env["LAB_BRIDGE_CAPABILITIES"])
        self.assertNotIn("LAB_FEISHU_CLI", bridge_env)

    def test_feishu_setup_and_pinned_runtime_are_mutually_exclusive(self):
        with tempfile.TemporaryDirectory() as raw_root:
            cli = Path(raw_root) / "lark-cli"
            cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            cli.chmod(0o700)
            with self.assertRaisesRegex(RuntimeError, "setup|mutually|exclusive"):
                pc.release_manifest(
                    pc.canonical_workspace(),
                    release_revision=REVISION,
                    endpoint_generation=7,
                    feishu_admin_enabled=True,
                    feishu_cli_executable=cli,
                    feishu_cli_version="1.0.96",
                )

    def test_feishu_runtime_dependency_is_optional_pinned_and_expands_bridge_surface(self):
        plain = self._manifest()
        self.assertNotIn("feishu_cli", plain["runtime_dependencies"])
        plain_layout = pc.production_layout(
            plain,
            runtime_root=Path("/tmp/lab-feishu-plain-runtime"),
            bridge_socket=Path("/tmp/lab-feishu-plain.sock"),
        )
        plain_env = pc.production_component_env(
            plain, plain_layout, catdesk_slug="private-slug"
        )["bridge"]
        self.assertNotIn("LAB_FEISHU_CLI", plain_env)
        self.assertNotIn("collaboration_read", plain_env["LAB_BRIDGE_CAPABILITIES"])

        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            target = root / "lark-cli-1.0.96"
            target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            target.chmod(0o700)
            symlink = root / "lark-cli"
            symlink.symlink_to(target)

            manifest = pc.release_manifest(
                pc.canonical_workspace(),
                release_revision=REVISION,
                endpoint_generation=7,
                feishu_cli_executable=symlink,
                feishu_cli_version="1.0.96",
            )
            dep = manifest["runtime_dependencies"]["feishu_cli"]
            self.assertEqual(dep["path"], str(target.resolve(strict=True)))
            self.assertEqual(dep["version"], "1.0.96")
            self.assertEqual(dep["inode"], target.stat().st_ino)
            self.assertEqual(dep["sha256"], pc._sha256_file(target))
            for tool in pc.FEISHU_REQUIRED_TOOLS:
                self.assertIn(tool, manifest["required_tools"])

            layout = pc.production_layout(
                manifest,
                runtime_root=root / "runtime",
                bridge_socket=root / "socket" / "bridge.sock",
            )
            bridge_env = pc.production_component_env(
                manifest, layout, catdesk_slug="private-slug"
            )["bridge"]
            self.assertEqual(bridge_env["LAB_FEISHU_CLI"], dep["path"])
            self.assertEqual(bridge_env["LAB_FEISHU_TIMEOUT_MS"], "30000")
            self.assertEqual(bridge_env["LAB_FEISHU_OUTPUT_LIMIT"], str(1024 * 1024))
            self.assertIn("collaboration_read", bridge_env["LAB_BRIDGE_CAPABILITIES"])
            self.assertIn("collaboration_write", bridge_env["LAB_BRIDGE_CAPABILITIES"])

    def test_feishu_runtime_dependency_rejects_unpinned_or_tampered_cli(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            cli = root / "lark-cli"
            cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            cli.chmod(0o700)

            with self.assertRaisesRegex(RuntimeError, "version"):
                pc.release_manifest(
                    pc.canonical_workspace(),
                    release_revision=REVISION,
                    endpoint_generation=7,
                    feishu_cli_executable=cli,
                    feishu_cli_version="latest",
                )
            with self.assertRaisesRegex(RuntimeError, "version|required"):
                pc.release_manifest(
                    pc.canonical_workspace(),
                    release_revision=REVISION,
                    endpoint_generation=7,
                    feishu_cli_executable=cli,
                )

            manifest = pc.release_manifest(
                pc.canonical_workspace(),
                release_revision=REVISION,
                endpoint_generation=7,
                feishu_cli_executable=cli,
                feishu_cli_version="1.0.96",
            )
            cli.write_text("#!/bin/sh\necho tampered\n", encoding="utf-8")
            cli.chmod(0o700)
            with self.assertRaisesRegex(RuntimeError, "Feishu.*pin|hash"):
                pc._validate_runtime_dependencies(manifest)

    def test_tool_surface_requires_feishu_tools_only_for_feishu_release(self):
        base = set(pc.REQUIRED_TOOLS)
        pc.validate_tool_surface(base)
        with self.assertRaisesRegex(RuntimeError, "feishu_status"):
            pc.validate_tool_surface(base, base | set(pc.FEISHU_REQUIRED_TOOLS))
        pc.validate_tool_surface(
            base | set(pc.FEISHU_REQUIRED_TOOLS),
            base | set(pc.FEISHU_REQUIRED_TOOLS),
        )

    def test_release_manifest_rejects_invalid_revision_or_generation(self):
        with self.assertRaises(RuntimeError):
            pc.release_manifest(pc.canonical_workspace(), release_revision="dirty", endpoint_generation=1)
        with self.assertRaises(RuntimeError):
            pc.release_manifest(pc.canonical_workspace(), release_revision=REVISION, endpoint_generation=0)
        manifest = self._manifest()
        for ttl_ms, renew_before_ms in (
            (0, 1),
            (pc.PRODUCTION_BRIDGE_CAPABILITY_TTL_MS + 1, 1),
            (1_000, 100),
            (1_000, 0),
            (1_000, 1_000),
        ):
            broken = json.loads(json.dumps(manifest))
            broken["bridge"]["capability_ttl_ms"] = ttl_ms
            broken["bridge"]["capability_renew_before_ms"] = renew_before_ms
            with self.subTest(ttl_ms=ttl_ms, renew_before_ms=renew_before_ms):
                with self.assertRaisesRegex(RuntimeError, "renewal policy"):
                    pc._validate_manifest_contract(broken)

    def test_install_native_still_rejects_extension_below_current_manifest_minimum(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest,
                runtime_root=root / "runtime",
                bridge_socket=root / "bridge.sock",
            )
            layout["native_host_wrapper"].parent.mkdir(parents=True)
            layout["native_host_wrapper"].write_text(
                "#!/bin/sh\nexit 0\n",
                encoding="utf-8",
            )
            layout["native_host_wrapper"].chmod(0o700)
            extension_manifest = (
                layout["host_dir"] / "chrome-host" / "extension" / "manifest.json"
            )
            extension_manifest.parent.mkdir(parents=True)
            extension_manifest.write_text(
                json.dumps({"version": "0.1.1"}),
                encoding="utf-8",
            )
            with patch.object(pc, "_validate_immutable_native_wrapper"):
                with self.assertRaisesRegex(RuntimeError, "extension version is stale"):
                    pc._install_native_messaging_manifests_unlocked(layout, manifest)

    def test_handshake_accepts_only_same_contract_revision_generation_workspace_and_surface(self):
        manifest = self._manifest()
        healthy = self._healthy(manifest)
        pc.validate_release_handshake(manifest, healthy)

        mismatches = [
            ("connector_id", "legacy-connector"),
            ("contract_version", "old-v0"),
            ("release_revision", "b" * 40),
            ("endpoint_generation", 6),
            ("workspace", "/private/tmp/lab-catdesk-candidate-e2e/workspace"),
            ("tunnel_profile_name", "local-agent-bridge-poc"),
            ("tunnel_endpoint_generation", 6),
            ("bridge_release_revision", "b" * 40),
            ("bridge_contract_version", "old-v0"),
            ("bridge_endpoint_generation", 6),
            ("host_extension_id", "wrong-extension"),
            ("host_native_manifest_release", "b" * 40),
            ("host_contract_version", "old-v0"),
            ("host_endpoint_generation", 6),
        ]
        for key, value in mismatches:
            broken = dict(healthy)
            broken[key] = value
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                pc.validate_release_handshake(manifest, broken)

        broken = dict(healthy)
        broken["tools"] = [x for x in healthy["tools"] if x != "discover_current_entry"]
        with self.assertRaises(RuntimeError):
            pc.validate_release_handshake(manifest, broken)

    def test_production_layout_component_env_and_tunnel_profile_share_one_fence(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            socket = root / "socket" / "bridge.sock"
            layout = pc.production_layout(manifest, runtime_root=root, bridge_socket=socket)
            self.assertEqual(
                layout["release_dir"],
                root / "releases" / REVISION / "generation-7",
            )
            runtime_generation_dir = root / "runtime-state" / REVISION / "generation-7"
            self.assertEqual(layout.get("runtime_generation_dir"), runtime_generation_dir)
            for key in (
                "catdesk_home",
                "host_verifier_state",
                "host_reentry_state",
                "host_extension_binding",
                "host_tool_receipt_state",
                "bridge_state_dir",
                "bridge_receipt_journal",
                "catdesk_child_pid",
                "process_runtime_state_dir",
            ):
                self.assertTrue(layout[key].is_relative_to(runtime_generation_dir), key)
                self.assertFalse(layout[key].is_relative_to(layout["release_dir"]), key)
            host_socket_dir = socket.parent / f"{REVISION}-g7"
            self.assertEqual(layout["host_socket_dir"], host_socket_dir)
            self.assertEqual(layout["host_socket"], host_socket_dir / "host.sock")
            self.assertEqual(layout["runtime_hook_socket_dir"], host_socket_dir)
            self.assertEqual(
                layout["bridge_receipt_journal"],
                layout["bridge_state_dir"] / "tool-receipt-journal.json",
            )
            self.assertEqual(
                layout["runtime_hook_socket"],
                layout["runtime_hook_socket_dir"] / "rh.sock",
            )
            self.assertLessEqual(
                len(
                    os.fsencode(
                        str(pc.production_layout(manifest)["runtime_hook_socket"])
                    )
                ),
                103,
                "the canonical generation-fenced Runtime hook socket must fit macOS sockaddr_un",
            )
            self.assertFalse(layout["host_socket"].is_relative_to(layout["release_dir"]))
            self.assertEqual(
                layout["release_catdesk_config"],
                layout["release_dir"] / "catdesk-home" / ".catdesk" / "config.toml",
            )
            self.assertEqual(
                layout["release_catdesk_backend_key"],
                layout["release_dir"] / "catdesk-home" / ".catdesk" / "backend.key",
            )
            self.assertEqual(
                layout["runtime_verifier_wrapper"],
                layout["release_dir"] / "bin" / "runtime-verifier-production",
            )
            self.assertEqual(
                layout["node_binary"],
                layout["release_dir"] / "bin" / "node-runtime",
            )
            self.assertEqual(
                layout["process_runtime_binary"],
                layout["release_dir"] / "bin" / "process-runtime",
            )
            self.assertEqual(
                layout["process_runtime_backend_key"],
                layout["release_dir"] / "secrets" / "process-runtime-backend.key",
            )
            self.assertEqual(
                layout["process_runtime_state_dir"],
                runtime_generation_dir / "process-runtime",
            )
            self.assertTrue(
                layout["process_runtime_state_dir"].is_relative_to(runtime_generation_dir)
            )
            self.assertEqual(layout["bridge_socket"], socket)
            self.assertEqual(
                layout["tunnel_profile"],
                root / "tunnel" / "local-agent-bridge-production.yaml",
            )

            env = pc.production_component_env(manifest, layout, catdesk_slug="private-slug")
            for component in ("host", "bridge"):
                self.assertEqual(env[component]["LAB_PRODUCTION_CONNECTOR_ID"], manifest["connector_id"])
                self.assertEqual(env[component]["LAB_PRODUCTION_CONTRACT_VERSION"], manifest["contract_version"])
                self.assertEqual(env[component]["LAB_PRODUCTION_RELEASE_REVISION"], REVISION)
                self.assertEqual(env[component]["LAB_PRODUCTION_ENDPOINT_GENERATION"], "7")
                self.assertEqual(
                    env[component]["LAB_PRODUCTION_RELEASE_MANIFEST_FILE"],
                    str(layout["release_manifest"]),
                )
            self.assertEqual(env["bridge"]["LAB_BRIDGE_SOCKET"], str(socket))
            self.assertEqual(
                env["host"]["LAB_HOST_TOOL_RECEIPT_STATE_FILE"],
                str(layout["host_tool_receipt_state"]),
            )
            self.assertEqual(
                env["bridge"]["LAB_RUNTIME_HOST_TOOL_HOOK_SOCKET"],
                str(layout["runtime_hook_socket"]),
            )
            self.assertEqual(
                env["bridge"]["LAB_HOST_ADAPTER_SOCKET"],
                str(layout["host_socket"]),
            )
            self.assertEqual(
                env["bridge"]["LAB_BRIDGE_STATE_DIR"],
                str(layout["bridge_state_dir"]),
            )
            self.assertEqual(
                env["bridge"]["LAB_BRIDGE_CAPABILITY_TTL_MS"],
                str(pc.PRODUCTION_BRIDGE_CAPABILITY_TTL_MS),
            )
            self.assertEqual(
                env["bridge"]["LAB_BRIDGE_CAPABILITY_RENEW_BEFORE_MS"],
                str(pc.PRODUCTION_BRIDGE_CAPABILITY_RENEW_BEFORE_MS),
            )
            self.assertEqual(env["bridge"]["LAB_PRODUCTION_WORKSPACE"], str(pc.canonical_workspace()))
            self.assertEqual(env["bridge"]["LAB_PRODUCTION_TUNNEL_PROFILE_NAME"], pc.TUNNEL_PROFILE_NAME)
            self.assertIn("host_attestation", env["bridge"]["LAB_BRIDGE_CAPABILITIES"])
            self.assertEqual(
                env["bridge"]["LAB_PROCESS_BACKEND_URL"],
                f"http://127.0.0.1:{pc.PRODUCTION_PROCESS_RUNTIME_PORT}/mcp",
            )
            self.assertEqual(
                env["bridge"]["LAB_PROCESS_BACKEND_KEY_FILE"],
                str(layout["process_runtime_backend_key"]),
            )
            self.assertEqual(
                env["process_runtime"]["PROCESS_RUNTIME_BIND"],
                f"127.0.0.1:{pc.PRODUCTION_PROCESS_RUNTIME_PORT}",
            )
            self.assertEqual(
                env["process_runtime"]["PROCESS_RUNTIME_BACKEND_KEY_FILE"],
                str(layout["process_runtime_backend_key"]),
            )
            self.assertEqual(
                env["process_runtime"]["PROCESS_RUNTIME_WORKSPACE"],
                str(pc.canonical_workspace()),
            )
            self.assertEqual(
                env["process_runtime"]["PROCESS_RUNTIME_WORKSPACE_ID"],
                pc.CONNECTOR_ID,
            )
            self.assertEqual(
                env["process_runtime"]["PROCESS_RUNTIME_STATE_DIR"],
                str(layout["process_runtime_state_dir"]),
            )

            source = {
                "config_version": 1,
                "control_plane": {
                    "tunnel_id": "tunnel_existing_private_connector",
                    "base_url": "https://api.openai.com",
                    "api_key": "file:/private/key",
                },
                "mcp": {
                    "server_urls": [
                        {"channel": "main", "url": "http://localhost/mcp", "unix_socket": "/tmp/lab-real-ingress-e2e/bridge.sock"}
                    ]
                },
            }
            profile = pc.production_tunnel_profile(source, manifest, layout)
            self.assertEqual(profile["control_plane"], source["control_plane"])
            self.assertEqual(profile["mcp"]["server_urls"][0]["unix_socket"], str(socket))
            self.assertNotIn("/tmp/lab-real-ingress-e2e", json.dumps(profile))
            self.assertIn("local-agent-bridge-production", profile["health"]["url_file"])
            self.assertIn("local-agent-bridge-production", profile["log"]["file"])

    def test_bridge_capability_lease_status_reads_exact_active_bounded_lease(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest, runtime_root=root / "runtime", bridge_socket=root / "bridge.sock"
            )
            state = {
                "bridge_capability_id": "bridge-cap-production-test",
                "bridge_execution_session_id": "bridge-session-production-test",
                "bridge_capability_ttl_ms": pc.PRODUCTION_BRIDGE_CAPABILITY_TTL_MS,
                "bridge_capability_renew_before_ms": pc.PRODUCTION_BRIDGE_CAPABILITY_RENEW_BEFORE_MS,
            }
            now_ms = 10_000
            pc._write_json_private(
                layout["bridge_state_dir"] / "authority-state.json",
                {
                    "schema_version": 2,
                    "key_generation": 1,
                    "records": {
                        state["bridge_capability_id"]: {
                            "lease": {
                                "grant": {
                                    "principal_id": "tunnel-client-production-uds",
                                    "execution_session_id": state["bridge_execution_session_id"],
                                    "capability_id": state["bridge_capability_id"],
                                    "workspace_id": pc.CONNECTOR_ID,
                                    "capabilities": [
                                        "Discovery",
                                        "HostAttestation",
                                        "FileRead",
                                        "FileWrite",
                                        "FileMove",
                                        "Shell",
                                        "ProcessJobs",
                                    ],
                                },
                                "issued_at_ms": now_ms - 1_000,
                                "expires_at_ms": now_ms - 1_000
                                + pc.PRODUCTION_BRIDGE_CAPABILITY_TTL_MS,
                                "session_generation": 1,
                            },
                            "revoked": False,
                            "used_nonces": [],
                            "used_request_ids": [],
                        }
                    },
                    "session_generations": {
                        state["bridge_execution_session_id"]: 1,
                    },
                },
            )
            observed = pc._bridge_capability_lease_status(
                layout, state, now_ms=now_ms
            )
            self.assertTrue(observed["ready"])
            self.assertTrue(observed["renewal_enabled"])
            self.assertEqual(
                observed["remaining_ms"],
                pc.PRODUCTION_BRIDGE_CAPABILITY_TTL_MS - 1_000,
            )

    def test_bridge_capability_lease_status_rejects_expired_unbounded_or_identity_drift(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest, runtime_root=root / "runtime", bridge_socket=root / "bridge.sock"
            )
            state = {
                "bridge_capability_id": "bridge-cap-production-test",
                "bridge_execution_session_id": "bridge-session-production-test",
                "bridge_capability_ttl_ms": pc.PRODUCTION_BRIDGE_CAPABILITY_TTL_MS,
                "bridge_capability_renew_before_ms": pc.PRODUCTION_BRIDGE_CAPABILITY_RENEW_BEFORE_MS,
            }

            def write_record(*, issued_at_ms, expires_at_ms, session_id=None, revoked=False):
                pc._write_json_private(
                    layout["bridge_state_dir"] / "authority-state.json",
                    {
                        "schema_version": 2,
                        "key_generation": 1,
                        "records": {
                            state["bridge_capability_id"]: {
                                "lease": {
                                    "grant": {
                                        "principal_id": "tunnel-client-production-uds",
                                        "execution_session_id": session_id
                                        or state["bridge_execution_session_id"],
                                        "capability_id": state["bridge_capability_id"],
                                        "workspace_id": pc.CONNECTOR_ID,
                                        "capabilities": [
                                            "Discovery",
                                            "HostAttestation",
                                            "FileRead",
                                            "FileWrite",
                                            "FileMove",
                                            "Shell",
                                            "ProcessJobs",
                                        ],
                                    },
                                    "issued_at_ms": issued_at_ms,
                                    "expires_at_ms": expires_at_ms,
                                    "session_generation": 1,
                                },
                                "revoked": revoked,
                                "used_nonces": [],
                                "used_request_ids": [],
                            }
                        },
                        "session_generations": {
                            state["bridge_execution_session_id"]: 1,
                        },
                    },
                )

            now_ms = 100_000_000
            ttl_ms = pc.PRODUCTION_BRIDGE_CAPABILITY_TTL_MS
            renew_before_ms = pc.PRODUCTION_BRIDGE_CAPABILITY_RENEW_BEFORE_MS
            for label, kwargs in (
                (
                    "expired",
                    {
                        "issued_at_ms": now_ms - ttl_ms - 1,
                        "expires_at_ms": now_ms - 1,
                    },
                ),
                (
                    "unbounded",
                    {
                        "issued_at_ms": now_ms - 1_000,
                        "expires_at_ms": now_ms - 1_000 + ttl_ms + 1,
                    },
                ),
                (
                    "missed-renewal-window",
                    {
                        "issued_at_ms": now_ms - ttl_ms + renew_before_ms // 2,
                        "expires_at_ms": now_ms + renew_before_ms // 2,
                    },
                ),
                (
                    "identity",
                    {
                        "issued_at_ms": now_ms - 1_000,
                        "expires_at_ms": now_ms - 1_000 + ttl_ms,
                        "session_id": "bridge-session-production-attacker",
                    },
                ),
                (
                    "revoked",
                    {
                        "issued_at_ms": now_ms - 1_000,
                        "expires_at_ms": now_ms - 1_000 + ttl_ms,
                        "revoked": True,
                    },
                ),
            ):
                with self.subTest(label=label):
                    write_record(**kwargs)
                    with self.assertRaises(RuntimeError):
                        pc._bridge_capability_lease_status(
                            layout, state, now_ms=now_ms
                        )
    def test_install_release_bundle_is_private_immutable_and_hash_verified(self):
        manifest = self._manifest()
        source_node = dict(manifest["runtime_dependencies"]["node"])
        with tempfile.TemporaryDirectory(dir="/tmp") as raw_root:
            root = Path(raw_root)
            sources = self._release_sources(root)
            sources["catdesk_config"].write_bytes(
                b"mcpSlug = 'private-slug'\n"
                b"showDetailMode = \"expanded\" # production policy input\n"
                b"[usage]\nenabled = true\n"
            )
            sources["catdesk_config"].chmod(0o600)
            layout = pc.production_layout(
                manifest,
                runtime_root=root / "runtime",
                bridge_socket=root / "socket" / "bridge.sock",
            )
            installed = pc.install_release_bundle(manifest, layout, sources)
            self.assertEqual(installed, layout)
            pc.validate_installed_release(layout)
            self.assertEqual(stat.S_IMODE(layout["release_manifest"].stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(layout["host_backend_key"].stat().st_mode), 0o600)
            self.assertEqual(
                stat.S_IMODE(layout["process_runtime_backend_key"].stat().st_mode), 0o600
            )
            self.assertEqual(stat.S_IMODE(layout["tunnel_profile"].stat().st_mode), 0o600)
            self.assertTrue(os.access(layout["bridge_binary"], os.X_OK))
            self.assertTrue(os.access(layout["process_runtime_binary"], os.X_OK))
            self.assertEqual(stat.S_IMODE(layout["process_runtime_binary"].stat().st_mode), 0o700)
            self.assertTrue(os.access(layout["native_host_wrapper"], os.X_OK))
            self.assertEqual(
                pc.tomllib.loads(
                    layout["release_catdesk_config"].read_text(encoding="utf-8")
                )["showDetailMode"],
                "disable",
            )
            self.assertEqual(
                pc.tomllib.loads(
                    sources["catdesk_config"].read_text(encoding="utf-8")
                )["showDetailMode"],
                "expanded",
            )
            self.assertEqual(
                layout["release_catdesk_config"].read_text(encoding="utf-8"),
                "mcpSlug = 'private-slug'\n"
                'showDetailMode = "disable" # production policy input\n'
                "[usage]\nenabled = true\n",
            )
            pc._materialize_catdesk_runtime(layout)
            self.assertEqual(
                pc._sha256_file(layout["catdesk_config"]),
                pc._sha256_file(layout["release_catdesk_config"]),
            )
            self.assertEqual(
                pc.tomllib.loads(layout["catdesk_config"].read_text(encoding="utf-8"))[
                    "showDetailMode"
                ],
                "disable",
            )
            installed_manifest = json.loads(layout["release_manifest"].read_text(encoding="utf-8"))
            installed_artifacts = dict(installed_manifest["artifacts_sha256"])
            node = installed_manifest["runtime_dependencies"]["node"]
            self.assertTrue(Path(node["path"]).is_absolute())
            self.assertEqual(node["sha256"], pc._sha256_file(Path(node["path"])))
            runtime_python = installed_manifest["runtime_dependencies"]["python"]
            self.assertTrue(Path(runtime_python["path"]).is_absolute())
            self.assertEqual(runtime_python["sha256"], pc._sha256_file(Path(runtime_python["path"])))
            self.assertEqual(runtime_python["inode"], Path(runtime_python["path"]).stat().st_ino)
            runtime_hook = layout["runtime_hook_entrypoint"]
            self.assertTrue(runtime_hook.is_file())
            self.assertEqual(
                installed_manifest["runtime_host_tool_hook"]["protocol"],
                "runtime_host_tool_hook_v1",
            )
            self.assertEqual(
                installed_manifest["runtime_host_tool_hook"]["runtime_revision"],
                pc.RUNTIME_HOST_TOOL_HOOK_RUNTIME_REVISION,
            )
            self.assertEqual(
                installed_manifest["runtime_host_tool_hook"]["bundle_sha256"][str(runtime_hook.resolve())],
                pc._sha256_file(runtime_hook),
            )
            self.assertIn(f"exec {node['path']}", layout["native_host_wrapper"].read_text(encoding="utf-8"))
            verifier_wrapper = layout["runtime_verifier_wrapper"].read_text(
                encoding="utf-8"
            )
            self.assertTrue(os.access(layout["runtime_verifier_wrapper"], os.X_OK))
            for name, path in (
                ("LAB_HOST_ADAPTER_SOCKET", layout["host_socket"]),
                ("LAB_HOST_ATTESTATION_KEY_FILE", layout["host_attestation_key"]),
                ("LAB_HOST_VERIFIER_STATE_FILE", layout["host_verifier_state"]),
                ("LAB_HOST_REENTRY_STATE_FILE", layout["host_reentry_state"]),
                ("LAB_HOST_EXTENSION_BINDING_FILE", layout["host_extension_binding"]),
            ):
                self.assertIn(f"export {name}={path}", verifier_wrapper)
            self.assertIn(
                f"exec {node['path']} {layout['runtime_verifier_cli']} \"$@\"",
                verifier_wrapper,
            )
            verifier_bundle = pc._runtime_verifier_bundle_paths(layout)
            self.assertEqual(len(verifier_bundle), 21)
            self.assertEqual(len(set(verifier_bundle)), 21)
            self.assertEqual(verifier_bundle[0], layout["runtime_verifier_wrapper"])
            self.assertEqual(
                pc.RUNTIME_VERIFIER_BUNDLE_RELATIVE_PATHS,
                (
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
                ),
            )
            self.assertTrue(
                {str(path.relative_to(layout["release_dir"])) for path in verifier_bundle}
                .issubset(installed_artifacts)
            )

            # Real components may create these files after install. They must
            # remain outside the immutable release artifact boundary.
            for path in (
                layout["catdesk_home"] / ".catdesk" / "binagotchy" / "runtime.json",
                layout["host_verifier_state"],
                layout["host_reentry_state"],
                layout["bridge_state_dir"] / "authority.json",
                layout["catdesk_child_pid"],
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("runtime-state\n", encoding="utf-8")
            host_listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                layout["host_socket"].parent.mkdir(parents=True, exist_ok=True)
                host_listener.bind(str(layout["host_socket"]))
                try:
                    observed_manifest = pc.validate_installed_release(layout)
                except RuntimeError as error:
                    self.fail(f"runtime state dirtied the immutable release: {error}")
            finally:
                host_listener.close()
            self.assertEqual(observed_manifest["artifacts_sha256"], installed_artifacts)
            for relative, expected_hash in installed_artifacts.items():
                self.assertEqual(pc._sha256_file(layout["release_dir"] / relative), expected_hash)

            release_manifest_before_retry = layout["release_manifest"].read_bytes()
            tunnel_profile_before_retry = layout["tunnel_profile"].read_bytes()
            with self.assertRaisesRegex(RuntimeError, "immutable|already exists"):
                pc.install_release_bundle(manifest, layout, sources)
            self.assertTrue(layout["release_dir"].is_dir())
            self.assertEqual(
                layout["release_manifest"].read_bytes(), release_manifest_before_retry
            )
            self.assertEqual(
                layout["tunnel_profile"].read_bytes(), tunnel_profile_before_retry
            )

            legacy_manifest = json.loads(release_manifest_before_retry)
            legacy_manifest["runtime_dependencies"]["node"] = {
                "path": source_node["path"],
                "sha256": source_node["sha256"],
            }
            pc._write_json_private(layout["release_manifest"], legacy_manifest)
            with self.assertRaisesRegex(RuntimeError, "Node.*immutable release"):
                pc.validate_installed_release(layout)
            with patch.object(pc, "_spawn_logged") as spawn:
                with self.assertRaisesRegex(RuntimeError, "Node.*immutable release"):
                    pc.start_local_runtime(manifest, layout)
                spawn.assert_not_called()
            layout["release_manifest"].write_bytes(release_manifest_before_retry)
            layout["release_manifest"].chmod(0o600)

            extra = layout["release_dir"] / "unlisted-evidence.txt"
            extra.write_text("not in release manifest", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "extra|unlisted|artifact"):
                pc.validate_installed_release(layout)
            extra.unlink()

            layout["bridge_binary"].write_bytes(b"tampered")
            with self.assertRaisesRegex(RuntimeError, "hash|artifact"):
                pc.validate_installed_release(layout)

    def test_runtime_host_tool_hook_source_and_socket_identity_fail_closed(self):
        source = pc._runtime_host_tool_hook_source_bytes(
            pc.RUNTIME_HOST_TOOL_HOOK_ENTRYPOINT_RELATIVE_PATH
        )
        self.assertIn(b"HOOK_PROTOCOL = \"runtime_host_tool_hook_v1\"", source)
        with patch.object(pc, "RUNTIME_HOST_TOOL_HOOK_RUNTIME_REVISION", "0" * 40):
            with self.assertRaisesRegex(RuntimeError, "revision|source"):
                pc._runtime_host_tool_hook_source_bytes(
                    pc.RUNTIME_HOST_TOOL_HOOK_ENTRYPOINT_RELATIVE_PATH
                )

        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            socket_path = root / "generation-fenced" / "runtime.sock"
            pc._prepare_private_unix_socket_path(socket_path)
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                listener.bind(str(socket_path))
                socket_path.chmod(0o600)
                listener.listen(1)
                identity = pc._runtime_hook_socket_identity(socket_path)
                self.assertTrue(pc._runtime_hook_socket_is_live(socket_path, identity))
                socket_path.chmod(0o660)
                self.assertFalse(pc._runtime_hook_socket_is_live(socket_path, identity))
            finally:
                listener.close()

    def test_current_release_stop_requires_runtime_hook_identity(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest, runtime_root=root / "runtime", bridge_socket=root / "bridge.sock"
            )
            with self.assertRaisesRegex(RuntimeError, "Runtime Host tool hook state"):
                pc._stop_verified_local_runtime(
                    {"release_manifest": str(layout["release_manifest"])}, layout
                )

    def test_install_release_rejects_noncanonical_show_detail_mode_and_cleans_partial_release(self):
        manifest = self._manifest()
        invalid_configs = {
            "missing": "mcpSlug = 'private-slug'\n",
            "duplicate": (
                "mcpSlug = 'private-slug'\n"
                'showDetailMode = "expanded"\n'
                'showDetailMode = "collapsed"\n'
            ),
            "unsupported": (
                "mcpSlug = 'private-slug'\nshowDetailMode = \"verbose\"\n"
            ),
            "non-string": "mcpSlug = 'private-slug'\nshowDetailMode = true\n",
            "nested": (
                "mcpSlug = 'private-slug'\n[widget]\n"
                'showDetailMode = "expanded"\n'
            ),
            "single-quoted": (
                "mcpSlug = 'private-slug'\nshowDetailMode = 'expanded'\n"
            ),
        }
        for case, config in invalid_configs.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as raw_root:
                root = Path(raw_root)
                sources = self._release_sources(root)
                sources["catdesk_config"].write_text(config, encoding="utf-8")
                sources["catdesk_config"].chmod(0o600)
                layout = pc.production_layout(
                    manifest,
                    runtime_root=root / "runtime",
                    bridge_socket=root / "socket" / "bridge.sock",
                )
                with self.assertRaisesRegex(RuntimeError, "showDetailMode"):
                    pc.install_release_bundle(manifest, layout, sources)
                self.assertFalse(layout["release_dir"].exists())
                self.assertEqual(
                    sources["catdesk_config"].read_text(encoding="utf-8"), config
                )

    def test_release_source_preflight_rejects_missing_profile_before_creating_release_dir(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            sources = self._release_sources(root)
            sources["tunnel_profile"] = root / "snapshot" / "experiments" / "web-local-poc" / "tunnel-runtime" / "local-agent-bridge-poc.yaml"
            layout = pc.production_layout(
                manifest,
                runtime_root=root / "runtime",
                bridge_socket=root / "socket" / "bridge.sock",
            )
            with patch.object(pc, "_copy_owned_file") as forbidden_copy, patch.object(
                pc.shutil, "rmtree"
            ) as forbidden_cleanup:
                with self.assertRaisesRegex(RuntimeError, "tunnel profile.*missing|LAB_PRODUCTION_TUNNEL_SOURCE_PROFILE"):
                    pc.install_release_bundle(manifest, layout, sources)
            forbidden_copy.assert_not_called()
            forbidden_cleanup.assert_not_called()
            self.assertFalse(layout["release_dir"].exists())

            sources["tunnel_profile"] = Path("relative-private-profile.json")
            with patch.object(pc, "_copy_owned_file") as forbidden_copy:
                with self.assertRaisesRegex(RuntimeError, "absolute path|LAB_PRODUCTION_TUNNEL_SOURCE_PROFILE"):
                    pc.install_release_bundle(manifest, layout, sources)
            forbidden_copy.assert_not_called()
            self.assertFalse(layout["release_dir"].exists())

    def test_release_copy_failure_removes_partial_dir_and_allows_exact_retry(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            sources = self._release_sources(root)
            layout = pc.production_layout(
                manifest,
                runtime_root=root / "runtime",
                bridge_socket=root / "socket" / "bridge.sock",
            )
            real_copy = pc._copy_owned_file
            calls = 0

            def fail_second_copy(source, destination, mode):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected copy failure")
                return real_copy(source, destination, mode)

            with patch.object(pc, "_copy_owned_file", side_effect=fail_second_copy):
                with self.assertRaisesRegex(OSError, "copy failure"):
                    pc.install_release_bundle(manifest, layout, sources)
            self.assertFalse(layout["release_dir"].exists())

            installed = pc.install_release_bundle(manifest, layout, sources)
            self.assertEqual(installed, layout)
            pc.validate_installed_release(layout)

    def test_release_validation_failure_restores_or_removes_published_tunnel_profile(self):
        manifest = self._manifest()
        for existing in (False, True):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as raw_root:
                root = Path(raw_root)
                sources = self._release_sources(root)
                layout = pc.production_layout(
                    manifest,
                    runtime_root=root / "runtime",
                    bridge_socket=root / "socket" / "bridge.sock",
                )
                previous = '{"previous": true}\n'
                if existing:
                    layout["tunnel_profile"].parent.mkdir(parents=True)
                    layout["tunnel_profile"].write_text(previous, encoding="utf-8")
                    layout["tunnel_profile"].chmod(0o600)
                with patch.object(
                    pc,
                    "validate_installed_release",
                    side_effect=RuntimeError("injected final validation failure"),
                ):
                    with self.assertRaisesRegex(RuntimeError, "final validation failure"):
                        pc.install_release_bundle(manifest, layout, sources)
                self.assertFalse(layout["release_dir"].exists())
                if existing:
                    self.assertEqual(
                        layout["tunnel_profile"].read_text(encoding="utf-8"), previous
                    )
                    self.assertEqual(
                        stat.S_IMODE(layout["tunnel_profile"].stat().st_mode), 0o600
                    )
                else:
                    self.assertFalse(layout["tunnel_profile"].exists())

    def test_profile_publication_unknown_after_replace_restores_previous_profile(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            sources = self._release_sources(root)
            layout = pc.production_layout(
                manifest,
                runtime_root=root / "runtime",
                bridge_socket=root / "socket" / "bridge.sock",
            )
            previous = '{"previous": true}\n'
            layout["tunnel_profile"].parent.mkdir(parents=True)
            layout["tunnel_profile"].write_text(previous, encoding="utf-8")
            layout["tunnel_profile"].chmod(0o600)
            real_write_json = pc._write_json_private

            def replace_then_report_unknown(path, value, **kwargs):
                real_write_json(path, value, **kwargs)
                if Path(path) == layout["tunnel_profile"]:
                    raise OSError("injected directory fsync failure after replace")

            with patch.object(
                pc, "_write_json_private", side_effect=replace_then_report_unknown
            ):
                with self.assertRaisesRegex(OSError, "fsync failure after replace"):
                    pc.install_release_bundle(manifest, layout, sources)
            self.assertFalse(layout["release_dir"].exists())
            self.assertEqual(
                layout["tunnel_profile"].read_text(encoding="utf-8"), previous
            )

    def test_release_cleanup_failure_reports_original_and_cleanup_errors(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            sources = self._release_sources(root)
            layout = pc.production_layout(
                manifest,
                runtime_root=root / "runtime",
                bridge_socket=root / "socket" / "bridge.sock",
            )
            with patch.object(
                pc, "_copy_owned_file", side_effect=OSError("injected copy failure")
            ), patch.object(
                pc.shutil, "rmtree", side_effect=OSError("injected cleanup failure")
            ):
                with self.assertRaises(RuntimeError) as raised:
                    pc.install_release_bundle(manifest, layout, sources)
            self.assertIn("injected copy failure", str(raised.exception))
            self.assertIn("injected cleanup failure", str(raised.exception))

    def test_native_messaging_manifests_pin_both_chrome_targets_to_same_release(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest,
                runtime_root=root / "runtime",
                bridge_socket=root / "socket" / "bridge.sock",
            )
            layout["native_host_wrapper"].parent.mkdir(parents=True)
            layout["native_host_wrapper"].write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            layout["native_host_wrapper"].chmod(0o700)
            layout["runtime_verifier_wrapper"].write_text(
                "#!/bin/sh\nexit 0\n", encoding="utf-8"
            )
            layout["runtime_verifier_wrapper"].chmod(0o700)
            layout["node_binary"].write_bytes(b"release-local-node")
            layout["node_binary"].chmod(0o700)
            for relative in pc.RUNTIME_VERIFIER_BUNDLE_RELATIVE_PATHS:
                path = layout["host_dir"] / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"// {relative}\n", encoding="utf-8")
                path.chmod(
                    0o700
                    if relative == "chrome-host/runtime-verifier-cli.mjs"
                    else 0o600
                )
            extension_manifest = layout["host_dir"] / "chrome-host" / "extension" / "manifest.json"
            extension_manifest.write_text(
                json.dumps({"version": pc.MIN_HOST_EXTENSION_VERSION}), encoding="utf-8"
            )
            extension_manifest.chmod(0o600)
            installed_release = json.loads(json.dumps(manifest))
            installed_release["runtime_dependencies"]["node"] = {
                "path": str(layout["node_binary"].resolve(strict=True)),
                "sha256": pc._sha256_file(layout["node_binary"]),
            }
            installed_release["artifacts_sha256"] = {
                str(path.relative_to(layout["release_dir"])): pc._sha256_file(path)
                for path in (
                    layout["native_host_wrapper"],
                    layout["node_binary"],
                    *pc._runtime_verifier_bundle_paths(layout),
                )
            }
            custom_dirs = [
                root / ".chatgpt-web-mcp" / "chrome-profile",
                root / ".chatgpt-web-mcp" / "chrome-for-testing-profile",
                root / ".chatgpt-web-mcp" / "chrome-profile-current-entry-v2",
            ]
            browser_executable = self._browser_executable(root)
            self._write_managed_extension_preferences(
                root / "home",
                layout["host_dir"] / "chrome-host" / "extension",
                custom_dirs,
            )
            verifier_path = (
                root / "home" / ".codex" / "adaptive-delivery-host-attestation-verifiers.json"
            )
            verifier_path.parent.mkdir(parents=True, mode=0o700)
            verifier_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "verifiers": {
                            "desktop": {
                                "protocol": "test",
                                "executable": "/private/preserved",
                            }
                        },
                        "preserved_top_level": {"owner": "runtime"},
                    }
                ),
                encoding="utf-8",
            )
            verifier_path.chmod(0o600)
            with patch.object(pc, "validate_installed_release", return_value=installed_release):
                installed = pc.install_native_messaging_manifests(
                    layout,
                    manifest,
                    home_dir=root / "home",
                    native_user_data_dirs=custom_dirs,
                    browser_executables=[browser_executable],
                )
            self.assertEqual(
                set(installed),
                {
                    "chrome",
                    "chrome-for-testing",
                    *(f"custom:{path.resolve()}" for path in custom_dirs),
                    *(
                        f"custom:{(root / 'home' / '.chatgpt-web-mcp' / name).resolve()}"
                        for name in pc.DEFAULT_NATIVE_USER_DATA_DIR_NAMES
                    ),
                },
            )
            observed_paths = set()
            for path in installed.values():
                value = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(value["name"], "com.local_agent_bridge.web_host")
                self.assertEqual(value["path"], str(layout["native_host_wrapper"]))
                self.assertEqual(value["allowed_origins"], [f"chrome-extension://{pc.HOST_EXTENSION_ID}/"])
                observed_paths.add(value["path"])
            self.assertEqual(observed_paths, {str(layout["native_host_wrapper"])})
            verifier_config = json.loads(
                verifier_path.read_text(encoding="utf-8")
            )
            self.assertEqual(
                verifier_config["verifiers"]["desktop"]["executable"],
                "/private/preserved",
            )
            self.assertEqual(
                verifier_config["preserved_top_level"], {"owner": "runtime"}
            )
            verifier = verifier_config["verifiers"]["web"]
            self.assertEqual(
                verifier["executable"],
                str(layout["runtime_verifier_wrapper"].resolve(strict=True)),
            )
            self.assertEqual(verifier["protocol"], "runtime_host_verifier_cli_v2")
            self.assertEqual(
                set(verifier["bundle_sha256"]),
                {
                    *(
                        str(path.resolve(strict=True))
                        for path in pc._runtime_verifier_bundle_paths(layout)
                    ),
                    installed_release["runtime_dependencies"]["node"]["path"],
                    str(layout["host_extension_binding"].resolve(strict=True)),
                },
            )
            for path, digest in verifier["bundle_sha256"].items():
                self.assertEqual(digest, pc._sha256_file(Path(path)))

            with patch.object(pc, "validate_installed_release", return_value=installed_release):
                pc.validate_native_messaging_manifests(
                    layout,
                    manifest,
                    home_dir=root / "home",
                    native_user_data_dirs=custom_dirs,
                )
            duplicate_profile = custom_dirs[0] / "Profile 1" / "Secure Preferences"
            duplicate_profile.parent.mkdir(parents=True)
            shutil.copy2(
                custom_dirs[0] / "Default" / "Secure Preferences",
                duplicate_profile,
            )
            duplicate_profile.chmod(0o600)
            with patch.object(pc, "validate_installed_release", return_value=installed_release):
                with self.assertRaisesRegex(RuntimeError, "unbound Chrome profile"):
                    pc.install_native_messaging_manifests(
                        layout,
                        manifest,
                        home_dir=root / "home",
                        native_user_data_dirs=custom_dirs,
                        browser_executables=[browser_executable],
                    )
            duplicate_profile.unlink()
            duplicate_profile.parent.rmdir()
            chrome_path = installed["chrome"]
            chrome_value = json.loads(chrome_path.read_text(encoding="utf-8"))
            chrome_value["path"] = "/private/stale/native-host"
            chrome_path.write_text(json.dumps(chrome_value), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "Native Messaging.*pin|Native Messaging.*mismatch"):
                with patch.object(pc, "validate_installed_release", return_value=installed_release):
                    pc.validate_native_messaging_manifests(
                        layout,
                        manifest,
                        home_dir=root / "home",
                        native_user_data_dirs=custom_dirs,
                    )

            chrome_value["path"] = str(layout["native_host_wrapper"])
            chrome_path.write_text(json.dumps(chrome_value), encoding="utf-8")
            chrome_path.chmod(0o600)

            verifier_config["verifiers"]["web"]["sha256"] = "0" * 64
            verifier_path.write_text(json.dumps(verifier_config), encoding="utf-8")
            verifier_path.chmod(0o600)
            with self.assertRaisesRegex(RuntimeError, "verifier.*pin|verifier.*mismatch"):
                with patch.object(pc, "validate_installed_release", return_value=installed_release):
                    pc.validate_native_messaging_manifests(
                        layout,
                        manifest,
                        home_dir=root / "home",
                        native_user_data_dirs=custom_dirs,
                    )

            original_raw = verifier_path.read_bytes()
            with patch.object(
                pc,
                "_runtime_verifier_record",
                return_value=verifier_config["verifiers"]["web"],
            ), patch.object(
                pc,
                "_read_runtime_verifier_config",
                return_value=(verifier_config, original_raw),
            ), patch.object(
                pc,
                "_read_private_bytes_if_present",
                return_value=original_raw + b" ",
            ), patch.object(
                pc, "validate_installed_release", return_value=installed_release
            ):
                with self.assertRaisesRegex(RuntimeError, "changed concurrently"):
                    pc._register_runtime_host_verifier(
                        layout, manifest, home_dir=root / "home"
                    )

            verifier_path.unlink()
            symlink_target = root / "unsafe-verifier-target.json"
            symlink_target.write_bytes(original_raw)
            symlink_target.chmod(0o600)
            verifier_path.symlink_to(symlink_target)
            with patch.object(
                pc, "validate_installed_release", return_value=installed_release
            ):
                with self.assertRaisesRegex(RuntimeError, "opened safely|regular file"):
                    pc._validate_runtime_host_verifier_registration(
                        layout, manifest, home_dir=root / "home"
                    )

            with self.assertRaisesRegex(RuntimeError, "absolute"):
                pc._native_messaging_paths(
                    root / "home",
                    native_user_data_dirs=[Path("relative-chrome-profile")],
                )

    def test_real_release_registers_and_revalidates_runtime_host_verifier(self):
        manifest = self._manifest()
        source_node = dict(manifest["runtime_dependencies"]["node"])
        with tempfile.TemporaryDirectory(dir="/tmp") as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest,
                runtime_root=root / "runtime",
                bridge_socket=root / "socket" / "bridge.sock",
            )
            pc.install_release_bundle(manifest, layout, self._release_sources(root))
            loaded_extension = root / "loaded-extension"
            shutil.copytree(
                layout["host_dir"] / "chrome-host" / "extension",
                loaded_extension,
            )
            for path in loaded_extension.iterdir():
                if path.is_file():
                    path.chmod(0o644)
            self._write_managed_extension_preferences(
                root / "home", loaded_extension
            )
            pc.install_native_messaging_manifests(
                layout,
                manifest,
                home_dir=root / "home",
                browser_executables=[self._browser_executable(root)],
            )
            pc.validate_native_messaging_manifests(
                layout, manifest, home_dir=root / "home"
            )
            config_path = pc._runtime_verifier_config_path(root / "home")
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(config_path.parent.stat().st_mode), 0o700)
            self.assertEqual(len(config["verifiers"]["web"]["bundle_sha256"]), 34)
            for relative in pc.RUNTIME_EXTENSION_EXECUTION_RELATIVE_PATHS:
                self.assertIn(
                    str((loaded_extension / relative).resolve(strict=True)),
                    config["verifiers"]["web"]["bundle_sha256"],
                )
            node = json.loads(
                layout["release_manifest"].read_text(encoding="utf-8")
            )["runtime_dependencies"]["node"]
            self.assertEqual(
                node["path"], str(layout["node_binary"].resolve(strict=True))
            )
            self.assertEqual(stat.S_IMODE(layout["node_binary"].stat().st_mode), 0o700)
            self.assertEqual(node["sha256"], pc._sha256_file(layout["node_binary"]))
            self.assertEqual(node["sha256"], source_node["sha256"])
            self.assertEqual(
                config["verifiers"]["web"]["bundle_sha256"][node["path"]],
                node["sha256"],
            )
            native_wrapper = layout["native_host_wrapper"].read_text(encoding="utf-8")
            verifier_wrapper = layout["runtime_verifier_wrapper"].read_text(
                encoding="utf-8"
            )
            release_node = layout["node_binary"].resolve(strict=True)
            self.assertIn(f"exec {release_node}", native_wrapper)
            self.assertIn(f"exec {release_node}", verifier_wrapper)
            self.assertNotIn(
                self._manifest()["runtime_dependencies"]["node"]["path"],
                native_wrapper,
            )
            self.assertNotIn(
                self._manifest()["runtime_dependencies"]["node"]["path"],
                verifier_wrapper,
            )

            dependency = layout["host_dir"] / "chrome-host" / "client.mjs"
            dependency.chmod(0o640)
            with self.assertRaisesRegex(RuntimeError, "unsafe"):
                pc.validate_native_messaging_manifests(
                    layout, manifest, home_dir=root / "home"
                )
            dependency.chmod(0o600)
            dependency.write_text("// tampered\n", encoding="utf-8")
            dependency.chmod(0o600)
            with self.assertRaisesRegex(RuntimeError, "hash mismatch|not pinned"):
                pc.validate_native_messaging_manifests(
                    layout, manifest, home_dir=root / "home"
                )

    def test_native_and_verifier_publication_rolls_back_as_one_transaction(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory(dir="/tmp") as raw_root:
            root = Path(raw_root)
            home = root / "home"
            layout = pc.production_layout(
                manifest,
                runtime_root=root / "runtime",
                bridge_socket=root / "socket" / "bridge.sock",
            )
            pc.install_release_bundle(manifest, layout, self._release_sources(root))
            self._write_managed_extension_preferences(
                home, layout["host_dir"] / "chrome-host" / "extension"
            )
            paths = pc._native_messaging_paths(home)
            existing = paths["chrome"]
            existing.parent.mkdir(parents=True)
            old_payload = '{"path":"/private/preserved-old-host"}\n'
            existing.write_text(old_payload, encoding="utf-8")
            existing.chmod(0o600)

            with patch.object(
                pc,
                "_register_runtime_host_verifier_locked",
                side_effect=RuntimeError("injected verifier registration failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "injected verifier"):
                    pc.install_native_messaging_manifests(
                        layout,
                        manifest,
                        home_dir=home,
                        browser_executables=[self._browser_executable(root)],
                    )

            self.assertEqual(existing.read_text(encoding="utf-8"), old_payload)
            for browser, path in paths.items():
                if browser != "chrome":
                    self.assertFalse(path.exists(), browser)
            self.assertFalse(pc._runtime_verifier_config_path(home).exists())
            self.assertFalse(
                pc._runtime_verifier_config_path(home)
                .with_name("adaptive-delivery-host-attestation-verifiers.json.lock")
                .exists()
            )

    def test_default_release_sources_and_head_fence_are_canonical_and_fail_closed(self):
        sources = pc.default_release_sources()
        self.assertEqual(
            sources["bridge_binary"],
            pc.CANONICAL_PROJECT_ROOT / "crates" / "local-tool-bridge" / "target" / "release" / "local-tool-bridge",
        )
        self.assertEqual(
            sources["process_runtime_binary"],
            pc.CANONICAL_PROJECT_ROOT
            / "crates"
            / "process-runtime"
            / "target"
            / "release"
            / "process-runtime",
        )
        self.assertEqual(sources["host_source_dir"], pc.CANONICAL_PROJECT_ROOT / "experiments" / "web-host-attestation")
        self.assertEqual(
            sources["tunnel_profile"],
            pc.CANONICAL_PROJECT_ROOT / "experiments" / "web-local-poc" / "tunnel-runtime" / "local-agent-bridge-poc.yaml",
        )
        self.assertIn("lab-catdesk-candidate-e2e", str(sources["catdesk_config"]))
        with patch.object(pc, "_git_head", return_value=REVISION):
            self.assertEqual(pc.require_exact_git_head(REVISION), REVISION)
        with patch.object(pc, "_git_head", return_value="b" * 40):
            with self.assertRaisesRegex(RuntimeError, "HEAD|revision"):
                pc.require_exact_git_head(REVISION)

    def test_tunnel_process_parser_distinguishes_production_from_historical_m0(self):
        production = (
            "/opt/tunnel-client run --profile-dir /private/production/tunnel "
            "--profile local-agent-bridge-production"
        )
        historical = (
            "/opt/tunnel-client run --profile-dir /private/m0/tunnel "
            "--profile local-agent-bridge-poc"
        )
        self.assertEqual(
            pc.parse_tunnel_process(production),
            {"profile_dir": Path("/private/production/tunnel"), "profile_name": "local-agent-bridge-production"},
        )
        self.assertEqual(
            pc.parse_tunnel_process(historical),
            {"profile_dir": Path("/private/m0/tunnel"), "profile_name": "local-agent-bridge-poc"},
        )
        self.assertIsNone(pc.parse_tunnel_process("/opt/tunnel-client run --profile local-agent-bridge-poc"))
        self.assertIsNone(pc.parse_tunnel_process("/opt/other run --profile-dir /x --profile local-agent-bridge-production"))

    def test_macos_procargs2_preserves_spaces_equals_and_exact_argc(self):
        argv = [
            "/Applications/AI-Bridge.app/Contents/MacOS/tunnel-client",
            "run",
            "--health.url-file",
            "/Users/test/Library/Application Support/ai.originone.gpt-bridge/health-url",
            "--mcp.server-url=url=http://127.0.0.1:52342/mcp/slug",
        ]
        raw = (
            struct.pack("=i", len(argv))
            + argv[0].encode()
            + b"\0\0\0"
            + b"\0".join(value.encode() for value in argv)
            + b"\0HOME=/private/home\0"
        )
        self.assertEqual(pc._parse_macos_procargs2(raw), argv)

        truncated = struct.pack("=i", 3) + b"/opt/tunnel-client\0\0arg0\0arg1\0"
        with self.assertRaisesRegex(RuntimeError, "argc|truncated|argv"):
            pc._parse_macos_procargs2(truncated)
        malformed = struct.pack("=i", 1) + b"/opt/tunnel-client-without-nul"
        with self.assertRaisesRegex(RuntimeError, "NUL|malformed|argv"):
            pc._parse_macos_procargs2(malformed)
        with self.assertRaisesRegex(RuntimeError, "argc"):
            pc._parse_macos_procargs2(struct.pack("=i", 0) + b"ignored\0")

    def test_platform_process_argv_fails_closed_on_sysctl_failure_and_malformed_proc(self):
        class FailedLibc:
            def sysctl(self, *_args):
                return -1

        with self.assertRaisesRegex(RuntimeError, "sysctl|argv"):
            pc._macos_process_argv(123, libc=FailedLibc())

        with patch.object(pc.sys, "platform", "linux"), patch.object(
            Path, "read_bytes", return_value=b"/opt/tunnel-client\0arg with space\0--x=a=b\0"
        ):
            self.assertEqual(
                pc._process_argv(123),
                ["/opt/tunnel-client", "arg with space", "--x=a=b"],
            )
        with patch.object(pc.sys, "platform", "linux"), patch.object(
            Path, "read_bytes", return_value=b"/opt/tunnel-client\0unterminated"
        ):
            with self.assertRaisesRegex(RuntimeError, "NUL|argv|malformed"):
                pc._process_argv(123)

    def test_kernel_python_executable_prefers_trusted_macos_framework_app_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            prefix = root / "Python.framework" / "Versions" / "3.11"
            framework_python = prefix / "Resources" / "Python.app" / "Contents" / "MacOS" / "Python"
            framework_python.parent.mkdir(parents=True)
            framework_python.write_bytes(b"framework-python")
            framework_python.chmod(0o755)
            base_python = root / "bin" / "python-base"
            base_python.parent.mkdir()
            base_python.write_bytes(b"base-python")
            base_python.chmod(0o755)
            clt_versions = root / "CommandLineTools" / "Python3.framework" / "Versions"
            clt_python = clt_versions / "3.9" / "Resources" / "Python.app" / "Contents" / "MacOS" / "Python"
            clt_python.parent.mkdir(parents=True)
            clt_python.write_bytes(b"clt-python")
            clt_python.chmod(0o755)
            (clt_versions / "Current").symlink_to("3.9", target_is_directory=True)
            clt_current = clt_versions / "Current" / "Resources" / "Python.app" / "Contents" / "MacOS" / "Python"

            with patch.object(pc.sys, "platform", "darwin"), patch.object(
                pc.sys, "prefix", str(prefix)
            ), patch.object(
                pc, "MACOS_CLT_PYTHON_CURRENT", clt_current
            ), patch.object(pc.sys, "_base_executable", str(base_python), create=True):
                self.assertEqual(pc._kernel_python_executable(), framework_python)

            framework_python.chmod(0o775)
            with patch.object(pc.sys, "platform", "darwin"), patch.object(
                pc.sys, "prefix", str(prefix)
            ), patch.object(
                pc, "MACOS_CLT_PYTHON_CURRENT", clt_current
            ), patch.object(pc.sys, "_base_executable", str(base_python), create=True):
                self.assertEqual(pc._kernel_python_executable(), clt_python.resolve())

            framework_python.unlink()
            framework_python.symlink_to(base_python)
            with patch.object(pc.sys, "platform", "darwin"), patch.object(
                pc.sys, "prefix", str(prefix)
            ), patch.object(
                pc, "MACOS_CLT_PYTHON_CURRENT", root / "missing-clt-python"
            ), patch.object(pc.sys, "_base_executable", str(base_python), create=True):
                with self.assertRaisesRegex(RuntimeError, "trusted macOS Python|Framework|CLT"):
                    pc._kernel_python_executable()

            framework_python.unlink()
            framework_python.write_bytes(b"framework-python")
            framework_python.chmod(0o775)
            clt_python.chmod(0o775)
            with patch.object(pc.sys, "platform", "darwin"), patch.object(
                pc.sys, "prefix", str(prefix)
            ), patch.object(
                pc, "MACOS_CLT_PYTHON_CURRENT", clt_current
            ), patch.object(pc.sys, "_base_executable", str(base_python), create=True):
                with self.assertRaisesRegex(RuntimeError, "trusted macOS Python|Framework|CLT"):
                    pc._kernel_python_executable()

            with patch.object(pc.sys, "platform", "linux"), patch.object(
                pc.sys, "prefix", str(prefix)
            ), patch.object(pc.sys, "_base_executable", str(base_python), create=True):
                self.assertEqual(pc._kernel_python_executable(), base_python.resolve())

            base_python.chmod(0o777)
            with patch.object(pc.sys, "platform", "linux"), patch.object(
                pc.sys, "_base_executable", str(base_python), create=True
            ):
                with self.assertRaisesRegex(RuntimeError, "base Python.*trusted|writable|unsafe"):
                    pc._kernel_python_executable()

    def test_global_tunnel_id_discovery_covers_direct_flags_and_rejects_unknown_forms(self):
        direct_argv = [
            "/opt/tunnel-client",
            "run",
            "--control-plane.tunnel-id",
            "tunnel-target",
            "--control-plane.base-url",
            "https://api.openai.com",
            "--control-plane.api-key",
            "file:/private/key",
            "--mcp.server-url",
            "url=http://127.0.0.1:52342/mcp/private-slug",
            "--health.listen-addr",
            "127.0.0.1:8080",
            "--log.file",
            "/dev/null",
            "--log.level",
            "error",
        ]
        ps = subprocess.CompletedProcess(
            [], 0, stdout="28459 /opt/tunnel-client\n", stderr=""
        )
        identity = self._identity(28459, direct_argv)
        with patch.object(pc.subprocess, "run", return_value=ps), patch.object(
            pc, "_process_argv", return_value=direct_argv
        ), patch.object(
            pc, "_capture_process_identity", return_value=identity
        ):
            found = pc._discover_tunnels_by_id("tunnel-target")
        self.assertEqual(found[0]["pid"], 28459)
        self.assertEqual(found[0]["tunnel_id"], "tunnel-target")
        self.assertEqual(found[0]["process_identity"], identity)
        self.assertEqual(found[0]["command"], pc.shlex.join(direct_argv))

        equals_argv = [
            "/opt/tunnel-client",
            "run",
            "--control-plane.tunnel-id=tunnel-target",
            "--control-plane.base-url=https://api.openai.com",
            "--control-plane.api-key=file:/private/key",
            "--mcp.server-url=unix-socket=/tmp/production.sock",
            "--health.listen-addr=127.0.0.1:0",
            "--log.file=/dev/null",
        ]
        ps = subprocess.CompletedProcess(
            [], 0, stdout="28461 /opt/tunnel-client\n", stderr=""
        )
        with patch.object(pc.subprocess, "run", return_value=ps), patch.object(
            pc, "_process_argv", return_value=equals_argv
        ), patch.object(
            pc, "_capture_process_identity", return_value=self._identity(28461, equals_argv)
        ):
            found = pc._discover_tunnels_by_id(
                "tunnel-target", expected_socket=Path("/tmp/production.sock")
            )
        self.assertEqual([item["pid"] for item in found], [28461])

        http_only = [
            "/opt/tunnel-client", "run",
            "--control-plane.tunnel-id=tunnel-target",
            "--control-plane.base-url=https://api.openai.com",
            "--mcp.server-url=url=http://127.0.0.1:52342/mcp/private",
            "--health.listen-addr=127.0.0.1:0",
        ]
        ps = subprocess.CompletedProcess(
            [], 0, stdout="28462 /opt/tunnel-client\n", stderr=""
        )
        with patch.object(pc.subprocess, "run", return_value=ps), patch.object(
            pc, "_process_argv", return_value=http_only
        ), patch.object(
            pc, "_capture_process_identity", return_value=self._identity(28462, http_only)
        ):
            owners = pc._discover_tunnels_by_id(
                "tunnel-target", expected_socket=Path("/tmp/production.sock")
            )
        self.assertEqual([item["pid"] for item in owners], [28462])
        self.assertFalse(owners[0]["expected_endpoint_matches"])
        with patch.object(pc, "_discover_tunnels_by_id", return_value=owners):
            with self.assertRaisesRegex(RuntimeError, "endpoint|socket|ownership"):
                pc._require_exact_tunnel_id_owners(
                    "tunnel-target", {28462}, expected_socket=Path("/tmp/production.sock")
                )

        different_id = [value.replace("tunnel-target", "tunnel-other") for value in http_only]
        ps = subprocess.CompletedProcess(
            [], 0, stdout="28464 /opt/tunnel-client\n", stderr=""
        )
        with patch.object(pc.subprocess, "run", return_value=ps), patch.object(
            pc, "_process_argv", return_value=different_id
        ):
            self.assertEqual(
                pc._discover_tunnels_by_id(
                    "tunnel-target", expected_socket=Path("/tmp/production.sock")
                ),
                [],
            )

        for bad_tail in (
            ["--control-plane.tunnel-id=tunnel-target", "--control-plane.tunnel-id", "tunnel-target"],
            ["--control-plane.tunnel-id="],
            ["--profile", "local-agent-bridge-poc", "--control-plane.tunnel-id", "tunnel-target"],
        ):
            with self.subTest(bad_tail=bad_tail):
                command = ["/opt/tunnel-client", "run", *bad_tail]
                ps = subprocess.CompletedProcess([], 0, stdout="28463 /opt/tunnel-client\n", stderr="")
                with patch.object(pc.subprocess, "run", return_value=ps), patch.object(
                    pc, "_process_argv", return_value=command
                ):
                    with self.assertRaisesRegex(RuntimeError, "duplicate|empty|mixed|canonical"):
                        pc._discover_tunnels_by_id("tunnel-target")

        unknown = subprocess.CompletedProcess(
            [],
            0,
            stdout="28460 /opt/tunnel-client\n",
            stderr="",
        )
        unknown_argv = [
            "/opt/tunnel-client", "run", "--control-plane.tunnel-id", "tunnel-other",
            "--unknown.future-flag", "value",
        ]
        with patch.object(pc.subprocess, "run", return_value=unknown), patch.object(
            pc, "_process_argv", return_value=unknown_argv
        ):
            with self.assertRaisesRegex(RuntimeError, "unknown|cannot prove|unsupported"):
                pc._discover_tunnels_by_id("tunnel-target")

    def test_historical_m0_stop_requires_one_exact_live_command_and_rechecks_before_signal(self):
        item = {
            "pid": 17,
            "command": "/opt/tunnel-client run --profile-dir /private/m0 --profile local-agent-bridge-poc",
            "profile_dir": "/private/m0",
            "profile_name": "local-agent-bridge-poc",
            "profile_path": "/private/m0/local-agent-bridge-poc.yaml",
            "profile_sha256": "a" * 64,
            "expected_socket": str(pc.HISTORICAL_M0_BRIDGE_SOCKET),
            "tunnel_id": "tunnel-existing",
        }
        with patch.object(pc, "_discover_tunnel_processes", return_value=[item]):
            self.assertEqual(
                pc.find_verified_historical_m0_tunnel(
                    Path("/private/m0"), profile_name="local-agent-bridge-poc"
                )["pid"],
                17,
            )
        with patch.object(pc, "_discover_tunnel_processes", return_value=[item, {**item, "pid": 18}]):
            with self.assertRaisesRegex(RuntimeError, "multiple"):
                pc.find_verified_historical_m0_tunnel(
                    Path("/private/m0"), profile_name="local-agent-bridge-poc"
                )
        with patch.object(pc, "_pid_alive", return_value=True), patch.object(
            pc, "_process_identity_matches", return_value=False
        ), patch.object(pc.os, "kill") as kill:
            with self.assertRaisesRegex(RuntimeError, "command changed"):
                pc._stop_verified_tunnel(item)
        kill.assert_not_called()

    @unittest.skipUnless(
        Path("/tmp").is_symlink()
        and Path("/tmp").resolve() == Path("/private/tmp"),
        "requires the macOS /tmp -> /private/tmp system alias",
    )
    def test_process_owned_unix_socket_accepts_only_the_macos_tmp_alias(self):
        raw_root = tempfile.mkdtemp(prefix="lab-socket-alias-", dir="/tmp")
        root = Path(raw_root)
        live_socket = root / "bridge.sock"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(str(live_socket))
            live_socket.chmod(0o600)
            identity = pc._capture_process_identity(os.getpid())
            self.assertTrue(pc._trusted_macos_tmp_alias())
            with patch.object(pc.os, "readlink", return_value="/private/tmp"):
                self.assertFalse(pc._trusted_macos_tmp_alias())
            pc._verify_process_owned_unix_socket(identity, live_socket, "test Bridge")

            real_parent = root / "real-parent"
            real_parent.mkdir()
            linked_parent = root / "linked-parent"
            linked_parent.symlink_to(real_parent, target_is_directory=True)
            linked_socket = real_parent / "linked.sock"
            second = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                second.bind(str(linked_socket))
                linked_socket.chmod(0o600)
                with self.assertRaisesRegex(RuntimeError, "Unix socket is unsafe"):
                    pc._verify_process_owned_unix_socket(
                        identity,
                        linked_parent / linked_socket.name,
                        "symlinked Bridge",
                    )
            finally:
                second.close()

            target_socket = root / "target.sock"
            socket_alias = root / "socket-alias.sock"
            third = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                third.bind(str(target_socket))
                target_socket.chmod(0o600)
                socket_alias.symlink_to(target_socket.name)
                with self.assertRaisesRegex(RuntimeError, "Unix socket is unsafe"):
                    pc._verify_process_owned_unix_socket(
                        identity,
                        socket_alias,
                        "symlinked socket",
                    )
            finally:
                third.close()
        finally:
            server.close()
            shutil.rmtree(root, ignore_errors=True)

    def test_managed_http_direct_tunnel_uses_owned_health_status_only_to_classify_id(self):
        managed = [
            "/Applications/AI-Bridge.app/Contents/MacOS/tunnel-client",
            "run",
            "--mcp.server-url",
            "http://127.0.0.1:52342/mcp/private",
            "--cloudflared.path",
            "/Applications/AI-Bridge.app/Contents/MacOS/cloudflared",
            "--health.listen-addr",
            "127.0.0.1:0",
            "--health.url-file",
            "/Users/test/Library/Application Support/ai.originone.gpt-bridge/health-url",
            "--log.level",
            "warn",
            "--log.file",
            "/private/managed.log",
        ]
        ps = subprocess.CompletedProcess([], 0, stdout="28393 /Applications/AI-Bridge.app/Contents/MacOS/tunnel-client\n", stderr="")
        parsed_health = pc.urllib.parse.urlsplit("http://127.0.0.1:52354")
        with patch.object(pc.subprocess, "run", return_value=ps), patch.object(
            pc, "_process_argv", return_value=managed
        ), patch.object(
            pc, "_read_loopback_url_file", return_value=("http://127.0.0.1:52354", parsed_health)
        ), patch.object(
            pc, "_request_json", return_value={"control_plane_tunnel_id": "tunnel-other"}
        ), patch.object(pc, "_capture_process_identity") as capture:
            self.assertEqual(pc._discover_tunnels_by_id("tunnel-target"), [])
        capture.assert_not_called()

        with patch.object(pc.subprocess, "run", return_value=ps), patch.object(
            pc, "_process_argv", return_value=managed
        ), patch.object(
            pc, "_read_loopback_url_file", return_value=("http://127.0.0.1:52354", parsed_health)
        ), patch.object(
            pc, "_request_json", return_value={"control_plane_tunnel_id": "tunnel-target"}
        ), patch.object(
            pc, "_capture_process_identity", return_value=self._identity(28393, managed)
        ):
            owners = pc._discover_tunnels_by_id(
                "tunnel-target", expected_socket=Path("/tmp/production.sock")
            )
        self.assertEqual([item["pid"] for item in owners], [28393])
        self.assertFalse(owners[0]["expected_endpoint_matches"])

    def test_stop_verified_tunnel_rechecks_profile_hash_and_tunnel_id_on_every_path(self):
        identity = self._identity(
            17,
            [
                "/opt/tunnel-client",
                "run",
                "--profile-dir",
                "/private/m0",
                "--profile",
                "local-agent-bridge-poc",
            ],
        )
        item = {
            "pid": 17,
            "command": " ".join(identity["argv"]),
            "profile_dir": "/private/m0",
            "profile_name": "local-agent-bridge-poc",
            "profile_path": "/private/m0/local-agent-bridge-poc.yaml",
            "profile_sha256": "a" * 64,
            "expected_socket": str(pc.HISTORICAL_M0_BRIDGE_SOCKET),
            "tunnel_id": "tunnel-existing",
            "process_identity": identity,
        }
        with patch.object(pc, "_pid_alive", return_value=True), patch.object(
            pc, "_process_identity_matches", return_value=True
        ), patch.object(
            pc, "_parse_verified_tunnel_process", return_value=dict(item)
        ), patch.object(
            pc,
            "_validate_tunnel_profile",
            return_value={"profile_sha256": "b" * 64, "tunnel_id": "tunnel-existing"},
        ), patch.object(pc, "_stop_verified_process_identity") as stop:
            with self.assertRaisesRegex(RuntimeError, "profile.*changed|hash"):
                pc._stop_verified_tunnel(item)
        stop.assert_not_called()

        with patch.object(pc, "_pid_alive", return_value=True), patch.object(
            pc, "_process_identity_matches", return_value=True
        ), patch.object(
            pc, "_parse_verified_tunnel_process", return_value=dict(item)
        ), patch.object(
            pc,
            "_validate_tunnel_profile",
            return_value={"profile_sha256": "a" * 64, "tunnel_id": "tunnel-changed"},
        ), patch.object(pc, "_stop_verified_process_identity") as stop:
            with self.assertRaisesRegex(RuntimeError, "tunnel.*changed|tunnel.*id"):
                pc._stop_verified_tunnel(item)
        stop.assert_not_called()

    def test_production_tunnel_status_requires_exact_profile_process_and_generation_state(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(manifest, runtime_root=root / "runtime", bridge_socket=root / "bridge.sock")
            layout["tunnel_dir"].mkdir(parents=True)
            profile = {
                "control_plane": {
                    "tunnel_id": "tunnel-production",
                    "base_url": "https://api.openai.com",
                    "api_key": "file:/private/key",
                },
                "mcp": {"server_urls": [{"unix_socket": str(layout["bridge_socket"])}]},
            }
            layout["tunnel_profile"].write_text(json.dumps(profile), encoding="utf-8")
            layout["tunnel_profile"].chmod(0o600)
            identity = {
                "pid": 717,
                "uid": os.geteuid(),
                "lstart": "Thu Sep 11 12:00:00 2026",
                "start_timestamp": 1789099200.0,
                "pgid": 717,
                "exe_realpath": "/opt/tunnel-client",
                "exe_inode": 123,
                "exe_sha256": "a" * 64,
                "argv": [
                    "/opt/tunnel-client", "run", "--profile-dir", str(layout["tunnel_dir"]),
                    "--profile", pc.TUNNEL_PROFILE_NAME,
                ],
            }
            layout["runtime_state"].parent.mkdir(parents=True, exist_ok=True)
            layout["runtime_state"].write_text(json.dumps({
                "release_revision": REVISION,
                "endpoint_generation": 7,
                "tunnel_profile_name": pc.TUNNEL_PROFILE_NAME,
                "tunnel_pid": 717,
                "tunnel_id": "tunnel-production",
                "tunnel_profile_sha256": pc._sha256_file(layout["tunnel_profile"]),
                "tunnel_started_at": 1000.0,
                "tunnel_process_identity": identity,
            }), encoding="utf-8")
            layout["runtime_state"].chmod(0o600)
            discovered = [{
                "pid": 717,
                "command": f"/opt/tunnel-client run --profile-dir {layout['tunnel_dir']} --profile {pc.TUNNEL_PROFILE_NAME}",
                "profile_dir": str(layout["tunnel_dir"]),
                "profile_path": str(layout["tunnel_profile"]),
                "profile_sha256": pc._sha256_file(layout["tunnel_profile"]),
                "tunnel_id": "tunnel-production",
                "process_identity": identity,
            }]
            with patch.object(pc, "_discover_tunnel_processes", return_value=discovered), patch.object(
                pc, "_discover_tunnels_by_id", return_value=list(discovered)
            ), patch.object(
                pc, "_verify_tunnel_operational", return_value={"ok": True}
            ):
                status = pc.production_tunnel_status(manifest, layout)
            self.assertTrue(status["ready"])
            self.assertEqual(status["pid"], 717)
            self.assertEqual(status["generation"], 7)

            with patch.object(pc, "_discover_tunnel_processes", return_value=discovered), patch.object(
                pc, "_discover_tunnels_by_id", return_value=[*discovered, {"pid": 719}]
            ), patch.object(pc, "_verify_tunnel_operational", return_value={"ok": True}):
                status = pc.production_tunnel_status(manifest, layout)
            self.assertFalse(status["ready"])
            self.assertRegex(status["reason"], "globally exclusive|ownership")

            discovered.append({**discovered[0], "pid": 718})
            with patch.object(pc, "_discover_tunnel_processes", return_value=discovered), patch.object(
                pc, "_discover_tunnels_by_id", return_value=list(discovered)
            ), patch.object(
                pc, "_verify_tunnel_operational", return_value={"ok": True}
            ):
                status = pc.production_tunnel_status(manifest, layout)
            self.assertFalse(status["ready"])
            self.assertEqual(status["reason"], "multiple_active_tunnels")

    def test_production_tunnel_status_names_runtime_json_pid_and_generation_on_mismatch(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest, runtime_root=root / "runtime", bridge_socket=root / "bridge.sock"
            )
            layout["tunnel_dir"].mkdir(parents=True)
            profile = {
                "control_plane": {
                    "tunnel_id": "tunnel-production",
                    "base_url": "https://api.openai.com",
                    "api_key": "file:/private/key",
                },
                "mcp": {"server_urls": [{"unix_socket": str(layout["bridge_socket"])}]},
            }
            layout["tunnel_profile"].write_text(json.dumps(profile), encoding="utf-8")
            layout["tunnel_profile"].chmod(0o600)
            live_identity = {
                "pid": 717,
                "uid": os.geteuid(),
                "lstart": "Sat Sep 19 00:22:44 2026",
                "start_timestamp": 1789748564.0,
                "pgid": 717,
                "exe_realpath": "/opt/tunnel-client",
                "exe_inode": 123,
                "exe_sha256": "a" * 64,
                "argv": [
                    "/opt/tunnel-client", "run", "--profile-dir", str(layout["tunnel_dir"]),
                    "--profile", pc.TUNNEL_PROFILE_NAME,
                ],
            }
            recorded_identity = {
                **live_identity,
                "pid": 716,
                "pgid": 716,
                "lstart": "Fri Sep 18 22:48:11 2026",
                "start_timestamp": 1789742891.0,
            }
            layout["runtime_state"].parent.mkdir(parents=True, exist_ok=True)
            layout["runtime_state"].write_text(json.dumps({
                "release_revision": REVISION,
                "endpoint_generation": 7,
                "tunnel_profile_name": pc.TUNNEL_PROFILE_NAME,
                "tunnel_pid": 716,
                "tunnel_id": "tunnel-production",
                "tunnel_profile_sha256": pc._sha256_file(layout["tunnel_profile"]),
                "tunnel_started_at": 1000.0,
                "tunnel_process_identity": recorded_identity,
            }), encoding="utf-8")
            layout["runtime_state"].chmod(0o600)
            discovered = [{
                "pid": 717,
                "command": f"/opt/tunnel-client run --profile-dir {layout['tunnel_dir']} --profile {pc.TUNNEL_PROFILE_NAME}",
                "profile_dir": str(layout["tunnel_dir"]),
                "profile_path": str(layout["tunnel_profile"]),
                "profile_sha256": pc._sha256_file(layout["tunnel_profile"]),
                "tunnel_id": "tunnel-production",
                "process_identity": live_identity,
            }]
            with patch.object(pc, "_discover_tunnel_processes", return_value=discovered), patch.object(
                pc, "_discover_tunnels_by_id", return_value=list(discovered)
            ), patch.object(pc, "_pid_alive", return_value=False), patch.object(
                pc, "_verify_tunnel_operational", return_value={"ok": True}
            ):
                status = pc.production_tunnel_status(manifest, layout)
            self.assertFalse(status["ready"])
            reason = status["reason"]
            self.assertTrue(reason.startswith("runtime_state_mismatch:"))
            self.assertIn(f"state={layout['runtime_state']}", reason)
            self.assertIn("endpoint_generation recorded=7 expected=7", reason)
            self.assertIn("tunnel_pid recorded=716 recorded_alive=false live=717", reason)
            self.assertIn("tunnel_process_identity keys=lstart,pgid,pid,start_timestamp", reason)
            self.assertNotIn("release_revision recorded=", reason)

    def _dead_pid_live_exclusive_fixture(self, root, *, recorded_pid=716, live_pid=717, generation=7, tunnel_id="tunnel-production", recorded_tunnel_id=None, recorded_generation=None, recorded_profile_name=None, recorded_profile_sha256=None):
        manifest = self._manifest(generation)
        layout = pc.production_layout(
            manifest, runtime_root=root / "runtime", bridge_socket=root / "bridge.sock"
        )
        layout["tunnel_dir"].mkdir(parents=True)
        profile = {
            "control_plane": {
                "tunnel_id": tunnel_id,
                "base_url": "https://api.openai.com",
                "api_key": "file:/private/key",
            },
            "mcp": {"server_urls": [{"unix_socket": str(layout["bridge_socket"])}]},
        }
        layout["tunnel_profile"].write_text(json.dumps(profile), encoding="utf-8")
        layout["tunnel_profile"].chmod(0o600)
        profile_sha256 = pc._sha256_file(layout["tunnel_profile"])
        live_identity = {
            "pid": live_pid,
            "uid": os.geteuid(),
            "lstart": "Sat Sep 19 00:22:44 2026",
            "start_timestamp": 1789748564.0,
            "pgid": live_pid,
            "exe_realpath": "/opt/tunnel-client",
            "exe_inode": 123,
            "exe_sha256": "a" * 64,
            "argv": [
                "/opt/tunnel-client", "run", "--profile-dir", str(layout["tunnel_dir"]),
                "--profile", pc.TUNNEL_PROFILE_NAME,
            ],
        }
        recorded_identity = {
            **live_identity,
            "pid": recorded_pid,
            "pgid": recorded_pid,
            "lstart": "Fri Sep 18 22:48:11 2026",
            "start_timestamp": 1789742891.0,
        }
        layout["runtime_state"].parent.mkdir(parents=True, exist_ok=True)
        state = {
            "release_revision": REVISION,
            "endpoint_generation": generation if recorded_generation is None else recorded_generation,
            "workspace": str(pc.canonical_workspace()),
            "tunnel_profile_name": pc.TUNNEL_PROFILE_NAME if recorded_profile_name is None else recorded_profile_name,
            "tunnel_pid": recorded_pid,
            "tunnel_id": tunnel_id if recorded_tunnel_id is None else recorded_tunnel_id,
            "tunnel_profile_sha256": profile_sha256 if recorded_profile_sha256 is None else recorded_profile_sha256,
            "tunnel_started_at": 1000.0,
            "tunnel_process_identity": recorded_identity,
            "bridge_pid": 62454,
        }
        layout["runtime_state"].write_text(json.dumps(state), encoding="utf-8")
        layout["runtime_state"].chmod(0o600)
        discovered = [{
            "pid": live_pid,
            "command": f"/opt/tunnel-client run --profile-dir {layout['tunnel_dir']} --profile {pc.TUNNEL_PROFILE_NAME}",
            "profile_dir": str(layout["tunnel_dir"].resolve()),
            "profile_path": str(layout["tunnel_profile"]),
            "profile_sha256": profile_sha256,
            "tunnel_id": tunnel_id,
            "process_identity": live_identity,
        }]
        return manifest, layout, state, discovered, live_identity

    def test_reconcile_dead_recorded_tunnel_pid_to_live_exclusive_rewrites_only_pid_and_identity(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            manifest, layout, before, discovered, live_identity = self._dead_pid_live_exclusive_fixture(root)
            before_bytes = layout["runtime_state"].read_bytes()
            with patch.object(pc, "_discover_tunnel_processes", return_value=discovered), patch.object(
                pc, "_discover_tunnels_by_id", return_value=list(discovered)
            ), patch.object(pc, "_pid_alive", return_value=False), patch.object(
                pc, "_verify_tunnel_operational", return_value={"ok": True}
            ):
                result = pc.reconcile_dead_recorded_tunnel_pid_to_live_exclusive(manifest, layout)
                status = pc.production_tunnel_status(manifest, layout)
            self.assertTrue(result["reconciled"])
            self.assertFalse(result["tunnel_ready"])
            self.assertEqual(result["recorded_pid_before"], 716)
            self.assertEqual(result["live_pid"], 717)
            state = json.loads(layout["runtime_state"].read_text(encoding="utf-8"))
            self.assertEqual(state["tunnel_pid"], 717)
            self.assertEqual(state["tunnel_process_identity"], live_identity)
            self.assertEqual(state["tunnel_started_at"], 1000.0)
            self.assertEqual(state["tunnel_id"], before["tunnel_id"])
            self.assertEqual(state["endpoint_generation"], before["endpoint_generation"])
            self.assertEqual(state["bridge_pid"], 62454)
            self.assertNotEqual(layout["runtime_state"].read_bytes(), before_bytes)
            self.assertTrue(status["ready"])
            self.assertEqual(status["pid"], 717)
            self.assertIsNone(status["reason"])
            self.assertNotIn("716", json.dumps(status))

    def test_reconcile_dead_recorded_tunnel_pid_fail_closed_when_recorded_pid_alive(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            manifest, layout, _, discovered, _ = self._dead_pid_live_exclusive_fixture(root)
            before = layout["runtime_state"].read_bytes()
            with patch.object(pc, "_discover_tunnel_processes", return_value=discovered), patch.object(
                pc, "_discover_tunnels_by_id", return_value=list(discovered)
            ), patch.object(pc, "_pid_alive", return_value=True):
                with self.assertRaisesRegex(RuntimeError, "recorded pid 716 is still alive"):
                    pc.reconcile_dead_recorded_tunnel_pid_to_live_exclusive(manifest, layout)
            self.assertEqual(layout["runtime_state"].read_bytes(), before)

    def test_reconcile_dead_recorded_tunnel_pid_fail_closed_on_tunnel_id_or_generation_mismatch(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            manifest, layout, _, discovered, _ = self._dead_pid_live_exclusive_fixture(
                root, recorded_tunnel_id="tunnel-other"
            )
            before = layout["runtime_state"].read_bytes()
            with patch.object(pc, "_discover_tunnel_processes", return_value=discovered), patch.object(
                pc, "_discover_tunnels_by_id", return_value=list(discovered)
            ), patch.object(pc, "_pid_alive", return_value=False):
                with self.assertRaisesRegex(RuntimeError, "recorded tunnel_id does not match live exclusive"):
                    pc.reconcile_dead_recorded_tunnel_pid_to_live_exclusive(manifest, layout)
            self.assertEqual(layout["runtime_state"].read_bytes(), before)

        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            manifest, layout, _, discovered, _ = self._dead_pid_live_exclusive_fixture(
                root, recorded_generation=6
            )
            before = layout["runtime_state"].read_bytes()
            with patch.object(pc, "_discover_tunnel_processes", return_value=discovered), patch.object(
                pc, "_discover_tunnels_by_id", return_value=list(discovered)
            ), patch.object(pc, "_pid_alive", return_value=False):
                with self.assertRaisesRegex(RuntimeError, "endpoint generation does not match live exclusive"):
                    pc.reconcile_dead_recorded_tunnel_pid_to_live_exclusive(manifest, layout)
            self.assertEqual(layout["runtime_state"].read_bytes(), before)

    def test_reconcile_dead_recorded_tunnel_pid_fail_closed_without_exclusive_live_tunnel(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            manifest, layout, _, discovered, _ = self._dead_pid_live_exclusive_fixture(root)
            before = layout["runtime_state"].read_bytes()
            extra = {**discovered[0], "pid": 718}
            with patch.object(pc, "_discover_tunnel_processes", return_value=[*discovered, extra]), patch.object(
                pc, "_discover_tunnels_by_id", return_value=[*discovered, extra]
            ), patch.object(pc, "_pid_alive", return_value=False):
                with self.assertRaisesRegex(RuntimeError, "unique live exclusive tunnel"):
                    pc.reconcile_dead_recorded_tunnel_pid_to_live_exclusive(manifest, layout)
            self.assertEqual(layout["runtime_state"].read_bytes(), before)

            owners = [{**discovered[0], "pid": 719}]
            with patch.object(pc, "_discover_tunnel_processes", return_value=discovered), patch.object(
                pc, "_discover_tunnels_by_id", return_value=owners
            ), patch.object(pc, "_pid_alive", return_value=False):
                with self.assertRaisesRegex(RuntimeError, "not globally exclusive"):
                    pc.reconcile_dead_recorded_tunnel_pid_to_live_exclusive(manifest, layout)
            self.assertEqual(layout["runtime_state"].read_bytes(), before)

    def _dead_bridge_pid_live_exclusive_fixture(
        self,
        root,
        *,
        recorded_pid=62454,
        live_pid=1130,
        generation=7,
        recorded_generation=None,
        recorded_revision=None,
    ):
        manifest = self._manifest(generation)
        layout = pc.production_layout(
            manifest, runtime_root=root / "runtime", bridge_socket=root / "bridge.sock"
        )
        layout["bridge_binary"].parent.mkdir(parents=True, exist_ok=True)
        layout["bridge_binary"].write_bytes(b"production-bridge")
        layout["bridge_binary"].chmod(0o700)
        expected_binary = str(layout["bridge_binary"].resolve(strict=True))
        live_identity = {
            **self._identity(live_pid, [expected_binary]),
            "lstart": "Sat Sep 19 18:10:49 2026",
            "start_timestamp": 1789812649.0,
        }
        recorded_identity = {
            **self._identity(recorded_pid, [expected_binary]),
            "lstart": "Sat Sep 19 17:23:10 2026",
            "start_timestamp": 1789809790.0,
        }
        layout["runtime_state"].parent.mkdir(parents=True, exist_ok=True)
        state = {
            "release_revision": REVISION if recorded_revision is None else recorded_revision,
            "endpoint_generation": generation if recorded_generation is None else recorded_generation,
            "workspace": str(pc.canonical_workspace()),
            "tunnel_profile_name": pc.TUNNEL_PROFILE_NAME,
            "tunnel_pid": 47423,
            "tunnel_id": "tunnel-production",
            "process_runtime_pid": 61002,
            "bridge_pid": recorded_pid,
            "bridge_process_identity": recorded_identity,
            "bridge_capability_id": "bridge-cap-production-keep",
            "bridge_execution_session_id": "bridge-session-production-keep",
        }
        layout["runtime_state"].write_text(json.dumps(state), encoding="utf-8")
        layout["runtime_state"].chmod(0o600)
        return manifest, layout, state, live_identity

    def test_reconcile_dead_recorded_bridge_pid_to_live_exclusive_rewrites_only_pid_and_identity(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            manifest, layout, before, live_identity = self._dead_bridge_pid_live_exclusive_fixture(root)
            before_bytes = layout["runtime_state"].read_bytes()
            with patch.object(
                pc, "_unique_process_identity_for_executable", return_value=live_identity
            ), patch.object(
                pc, "_verify_process_owned_unix_socket"
            ) as verify_socket, patch.object(
                pc, "_pid_alive", return_value=False
            ):
                result = pc.reconcile_dead_recorded_bridge_pid_to_live_exclusive(manifest, layout)
            self.assertTrue(result["reconciled"])
            self.assertFalse(result["bridge_ready"])
            self.assertEqual(result["recorded_pid_before"], 62454)
            self.assertEqual(result["live_pid"], 1130)
            verify_socket.assert_called()
            state = json.loads(layout["runtime_state"].read_text(encoding="utf-8"))
            self.assertEqual(state["bridge_pid"], 1130)
            self.assertEqual(state["bridge_process_identity"], live_identity)
            self.assertEqual(state["tunnel_pid"], 47423)
            self.assertEqual(state["process_runtime_pid"], 61002)
            self.assertEqual(state["bridge_capability_id"], before["bridge_capability_id"])
            self.assertEqual(
                state["bridge_execution_session_id"], before["bridge_execution_session_id"]
            )
            self.assertEqual(state["endpoint_generation"], before["endpoint_generation"])
            self.assertNotEqual(layout["runtime_state"].read_bytes(), before_bytes)

    def test_reconcile_dead_recorded_bridge_pid_fail_closed_when_recorded_pid_alive(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            manifest, layout, _, live_identity = self._dead_bridge_pid_live_exclusive_fixture(root)
            before = layout["runtime_state"].read_bytes()
            with patch.object(
                pc, "_unique_process_identity_for_executable", return_value=live_identity
            ), patch.object(
                pc, "_verify_process_owned_unix_socket"
            ), patch.object(
                pc, "_pid_alive", return_value=True
            ):
                with self.assertRaisesRegex(RuntimeError, "recorded pid 62454 is still alive"):
                    pc.reconcile_dead_recorded_bridge_pid_to_live_exclusive(manifest, layout)
            self.assertEqual(layout["runtime_state"].read_bytes(), before)
            self.assertFalse(
                json.loads(layout["runtime_state"].read_text(encoding="utf-8")).get("bridge_ready")
            )

    def test_reconcile_dead_recorded_bridge_pid_fail_closed_on_generation_or_revision_mismatch(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            manifest, layout, _, live_identity = self._dead_bridge_pid_live_exclusive_fixture(
                root, recorded_generation=6
            )
            before = layout["runtime_state"].read_bytes()
            with patch.object(
                pc, "_unique_process_identity_for_executable", return_value=live_identity
            ), patch.object(
                pc, "_verify_process_owned_unix_socket"
            ), patch.object(
                pc, "_pid_alive", return_value=False
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "endpoint generation does not match live exclusive"
                ):
                    pc.reconcile_dead_recorded_bridge_pid_to_live_exclusive(manifest, layout)
            self.assertEqual(layout["runtime_state"].read_bytes(), before)

        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            manifest, layout, _, live_identity = self._dead_bridge_pid_live_exclusive_fixture(
                root, recorded_revision="b" * 40
            )
            before = layout["runtime_state"].read_bytes()
            with patch.object(
                pc, "_unique_process_identity_for_executable", return_value=live_identity
            ), patch.object(
                pc, "_verify_process_owned_unix_socket"
            ), patch.object(
                pc, "_pid_alive", return_value=False
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "release revision does not match live exclusive"
                ):
                    pc.reconcile_dead_recorded_bridge_pid_to_live_exclusive(manifest, layout)
            self.assertEqual(layout["runtime_state"].read_bytes(), before)

    def test_reconcile_dead_recorded_bridge_pid_fail_closed_without_exclusive_live_bridge(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            manifest, layout, _, live_identity = self._dead_bridge_pid_live_exclusive_fixture(root)
            before = layout["runtime_state"].read_bytes()
            with patch.object(
                pc,
                "_unique_process_identity_for_executable",
                side_effect=RuntimeError("production Bridge executable must have exactly one live process"),
            ), patch.object(
                pc, "_pid_alive", return_value=False
            ):
                with self.assertRaisesRegex(RuntimeError, "unique live exclusive Bridge"):
                    pc.reconcile_dead_recorded_bridge_pid_to_live_exclusive(manifest, layout)
            self.assertEqual(layout["runtime_state"].read_bytes(), before)

            with patch.object(
                pc, "_unique_process_identity_for_executable", return_value=live_identity
            ), patch.object(
                pc,
                "_verify_process_owned_unix_socket",
                side_effect=RuntimeError("production Bridge Unix socket owner identity mismatch"),
            ), patch.object(
                pc, "_pid_alive", return_value=False
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "does not own the production Bridge socket"
                ):
                    pc.reconcile_dead_recorded_bridge_pid_to_live_exclusive(manifest, layout)
            self.assertEqual(layout["runtime_state"].read_bytes(), before)

    def test_production_status_reconciles_dead_recorded_bridge_pid_then_uses_live_pid(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            manifest, layout, _, live_identity = self._dead_bridge_pid_live_exclusive_fixture(
                root, recorded_pid=1130, live_pid=12057
            )
            layout["bridge_socket"].write_bytes(b"")
            layout["bridge_socket"].chmod(0o600)

            def pid_alive(pid):
                return pid == 12057

            def identity_matches(identity):
                return identity.get("pid") == 12057

            with patch.object(
                pc, "_unique_process_identity_for_executable", return_value=live_identity
            ), patch.object(
                pc, "_verify_process_owned_unix_socket"
            ), patch.object(
                pc, "_pid_alive", side_effect=pid_alive
            ), patch.object(
                pc, "_process_identity_matches", side_effect=identity_matches
            ), patch.object(
                pc, "smoke_tools", return_value=sorted(pc.REQUIRED_TOOLS)
            ), patch.object(
                pc,
                "_bridge_capability_lease_status",
                return_value={
                    "ready": True,
                    "renewal_enabled": True,
                    "remaining_ms": 1000,
                    "expires_at_ms": 2000,
                },
            ), patch.object(
                pc, "production_tunnel_status", return_value={"ready": False}
            ):
                status = pc.production_status(manifest, layout)

            self.assertEqual(status["bridge_pid"], 12057)
            self.assertTrue(status["bridge_ready"])
            self.assertEqual(status["tools"], sorted(pc.REQUIRED_TOOLS))
            state = json.loads(layout["runtime_state"].read_text(encoding="utf-8"))
            self.assertEqual(state["bridge_pid"], 12057)
            self.assertEqual(state["bridge_process_identity"], live_identity)
            self.assertIsNot(state.get("bridge_ready"), True)

    def test_production_status_does_not_rewrite_when_recorded_bridge_pid_is_alive(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            manifest, layout, _, live_identity = self._dead_bridge_pid_live_exclusive_fixture(
                root, recorded_pid=1130, live_pid=12057
            )
            before = layout["runtime_state"].read_bytes()
            with patch.object(
                pc, "_unique_process_identity_for_executable", return_value=live_identity
            ), patch.object(
                pc, "_verify_process_owned_unix_socket"
            ), patch.object(
                pc, "_pid_alive", return_value=True
            ), patch.object(
                pc, "reconcile_dead_recorded_bridge_pid_to_live_exclusive"
            ) as forbidden_locked, patch.object(
                pc, "_reconcile_dead_recorded_bridge_pid_to_live_exclusive_unlocked"
            ) as forbidden_unlocked, patch.object(
                pc, "production_tunnel_status", return_value={"ready": False}
            ):
                status = pc.production_status(manifest, layout)
            self.assertEqual(status["bridge_pid"], 1130)
            self.assertFalse(status["bridge_ready"])
            self.assertEqual(layout["runtime_state"].read_bytes(), before)
            forbidden_locked.assert_not_called()
            forbidden_unlocked.assert_not_called()

    def test_production_status_keeps_dead_bridge_pid_unready_when_reconcile_fails(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            manifest, layout, _, live_identity = self._dead_bridge_pid_live_exclusive_fixture(
                root, recorded_pid=1130, live_pid=12057
            )
            before = layout["runtime_state"].read_bytes()
            layout["bridge_socket"].write_bytes(b"")
            layout["bridge_socket"].chmod(0o600)
            with patch.object(
                pc,
                "_unique_process_identity_for_executable",
                side_effect=RuntimeError("production Bridge executable must have exactly one live process"),
            ), patch.object(
                pc, "_pid_alive", return_value=False
            ), patch.object(
                pc, "smoke_tools", return_value=sorted(pc.REQUIRED_TOOLS)
            ) as smoke, patch.object(
                pc,
                "_bridge_capability_lease_status",
                return_value={
                    "ready": True,
                    "renewal_enabled": True,
                    "remaining_ms": 1000,
                    "expires_at_ms": 2000,
                },
            ) as lease, patch.object(
                pc, "production_tunnel_status", return_value={"ready": False}
            ):
                status = pc.production_status(manifest, layout)
            self.assertEqual(status["bridge_pid"], 1130)
            self.assertFalse(status["bridge_ready"])
            self.assertEqual(status["tools"], [])
            self.assertEqual(layout["runtime_state"].read_bytes(), before)
            smoke.assert_not_called()
            lease.assert_not_called()

    def test_tunnel_operational_gate_requires_loopback_exact_status_and_fresh_structured_event_chain(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest,
                runtime_root=root / "runtime",
                bridge_socket=root / "bridge.sock",
            )
            layout["tunnel_health_url"].parent.mkdir(parents=True)
            layout["tunnel_health_url"].write_text("http://127.0.0.1:49152\n", encoding="utf-8")
            layout["tunnel_health_url"].chmod(0o600)
            layout["tunnel_log"].parent.mkdir(parents=True)
            layout["tunnel_log"].write_text(
                "\n".join(
                    json.dumps(event)
                    for event in (
                        {
                            "timestamp": 1001.0,
                            "component": "mcpclient",
                            "msg": "mcp session initialized",
                            "tunnel_id": "tunnel-production",
                            "client_instance_id": "instance-production-1",
                            "server_name": "catdesk",
                            "protocol_version": "2026-07-28",
                        },
                        {
                            "timestamp": 1002.0,
                            "component": "controlplane",
                            "msg": "tunnel metadata fetched",
                            "tunnel_id": "tunnel-production",
                            "client_instance_id": "instance-production-1",
                            "name": "Production Connector",
                        },
                        {
                            "timestamp": 1003.0,
                            "msg": "🟢 tunnel-client started",
                            "tunnel_id": "tunnel-production",
                            "client_instance_id": "instance-production-1",
                            "name": "Production Connector",
                            "version": "0.0.14+test",
                        },
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            layout["tunnel_log"].chmod(0o600)
            response = MagicMock()
            response.status = 200
            response.read.return_value = json.dumps({
                "control_plane_tunnel_id": "tunnel-production",
                "channels": {"main": {"enabled": True, "probe_status": "ok"}},
            }).encode("utf-8")
            response.geturl.return_value = "http://127.0.0.1:49152/api/status"
            response.__enter__.return_value = response
            metrics_response = MagicMock()
            metrics_response.status = 200
            metrics_response.read.return_value = (
                'commands_poll_cycles_total{otel_scope_name="controlplane",service_name="tunnel-client"} 21\n'
                'commands_poll_last_successful_timestamp_seconds{otel_scope_name="controlplane",service_name="tunnel-client"} 1002.5\n'
            ).encode("utf-8")
            metrics_response.geturl.return_value = "http://127.0.0.1:49152/metrics"
            metrics_response.headers.get.return_value = (
                "text/plain; version=0.0.4; charset=utf-8; escaping=underscores"
            )
            metrics_response.__enter__.return_value = metrics_response
            with patch.object(
                pc.TUNNEL_HEALTH_OPENER, "open", side_effect=[response, metrics_response]
            ) as open_request:
                try:
                    with patch.object(pc.time, "time", return_value=1003.0):
                        observed = pc._verify_tunnel_operational(
                            layout,
                            expected_tunnel_id="tunnel-production",
                            started_at=1000.0,
                        )
                except RuntimeError as error:
                    self.fail(f"real tunnel startup event chain was rejected: {error}")
            self.assertTrue(observed["ok"])
            self.assertEqual(
                [event["msg"] for event in observed["startup_operational_events"]],
                [
                    "mcp session initialized",
                    "tunnel metadata fetched",
                    "🟢 tunnel-client started",
                ],
            )
            self.assertEqual(
                [call.args[0].full_url for call in open_request.call_args_list],
                [
                    "http://127.0.0.1:49152/api/status",
                    "http://127.0.0.1:49152/metrics",
                ],
            )

            layout["tunnel_health_url"].write_text("https://api.openai.com/health\n", encoding="utf-8")
            with patch.object(pc.TUNNEL_HEALTH_OPENER, "open") as forbidden_request:
                with self.assertRaisesRegex(RuntimeError, "loopback"):
                    pc._verify_tunnel_operational(
                        layout,
                        expected_tunnel_id="tunnel-production",
                        started_at=1000.0,
                    )
            forbidden_request.assert_not_called()

    def test_tunnel_startup_chain_rejects_future_or_incomplete_mcp_init_events(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest, runtime_root=root / "runtime", bridge_socket=root / "bridge.sock"
            )
            layout["tunnel_health_url"].parent.mkdir(parents=True)
            layout["tunnel_health_url"].write_text(
                "http://127.0.0.1:49152\n", encoding="utf-8"
            )
            layout["tunnel_health_url"].chmod(0o600)
            layout["tunnel_log"].parent.mkdir(parents=True)
            status_response = MagicMock()
            status_response.status = 200
            status_response.read.return_value = json.dumps({
                "control_plane_tunnel_id": "tunnel-production",
                "channels": {"main": {"enabled": True, "probe_status": "ok"}},
            }).encode("utf-8")
            status_response.geturl.return_value = "http://127.0.0.1:49152/api/status"
            status_response.__enter__.return_value = status_response
            base_events = [
                {
                    "timestamp": 1001.0,
                    "component": "mcpclient",
                    "msg": "mcp session initialized",
                    "tunnel_id": "tunnel-production",
                    "client_instance_id": "instance-production-1",
                    "server_name": "catdesk",
                    "protocol_version": "2026-07-28",
                },
                {
                    "timestamp": 1002.0,
                    "component": "controlplane",
                    "msg": "tunnel metadata fetched",
                    "tunnel_id": "tunnel-production",
                    "client_instance_id": "instance-production-1",
                    "name": "Production Connector",
                },
                {
                    "timestamp": 1003.0,
                    "msg": "🟢 tunnel-client started",
                    "tunnel_id": "tunnel-production",
                    "client_instance_id": "instance-production-1",
                    "name": "Production Connector",
                    "version": "0.0.14+test",
                },
            ]
            variants = {
                "future": [{**event, "timestamp": event["timestamp"] + 100.0} for event in base_events],
                "nan_timestamp": [{**base_events[0], "timestamp": float("nan")}, *base_events[1:]],
                "positive_infinite_timestamp": [
                    {**base_events[0], "timestamp": float("inf")}, *base_events[1:]
                ],
                "negative_infinite_timestamp": [
                    {**base_events[0], "timestamp": float("-inf")}, *base_events[1:]
                ],
                "missing_server": [
                    {key: value for key, value in base_events[0].items() if key != "server_name"},
                    *base_events[1:],
                ],
                "missing_protocol": [
                    {key: value for key, value in base_events[0].items() if key != "protocol_version"},
                    *base_events[1:],
                ],
                "fuzzy_legacy_message": [{
                    "timestamp": 1001.0,
                    "message": "control-plane polling operational",
                    "tunnel_id": "tunnel-production",
                }],
                "wrong_tunnel": [
                    base_events[0],
                    {**base_events[1], "tunnel_id": "tunnel-other"},
                    base_events[2],
                ],
                "mismatched_instance": [
                    *base_events[:2],
                    {**base_events[2], "client_instance_id": "instance-production-2"},
                ],
                "mismatched_name": [
                    *base_events[:2],
                    {**base_events[2], "name": "Different Connector"},
                ],
                "missing_version": [
                    *base_events[:2],
                    {key: value for key, value in base_events[2].items() if key != "version"},
                ],
            }
            for name, events in variants.items():
                with self.subTest(name=name):
                    layout["tunnel_log"].write_text(
                        "\n".join(json.dumps(event) for event in events) + "\n",
                        encoding="utf-8",
                    )
                    layout["tunnel_log"].chmod(0o600)
                    with patch.object(pc.time, "time", return_value=1003.0), patch.object(
                        pc.TUNNEL_HEALTH_OPENER, "open", return_value=status_response
                    ):
                        with self.assertRaisesRegex(
                            RuntimeError,
                            "finite|future|server_name|protocol_version|event chain|tunnel.?id|instance|name|version",
                        ):
                            pc._verify_tunnel_operational(
                                layout,
                                expected_tunnel_id="tunnel-production",
                                started_at=1000.0,
                            )

            metrics_response = MagicMock()
            metrics_response.status = 200
            metrics_response.read.return_value = (
                'commands_poll_cycles_total{otel_scope_name="controlplane"} 1\n'
                'commands_poll_last_successful_timestamp_seconds{otel_scope_name="controlplane"} 1002\n'
            ).encode("utf-8")
            metrics_response.geturl.return_value = "http://127.0.0.1:49152/metrics"
            metrics_response.headers.get.return_value = (
                "text/plain; version=0.0.4; charset=utf-8; escaping=underscores"
            )
            metrics_response.__enter__.return_value = metrics_response
            layout["tunnel_log"].write_text(
                "\n".join(json.dumps(event) for event in base_events[:2]) + "\n",
                encoding="utf-8",
            )
            calls = 0

            def complete_log_after_retry(*_args, **_kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    layout["tunnel_log"].write_text(
                        "\n".join(json.dumps(event) for event in base_events) + "\n",
                        encoding="utf-8",
                    )
                    layout["tunnel_log"].chmod(0o600)
                return status_response if calls <= 2 else metrics_response

            with patch.object(pc.time, "time", return_value=1003.0), patch.object(
                pc.time, "monotonic", side_effect=[0.0, 0.0]
            ), patch.object(pc.time, "sleep") as readiness_sleep, patch.object(
                pc.TUNNEL_HEALTH_OPENER, "open", side_effect=complete_log_after_retry
            ) as readiness_open:
                pc._verify_tunnel_operational(
                    layout,
                    expected_tunnel_id="tunnel-production",
                    started_at=1000.0,
                    retry_transient_metrics=True,
                )
            self.assertEqual(readiness_open.call_count, 3)
            readiness_sleep.assert_called_once()

            layout["tunnel_log"].write_text(
                "\n".join(json.dumps(event) for event in base_events[:2]) + "\n",
                encoding="utf-8",
            )
            with patch.object(pc.time, "time", return_value=1003.0), patch.object(
                pc.time,
                "monotonic",
                side_effect=[0.0, 0.0, pc.TUNNEL_POLL_STARTUP_TIMEOUT_SECONDS + 1.0],
            ), patch.object(pc.time, "sleep") as readiness_sleep, patch.object(
                pc.TUNNEL_HEALTH_OPENER,
                "open",
                side_effect=[status_response, status_response],
            ) as readiness_open:
                with self.assertRaisesRegex(RuntimeError, "deadline|event chain"):
                    pc._verify_tunnel_operational(
                        layout,
                        expected_tunnel_id="tunnel-production",
                        started_at=1000.0,
                        retry_transient_metrics=True,
                    )
            self.assertEqual(readiness_open.call_count, 2)
            readiness_sleep.assert_called_once()

            layout["tunnel_log"].write_text(
                "\n".join(
                    json.dumps(event)
                    for event in [
                        base_events[0],
                        {**base_events[1], "component": "wrong"},
                        base_events[2],
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            with patch.object(pc.time, "time", return_value=1003.0), patch.object(
                pc.time, "monotonic", return_value=0.0
            ), patch.object(pc.time, "sleep") as readiness_sleep, patch.object(
                pc.TUNNEL_HEALTH_OPENER, "open", return_value=status_response
            ) as readiness_open:
                with self.assertRaisesRegex(RuntimeError, "component"):
                    pc._verify_tunnel_operational(
                        layout,
                        expected_tunnel_id="tunnel-production",
                        started_at=1000.0,
                        retry_transient_metrics=True,
                    )
            readiness_open.assert_called_once()
            readiness_sleep.assert_not_called()

    def test_tunnel_startup_chain_accepts_only_v0014_alternate_chain_without_metadata(self):
        init = {
            "timestamp": 1001.0,
            "component": "mcpclient",
            "msg": "mcp session initialized",
            "tunnel_id": "tunnel-production",
            "client_instance_id": "instance-production-1",
            "server_name": "catdesk",
            "protocol_version": "2026-07-28",
        }
        started = {
            "timestamp": 1002.0,
            "msg": "🟢 tunnel-client started",
            "tunnel_id": "tunnel-production",
            "client_instance_id": "instance-production-1",
            "name": "Production Connector",
            "version": "0.0.14+test",
        }
        with tempfile.TemporaryDirectory() as raw_root:
            log_path = Path(raw_root) / "tunnel.jsonl"

            def read(events):
                log_path.write_text(
                    "\n".join(json.dumps(event) for event in events) + "\n",
                    encoding="utf-8",
                )
                log_path.chmod(0o600)
                return pc._read_tunnel_startup_event_chain(
                    log_path,
                    expected_tunnel_id="tunnel-production",
                    started_at=1000.0,
                    verification_time=1003.0,
                )

            self.assertEqual(
                [event["msg"] for event in read([init, started])],
                ["mcp session initialized", "🟢 tunnel-client started"],
            )

            for name, events in {
                "started_without_init": [started],
                "metadata_without_init": [{
                    "timestamp": 1001.0,
                    "component": "controlplane",
                    "msg": "tunnel metadata fetched",
                    "tunnel_id": "tunnel-production",
                    "client_instance_id": "instance-production-1",
                    "name": "Production Connector",
                }],
                "metadata_then_started_without_init": [{
                    "timestamp": 1001.0,
                    "component": "controlplane",
                    "msg": "tunnel metadata fetched",
                    "tunnel_id": "tunnel-production",
                    "client_instance_id": "instance-production-1",
                    "name": "Production Connector",
                }, started],
                "wrong_tunnel": [init, {**started, "tunnel_id": "tunnel-other"}],
                "wrong_instance": [init, {**started, "client_instance_id": "instance-production-2"}],
                "out_of_order_time": [init, {**started, "timestamp": 1000.5}],
                "missing_started_name": [init, {key: value for key, value in started.items() if key != "name"}],
                "missing_version": [init, {key: value for key, value in started.items() if key != "version"}],
                "non_v0014_alternate_chain": [init, {**started, "version": "0.0.13"}],
            }.items():
                with self.subTest(name=name):
                    with self.assertRaisesRegex(
                        RuntimeError, "event chain|tunnel.?id|instance|timestamps|name|version"
                    ):
                        read(events)

            with self.assertRaises(pc._TransientTunnelStartupEventError):
                read([init])

    def test_tunnel_operational_gate_requires_all_evidence_for_v0014_alternate_chain(self):
        init = {
            "timestamp": 1001.0,
            "component": "mcpclient",
            "msg": "mcp session initialized",
            "tunnel_id": "tunnel-production",
            "client_instance_id": "instance-production-1",
            "server_name": "catdesk",
            "protocol_version": "2026-07-28",
        }
        started = {
            "timestamp": 1002.0,
            "msg": "🟢 tunnel-client started",
            "tunnel_id": "tunnel-production",
            "client_instance_id": "instance-production-1",
            "name": "Production Connector",
            "version": "0.0.14+test",
        }
        status_body = json.dumps({
            "control_plane_tunnel_id": "tunnel-production",
            "channels": {"main": {"enabled": True, "probe_status": "ok"}},
        }).encode("utf-8")
        fresh_metrics = (
            'commands_poll_cycles_total{otel_scope_name="controlplane",service_name="tunnel-client"} 1\n'
            'commands_poll_last_successful_timestamp_seconds{otel_scope_name="controlplane",service_name="tunnel-client"} 1001\n'
        ).encode("utf-8")

        def response(url, body, content_type="application/json"):
            observed = MagicMock()
            observed.status = 200
            observed.read.return_value = body
            observed.geturl.return_value = url
            observed.headers.get.return_value = content_type
            observed.__enter__.return_value = observed
            return observed

        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                self._manifest(), runtime_root=root / "runtime", bridge_socket=root / "bridge.sock"
            )
            layout["tunnel_health_url"].parent.mkdir(parents=True)
            layout["tunnel_health_url"].write_text("http://127.0.0.1:49152\n", encoding="utf-8")
            layout["tunnel_health_url"].chmod(0o600)
            layout["tunnel_log"].parent.mkdir(parents=True)
            layout["tunnel_log"].write_text(
                "\n".join(json.dumps(event) for event in [init, started]) + "\n",
                encoding="utf-8",
            )
            layout["tunnel_log"].chmod(0o600)
            identity = self._identity(303, ["/opt/tunnel-client", "run"])

            with patch.object(pc.time, "time", return_value=1003.0), patch.object(
                pc, "_process_identity_matches", return_value=True
            ), patch.object(
                pc.TUNNEL_HEALTH_OPENER,
                "open",
                side_effect=[
                    response("http://127.0.0.1:49152/api/status", status_body),
                    response(
                        "http://127.0.0.1:49152/metrics",
                        fresh_metrics,
                        "text/plain; version=0.0.4; charset=utf-8; escaping=underscores",
                    ),
                ],
            ):
                observed = pc._verify_tunnel_operational(
                    layout,
                    expected_tunnel_id="tunnel-production",
                    started_at=1000.0,
                    expected_process_identity=identity,
                )
            self.assertEqual(
                [event["msg"] for event in observed["startup_operational_events"]],
                ["mcp session initialized", "🟢 tunnel-client started"],
            )

            for name, metrics in {
                "zero_cycles": fresh_metrics.replace(b"} 1\n", b"} 0\n", 1),
                "before_started": fresh_metrics.replace(b"} 1001\n", b"} 999\n", 1),
                "stale_success": fresh_metrics.replace(b"} 1001\n", b"} 900\n", 1),
            }.items():
                with self.subTest(name=name), patch.object(pc.time, "time", return_value=1003.0), patch.object(
                    pc, "_process_identity_matches", return_value=True
                ), patch.object(
                    pc.TUNNEL_HEALTH_OPENER,
                    "open",
                    side_effect=[
                        response("http://127.0.0.1:49152/api/status", status_body),
                        response(
                            "http://127.0.0.1:49152/metrics",
                            metrics,
                            "text/plain; version=0.0.4; charset=utf-8; escaping=underscores",
                        ),
                    ],
                ):
                    with self.assertRaisesRegex(RuntimeError, "cycles|fresh|stale"):
                        pc._verify_tunnel_operational(
                            layout,
                            expected_tunnel_id="tunnel-production",
                            started_at=1000.0,
                            expected_process_identity=identity,
                        )

            with patch.object(pc.time, "time", return_value=1003.0), patch.object(
                pc, "_process_identity_matches", side_effect=[True, False]
            ), patch.object(
                pc.TUNNEL_HEALTH_OPENER,
                "open",
                side_effect=[
                    response("http://127.0.0.1:49152/api/status", status_body),
                    response(
                        "http://127.0.0.1:49152/metrics",
                        fresh_metrics,
                        "text/plain; version=0.0.4; charset=utf-8; escaping=underscores",
                    ),
                ],
            ):
                with self.assertRaisesRegex(RuntimeError, "identity"):
                    pc._verify_tunnel_operational(
                        layout,
                        expected_tunnel_id="tunnel-production",
                        started_at=1000.0,
                        expected_process_identity=identity,
                    )

            for name, rejected_status, error in (
                ("wrong_tunnel_id", status_body.replace(b"tunnel-production", b"tunnel-other"), "tunnel_id"),
                ("pending_main", status_body.replace(b'"ok"', b'"pending"'), "main channel"),
            ):
                with self.subTest(name=name), patch.object(pc.time, "time", return_value=1003.0), patch.object(
                    pc, "_process_identity_matches", return_value=True
                ), patch.object(
                    pc.TUNNEL_HEALTH_OPENER,
                    "open",
                    return_value=response("http://127.0.0.1:49152/api/status", rejected_status),
                ):
                    with self.assertRaisesRegex(RuntimeError, error):
                        pc._verify_tunnel_operational(
                            layout,
                            expected_tunnel_id="tunnel-production",
                            started_at=1000.0,
                            expected_process_identity=identity,
                        )

    def test_tunnel_activation_retry_preserves_identity_origin_and_error_classification(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest, runtime_root=root / "runtime", bridge_socket=root / "bridge.sock"
            )
            layout["tunnel_health_url"].parent.mkdir(parents=True)
            layout["tunnel_log"].parent.mkdir(parents=True, exist_ok=True)
            events = [
                {
                    "timestamp": 1001.0,
                    "component": "mcpclient",
                    "msg": "mcp session initialized",
                    "tunnel_id": "tunnel-production",
                    "client_instance_id": "instance-1",
                    "server_name": "catdesk",
                    "protocol_version": "2026-07-28",
                },
                {
                    "timestamp": 1002.0,
                    "component": "controlplane",
                    "msg": "tunnel metadata fetched",
                    "tunnel_id": "tunnel-production",
                    "client_instance_id": "instance-1",
                    "name": "Production Connector",
                },
                {
                    "timestamp": 1003.0,
                    "msg": "🟢 tunnel-client started",
                    "tunnel_id": "tunnel-production",
                    "client_instance_id": "instance-1",
                    "name": "Production Connector",
                    "version": "0.0.14+test",
                },
            ]
            event_text = "\n".join(json.dumps(event) for event in events) + "\n"

            def response(url: str, body: bytes, content_type: str = "application/json"):
                observed = MagicMock()
                observed.status = 200
                observed.read.return_value = body
                observed.geturl.return_value = url
                observed.headers.get.return_value = content_type
                observed.__enter__.return_value = observed
                return observed

            status_body = json.dumps({
                "control_plane_tunnel_id": "tunnel-production",
                "channels": {"main": {"enabled": True, "probe_status": "ok"}},
            }).encode("utf-8")
            ready_status = response("http://127.0.0.1:49152/api/status", status_body)
            fresh_metrics_body = (
                'commands_poll_cycles_total{otel_scope_name="controlplane"} 1\n'
                'commands_poll_last_successful_timestamp_seconds{otel_scope_name="controlplane"} 1002\n'
            ).encode("utf-8")
            stale_metrics_body = fresh_metrics_body.replace(b" 1\n", b" 0\n", 1)
            fresh_metrics = response(
                "http://127.0.0.1:49152/metrics",
                fresh_metrics_body,
                "text/plain; version=0.0.4; charset=utf-8; escaping=underscores",
            )
            stale_metrics = response(
                "http://127.0.0.1:49152/metrics",
                stale_metrics_body,
                "text/plain; version=0.0.4; charset=utf-8; escaping=underscores",
            )
            identity = self._identity(303, ["/opt/tunnel-client", "run"])

            materialize_count = 0

            def materialize_missing_readiness(_seconds):
                nonlocal materialize_count
                materialize_count += 1
                if materialize_count == 1:
                    layout["tunnel_health_url"].write_text(
                        "http://127.0.0.1:49152\n", encoding="utf-8"
                    )
                    layout["tunnel_health_url"].chmod(0o600)
                else:
                    layout["tunnel_log"].write_text(event_text, encoding="utf-8")
                    layout["tunnel_log"].chmod(0o600)

            with patch.object(pc.time, "time", return_value=1003.0), patch.object(
                pc.time, "monotonic", side_effect=[0.0, 0.0, 0.0]
            ), patch.object(
                pc.time, "sleep", side_effect=materialize_missing_readiness
            ) as readiness_sleep, patch.object(
                pc, "_process_identity_matches", return_value=True
            ) as identity_check, patch.object(
                pc.TUNNEL_HEALTH_OPENER,
                "open",
                side_effect=[ready_status, ready_status, fresh_metrics],
            ):
                pc._verify_tunnel_operational(
                    layout,
                    expected_tunnel_id="tunnel-production",
                    started_at=1000.0,
                    retry_transient_metrics=True,
                    expected_process_identity=identity,
                )
            self.assertEqual(readiness_sleep.call_count, 2)
            self.assertEqual(identity_check.call_count, 4)

            pending_status = response(
                "http://127.0.0.1:49152/api/status",
                json.dumps({
                    "control_plane_tunnel_id": "tunnel-production",
                    "channels": {"main": {"enabled": True, "probe_status": "probing"}},
                }).encode("utf-8"),
            )
            with patch.object(pc.time, "time", return_value=1003.0), patch.object(
                pc.time, "monotonic", side_effect=[0.0, 0.0]
            ), patch.object(pc.time, "sleep") as pending_sleep, patch.object(
                pc.TUNNEL_HEALTH_OPENER,
                "open",
                side_effect=[pending_status, ready_status, fresh_metrics],
            ):
                pc._verify_tunnel_operational(
                    layout,
                    expected_tunnel_id="tunnel-production",
                    started_at=1000.0,
                    retry_transient_metrics=True,
                )
            pending_sleep.assert_called_once()

            with patch.object(pc.time, "time", return_value=1003.0), patch.object(
                pc.time,
                "monotonic",
                side_effect=[0.0, 0.0, pc.TUNNEL_POLL_STARTUP_TIMEOUT_SECONDS + 1.0],
            ), patch.object(pc.time, "sleep") as pending_sleep, patch.object(
                pc.TUNNEL_HEALTH_OPENER, "open", side_effect=[pending_status, pending_status]
            ):
                with self.assertRaisesRegex(RuntimeError, "deadline|status"):
                    pc._verify_tunnel_operational(
                        layout,
                        expected_tunnel_id="tunnel-production",
                        started_at=1000.0,
                        retry_transient_metrics=True,
                    )
            pending_sleep.assert_called_once()

            with patch.object(pc.time, "time", return_value=1003.0), patch.object(
                pc.time, "monotonic", return_value=0.0
            ), patch.object(pc.time, "sleep") as forbidden_sleep, patch.object(
                pc, "_process_identity_matches", side_effect=[True, False]
            ), patch.object(
                pc.TUNNEL_HEALTH_OPENER, "open", side_effect=[ready_status, fresh_metrics]
            ):
                with self.assertRaisesRegex(RuntimeError, "identity"):
                    pc._verify_tunnel_operational(
                        layout,
                        expected_tunnel_id="tunnel-production",
                        started_at=1000.0,
                        retry_transient_metrics=True,
                        expected_process_identity=identity,
                    )
            forbidden_sleep.assert_not_called()

            def drift_health_url(_seconds):
                layout["tunnel_health_url"].write_text(
                    "http://127.0.0.1:49153\n", encoding="utf-8"
                )

            with patch.object(pc.time, "time", return_value=1003.0), patch.object(
                pc.time, "monotonic", side_effect=[0.0, 0.0]
            ), patch.object(pc.time, "sleep", side_effect=drift_health_url), patch.object(
                pc.TUNNEL_HEALTH_OPENER,
                "open",
                side_effect=[ready_status, stale_metrics, ready_status, fresh_metrics],
            ) as drift_open:
                with self.assertRaisesRegex(RuntimeError, "health URL|drift"):
                    pc._verify_tunnel_operational(
                        layout,
                        expected_tunnel_id="tunnel-production",
                        started_at=1000.0,
                        retry_transient_metrics=True,
                    )
            self.assertEqual(drift_open.call_count, 2)

            layout["tunnel_health_url"].write_text(
                "http://127.0.0.1:49152\n", encoding="utf-8"
            )
            duplicate_metrics = response(
                "http://127.0.0.1:49152/metrics",
                fresh_metrics_body
                + b'commands_poll_last_successful_timestamp_seconds{otel_scope_name="controlplane"} 1002\n',
                "text/plain; version=0.0.4; charset=utf-8; escaping=underscores",
            )
            with patch.object(pc.time, "time", return_value=1003.0), patch.object(
                pc.time, "monotonic", return_value=0.0
            ), patch.object(pc.time, "sleep") as forbidden_sleep, patch.object(
                pc.TUNNEL_HEALTH_OPENER,
                "open",
                side_effect=[ready_status, duplicate_metrics],
            ):
                with self.assertRaisesRegex(RuntimeError, "unique|duplicate"):
                    pc._verify_tunnel_operational(
                        layout,
                        expected_tunnel_id="tunnel-production",
                        started_at=1000.0,
                        retry_transient_metrics=True,
                    )
            forbidden_sleep.assert_not_called()

            wrong_id_status = response(
                "http://127.0.0.1:49152/api/status",
                status_body.replace(b"tunnel-production", b"tunnel-other"),
            )
            with patch.object(pc.time, "time", return_value=1003.0), patch.object(
                pc.time, "monotonic", side_effect=[0.0, 0.0]
            ), patch.object(pc.time, "sleep") as one_sleep, patch.object(
                pc.TUNNEL_HEALTH_OPENER,
                "open",
                side_effect=[ready_status, stale_metrics, wrong_id_status],
            ) as id_drift_open:
                with self.assertRaisesRegex(RuntimeError, "tunnel_id mismatch"):
                    pc._verify_tunnel_operational(
                        layout,
                        expected_tunnel_id="tunnel-production",
                        started_at=1000.0,
                        retry_transient_metrics=True,
                    )
            one_sleep.assert_called_once()
            self.assertEqual(id_drift_open.call_count, 3)

            with patch.object(pc.time, "time", return_value=1003.0), patch.object(
                pc.time, "monotonic", side_effect=[0.0, 0.0]
            ), patch.object(pc.time, "sleep") as one_sleep, patch.object(
                pc, "_process_identity_matches", side_effect=[True, False]
            ), patch.object(
                pc.TUNNEL_HEALTH_OPENER, "open", side_effect=[ready_status, stale_metrics]
            ) as identity_drift_open:
                with self.assertRaisesRegex(RuntimeError, "identity"):
                    pc._verify_tunnel_operational(
                        layout,
                        expected_tunnel_id="tunnel-production",
                        started_at=1000.0,
                        retry_transient_metrics=True,
                        expected_process_identity=identity,
                    )
            one_sleep.assert_called_once()
            self.assertEqual(identity_drift_open.call_count, 2)

            for enabled, probe_status in ((False, "pending"), (True, "failed"), (True, "mystery")):
                with self.subTest(enabled=enabled, probe_status=probe_status):
                    invalid_status = response(
                        "http://127.0.0.1:49152/api/status",
                        json.dumps({
                            "control_plane_tunnel_id": "tunnel-production",
                            "channels": {
                                "main": {"enabled": enabled, "probe_status": probe_status}
                            },
                        }).encode("utf-8"),
                    )
                    with patch.object(pc.time, "time", return_value=1003.0), patch.object(
                        pc.time, "monotonic", return_value=0.0
                    ), patch.object(pc.time, "sleep") as forbidden_sleep, patch.object(
                        pc.TUNNEL_HEALTH_OPENER, "open", return_value=invalid_status
                    ):
                        with self.assertRaisesRegex(
                            RuntimeError, "disabled|failed|unknown"
                        ):
                            pc._verify_tunnel_operational(
                                layout,
                                expected_tunnel_id="tunnel-production",
                                started_at=1000.0,
                                retry_transient_metrics=True,
                            )
                    forbidden_sleep.assert_not_called()

            layout["tunnel_health_url"].write_text(
                "https://api.openai.com/health\n", encoding="utf-8"
            )
            with patch.object(pc.time, "monotonic", return_value=0.0), patch.object(
                pc.time, "sleep"
            ) as forbidden_sleep, patch.object(
                pc.TUNNEL_HEALTH_OPENER, "open"
            ) as forbidden_open:
                with self.assertRaisesRegex(RuntimeError, "loopback"):
                    pc._verify_tunnel_operational(
                        layout,
                        expected_tunnel_id="tunnel-production",
                        started_at=1000.0,
                        retry_transient_metrics=True,
                    )
            forbidden_sleep.assert_not_called()
            forbidden_open.assert_not_called()

    def test_tunnel_poll_metrics_require_unique_finite_fresh_controlplane_samples(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest, runtime_root=root / "runtime", bridge_socket=root / "bridge.sock"
            )
            layout["tunnel_health_url"].parent.mkdir(parents=True)
            layout["tunnel_health_url"].write_text(
                "http://127.0.0.1:49152\n", encoding="utf-8"
            )
            layout["tunnel_health_url"].chmod(0o600)
            layout["tunnel_log"].parent.mkdir(parents=True)
            layout["tunnel_log"].write_text(
                "\n".join(json.dumps(event) for event in (
                    {
                        "timestamp": 1001.0,
                        "component": "mcpclient",
                        "msg": "mcp session initialized",
                        "tunnel_id": "tunnel-production",
                        "client_instance_id": "instance-production-1",
                        "server_name": "catdesk",
                        "protocol_version": "2026-07-28",
                    },
                    {
                        "timestamp": 1002.0,
                        "component": "controlplane",
                        "msg": "tunnel metadata fetched",
                        "tunnel_id": "tunnel-production",
                        "client_instance_id": "instance-production-1",
                        "name": "Production Connector",
                    },
                    {
                        "timestamp": 1003.0,
                        "msg": "🟢 tunnel-client started",
                        "tunnel_id": "tunnel-production",
                        "client_instance_id": "instance-production-1",
                        "name": "Production Connector",
                        "version": "0.0.14+test",
                    },
                )) + "\n",
                encoding="utf-8",
            )
            layout["tunnel_log"].chmod(0o600)
            status_response = MagicMock()
            status_response.status = 200
            status_response.read.return_value = json.dumps({
                "control_plane_tunnel_id": "tunnel-production",
                "channels": {"main": {"enabled": True, "probe_status": "ok"}},
            }).encode("utf-8")
            status_response.geturl.return_value = "http://127.0.0.1:49152/api/status"
            status_response.__enter__.return_value = status_response
            bad_metrics = {
                "missing": 'commands_poll_cycles_total{otel_scope_name="controlplane"} 2\n',
                "duplicate": (
                    'commands_poll_cycles_total{otel_scope_name="controlplane"} 2\n'
                    'commands_poll_cycles_total{otel_scope_name="controlplane",copy="two"} 3\n'
                    'commands_poll_last_successful_timestamp_seconds{otel_scope_name="controlplane"} 1002\n'
                ),
                "duplicate_last_success": (
                    'commands_poll_cycles_total{otel_scope_name="controlplane"} 2\n'
                    'commands_poll_last_successful_timestamp_seconds{otel_scope_name="controlplane"} 1002\n'
                    'commands_poll_last_successful_timestamp_seconds{otel_scope_name="controlplane",copy="two"} 1002\n'
                ),
                "mismatched_series_labels": (
                    'commands_poll_cycles_total{otel_scope_name="controlplane",instance="one"} 2\n'
                    'commands_poll_last_successful_timestamp_seconds{otel_scope_name="controlplane",instance="two"} 1002\n'
                ),
                "wrong_service": (
                    'commands_poll_cycles_total{otel_scope_name="controlplane",service_name="other"} 2\n'
                    'commands_poll_last_successful_timestamp_seconds{otel_scope_name="controlplane"} 1002\n'
                ),
                "non_numeric": (
                    'commands_poll_cycles_total{otel_scope_name="controlplane"} nope\n'
                    'commands_poll_last_successful_timestamp_seconds{otel_scope_name="controlplane"} 1002\n'
                ),
                "nan": (
                    'commands_poll_cycles_total{otel_scope_name="controlplane"} NaN\n'
                    'commands_poll_last_successful_timestamp_seconds{otel_scope_name="controlplane"} 1002\n'
                ),
                "infinite": (
                    'commands_poll_cycles_total{otel_scope_name="controlplane"} +Inf\n'
                    'commands_poll_last_successful_timestamp_seconds{otel_scope_name="controlplane"} 1002\n'
                ),
                "negative_infinite_cycles": (
                    'commands_poll_cycles_total{otel_scope_name="controlplane"} -Inf\n'
                    'commands_poll_last_successful_timestamp_seconds{otel_scope_name="controlplane"} 1002\n'
                ),
                "nan_last_success": (
                    'commands_poll_cycles_total{otel_scope_name="controlplane"} 2\n'
                    'commands_poll_last_successful_timestamp_seconds{otel_scope_name="controlplane"} NaN\n'
                ),
                "positive_infinite_last_success": (
                    'commands_poll_cycles_total{otel_scope_name="controlplane"} 2\n'
                    'commands_poll_last_successful_timestamp_seconds{otel_scope_name="controlplane"} +Inf\n'
                ),
                "negative_infinite_last_success": (
                    'commands_poll_cycles_total{otel_scope_name="controlplane"} 2\n'
                    'commands_poll_last_successful_timestamp_seconds{otel_scope_name="controlplane"} -Inf\n'
                ),
                "negative_last_success": (
                    'commands_poll_cycles_total{otel_scope_name="controlplane"} 2\n'
                    'commands_poll_last_successful_timestamp_seconds{otel_scope_name="controlplane"} -1\n'
                ),
                "zero_cycles": (
                    'commands_poll_cycles_total{otel_scope_name="controlplane"} 0\n'
                    'commands_poll_last_successful_timestamp_seconds{otel_scope_name="controlplane"} 1002\n'
                ),
                "fractional_cycles": (
                    'commands_poll_cycles_total{otel_scope_name="controlplane"} 1.5\n'
                    'commands_poll_last_successful_timestamp_seconds{otel_scope_name="controlplane"} 1002\n'
                ),
                "old": (
                    'commands_poll_cycles_total{otel_scope_name="controlplane"} 2\n'
                    'commands_poll_last_successful_timestamp_seconds{otel_scope_name="controlplane"} 999\n'
                ),
                "future": (
                    'commands_poll_cycles_total{otel_scope_name="controlplane"} 2\n'
                    'commands_poll_last_successful_timestamp_seconds{otel_scope_name="controlplane"} 1100\n'
                ),
            }
            for name, metrics in bad_metrics.items():
                with self.subTest(name=name):
                    metrics_response = MagicMock()
                    metrics_response.status = 200
                    metrics_response.read.return_value = metrics.encode("utf-8")
                    metrics_response.geturl.return_value = "http://127.0.0.1:49152/metrics"
                    metrics_response.headers.get.return_value = (
                        "text/plain; version=0.0.4; charset=utf-8; escaping=underscores"
                    )
                    metrics_response.__enter__.return_value = metrics_response
                    with patch.object(pc.time, "time", return_value=1003.0), patch.object(
                        pc.TUNNEL_HEALTH_OPENER,
                        "open",
                        side_effect=[status_response, metrics_response],
                    ):
                        with self.assertRaisesRegex(
                            RuntimeError, "metrics|sample|finite|cycles|success|future|fresh|labels"
                        ):
                            pc._verify_tunnel_operational(
                                layout,
                                expected_tunnel_id="tunnel-production",
                                started_at=1000.0,
                            )

            same_second_metrics = MagicMock()
            same_second_metrics.status = 200
            same_second_metrics.read.return_value = (
                'commands_poll_cycles_total{otel_scope_name="controlplane"} 1\n'
                'commands_poll_last_successful_timestamp_seconds{otel_scope_name="controlplane"} 1000\n'
            ).encode("utf-8")
            same_second_metrics.geturl.return_value = "http://127.0.0.1:49152/metrics"
            same_second_metrics.headers.get.return_value = (
                "text/plain; version=0.0.4; charset=utf-8; escaping=underscores"
            )
            same_second_metrics.__enter__.return_value = same_second_metrics
            with patch.object(pc.time, "time", return_value=1003.0), patch.object(
                pc.TUNNEL_HEALTH_OPENER,
                "open",
                side_effect=[status_response, same_second_metrics],
            ):
                try:
                    same_second = pc._verify_tunnel_operational(
                        layout,
                        expected_tunnel_id="tunnel-production",
                        started_at=1000.9,
                    )
                except RuntimeError as error:
                    self.fail(f"same-second integer poll metric was rejected: {error}")
            self.assertEqual(
                same_second["controlplane_poll_metrics"]["last_successful_timestamp"],
                1000.0,
            )
            valid_metric_bytes = (
                'commands_poll_cycles_total{otel_scope_name="controlplane"} 1\n'
                'commands_poll_last_successful_timestamp_seconds{otel_scope_name="controlplane"} 1000\n'
            ).encode("utf-8")
            for name, started_at, now in (
                ("nonfinite_started_at", float("nan"), 1003.0),
                ("nonfinite_now", 1000.0, float("inf")),
                ("future_started_at", 1006.0, 1003.0),
            ):
                with self.subTest(time_bound=name):
                    with self.assertRaisesRegex(RuntimeError, "finite|future"):
                        pc._parse_controlplane_poll_metrics(
                            valid_metric_bytes, started_at=started_at, now=now
                        )
            future_boundary_metrics = valid_metric_bytes.replace(b" 1000\n", b" 1005\n")
            try:
                pc._parse_controlplane_poll_metrics(
                    future_boundary_metrics, started_at=1000.0, now=1003.0
                )
            except RuntimeError as error:
                self.fail(f"exact +2 second metrics boundary was rejected: {error}")

            stale_metrics_response = MagicMock()
            stale_metrics_response.status = 200
            stale_metrics_response.read.return_value = (
                'commands_poll_cycles_total{otel_scope_name="controlplane"} 0\n'
                'commands_poll_last_successful_timestamp_seconds{otel_scope_name="controlplane"} 999\n'
            ).encode("utf-8")
            stale_metrics_response.geturl.return_value = "http://127.0.0.1:49152/metrics"
            stale_metrics_response.headers.get.return_value = (
                "text/plain; version=0.0.4; charset=utf-8; escaping=underscores"
            )
            stale_metrics_response.__enter__.return_value = stale_metrics_response
            fresh_metrics_response = MagicMock()
            fresh_metrics_response.status = 200
            fresh_metrics_response.read.return_value = valid_metric_bytes
            fresh_metrics_response.geturl.return_value = "http://127.0.0.1:49152/metrics"
            fresh_metrics_response.headers.get.return_value = (
                "text/plain; version=0.0.4; charset=utf-8; escaping=underscores"
            )
            fresh_metrics_response.__enter__.return_value = fresh_metrics_response
            with patch.object(pc.time, "time", return_value=1003.0), patch.object(
                pc.time, "monotonic", side_effect=[0.0, 31.0]
            ), patch.object(pc.time, "sleep") as retry_sleep, patch.object(
                pc.TUNNEL_HEALTH_OPENER,
                "open",
                side_effect=[
                    status_response,
                    stale_metrics_response,
                    status_response,
                    fresh_metrics_response,
                ],
            ) as retry_open:
                pc._verify_tunnel_operational(
                    layout,
                    expected_tunnel_id="tunnel-production",
                    started_at=1000.0,
                    retry_transient_metrics=True,
                )
            self.assertEqual(retry_open.call_count, 4)
            retry_sleep.assert_called_once()

            with patch.object(pc.time, "time", return_value=1003.0), patch.object(
                pc.time,
                "monotonic",
                side_effect=[0.0, 0.0, pc.TUNNEL_POLL_STARTUP_TIMEOUT_SECONDS + 1.0],
            ), patch.object(pc.time, "sleep") as retry_sleep, patch.object(
                pc.TUNNEL_HEALTH_OPENER,
                "open",
                side_effect=[
                    status_response,
                    stale_metrics_response,
                    status_response,
                    stale_metrics_response,
                ],
            ) as retry_open:
                with self.assertRaisesRegex(RuntimeError, "poll|fresh|cycles") as caught:
                    pc._verify_tunnel_operational(
                        layout,
                        expected_tunnel_id="tunnel-production",
                        started_at=1000.0,
                        retry_transient_metrics=True,
                    )
            self.assertIn(
                "production tunnel controlplane poll cycles have not started",
                str(caught.exception),
            )
            self.assertEqual(retry_open.call_count, 4)
            retry_sleep.assert_called_once()

            now_stale_metrics = valid_metric_bytes.replace(b" 1000\n", b" 940\n")
            with self.assertRaisesRegex(RuntimeError, "stale|fresh"):
                pc._parse_controlplane_poll_metrics(
                    now_stale_metrics, started_at=900.0, now=1003.0
                )

            valid_metrics = (
                'commands_poll_cycles_total{otel_scope_name="controlplane"} 2\n'
                'commands_poll_last_successful_timestamp_seconds{otel_scope_name="controlplane"} 1002\n'
            )
            redirect_handler = pc._NoTunnelHealthRedirect()
            self.assertIsNone(
                redirect_handler.redirect_request(
                    pc.urllib.request.Request("http://127.0.0.1:49152/api/status"),
                    None,
                    302,
                    "Found",
                    {},
                    "http://127.0.0.1:49153/api/status",
                )
            )
            self.assertIsInstance(
                pc.TUNNEL_HEALTH_PROXY_HANDLER, pc.urllib.request.ProxyHandler
            )
            self.assertEqual(pc.TUNNEL_HEALTH_PROXY_HANDLER.proxies, {})
            redirect_cases = (
                ("status", "http://127.0.0.1:49153/api/status", None),
                ("metrics", "http://127.0.0.1:49152/api/status", "http://127.0.0.1:49153/metrics"),
            )
            for name, status_url, metrics_url in redirect_cases:
                with self.subTest(redirect=name):
                    status_response.geturl.return_value = status_url
                    metrics_response = MagicMock()
                    metrics_response.status = 200
                    metrics_response.read.return_value = valid_metrics.encode("utf-8")
                    metrics_response.geturl.return_value = metrics_url
                    metrics_response.headers.get.return_value = (
                        "text/plain; version=0.0.4; charset=utf-8; escaping=underscores"
                    )
                    metrics_response.__enter__.return_value = metrics_response
                    responses = [status_response]
                    if metrics_url is not None:
                        responses.append(metrics_response)
                    with patch.object(pc.time, "time", return_value=1003.0), patch.object(
                        pc.TUNNEL_HEALTH_OPENER, "open", side_effect=responses
                    ) as open_request, patch.object(pc.time, "sleep") as forbidden_sleep:
                        with self.assertRaisesRegex(RuntimeError, "redirect|URL"):
                            pc._verify_tunnel_operational(
                                layout,
                                expected_tunnel_id="tunnel-production",
                                started_at=1000.0,
                                retry_transient_metrics=True,
                            )
                    self.assertEqual(open_request.call_count, 1 if name == "status" else 2)
                    forbidden_sleep.assert_not_called()
            status_response.geturl.return_value = "http://127.0.0.1:49152/api/status"

            for name, content_type, body in (
                ("content_type", "text/html", valid_metrics.encode("utf-8")),
                (
                    "oversized",
                    "text/plain; version=0.0.4; charset=utf-8; escaping=underscores",
                    (valid_metrics + "#" * (1024 * 1024)).encode("utf-8"),
                ),
            ):
                with self.subTest(response=name):
                    metrics_response = MagicMock()
                    metrics_response.status = 200
                    metrics_response.read.return_value = body
                    metrics_response.geturl.return_value = "http://127.0.0.1:49152/metrics"
                    metrics_response.headers.get.return_value = content_type
                    metrics_response.__enter__.return_value = metrics_response
                    with patch.object(pc.time, "time", return_value=1003.0), patch.object(
                        pc.TUNNEL_HEALTH_OPENER,
                        "open",
                        side_effect=[status_response, metrics_response],
                    ), patch.object(pc.time, "sleep") as forbidden_sleep:
                        with self.assertRaisesRegex(RuntimeError, "Content-Type|body|large"):
                            pc._verify_tunnel_operational(
                                layout,
                                expected_tunnel_id="tunnel-production",
                                started_at=1000.0,
                                retry_transient_metrics=True,
                            )
                    forbidden_sleep.assert_not_called()

    def test_ai_bridge_fallback_guard_is_read_only_loopback_and_compute_ready(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            health_file = root / "health-url"
            health_file.write_text("http://127.0.0.1:52354\n", encoding="utf-8")
            health_file.chmod(0o600)
            health_response = MagicMock()
            health_response.status = 200
            health_response.read.return_value = json.dumps({
                "mcp_server_url": "http://127.0.0.1:52342/mcp/private-slug",
                "mcp_routes": [
                    {"channel": "main", "target": "127.0.0.1:52342"},
                ],
                "channels": [
                    {
                        "name": "main",
                        "enabled": True,
                        "probe_status": "ok",
                        "server_kind": "mcp",
                        "transport_kind": "http",
                        "details": [
                            {"key": "address", "value": "http://127.0.0.1:52342/mcp/private-slug"}
                        ],
                    }
                ]
            }).encode("utf-8")
            health_response.__enter__.return_value = health_response
            tools_status = {
                "jsonrpc": "2.0",
                "id": "fallback-tools-list",
                "result": {"tools": [
                    {"name": "computer"},
                    {"name": "shell_command"},
                    {"name": "bridge_get_status"},
                ]},
            }
            process_identities = {
                "ai_bridge": self._identity(
                    101, ["/Applications/AI-Bridge.app/Contents/MacOS/ai-bridge"]
                ),
                "tunnel": self._identity(102, ["/private/tunnel-client", "run"]),
                "compute_helper": self._identity(
                    103,
                    ["/Applications/Compute Helper.app/Contents/MacOS/compute-helper"],
                ),
            }
            with patch.object(
                pc.urllib.request,
                "urlopen",
                return_value=health_response,
            ) as urlopen, patch.object(
                pc, "_tcp_ready", return_value=True
            ) as tcp_ready, patch.object(
                pc, "_mcp_streamable_tools_list", return_value=tools_status
            ) as handshake, patch.object(
                pc,
                "_ai_bridge_fallback_process_identities",
                return_value=process_identities,
            ) as process_check:
                guard = pc._verify_ai_bridge_fallback(health_file)
            self.assertTrue(guard["ready"])
            self.assertFalse(guard["ai_bridge_is_trusted_receipt_source"])
            self.assertEqual(guard["compute_host"], "127.0.0.1")
            self.assertEqual(guard["compute_port"], 52342)
            self.assertEqual(guard["tools"], ["bridge_get_status", "computer", "shell_command"])
            self.assertEqual(urlopen.call_count, 1)
            handshake.assert_called_once_with("http://127.0.0.1:52342/mcp/private-slug")
            tcp_ready.assert_called_once_with("127.0.0.1", 52342)
            process_check.assert_called_once_with(
                health_host="127.0.0.1",
                health_port=52354,
                compute_host="127.0.0.1",
                compute_port=52342,
            )

            for mismatched_route in (
                "127.0.0.1:52343",
                "http://127.0.0.1:52342/mcp/different-slug",
            ):
                with self.subTest(mismatched_route=mismatched_route):
                    health_response.read.return_value = json.dumps({
                        "mcp_server_url": "http://127.0.0.1:52342/mcp/private-slug",
                        "mcp_routes": [{"channel": "main", "target": mismatched_route}],
                        "channels": [{
                            "name": "main",
                            "enabled": True,
                            "probe_status": "ok",
                            "details": [{
                                "key": "address",
                                "value": "http://127.0.0.1:52342/mcp/private-slug",
                            }],
                        }],
                    }).encode("utf-8")
                    with patch.object(pc.urllib.request, "urlopen", return_value=health_response), patch.object(
                        pc, "_tcp_ready", return_value=True
                    ):
                        with self.assertRaisesRegex(RuntimeError, "route target|compute address"):
                            pc._verify_ai_bridge_fallback(health_file)

            health_response.read.return_value = json.dumps({
                "channels": {
                    "main": {
                        "enabled": True,
                        "probe_status": "ok",
                        "address": "https://remote.example.invalid/mcp",
                    }
                }
            }).encode("utf-8")
            with patch.object(pc.urllib.request, "urlopen", return_value=health_response), patch.object(
                pc, "_tcp_ready", return_value=True
            ):
                        with self.assertRaisesRegex(RuntimeError, "loopback|compute"):
                            pc._verify_ai_bridge_fallback(health_file)

    def test_ai_bridge_fallback_stable_identity_excludes_uptime_and_binds_processes(self):
        status = {
            "client_instance_id": "ai-bridge-instance-1",
            "version": "1.2.3",
            "control_plane_tunnel_id": "tunnel-main",
            "control_plane_base_url": "https://api.openai.com",
            "control_plane_route": "/v1/agent-bridge",
            "mcp_server_url": "http://127.0.0.1:52342/mcp",
            "health_listen_addr": "127.0.0.1:52354",
            "control_plane": {"tunnel_id": "tunnel-main", "base_url": "https://api.openai.com"},
            "uptime_seconds": 10,
            "channels": [{
                "name": "main", "enabled": True, "probe_status": "ok",
                "server_kind": "mcp", "transport_kind": "http",
                "started_at": "2026-09-12T00:00:00Z",
            }],
            "mcp_routes": [{"channel": "main", "target": "127.0.0.1:52342"}],
        }
        process_identities = {
            "ai_bridge": self._identity(101, ["/Applications/AI-Bridge.app/Contents/MacOS/ai-bridge"]),
            "tunnel": self._identity(102, ["/private/tunnel-client", "run"]),
            "compute_helper": self._identity(103, ["/Applications/Compute Helper.app/Contents/MacOS/compute-helper"]),
        }
        first = pc._stable_ai_bridge_fallback_identity(
            status, status["channels"][0], "http://127.0.0.1:52342/mcp", process_identities
        )
        second_status = json.loads(json.dumps(status))
        second_status["uptime_seconds"] = 11
        second = pc._stable_ai_bridge_fallback_identity(
            second_status, second_status["channels"][0], "http://127.0.0.1:52342/mcp", process_identities
        )
        self.assertEqual(first, second)
        self.assertEqual(first["ai_bridge_process_identity"], process_identities["ai_bridge"])
        self.assertEqual(first["compute_helper_process_identity"], process_identities["compute_helper"])
        self.assertEqual(first["tunnel_process_identity"], process_identities["tunnel"])
        self.assertEqual(first["service"]["control_plane_tunnel_id"], "tunnel-main")
        self.assertEqual(first["service"]["control_plane_route"], "/v1/agent-bridge")

    def test_ai_bridge_fallback_process_topology_binds_real_listener_owners_and_helper_socket(self):
        ai_bridge = self._identity(101, [str(pc.AI_BRIDGE_EXECUTABLE)])
        tunnel = self._identity(102, ["/private/tunnel-client", "run"])
        compute_helper = self._identity(103, [str(pc.COMPUTE_HELPER_EXECUTABLE)])
        with patch.object(
            pc,
            "_unique_process_identity_for_executable",
            side_effect=[ai_bridge, compute_helper],
        ) as process_lookup, patch.object(
            pc, "_unique_tunnel_child_identity", return_value=tunnel
        ) as tunnel_lookup, patch.object(
            pc,
            "_unique_loopback_listener_identity",
            side_effect=[tunnel, ai_bridge],
        ) as listener_lookup, patch.object(
            pc, "_verify_process_owned_unix_socket"
        ) as socket_check:
            observed = pc._ai_bridge_fallback_process_identities(
                health_host="127.0.0.1",
                health_port=52354,
                compute_host="127.0.0.1",
                compute_port=52342,
            )
        self.assertEqual(
            observed,
            {"ai_bridge": ai_bridge, "tunnel": tunnel, "compute_helper": compute_helper},
        )
        self.assertEqual(process_lookup.call_count, 2)
        tunnel_lookup.assert_called_once_with(ai_bridge)
        self.assertEqual(
            listener_lookup.call_args_list,
            [
                call("127.0.0.1", 52354, "AI-Bridge health"),
                call("127.0.0.1", 52342, "AI-Bridge compute route"),
            ],
        )
        socket_check.assert_called_once_with(
            compute_helper, pc.COMPUTE_HELPER_SOCKET, "Compute Helper"
        )

    def test_fallback_mcp_streamable_handshake_orders_requests_and_cleans_session(self):
        with fake_mcp_streamable_server() as (compute_url, events):
            response = pc._mcp_streamable_tools_list(compute_url)
        self.assertEqual(
            [event[1]["method"] if event[0] == "POST" else "DELETE" for event in events],
            ["initialize", "notifications/initialized", "tools/list", "DELETE"],
        )
        self.assertEqual(response["result"]["tools"], [
            {"name": "computer"},
            {"name": "shell_command"},
        ])
        for index, (_method, _payload, headers) in enumerate(events):
            lowered = {key.lower(): value for key, value in headers.items()}
            self.assertEqual(lowered["mcp-protocol-version"], "2025-03-26")
            self.assertIn("application/json", lowered["accept"])
            self.assertIn("text/event-stream", lowered["accept"])
            if index:
                self.assertEqual(lowered["mcp-session-id"], "session-test-123")

    def test_fallback_mcp_streamable_rejects_missing_or_mismatched_session(self):
        for mode, message in (
            ("missing_session", "session"),
            ("mismatched_session", "session"),
        ):
            with self.subTest(mode=mode), fake_mcp_streamable_server(mode) as (compute_url, events):
                with self.assertRaisesRegex(RuntimeError, message):
                    pc._mcp_streamable_tools_list(compute_url)
            if mode == "mismatched_session":
                self.assertEqual(events[-1][0], "DELETE")

    def test_fallback_mcp_streamable_rejects_422_wrong_id_error_and_multiple_results(self):
        for mode, message in (
            ("initialize_422", "HTTP 422"),
            ("wrong_initialize_id", "id"),
            ("wrong_tools_id", "id"),
            ("tools_error", "error"),
            ("multiple_tools_results", "single|multiple"),
        ):
            with self.subTest(mode=mode), fake_mcp_streamable_server(mode) as (compute_url, _events):
                with self.assertRaisesRegex(RuntimeError, message):
                    pc._mcp_streamable_tools_list(compute_url)

    def test_fallback_mcp_streamable_deletes_session_on_tools_failure_and_reports_cleanup_failure(self):
        with fake_mcp_streamable_server("tools_422") as (compute_url, events):
            with self.assertRaisesRegex(RuntimeError, "HTTP 422"):
                pc._mcp_streamable_tools_list(compute_url)
        self.assertEqual(events[-1][0], "DELETE")

    def test_fallback_mcp_streamable_rejects_wrong_content_type_or_body_format(self):
        for mode in ("text_plain_sse", "json_content_sse_body", "sse_content_json_body"):
            with self.subTest(mode=mode), fake_mcp_streamable_server(mode) as (compute_url, events):
                with self.assertRaisesRegex(RuntimeError, "Content-Type|media|JSON|SSE"):
                    pc._mcp_streamable_tools_list(compute_url)
            self.assertEqual(events[-1][0], "DELETE")

        with fake_mcp_streamable_server("cleanup_500") as (compute_url, events):
            with self.assertRaisesRegex(RuntimeError, "cleanup|HTTP 500"):
                pc._mcp_streamable_tools_list(compute_url)
        self.assertEqual(events[-1][0], "DELETE")

    def test_start_production_tunnel_owns_spawn_failure_cleanup_and_state_restore(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(manifest, runtime_root=root / "runtime", bridge_socket=root / "bridge.sock")
            layout["runtime_state"].parent.mkdir(parents=True)
            original = {"release_revision": REVISION, "endpoint_generation": 7, "sentinel": "preserve"}
            layout["runtime_state"].write_text(json.dumps(original), encoding="utf-8")
            layout["runtime_state"].chmod(0o600)
            process = MagicMock(pid=303)
            identity = {
                "pid": 303,
                "uid": os.geteuid(),
                "lstart": "Thu Sep 11 12:00:00 2026",
                "start_timestamp": 1789099200.0,
                "pgid": 303,
                "exe_realpath": "/opt/tunnel-client",
                "exe_inode": 123,
                "exe_sha256": "a" * 64,
                "argv": ["/opt/tunnel-client", "run", "--profile-dir", str(layout["tunnel_dir"]), "--profile", pc.TUNNEL_PROFILE_NAME],
            }
            with patch.object(pc, "_validate_tunnel_profile", return_value={"tunnel_id": "tunnel-production"}), patch.object(
                pc, "_find_tunnel_client", return_value=Path("/opt/tunnel-client")
            ), patch.object(pc, "_spawn_tunnel", return_value=process), patch.object(
                pc, "_capture_process_identity", return_value=identity
            ), patch.object(pc, "_wait_for_tunnel", side_effect=RuntimeError("status failed")), patch.object(
                pc, "_stop_verified_process_identity"
            ) as stop:
                with self.assertRaisesRegex(RuntimeError, "status failed"):
                    pc._start_production_tunnel(manifest, layout)
            stop.assert_called_once_with(identity, process=process)
            self.assertEqual(json.loads(layout["runtime_state"].read_text(encoding="utf-8")), original)

    def test_start_production_tunnel_preserves_status_reason_and_restores_state(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest,
                runtime_root=root / "runtime",
                bridge_socket=root / "bridge.sock",
            )
            layout["runtime_state"].parent.mkdir(parents=True)
            original = {
                "release_revision": REVISION,
                "endpoint_generation": 7,
                "sentinel": "preserve",
            }
            layout["runtime_state"].write_text(json.dumps(original), encoding="utf-8")
            layout["runtime_state"].chmod(0o600)
            process = MagicMock(pid=303)
            identity = self._identity(
                303,
                [
                    "/opt/tunnel-client",
                    "run",
                    "--profile-dir",
                    str(layout["tunnel_dir"]),
                    "--profile",
                    pc.TUNNEL_PROFILE_NAME,
                ],
            )
            item = {
                "pid": 303,
                "tunnel_id": "tunnel-production",
                "process_identity": identity,
            }

            def record_candidate(*_args, **_kwargs):
                layout["runtime_state"].write_text(
                    json.dumps({"candidate_tunnel_pid": 303}), encoding="utf-8"
                )

            with patch.object(
                pc,
                "_validate_tunnel_profile",
                return_value={"tunnel_id": "tunnel-production"},
            ), patch.object(
                pc, "_find_tunnel_client", return_value=Path("/opt/tunnel-client")
            ), patch.object(
                pc, "_spawn_tunnel", return_value=process
            ), patch.object(
                pc, "_capture_process_identity", return_value=identity
            ), patch.object(
                pc, "_wait_for_tunnel", return_value=item
            ), patch.object(
                pc, "_record_production_tunnel", side_effect=record_candidate
            ), patch.object(
                pc,
                "production_tunnel_status",
                return_value={
                    "ready": False,
                    "pid": 303,
                    "reason": (
                        "production tunnel controlplane poll readiness retry deadline "
                        "expired: production tunnel controlplane poll cycles have not started"
                    ),
                },
            ), patch.object(
                pc, "_stop_verified_process_identity"
            ) as stop:
                with self.assertRaisesRegex(
                    RuntimeError,
                    "poll cycles have not started.*expected_pid=303.*observed_pid=303",
                ):
                    pc._start_production_tunnel(manifest, layout)
            stop.assert_called_once_with(identity, process=process)
            self.assertEqual(
                json.loads(layout["runtime_state"].read_text(encoding="utf-8")),
                original,
            )

    def test_zombie_is_not_running_and_spawned_process_is_reaped_after_verified_signal(self):
        identity = self._identity(303, ["/opt/tunnel-client", "run"])
        with patch.object(pc.os, "kill"), patch.object(
            pc, "_ps_process_field", return_value="Z+"
        ):
            self.assertFalse(getattr(pc, "_process_is_running", pc._pid_alive)(303))
        with patch.object(pc.os, "kill"), patch.object(
            pc, "_ps_process_field", return_value="S+"
        ):
            self.assertTrue(getattr(pc, "_process_is_running", pc._pid_alive)(303))

        process = MagicMock(pid=303)
        process.poll.return_value = None
        with patch.object(pc, "_process_identity_matches", return_value=True), patch.object(
            pc.os, "kill"
        ) as signal_process, patch.object(pc, "_pid_alive", return_value=False):
            try:
                pc._stop_verified_process_identity(identity, process=process)
            except TypeError as error:
                self.fail(f"spawned process cannot be reaped: {error}")
        signal_process.assert_called_once_with(303, pc.signal.SIGTERM)
        process.wait.assert_called_once_with(timeout=5)

    def test_process_group_waits_until_no_non_zombie_members_and_fails_closed_on_unknown_ps(self):
        running_group = subprocess.CompletedProcess(
            [], 0, stdout="303 303 Z+\n304 303 S+\n", stderr=""
        )
        all_zombies = subprocess.CompletedProcess(
            [], 0, stdout="303 303 Z+\n304 303 Z+\n", stderr=""
        )
        ps_failure = subprocess.CompletedProcess([], 1, stdout="", stderr="denied")
        group_running = getattr(
            pc, "_process_group_has_running_members", lambda _pgid: False
        )
        with patch.object(pc.subprocess, "run", return_value=running_group):
            self.assertTrue(group_running(303))
        with patch.object(pc.subprocess, "run", return_value=all_zombies):
            self.assertFalse(group_running(303))
        with patch.object(pc.subprocess, "run", return_value=ps_failure):
            with self.assertRaisesRegex(RuntimeError, "enumerate|process group|ps"):
                group_running(303)

        identity = self._identity(303, ["/opt/python", "runner.py"])
        process = MagicMock(pid=303)
        process.poll.return_value = None
        with patch.object(pc, "_process_identity_matches", return_value=True), patch.object(
            pc.os, "getpgid", return_value=303
        ), patch.object(pc.os, "killpg"), patch.object(
            pc, "_process_is_running", return_value=False
        ), patch.object(
            pc,
            "_process_group_has_running_members",
            create=True,
            side_effect=[True, False],
        ) as group_check, patch.object(pc.time, "sleep"):
            pc._stop_verified_process_identity(
                identity, process_group=True, process=process
            )
        self.assertEqual(group_check.call_count, 2)
        process.wait.assert_called_once_with(timeout=5)

        exited_leader = MagicMock(pid=303)
        exited_leader.poll.return_value = 0
        with patch.object(pc, "_process_identity_matches") as identity_check, patch.object(
            pc.os, "getpgid"
        ) as getpgid, patch.object(pc.os, "killpg") as signal_group, patch.object(
            pc,
            "_process_group_has_running_members",
            return_value=True,
        ) as group_check, patch.object(pc.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "leader exited|ID reuse"):
                pc._stop_verified_process_identity(
                    identity, process_group=True, process=exited_leader
                )
        identity_check.assert_not_called()
        getpgid.assert_not_called()
        signal_group.assert_not_called()
        group_check.assert_called_once_with(303)

    def test_historical_rollback_rechecks_profile_and_refuses_duplicate_tunnel_id(self):
        item = {
            "pid": 101,
            "command": "/opt/tunnel-client run --profile-dir /private/m0 --profile local-agent-bridge-poc",
            "profile_dir": "/private/m0",
            "profile_name": "local-agent-bridge-poc",
            "profile_path": "/private/m0/local-agent-bridge-poc.yaml",
            "profile_sha256": "a" * 64,
            "tunnel_id": "tunnel-existing",
            "expected_socket": str(pc.HISTORICAL_M0_BRIDGE_SOCKET),
        }
        with patch.object(pc, "_validate_tunnel_profile", return_value={
            "profile_sha256": "b" * 64,
            "tunnel_id": "tunnel-existing",
        }), patch.object(pc, "_spawn_tunnel") as spawn:
            with self.assertRaisesRegex(RuntimeError, "profile.*changed|hash"):
                pc._restart_verified_historical_tunnel(item)
        spawn.assert_not_called()

        with patch.object(
            pc,
            "_discover_tunnels_by_id",
            return_value=[{"pid": 101}, {"pid": 303}],
        ):
            with self.assertRaisesRegex(RuntimeError, "exclusive|same tunnel-id"):
                pc._require_exact_tunnel_id_owners("tunnel-existing", {101})

        with patch.object(pc, "_validate_tunnel_profile", return_value={
            "profile_sha256": "a" * 64,
            "tunnel_id": "tunnel-existing",
        }), patch.object(pc, "_discover_tunnels_by_id", return_value=[{"pid": 303}]), patch.object(
            pc, "_spawn_tunnel"
        ) as spawn:
            with self.assertRaisesRegex(RuntimeError, "duplicate|already active|same tunnel"):
                pc._restart_verified_historical_tunnel(item)
        spawn.assert_not_called()

    def test_activation_requires_all_preconditions_and_rolls_back_only_verified_historical_tunnel(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(manifest, runtime_root=root / "runtime", bridge_socket=root / "bridge.sock")
            old = {
                "pid": 101,
                "command": "/opt/tunnel-client run --profile-dir /private/m0 --profile local-agent-bridge-poc",
                "profile_dir": "/private/m0",
                "tunnel_id": "tunnel-existing",
                "expected_endpoint_matches": True,
            }
            bridge = {"pid": 202, "command": "/private/m0/local-tool-bridge", "socket": str(pc.HISTORICAL_M0_BRIDGE_SOCKET)}
            calls = []
            fallback = {
                "ready": True,
                "health_url": "http://127.0.0.1:52354",
                "compute_url": "http://127.0.0.1:52342/mcp",
                "compute_host": "127.0.0.1",
                "compute_port": 52342,
            }
            with patch.object(pc, "validate_installed_release"), patch.object(
                pc, "production_status", return_value={"local_runtime_ready": True}
            ), patch.object(pc, "verify_caller_conversation_id_rejected"), patch.object(
                pc, "validate_native_messaging_manifests"
            ), patch.object(
                pc, "_validate_tunnel_profile", return_value={"tunnel_id": "tunnel-existing"}
            ), patch.object(pc, "_verify_ai_bridge_fallback", return_value=fallback), patch.object(
                pc, "_discover_tunnels_by_id", side_effect=[[old], []]
            ), patch.object(
                pc, "find_verified_historical_m0_tunnel", return_value=old
            ), patch.object(
                pc, "verified_historical_m0_bridge", return_value=bridge
            ), patch.object(pc, "_stop_verified_tunnel", side_effect=lambda value: calls.append(("stop-tunnel", value["pid"]))) as stop_tunnel, patch.object(
                pc, "_start_production_tunnel", side_effect=RuntimeError("production tunnel failed")
            ), patch.object(pc, "_restart_verified_historical_tunnel", side_effect=lambda value: calls.append(("rollback", value["pid"]))) as rollback:
                with self.assertRaisesRegex(RuntimeError, "production tunnel failed"):
                    pc.activate_production_tunnel(
                        manifest,
                        layout,
                        historical_profile_dir=root / "m0-profile",
                        historical_profile_name="local-agent-bridge-poc",
                        historical_runtime_state=root / "m0-runtime.json",
                        home_dir=root / "home",
                    )
            stop_tunnel.assert_called_once_with(old)
            rollback.assert_called_once_with(old)
            self.assertEqual(calls, [("stop-tunnel", 101), ("rollback", 101)])

    def test_activation_preserves_historical_bridge_after_production_tunnel_is_verified(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(manifest, runtime_root=root / "runtime", bridge_socket=root / "bridge.sock")
            old = {"pid": 101, "command": "/opt/tunnel-client run --profile-dir /private/m0 --profile local-agent-bridge-poc", "profile_dir": "/private/m0", "tunnel_id": "tunnel-existing", "expected_endpoint_matches": True}
            bridge = {"pid": 202, "command": "/private/m0/local-tool-bridge", "socket": str(pc.HISTORICAL_M0_BRIDGE_SOCKET)}
            calls = []
            fallback = {"ready": True, "health_url": "http://127.0.0.1:52354", "compute_url": "http://127.0.0.1:52342/mcp", "compute_host": "127.0.0.1", "compute_port": 52342}
            with patch.object(pc, "validate_installed_release"), patch.object(pc, "production_status", return_value={"local_runtime_ready": True}), patch.object(pc, "verify_caller_conversation_id_rejected"), patch.object(pc, "validate_native_messaging_manifests"), patch.object(pc, "_verify_ai_bridge_fallback", return_value=fallback) as fallback_guard, patch.object(pc, "_validate_tunnel_profile", return_value={"tunnel_id": "tunnel-existing"}), patch.object(pc, "_discover_tunnels_by_id", side_effect=[[old], [], [{"pid": 303, "expected_endpoint_matches": True}]]), patch.object(pc, "find_verified_historical_m0_tunnel", return_value=old), patch.object(pc, "verified_historical_m0_bridge", return_value=bridge), patch.object(pc, "_stop_verified_tunnel", side_effect=lambda value: calls.append("old-tunnel")), patch.object(pc, "_start_production_tunnel", return_value={"pid": 303, "tunnel_id": "tunnel-existing"}), patch.object(pc, "_write_activation_rollback_receipt", return_value={"path": str(layout["rollback_receipt"]), "sha256": "f" * 64}):
                result = pc.activate_production_tunnel(manifest, layout, historical_profile_dir=root / "m0-profile", historical_profile_name="local-agent-bridge-poc", historical_runtime_state=root / "m0-runtime.json", home_dir=root / "home")
            self.assertTrue(result["activated"])
            self.assertTrue(result["historical_bridge_preserved"])
            self.assertEqual(calls, ["old-tunnel"])
            self.assertEqual(fallback_guard.call_count, 2)

    def test_activation_rolls_back_if_post_stop_tunnel_ownership_check_fails(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest,
                runtime_root=root / "runtime",
                bridge_socket=root / "bridge.sock",
            )
            old = {"pid": 101, "tunnel_id": "tunnel-existing", "expected_endpoint_matches": True}
            bridge = {"pid": 202}
            fallback = {
                "ready": True,
                "health_url": "http://127.0.0.1:52354",
                "compute_url": "http://127.0.0.1:52342/mcp",
                "compute_host": "127.0.0.1",
                "compute_port": 52342,
            }
            calls = []
            with patch.object(pc, "validate_installed_release"), patch.object(
                pc, "production_status", return_value={"local_runtime_ready": True}
            ), patch.object(pc, "verify_caller_conversation_id_rejected"), patch.object(
                pc, "validate_native_messaging_manifests"
            ), patch.object(
                pc, "_verify_ai_bridge_fallback", return_value=fallback
            ), patch.object(
                pc, "_validate_tunnel_profile", return_value={"tunnel_id": "tunnel-existing"}
            ), patch.object(
                pc, "find_verified_historical_m0_tunnel", return_value=old
            ), patch.object(
                pc, "verified_historical_m0_bridge", return_value=bridge
            ), patch.object(
                pc,
                "_discover_tunnels_by_id",
                side_effect=[[old], RuntimeError("post-stop ps failed")],
            ), patch.object(
                pc,
                "_stop_verified_tunnel",
                side_effect=lambda value: calls.append(("stop", value["pid"])),
            ), patch.object(
                pc,
                "_restart_verified_historical_tunnel",
                side_effect=lambda value: calls.append(("rollback", value["pid"])),
            ) as rollback, patch.object(pc, "_start_production_tunnel") as start:
                with self.assertRaisesRegex(RuntimeError, "post-stop ps failed"):
                    pc.activate_production_tunnel(
                        manifest,
                        layout,
                        historical_profile_dir=root / "m0-profile",
                        historical_profile_name="local-agent-bridge-poc",
                        historical_runtime_state=root / "m0-runtime.json",
                        home_dir=root / "home",
                    )
            start.assert_not_called()
            rollback.assert_called_once_with(old)
            self.assertEqual(calls, [("stop", 101), ("rollback", 101)])

    def test_activation_failure_records_restarted_m0_identity_in_both_runtime_states(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest, runtime_root=root / "runtime", bridge_socket=root / "bridge.sock"
            )
            pc._write_json_private(layout["runtime_state"], {"local_sentinel": "preserve"})
            historical_state_path = root / "m0-runtime.json"
            pc._write_json_private(historical_state_path, {"bridge_pid": 202, "sentinel": "preserve"})
            old = {"pid": 101, "tunnel_id": "tunnel-existing", "expected_endpoint_matches": True}
            restarted = {
                "pid": 404,
                "tunnel_id": "tunnel-existing",
                "expected_endpoint_matches": True,
                "profile_name": "local-agent-bridge-poc",
                "profile_sha256": "a" * 64,
                "process_identity": self._identity(404, [
                    "/opt/tunnel-client", "run", "--profile-dir", "/private/m0",
                    "--profile", "local-agent-bridge-poc",
                ]),
            }
            with patch.object(pc, "validate_installed_release"), patch.object(
                pc, "production_status", return_value={"local_runtime_ready": True}
            ), patch.object(pc, "verify_caller_conversation_id_rejected"), patch.object(
                pc, "validate_native_messaging_manifests"
            ), patch.object(
                pc, "_verify_ai_bridge_fallback", return_value={"ready": True}
            ), patch.object(
                pc, "_validate_tunnel_profile", return_value={"tunnel_id": "tunnel-existing"}
            ), patch.object(
                pc, "find_verified_historical_m0_tunnel", return_value=old
            ), patch.object(
                pc, "verified_historical_m0_bridge", return_value={"pid": 202}
            ), patch.object(
                pc, "_discover_tunnels_by_id", side_effect=[[old], [], [restarted]]
            ), patch.object(pc, "_stop_verified_tunnel"), patch.object(
                pc, "_start_production_tunnel", side_effect=RuntimeError("production tunnel failed")
            ), patch.object(
                pc, "_restart_verified_historical_tunnel", return_value=restarted
            ):
                with self.assertRaisesRegex(RuntimeError, "production tunnel failed") as raised:
                    pc.activate_production_tunnel(
                        manifest,
                        layout,
                        historical_profile_dir=root / "m0-profile",
                        historical_profile_name="local-agent-bridge-poc",
                        historical_runtime_state=historical_state_path,
                        home_dir=root / "home",
                    )
            self.assertNotIn("rollback failed", str(raised.exception))
            historical_state = json.loads(historical_state_path.read_text(encoding="utf-8"))
            self.assertEqual(historical_state["tunnel_pid"], 404)
            self.assertEqual(historical_state["tunnel_process_identity"], restarted["process_identity"])
            production_state = json.loads(layout["runtime_state"].read_text(encoding="utf-8"))
            self.assertEqual(production_state["rollback_historical_tunnel_pid"], 404)
            self.assertEqual(production_state["local_sentinel"], "preserve")

    def test_activation_rolls_back_if_ai_bridge_fallback_is_not_healthy_after_cutover(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(manifest, runtime_root=root / "runtime", bridge_socket=root / "bridge.sock")
            old = {"pid": 101, "tunnel_id": "tunnel-existing", "expected_endpoint_matches": True}
            production = {"pid": 303, "tunnel_id": "tunnel-existing", "expected_endpoint_matches": True}
            bridge = {"pid": 202}
            fallback = {"ready": True, "health_url": "http://127.0.0.1:52354", "compute_url": "http://127.0.0.1:52342/mcp", "compute_host": "127.0.0.1", "compute_port": 52342}
            calls = []
            with patch.object(pc, "validate_installed_release"), patch.object(pc, "production_status", return_value={"local_runtime_ready": True}), patch.object(pc, "verify_caller_conversation_id_rejected"), patch.object(pc, "validate_native_messaging_manifests"), patch.object(pc, "_validate_tunnel_profile", return_value={"tunnel_id": "tunnel-existing"}), patch.object(pc, "_discover_tunnels_by_id", side_effect=[[old], [], [production]]), patch.object(pc, "find_verified_historical_m0_tunnel", return_value=old), patch.object(pc, "verified_historical_m0_bridge", return_value=bridge), patch.object(pc, "_verify_ai_bridge_fallback", side_effect=[fallback, RuntimeError("fallback unhealthy")]), patch.object(pc, "_stop_verified_tunnel", side_effect=lambda value: calls.append(("stop", value["pid"]))), patch.object(pc, "_start_production_tunnel", return_value=production), patch.object(pc, "_restart_verified_historical_tunnel", side_effect=lambda value: calls.append(("rollback", value["pid"]))):
                with self.assertRaisesRegex(RuntimeError, "fallback unhealthy"):
                    pc.activate_production_tunnel(manifest, layout, historical_profile_dir=root / "m0-profile", historical_profile_name="local-agent-bridge-poc", historical_runtime_state=root / "m0-runtime.json", home_dir=root / "home")
            self.assertEqual(calls, [("stop", 101), ("stop", 303), ("rollback", 101)])

    def test_activation_rolls_back_if_ai_bridge_tool_surface_changes(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest, runtime_root=root / "runtime", bridge_socket=root / "bridge.sock"
            )
            old = {"pid": 101, "tunnel_id": "tunnel-existing", "expected_endpoint_matches": True}
            production = {"pid": 303, "tunnel_id": "tunnel-existing", "expected_endpoint_matches": True}
            bridge = {"pid": 202}
            fallback = {
                "ready": True,
                "health_url": "http://127.0.0.1:52354",
                "compute_url": "http://127.0.0.1:52342/mcp",
                "compute_host": "127.0.0.1",
                "compute_port": 52342,
                "tools": ["computer", "shell_command"],
            }
            calls = []
            with patch.object(pc, "validate_installed_release"), patch.object(
                pc, "production_status", return_value={"local_runtime_ready": True}
            ), patch.object(pc, "verify_caller_conversation_id_rejected"), patch.object(
                pc, "validate_native_messaging_manifests"
            ), patch.object(
                pc, "_validate_tunnel_profile", return_value={"tunnel_id": "tunnel-existing"}
            ), patch.object(
                pc, "_discover_tunnels_by_id", side_effect=[[old], [], [production]]
            ), patch.object(
                pc, "find_verified_historical_m0_tunnel", return_value=old
            ), patch.object(
                pc, "verified_historical_m0_bridge", return_value=bridge
            ), patch.object(
                pc,
                "_verify_ai_bridge_fallback",
                side_effect=[fallback, {**fallback, "tools": ["computer", "shell_command", "unexpected"]}],
            ), patch.object(
                pc, "_stop_verified_tunnel", side_effect=lambda value: calls.append(("stop", value["pid"]))
            ), patch.object(
                pc, "_start_production_tunnel", return_value=production
            ), patch.object(
                pc,
                "_restart_verified_historical_tunnel",
                side_effect=lambda value: calls.append(("rollback", value["pid"])),
            ):
                with self.assertRaisesRegex(RuntimeError, "fallback identity changed"):
                    pc.activate_production_tunnel(
                        manifest,
                        layout,
                        historical_profile_dir=root / "m0-profile",
                        historical_profile_name="local-agent-bridge-poc",
                        historical_runtime_state=root / "m0-runtime.json",
                        home_dir=root / "home",
                    )
            self.assertEqual(calls, [("stop", 101), ("stop", 303), ("rollback", 101)])

    def test_adopt_current_rollback_arms_observed_production_without_inventing_activation(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest,
                runtime_root=root / "runtime",
                bridge_socket=root / "bridge.sock",
            )
            prior, historical, production, state, ready = self._rollback_adoption_fixture(
                root, manifest, layout
            )
            prior_receipt_sha256 = pc._sha256_file(layout["rollback_receipt"])
            runtime_before_sha256 = pc._sha256_file(layout["runtime_state"])
            installed = {
                **manifest,
                "host": {
                    **manifest["host"],
                    "minimum_extension_version": "0.1.1",
                },
                "artifacts_sha256": {"bin/bridge": "f" * 64},
            }
            fallback = {
                "ready": True,
                "health_url": "http://127.0.0.1:52354",
                "compute_url": "http://127.0.0.1:52342/mcp",
                "compute_host": "127.0.0.1",
                "compute_port": 52342,
                "tools": ["computer", "shell_command"],
                "status_sha256": "e" * 64,
                "ai_bridge_is_trusted_receipt_source": False,
            }

            def tunnel_processes(*, profile_dir, profile_name, expected_socket):
                del profile_dir, expected_socket
                return [] if profile_name == historical["profile_name"] else [production]

            def profile(_path, *, profile_name, expected_socket):
                del expected_socket
                if profile_name == historical["profile_name"]:
                    return {
                        "profile_sha256": historical["profile_sha256"],
                        "tunnel_id": historical["tunnel_id"],
                    }
                return {
                    "profile_sha256": production["profile_sha256"],
                    "tunnel_id": production["tunnel_id"],
                }

            with patch.object(
                pc, "validate_installed_release", return_value=installed
            ), patch.object(
                pc, "production_status", return_value=ready
            ) as status_guard, patch.object(
                pc, "verify_caller_conversation_id_rejected"
            ), patch.object(
                pc, "validate_native_messaging_manifests", return_value={}
            ) as native_guard, patch.object(
                pc, "_verify_ai_bridge_fallback", return_value=fallback
            ), patch.object(
                pc, "_validate_tunnel_profile", side_effect=profile
            ), patch.object(
                pc, "_discover_tunnel_processes", side_effect=tunnel_processes
            ), patch.object(
                pc,
                "_process_identity_matches",
                side_effect=lambda identity: identity.get("pid") != historical["pid"],
            ), patch.object(
                pc, "_require_exact_tunnel_id_owners"
            ), patch.object(
                pc, "_verify_process_owned_unix_socket"
            ) as socket_owner, patch.object(
                pc, "_stop_verified_tunnel"
            ) as forbidden_stop, patch.object(
                pc, "_start_production_tunnel"
            ) as forbidden_start:
                result = pc.adopt_current_production_rollback(
                    manifest,
                    layout,
                    home_dir=root / "home",
                    fallback_health_url_file=root / "fallback-health-url",
                )

            self.assertTrue(result["adopted"])
            status_guard.assert_called_with(installed, layout)
            native_guard.assert_called_with(
                layout,
                installed,
                home_dir=root / "home",
                native_user_data_dirs=(),
            )
            forbidden_stop.assert_not_called()
            forbidden_start.assert_not_called()
            socket_owner.assert_has_calls(
                [
                    call(
                        prior["historical_bridge"]["process_identity"],
                        pc.HISTORICAL_M0_BRIDGE_SOCKET,
                        "historical M0 Bridge",
                    ),
                    call(
                        state["bridge_process_identity"],
                        layout["bridge_socket"],
                        "current production Bridge",
                    ),
                ],
                any_order=True,
            )
            receipt = json.loads(layout["rollback_receipt"].read_text(encoding="utf-8"))
            self.assertEqual(receipt["status"], "armed")
            self.assertEqual(receipt["provenance"], "observed_current_production")
            self.assertEqual(receipt["original_pre_activation_baseline"], "unknown")
            self.assertEqual(receipt["historical"], historical)
            self.assertEqual(receipt["historical_bridge"], prior["historical_bridge"])
            self.assertEqual(
                receipt["prior_canonical_completed_rollback_receipt_sha256"],
                prior_receipt_sha256,
            )
            self.assertEqual(
                receipt["adoption_runtime_state_before_sha256"],
                runtime_before_sha256,
            )
            self.assertNotIn("activated_at", receipt)
            self.assertNotEqual(receipt.get("created_at"), prior["created_at"])
            updated_state = json.loads(layout["runtime_state"].read_text(encoding="utf-8"))
            self.assertEqual(
                updated_state["activation_rollback_receipt"],
                str(layout["rollback_receipt"].resolve()),
            )
            self.assertEqual(
                updated_state["activation_rollback_receipt_sha256"],
                pc._sha256_file(layout["rollback_receipt"]),
            )
            for key, value in state.items():
                self.assertEqual(updated_state[key], value)

    def test_adopt_current_rollback_rejects_receipt_identity_pin_ready_profile_and_status_drift_without_writes(self):
        manifest = self._manifest()
        cases = (
            "prior_receipt_not_completed",
            "prior_generation_not_predecessor",
            "prior_release_same_current",
            "component_identity",
            "receipt_pin_only",
            "receipt_hash_only",
            "not_ready",
            "profile",
            "status_drift",
            "installed_manifest_contract_drift",
            "installed_host_contract_drift",
            "installed_minimum_version_type",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as raw_root:
                root = Path(raw_root)
                layout = pc.production_layout(
                    manifest,
                    runtime_root=root / "runtime",
                    bridge_socket=root / "bridge.sock",
                )
                prior, historical, production, state, ready = self._rollback_adoption_fixture(
                    root, manifest, layout
                )
                if case == "prior_receipt_not_completed":
                    pc._write_json_private(
                        layout["rollback_receipt"], {**prior, "status": "armed", "phase": "intent"}
                    )
                if case == "prior_generation_not_predecessor":
                    pc._write_json_private(
                        layout["rollback_receipt"],
                        {**prior, "endpoint_generation": manifest["endpoint_generation"] - 2},
                    )
                if case == "prior_release_same_current":
                    pc._write_json_private(
                        layout["rollback_receipt"],
                        {**prior, "release_revision": manifest["release_revision"]},
                    )
                if case == "receipt_pin_only":
                    state["activation_rollback_receipt"] = str(layout["rollback_receipt"].resolve())
                    pc._write_json_private(layout["runtime_state"], state)
                if case == "receipt_hash_only":
                    state["activation_rollback_receipt_sha256"] = "d" * 64
                    pc._write_json_private(layout["runtime_state"], state)
                receipt_before = layout["rollback_receipt"].read_bytes()
                state_before = layout["runtime_state"].read_bytes()
                installed = {**manifest, "artifacts_sha256": {"bin/bridge": "f" * 64}}
                if case == "installed_manifest_contract_drift":
                    installed = {
                        **installed,
                        "required_tools": ["discover_current_entry"],
                    }
                if case == "installed_host_contract_drift":
                    installed = {
                        **installed,
                        "host": {
                            **installed["host"],
                            "machine_source": "caller_claimed_context",
                        },
                    }
                if case == "installed_minimum_version_type":
                    installed = {
                        **installed,
                        "host": {
                            **installed["host"],
                            "minimum_extension_version": 1,
                        },
                    }

                status_values = [dict(ready), dict(ready), dict(ready)]
                if case == "not_ready":
                    status_values[0]["tunnel_ready"] = False
                if case == "status_drift":
                    status_values[1]["tunnel_pid"] = 909

                def tunnel_processes(*, profile_dir, profile_name, expected_socket):
                    del profile_dir, expected_socket
                    return [] if profile_name == historical["profile_name"] else [production]

                def profile(_path, *, profile_name, expected_socket):
                    del expected_socket
                    if profile_name == historical["profile_name"]:
                        return {
                            "profile_sha256": historical["profile_sha256"],
                            "tunnel_id": historical["tunnel_id"],
                        }
                    return {
                        "profile_sha256": "0" * 64 if case == "profile" else production["profile_sha256"],
                        "tunnel_id": production["tunnel_id"],
                    }

                def identity_matches(identity):
                    if identity.get("pid") == historical["pid"]:
                        return False
                    if case == "component_identity" and identity.get("pid") == state["host_pid"]:
                        return False
                    return True

                fallback = {
                    "ready": True,
                    "health_url": "http://127.0.0.1:52354",
                    "compute_url": "http://127.0.0.1:52342/mcp",
                    "compute_host": "127.0.0.1",
                    "compute_port": 52342,
                    "tools": ["computer", "shell_command"],
                    "status_sha256": "e" * 64,
                    "ai_bridge_is_trusted_receipt_source": False,
                }
                with patch.object(
                    pc, "validate_installed_release", return_value=installed
                ), patch.object(
                    pc, "production_status", side_effect=status_values
                ), patch.object(
                    pc, "verify_caller_conversation_id_rejected"
                ), patch.object(
                    pc, "validate_native_messaging_manifests", return_value={}
                ), patch.object(
                    pc, "_verify_ai_bridge_fallback", return_value=fallback
                ), patch.object(
                    pc, "_validate_tunnel_profile", side_effect=profile
                ), patch.object(
                    pc, "_discover_tunnel_processes", side_effect=tunnel_processes
                ), patch.object(
                    pc, "_process_identity_matches", side_effect=identity_matches
                ), patch.object(
                    pc, "_require_exact_tunnel_id_owners"
                ), patch.object(
                    pc, "_verify_process_owned_unix_socket"
                ):
                    with self.assertRaises(RuntimeError):
                        pc.adopt_current_production_rollback(
                            manifest,
                            layout,
                            home_dir=root / "home",
                            fallback_health_url_file=root / "fallback-health-url",
                        )
                self.assertEqual(layout["rollback_receipt"].read_bytes(), receipt_before)
                self.assertEqual(layout["runtime_state"].read_bytes(), state_before)

    def test_adopt_current_rollback_restores_receipt_and_runtime_bytes_when_pin_write_fails_after_replace(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest,
                runtime_root=root / "runtime",
                bridge_socket=root / "bridge.sock",
            )
            prior, historical, production, state, ready = self._rollback_adoption_fixture(
                root, manifest, layout
            )
            receipt_before = layout["rollback_receipt"].read_bytes()
            state_before = layout["runtime_state"].read_bytes()
            observation = {
                "installed": {**manifest, "artifacts_sha256": {"bin/bridge": "f" * 64}},
                "status": ready,
                "state": state,
                "state_snapshot": (state_before, 0o600),
                "production": production,
                "component_identities": {
                    name: state[f"{name}_process_identity"]
                    for name in ("catdesk_supervisor", "host", "runtime_hook", "bridge")
                },
                "fallback_identity": {"status_sha256": "e" * 64},
            }
            original_write = pc._write_json_private

            def fail_after_runtime_replace(path, value, **kwargs):
                original_write(path, value, **kwargs)
                if path == layout["runtime_state"] and "activation_rollback_receipt" in value:
                    raise RuntimeError("injected runtime pin persistence failure")

            with patch.object(
                pc,
                "_completed_receipt_historical_baseline",
                return_value=(
                    historical,
                    prior["historical_bridge"],
                    Path(prior["historical_runtime_state"]),
                    pc._sha256_file(Path(prior["historical_runtime_state"])),
                ),
            ), patch.object(
                pc,
                "_observe_current_production_for_rollback_adoption",
                return_value=observation,
            ), patch.object(
                pc, "_write_json_private", side_effect=fail_after_runtime_replace
            ):
                with self.assertRaisesRegex(RuntimeError, "injected runtime pin persistence failure"):
                    pc.adopt_current_production_rollback(manifest, layout)
            self.assertEqual(layout["rollback_receipt"].read_bytes(), receipt_before)
            self.assertEqual(layout["runtime_state"].read_bytes(), state_before)

    def test_adopt_current_rollback_restores_receipt_when_receipt_write_fails_after_replace(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest,
                runtime_root=root / "runtime",
                bridge_socket=root / "bridge.sock",
            )
            prior, historical, production, state, ready = self._rollback_adoption_fixture(
                root, manifest, layout
            )
            receipt_before = layout["rollback_receipt"].read_bytes()
            state_before = layout["runtime_state"].read_bytes()
            observation = {
                "installed": {**manifest, "artifacts_sha256": {"bin/bridge": "f" * 64}},
                "status": ready,
                "state": state,
                "state_snapshot": (state_before, 0o600),
                "production": production,
                "component_identities": {
                    name: state[f"{name}_process_identity"]
                    for name in ("catdesk_supervisor", "host", "runtime_hook", "bridge")
                },
                "fallback_identity": {"status_sha256": "e" * 64},
            }
            original_write = pc._write_json_private

            def fail_after_receipt_replace(path, value, **kwargs):
                original_write(path, value, **kwargs)
                if (
                    path.resolve() == layout["rollback_receipt"].resolve()
                    and value.get("status") == "armed"
                ):
                    raise RuntimeError("injected receipt persistence failure")

            with patch.object(
                pc,
                "_completed_receipt_historical_baseline",
                return_value=(
                    historical,
                    prior["historical_bridge"],
                    Path(prior["historical_runtime_state"]),
                    pc._sha256_file(Path(prior["historical_runtime_state"])),
                ),
            ), patch.object(
                pc,
                "_observe_current_production_for_rollback_adoption",
                return_value=observation,
            ), patch.object(
                pc, "_write_json_private", side_effect=fail_after_receipt_replace
            ):
                with self.assertRaisesRegex(RuntimeError, "injected receipt persistence failure"):
                    pc.adopt_current_production_rollback(manifest, layout)
            self.assertEqual(layout["rollback_receipt"].read_bytes(), receipt_before)
            self.assertEqual(layout["runtime_state"].read_bytes(), state_before)

    def test_adopt_current_rollback_restores_both_files_when_post_pin_identity_recheck_drifts(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest,
                runtime_root=root / "runtime",
                bridge_socket=root / "bridge.sock",
            )
            prior, historical, production, state, ready = self._rollback_adoption_fixture(
                root, manifest, layout
            )
            receipt_before = layout["rollback_receipt"].read_bytes()
            state_before = layout["runtime_state"].read_bytes()
            observation = {
                "installed": {**manifest, "artifacts_sha256": {"bin/bridge": "f" * 64}},
                "status": ready,
                "state": state,
                "state_snapshot": (state_before, 0o600),
                "production": production,
                "component_identities": {
                    name: state[f"{name}_process_identity"]
                    for name in ("catdesk_supervisor", "host", "runtime_hook", "bridge")
                },
                "fallback_identity": {"status_sha256": "e" * 64},
            }
            drifted = {
                **observation,
                "production": {**production, "pid": 909},
            }
            with patch.object(
                pc,
                "_completed_receipt_historical_baseline",
                return_value=(
                    historical,
                    prior["historical_bridge"],
                    Path(prior["historical_runtime_state"]),
                    pc._sha256_file(Path(prior["historical_runtime_state"])),
                ),
            ), patch.object(
                pc,
                "_observe_current_production_for_rollback_adoption",
                side_effect=[observation, observation, drifted],
            ):
                with self.assertRaisesRegex(RuntimeError, "post-pin|drift"):
                    pc.adopt_current_production_rollback(manifest, layout)
            self.assertEqual(layout["rollback_receipt"].read_bytes(), receipt_before)
            self.assertEqual(layout["runtime_state"].read_bytes(), state_before)

    def test_adopted_current_production_receipt_is_consumed_by_existing_rollback_gate(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest,
                runtime_root=root / "runtime",
                bridge_socket=root / "bridge.sock",
            )
            _prior, historical, production, _state, ready = self._rollback_adoption_fixture(
                root, manifest, layout
            )
            installed = {**manifest, "artifacts_sha256": {"bin/bridge": "f" * 64}}
            fallback = {
                "ready": True,
                "health_url": "http://127.0.0.1:52354",
                "compute_url": "http://127.0.0.1:52342/mcp",
                "compute_host": "127.0.0.1",
                "compute_port": 52342,
                "tools": ["computer", "shell_command"],
                "status_sha256": "e" * 64,
                "ai_bridge_is_trusted_receipt_source": False,
            }

            def tunnel_processes(*, profile_dir, profile_name, expected_socket):
                del profile_dir, expected_socket
                return [] if profile_name == historical["profile_name"] else [production]

            def profile(_path, *, profile_name, expected_socket):
                del expected_socket
                return {
                    "profile_sha256": (
                        historical["profile_sha256"]
                        if profile_name == historical["profile_name"]
                        else production["profile_sha256"]
                    ),
                    "tunnel_id": production["tunnel_id"],
                }

            with patch.object(
                pc, "validate_installed_release", return_value=installed
            ), patch.object(
                pc, "production_status", return_value=ready
            ), patch.object(
                pc, "verify_caller_conversation_id_rejected"
            ), patch.object(
                pc, "validate_native_messaging_manifests", return_value={}
            ), patch.object(
                pc, "_verify_ai_bridge_fallback", return_value=fallback
            ), patch.object(
                pc, "_validate_tunnel_profile", side_effect=profile
            ), patch.object(
                pc, "_discover_tunnel_processes", side_effect=tunnel_processes
            ), patch.object(
                pc,
                "_process_identity_matches",
                side_effect=lambda identity: identity.get("pid") != historical["pid"],
            ), patch.object(
                pc, "_require_exact_tunnel_id_owners"
            ), patch.object(
                pc, "_verify_process_owned_unix_socket"
            ):
                pc.adopt_current_production_rollback(
                    manifest,
                    layout,
                    home_dir=root / "home",
                    fallback_health_url_file=root / "fallback-health-url",
                )

            restarted = {
                **historical,
                "pid": 505,
                "process_identity": self._identity(505, historical["process_identity"]["argv"]),
            }
            with patch.object(pc, "_validate_rollback_release"), patch.object(
                pc, "_process_identity_matches", return_value=True
            ), patch.object(
                pc, "_validate_tunnel_profile", side_effect=profile
            ), patch.object(
                pc, "_resume_rollback_tunnel_switch", return_value=restarted
            ) as switch, patch.object(
                pc, "_require_exact_tunnel_id_owners"
            ):
                result = pc.rollback_production_activation(manifest, layout)
            self.assertTrue(result["rolled_back"])
            self.assertFalse(result["already_rolled_back"])
            switch.assert_called_once()
            completed = json.loads(layout["rollback_receipt"].read_text(encoding="utf-8"))
            self.assertEqual(completed["status"], "rolled_back")
            self.assertEqual(completed["provenance"], "observed_current_production")

    def test_all_public_production_state_mutators_take_the_shared_install_lock_first(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest,
                runtime_root=root / "runtime",
                bridge_socket=root / "bridge.sock",
            )

            cases = (
                (
                    "install-native",
                    "_validate_manifest_contract",
                    lambda: pc.install_native_messaging_manifests(layout, manifest),
                ),
                (
                    "rollback",
                    "_validate_rollback_release",
                    lambda: pc.rollback_production_activation(manifest, layout),
                ),
                (
                    "activate",
                    "validate_installed_release",
                    lambda: pc.activate_production_tunnel(
                        manifest,
                        layout,
                        historical_profile_dir=root / "m0-profile",
                        historical_profile_name="local-agent-bridge-poc",
                    ),
                ),
                (
                    "start-local",
                    "validate_installed_release",
                    lambda: pc.start_local_runtime(manifest, layout),
                ),
            )
            for name, first_validation, invoke in cases:
                with self.subTest(action=name):
                    locked_roots = []

                    @contextmanager
                    def record_lock(runtime_root):
                        locked_roots.append(Path(runtime_root))
                        yield

                    with patch.object(
                        pc, "_production_install_lock", side_effect=record_lock
                    ), patch.object(
                        pc,
                        first_validation,
                        side_effect=RuntimeError("stop after lock probe"),
                    ):
                        with self.assertRaisesRegex(RuntimeError, "stop after lock probe"):
                            invoke()
                    self.assertEqual(locked_roots, [layout["runtime_root"]])

    def test_post_e2e_rollback_uses_pinned_receipt_and_updates_both_states_idempotently(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest, runtime_root=root / "runtime", bridge_socket=root / "bridge.sock"
            )
            historical_state_path = root / "m0-runtime.json"
            historical_state_path.write_text(
                json.dumps({"bridge_pid": 202, "sentinel": "preserve"}), encoding="utf-8"
            )
            historical_state_path.chmod(0o600)
            historical_identity = self._identity(101, [
                "/opt/tunnel-client", "run", "--profile-dir", "/private/m0",
                "--profile", "local-agent-bridge-poc",
            ])
            production_identity = self._identity(303, [
                "/opt/tunnel-client", "run", "--profile-dir", str(layout["tunnel_dir"]),
                "--profile", pc.TUNNEL_PROFILE_NAME,
            ])
            restarted_identity = self._identity(404, historical_identity["argv"])
            historical = {
                "pid": 101,
                "command": " ".join(historical_identity["argv"]),
                "profile_dir": "/private/m0",
                "profile_name": "local-agent-bridge-poc",
                "profile_path": "/private/m0/local-agent-bridge-poc.yaml",
                "profile_sha256": "a" * 64,
                "tunnel_id": "tunnel-existing",
                "process_identity": historical_identity,
                "expected_socket": str(pc.HISTORICAL_M0_BRIDGE_SOCKET),
            }
            production = {
                "pid": 303,
                "command": " ".join(production_identity["argv"]),
                "profile_dir": str(layout["tunnel_dir"]),
                "profile_name": pc.TUNNEL_PROFILE_NAME,
                "profile_path": str(layout["tunnel_profile"]),
                "profile_sha256": "b" * 64,
                "tunnel_id": "tunnel-existing",
                "process_identity": production_identity,
                "expected_socket": str(layout["bridge_socket"]),
            }
            restarted = {
                **historical,
                "pid": 404,
                "process_identity": restarted_identity,
            }
            receipt = {
                "schema_version": 1,
                "status": "armed",
                "connector_id": pc.CONNECTOR_ID,
                "contract_version": pc.CONTRACT_VERSION,
                "release_revision": REVISION,
                "endpoint_generation": 7,
                "tunnel_id": "tunnel-existing",
                "historical": historical,
                "production": production,
                "historical_bridge": {"pid": 202, "process_identity": self._identity(202, ["/opt/local-tool-bridge"])},
                "historical_runtime_state": str(historical_state_path.resolve()),
                "historical_runtime_state_sha256": pc._sha256_file(historical_state_path),
            }
            layout["rollback_receipt"].parent.mkdir(parents=True)
            pc._write_json_private(layout["rollback_receipt"], receipt)
            production_state = {
                "release_revision": REVISION,
                "endpoint_generation": 7,
                "workspace": manifest["workspace"],
                "tunnel_profile_name": pc.TUNNEL_PROFILE_NAME,
                "tunnel_pid": 303,
                "tunnel_id": "tunnel-existing",
                "tunnel_profile_sha256": "b" * 64,
                "tunnel_process_identity": production_identity,
                "activation_rollback_receipt": str(layout["rollback_receipt"].resolve()),
                "activation_rollback_receipt_sha256": pc._sha256_file(layout["rollback_receipt"]),
            }
            pc._write_json_private(layout["runtime_state"], production_state)

            def validated_profile(path, *, profile_name, expected_socket):
                return {
                    "profile_sha256": "a" * 64 if profile_name == "local-agent-bridge-poc" else "b" * 64,
                    "tunnel_id": "tunnel-existing",
                }

            with patch.object(pc, "_validate_rollback_release"), patch.object(
                pc, "_process_identity_matches", return_value=True
            ), patch.object(
                pc, "_validate_tunnel_profile", side_effect=validated_profile
            ), patch.object(
                pc, "_discover_tunnel_processes", return_value=[]
            ), patch.object(pc, "_require_exact_tunnel_id_owners") as owners, patch.object(
                pc, "_stop_verified_tunnel"
            ) as stop, patch.object(
                pc, "_restart_verified_historical_tunnel", return_value=restarted
            ) as restart:
                result = pc.rollback_production_activation(manifest, layout)
            self.assertTrue(result["rolled_back"])
            self.assertFalse(result["already_rolled_back"])
            stop.assert_called_once_with(production)
            restart.assert_called_once_with(historical)
            self.assertGreaterEqual(owners.call_count, 3)
            historical_state = json.loads(historical_state_path.read_text(encoding="utf-8"))
            self.assertEqual(historical_state["tunnel_pid"], 404)
            self.assertEqual(historical_state["tunnel_process_identity"], restarted_identity)
            self.assertEqual(historical_state["sentinel"], "preserve")
            updated_production = json.loads(layout["runtime_state"].read_text(encoding="utf-8"))
            self.assertNotIn("tunnel_pid", updated_production)
            self.assertEqual(updated_production["rollback_historical_tunnel_pid"], 404)
            self.assertEqual(
                updated_production["rollback_historical_tunnel_process_identity"], restarted_identity
            )
            completed_receipt = json.loads(
                layout["rollback_receipt"].read_text(encoding="utf-8")
            )
            self.assertEqual(completed_receipt["status"], "rolled_back")
            self.assertEqual(completed_receipt["phase"], "completed")
            self.assertEqual(len(completed_receipt["armed_receipt_sha256"]), 64)
            self.assertEqual(
                updated_production["activation_rollback_receipt_sha256"],
                pc._sha256_file(layout["rollback_receipt"]),
            )

            with patch.object(pc, "_validate_rollback_release"), patch.object(
                pc, "_process_identity_matches", return_value=True
            ), patch.object(
                pc, "_validate_tunnel_profile", side_effect=validated_profile
            ), patch.object(pc, "_require_exact_tunnel_id_owners"), patch.object(
                pc, "_stop_verified_tunnel"
            ) as forbidden_stop, patch.object(
                pc, "_restart_verified_historical_tunnel"
            ) as forbidden_restart:
                repeated = pc.rollback_production_activation(manifest, layout)
            self.assertTrue(repeated["already_rolled_back"])
            forbidden_stop.assert_not_called()
            forbidden_restart.assert_not_called()

            with self.assertRaisesRegex(RuntimeError, "canonical"):
                with patch.object(pc, "_validate_rollback_release"):
                    pc.rollback_production_activation(
                        manifest, layout, receipt_path=root / "attacker-controlled.json"
                    )

    def test_emergency_rollback_release_validation_does_not_require_node_or_artifacts(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest, runtime_root=root / "runtime", bridge_socket=root / "bridge.sock"
            )
            installed = json.loads(json.dumps(manifest))
            installed["runtime_dependencies"] = {
                "node": {"path": "/missing/node", "sha256": "0" * 64}
            }
            installed["artifacts_sha256"] = {"missing-artifact": "0" * 64}
            pc._write_json_private(layout["release_manifest"], installed)
            observed = pc._validate_rollback_release(manifest, layout)
            self.assertEqual(observed["release_revision"], REVISION)
            self.assertEqual(observed["endpoint_generation"], 7)

    def test_rollback_intent_reentry_adopts_exact_restarted_m0_without_second_spawn(self):
        historical = {
            "pid": 101,
            "profile_dir": "/private/m0",
            "profile_name": "local-agent-bridge-poc",
            "profile_path": "/private/m0/local-agent-bridge-poc.yaml",
            "profile_sha256": "a" * 64,
            "tunnel_id": "tunnel-existing",
        }
        production = {
            "pid": 303,
            "tunnel_id": "tunnel-existing",
            "process_identity": self._identity(303, ["/opt/tunnel-client", "run"]),
        }
        restarted = {
            **historical,
            "pid": 404,
            "process_identity": self._identity(404, [
                "/opt/tunnel-client", "run", "--profile-dir", "/private/m0",
                "--profile", "local-agent-bridge-poc",
            ]),
        }
        with patch.object(
            pc, "_process_identity_matches", side_effect=lambda identity: identity.get("pid") == 404
        ), patch.object(
            pc, "_discover_tunnel_processes", return_value=[restarted]
        ), patch.object(pc, "_validate_receipt_tunnel_profile"), patch.object(
            pc, "_require_exact_tunnel_id_owners"
        ), patch.object(pc, "_restart_verified_historical_tunnel") as forbidden_spawn, patch.object(
            pc, "_stop_verified_tunnel"
        ) as forbidden_stop:
            observed = pc._resume_rollback_tunnel_switch(
                historical, production, bridge_socket=Path("/tmp/production.sock")
            )
        self.assertEqual(observed, restarted)
        forbidden_spawn.assert_not_called()
        forbidden_stop.assert_not_called()

    def test_runtime_status_and_start_fail_closed_without_immutable_bundle(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest,
                runtime_root=root / "runtime",
                bridge_socket=root / "socket" / "bridge.sock",
            )
            status = pc.production_status(manifest, layout)
            self.assertFalse(status["release_installed"])
            self.assertFalse(status["local_runtime_ready"])
            self.assertEqual(status["release_revision"], REVISION)
            self.assertEqual(status["endpoint_generation"], 7)
            self.assertEqual(status["workspace"], str(pc.canonical_workspace()))
            self.assertEqual(status["tunnel_profile_name"], pc.TUNNEL_PROFILE_NAME)
            with self.assertRaisesRegex(RuntimeError, "manifest|release|artifact"):
                pc.start_local_runtime(manifest, layout)
            self.assertFalse(layout["bridge_socket"].exists())

    def test_runtime_status_rejects_host_started_with_nonrelease_node(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory(dir="/tmp") as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest,
                runtime_root=root / "runtime",
                bridge_socket=root / "bridge.sock",
            )
            release_node = layout["node_binary"]
            release_node.parent.mkdir(parents=True, exist_ok=True)
            release_node.write_text("release-node", encoding="utf-8")
            release_node.chmod(0o700)
            source_node = root / "homebrew-node"
            installed = json.loads(json.dumps(manifest))
            installed["runtime_dependencies"]["node"] = {
                "path": str(release_node),
                "sha256": "a" * 64,
            }
            state = {
                "release_revision": manifest["release_revision"],
                "endpoint_generation": manifest["endpoint_generation"],
                "workspace": manifest["workspace"],
                "tunnel_profile_name": pc.TUNNEL_PROFILE_NAME,
            }
            for name, pid, argv in (
                ("catdesk_supervisor", 801, ["/private/catdesk"]),
                ("host", 802, [str(source_node), str(layout["host_mcp_adapter"])]),
                ("runtime_hook", 803, ["/private/python", str(layout["runtime_hook_entrypoint"])]),
                ("bridge", 804, [str(layout["bridge_binary"])]),
            ):
                state[f"{name}_pid"] = pid
                state[f"{name}_process_identity"] = self._identity(pid, argv)
            pc._write_json_private(layout["runtime_state"], state)
            with patch.object(
                pc, "validate_installed_release", return_value=installed
            ), patch.object(
                pc, "_validate_runtime_host_verifier_registration"
            ), patch.object(
                pc, "_process_identity_matches", return_value=True
            ), patch.object(
                pc, "_tcp_ready", return_value=True
            ), patch.object(
                pc, "_runtime_hook_socket_is_live", return_value=True
            ), patch.object(
                pc, "smoke_tools", return_value=sorted(pc.REQUIRED_TOOLS)
            ), patch.object(
                pc,
                "_bridge_capability_lease_status",
                return_value={
                    "ready": True,
                    "renewal_enabled": True,
                    "remaining_ms": 1000,
                    "expires_at_ms": 2000,
                },
            ), patch.object(
                pc, "production_tunnel_status", return_value={"tunnel_ready": True}
            ):
                status = pc.production_status(manifest, layout)
            self.assertFalse(status["host_ready"])
            self.assertFalse(status["local_runtime_ready"])

    def test_start_local_post_status_failure_cleans_new_processes_and_preserves_verified_tunnel(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory(dir="/tmp") as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest, runtime_root=root / "runtime", bridge_socket=root / "bridge.sock"
            )
            layout["runtime_state"].parent.mkdir(parents=True)
            prior = {
                "release_revision": "b" * 40,
                "endpoint_generation": 7,
                "workspace": manifest["workspace"],
                "tunnel_profile_name": pc.TUNNEL_PROFILE_NAME,
                "tunnel_pid": 717,
                "tunnel_id": "tunnel-production",
                "tunnel_profile_sha256": "a" * 64,
                "tunnel_started_at": 1000.0,
                "tunnel_process_identity": self._identity(717, ["/opt/tunnel-client", "run"]),
            }
            layout["runtime_state"].write_text(json.dumps(prior), encoding="utf-8")
            layout["runtime_state"].chmod(0o600)
            layout["bridge_binary"].parent.mkdir(parents=True)
            layout["bridge_binary"].write_text("bridge", encoding="utf-8")
            layout["bridge_binary"].chmod(0o700)
            layout["process_runtime_binary"].write_text("process-runtime", encoding="utf-8")
            layout["process_runtime_binary"].chmod(0o700)
            for key, value in (
                ("release_catdesk_config", "mcpSlug = 'private'\n"),
                ("release_catdesk_backend_key", "k" * 64),
            ):
                layout[key].parent.mkdir(parents=True, exist_ok=True)
                layout[key].write_text(value, encoding="utf-8")
                layout[key].chmod(0o600)
            node = root / "node"
            node.write_text("node", encoding="utf-8")
            node.chmod(0o700)
            release_node = layout["node_binary"]
            release_node.parent.mkdir(parents=True, exist_ok=True)
            release_node.write_text("release-node", encoding="utf-8")
            release_node.chmod(0o700)
            kernel_python = root / "kernel-python"
            installed_release = json.loads(json.dumps(manifest))
            installed_release["runtime_dependencies"]["node"] = {
                "path": str(release_node),
                "sha256": pc._sha256_file(release_node),
            }
            processes = [
                MagicMock(pid=801),
                MagicMock(pid=802),
                MagicMock(pid=803),
                MagicMock(pid=804),
                MagicMock(pid=805),
            ]
            identities = [
                self._identity(process.pid, [f"/private/process-{process.pid}"])
                for process in processes
            ]
            bridge_listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

            def bridge_became_ready(_process, observed_layout):
                observed_layout["bridge_socket"].parent.mkdir(parents=True, exist_ok=True)
                bridge_listener.bind(str(observed_layout["bridge_socket"]))
                observed_layout["bridge_socket"].chmod(0o600)
                observed_layout["catdesk_child_pid"].parent.mkdir(parents=True, exist_ok=True)
                observed_layout["catdesk_child_pid"].write_text("904", encoding="utf-8")
                observed_layout["catdesk_child_pid"].chmod(0o600)
                return sorted(pc.REQUIRED_TOOLS)

            try:
                with patch.object(
                    pc, "validate_installed_release", return_value=installed_release
                ), patch.object(
                    pc,
                    "production_status",
                    side_effect=[
                        {"local_runtime_ready": False, "tunnel_ready": True},
                        {"local_runtime_ready": False, "tunnel_ready": True},
                    ],
                ), patch.object(pc, "_preserved_activation_state", return_value={}), patch.object(
                    pc, "_stop_prior_local_runtime_for_release_upgrade"
                ) as stop_prior, patch.object(
                    pc, "_tcp_ready", return_value=False
                ), patch.object(pc, "_catdesk_slug", return_value="private"), patch.object(
                    pc,
                    "production_component_env",
                    return_value={
                        "host": {},
                        "bridge": {
                            "LAB_PROCESS_BACKEND_URL": "http://127.0.0.1:33442/mcp",
                            "LAB_PROCESS_BACKEND_KEY_FILE": str(
                                layout["process_runtime_backend_key"]
                            ),
                        },
                        "process_runtime": {
                            "PROCESS_RUNTIME_BIND": "127.0.0.1:33442",
                        },
                    },
                ), patch.object(
                    pc, "_kernel_python_executable", return_value=kernel_python
                ), patch.object(
                    pc, "_spawn_logged", side_effect=processes
                ) as spawn, patch.object(
                    pc, "_capture_process_identity", side_effect=identities
                ) as capture, patch.object(pc, "_wait_for_tcp"), patch.object(
                    pc, "_wait_for_runtime_hook", return_value={
                        "path": str(layout["runtime_hook_socket"]),
                        "uid": os.geteuid(), "mode": 0o600, "device": 1, "inode": 2,
                    }
                ), patch.object(
                    pc, "_wait_for_bridge", side_effect=bridge_became_ready
                ), patch.object(
                    pc,
                    "_validate_runtime_dependencies",
                    side_effect=lambda observed: {
                        "node": {
                            "path": str(release_node if observed is installed_release else node)
                        },
                        "python": {"path": str(kernel_python)},
                    },
                ), patch.object(pc, "_stop_verified_process_identity") as stop:
                    with self.assertRaisesRegex(RuntimeError, "post-start status"):
                        pc.start_local_runtime(manifest, layout)
            finally:
                bridge_listener.close()
            self.assertEqual(stop.call_count, 5)
            stop_prior.assert_called_once_with(prior, layout)
            self.assertEqual(spawn.call_args_list[0].args[0][0], str(kernel_python))
            self.assertEqual(spawn.call_args_list[1].args[0][0], str(release_node))
            self.assertEqual(
                spawn.call_args_list[1].args[0][1],
                str(layout["host_mcp_adapter"].resolve()),
            )
            self.assertEqual(
                spawn.call_args_list[3].args[0][0],
                str(layout["process_runtime_binary"].resolve()),
            )
            self.assertEqual(
                spawn.call_args_list[4].args[0][0],
                str(layout["bridge_binary"].resolve()),
            )
            self.assertEqual(
                spawn.call_args_list[4].kwargs["env"]["LAB_PROCESS_BACKEND_URL"],
                "http://127.0.0.1:33442/mcp",
            )
            self.assertEqual(
                spawn.call_args_list[4].kwargs["env"]["LAB_PROCESS_BACKEND_KEY_FILE"],
                str(layout["process_runtime_backend_key"]),
            )
            self.assertEqual(
                capture.call_args_list[0].kwargs["expected_executable"], kernel_python
            )
            self.assertEqual(
                capture.call_args_list[0].kwargs["expected_argv"][0], str(kernel_python)
            )
            for call in spawn.call_args_list:
                self.assertEqual(call.kwargs["env"]["PYTHONDONTWRITEBYTECODE"], "1")
            state = json.loads(layout["runtime_state"].read_text(encoding="utf-8"))
            self.assertEqual(state["tunnel_pid"], 717)
            self.assertEqual(state["tunnel_process_identity"], prior["tunnel_process_identity"])
            self.assertNotIn("bridge_pid", state)
            self.assertFalse(layout["bridge_socket"].exists())
            self.assertFalse(layout["catdesk_child_pid"].exists())
            self.assertEqual(
                layout["catdesk_config"].read_text(encoding="utf-8"),
                "mcpSlug = 'private'\n",
            )
            self.assertEqual(
                layout["catdesk_backend_key"].read_text(encoding="utf-8"), "k" * 64
            )
            host_state_dir = layout["host_verifier_state"].parent
            self.assertTrue(host_state_dir.is_dir())
            self.assertEqual(stat.S_IMODE(host_state_dir.stat().st_mode), 0o700)

    def test_start_local_preserves_armed_rollback_pin_when_tunnel_health_is_unready(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest, runtime_root=root / "runtime", bridge_socket=root / "bridge.sock"
            )
            identity = self._identity(717, [
                "/opt/tunnel-client", "run", "--profile-dir", str(layout["tunnel_dir"]),
                "--profile", pc.TUNNEL_PROFILE_NAME,
            ])
            production = {
                "pid": 717,
                "command": pc.shlex.join(identity["argv"]),
                "profile_dir": str(layout["tunnel_dir"]),
                "profile_name": pc.TUNNEL_PROFILE_NAME,
                "profile_path": str(layout["tunnel_profile"]),
                "profile_sha256": "a" * 64,
                "tunnel_id": "tunnel-production",
                "process_identity": identity,
                "expected_socket": str(layout["bridge_socket"]),
            }
            receipt = {
                "schema_version": 1,
                "status": "armed",
                "connector_id": pc.CONNECTOR_ID,
                "contract_version": pc.CONTRACT_VERSION,
                "release_revision": REVISION,
                "endpoint_generation": 7,
                "tunnel_id": "tunnel-production",
                "production": production,
            }
            pc._write_json_private(layout["rollback_receipt"], receipt)
            prior = {
                "release_revision": REVISION,
                "endpoint_generation": 7,
                "workspace": manifest["workspace"],
                "tunnel_profile_name": pc.TUNNEL_PROFILE_NAME,
                "tunnel_pid": 717,
                "tunnel_id": "tunnel-production",
                "tunnel_profile_sha256": "a" * 64,
                "tunnel_started_at": 1000.0,
                "tunnel_process_identity": identity,
                "activation_rollback_receipt": str(layout["rollback_receipt"].resolve()),
                "activation_rollback_receipt_sha256": pc._sha256_file(layout["rollback_receipt"]),
            }
            pc._write_json_private(layout["runtime_state"], prior)
            with patch.object(pc, "_validate_tunnel_profile", return_value={
                "profile_sha256": "a" * 64,
                "tunnel_id": "tunnel-production",
            }), patch.object(pc, "_process_identity_matches", return_value=True), patch.object(
                pc, "_require_exact_tunnel_id_owners"
            ):
                preserved = pc._preserved_activation_state(manifest, layout, prior)
            self.assertEqual(preserved["tunnel_pid"], 717)
            self.assertEqual(
                preserved["activation_rollback_receipt_sha256"],
                prior["activation_rollback_receipt_sha256"],
            )

            layout["rollback_receipt"].write_text("{}\n", encoding="utf-8")
            layout["rollback_receipt"].chmod(0o600)
            with patch.object(pc, "validate_installed_release"), patch.object(
                pc, "production_status", return_value={"local_runtime_ready": False, "tunnel_ready": False}
            ), patch.object(pc, "_stop_verified_local_runtime") as forbidden_stop:
                with self.assertRaisesRegex(RuntimeError, "rollback receipt|pin|hash"):
                    pc.start_local_runtime(manifest, layout)
            forbidden_stop.assert_not_called()

    def test_release_upgrade_requires_a_completed_verified_rollback_before_discarding_old_tunnel_state(self):
        manifest = self._manifest()
        upgraded_manifest = {**manifest, "release_revision": "b" * 40, "endpoint_generation": 8}
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                upgraded_manifest, runtime_root=root / "runtime", bridge_socket=root / "bridge.sock"
            )
            old_identity = self._identity(717, ["/opt/tunnel-client", "run"])
            historical_identity = self._identity(404, ["/opt/tunnel-client", "run", "--profile", "m0"])
            historical_state_path = root / "historical-runtime.json"
            historical_state = {
                "tunnel_pid": 404,
                "tunnel_id": "tunnel-production",
                "tunnel_profile_sha256": "a" * 64,
                "tunnel_process_identity": historical_identity,
            }
            pc._write_json_private(historical_state_path, historical_state)
            receipt = {
                "schema_version": 1,
                "status": "armed",
                "connector_id": pc.CONNECTOR_ID,
                "contract_version": pc.CONTRACT_VERSION,
                "release_revision": manifest["release_revision"],
                "endpoint_generation": manifest["endpoint_generation"],
                "tunnel_id": "tunnel-production",
                "production": {
                    "pid": 717,
                    "tunnel_id": "tunnel-production",
                    "process_identity": old_identity,
                },
                "historical_runtime_state": str(historical_state_path),
                "historical_restarted": {
                    "pid": 404,
                    "tunnel_id": "tunnel-production",
                    "profile_sha256": "a" * 64,
                    "process_identity": historical_identity,
                },
            }
            pc._write_json_private(layout["rollback_receipt"], receipt)
            prior = {
                "release_revision": manifest["release_revision"],
                "endpoint_generation": manifest["endpoint_generation"],
                "activation_rollback_receipt": str(layout["rollback_receipt"].resolve()),
                "activation_rollback_receipt_sha256": pc._sha256_file(layout["rollback_receipt"]),
            }
            pc._write_json_private(layout["runtime_state"], prior)
            with self.assertRaisesRegex(RuntimeError, "completed rollback"):
                pc._preserved_activation_state(upgraded_manifest, layout, prior)

            receipt.update({"status": "rolled_back", "phase": "completed", "rolled_back_at": 1234.0})
            pc._write_json_private(layout["rollback_receipt"], receipt)
            prior.update(
                {
                    "activation_rollback_receipt_sha256": pc._sha256_file(layout["rollback_receipt"]),
                    "rollback_completed_at": 1234.0,
                    "rollback_historical_tunnel_pid": 404,
                    "rollback_historical_tunnel_id": "tunnel-production",
                    "rollback_historical_tunnel_process_identity": historical_identity,
                }
            )
            pc._write_json_private(layout["runtime_state"], prior)
            with patch.object(pc, "_process_identity_matches", side_effect=lambda value: value == historical_identity), patch.object(
                pc, "_validate_receipt_tunnel_profile"
            ) as profile, patch.object(pc, "_require_exact_tunnel_id_owners") as owners:
                self.assertEqual(pc._preserved_activation_state(upgraded_manifest, layout, prior), {})
            profile.assert_called_once_with(
                receipt["historical_restarted"],
                expected_socket=pc.HISTORICAL_M0_BRIDGE_SOCKET,
                label="completed rollback historical tunnel",
            )
            owners.assert_called_once_with(
                "tunnel-production", {404}, expected_socket=pc.HISTORICAL_M0_BRIDGE_SOCKET
            )

            malformed = dict(receipt)
            malformed["production"] = {
                "tunnel_id": "tunnel-production", "process_identity": {"pid": 717}
            }
            pc._write_json_private(layout["rollback_receipt"], malformed)
            prior["activation_rollback_receipt_sha256"] = pc._sha256_file(layout["rollback_receipt"])
            pc._write_json_private(layout["runtime_state"], prior)
            with patch.object(pc, "_process_identity_matches", return_value=True), patch.object(
                pc, "_validate_receipt_tunnel_profile"
            ), patch.object(pc, "_require_exact_tunnel_id_owners") as forbidden_owners:
                with self.assertRaisesRegex(RuntimeError, "process identity"):
                    pc._preserved_activation_state(upgraded_manifest, layout, prior)
            forbidden_owners.assert_not_called()

    def test_completed_rollback_historical_profile_tampering_is_rejected_before_upgrade_start(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            profile_dir = root / "historical"
            profile_name = "m0"
            profile_path = profile_dir / f"{profile_name}.yaml"
            profile = {
                "control_plane": {"tunnel_id": "tunnel-existing", "base_url": "https://api.openai.com"},
                "mcp": {"server_urls": [{"unix_socket": str(pc.HISTORICAL_M0_BRIDGE_SOCKET)}]},
            }
            pc._write_json_private(profile_path, profile)
            identity = self._identity(
                404,
                ["/opt/tunnel-client", "run", "--profile-dir", str(profile_dir), "--profile", profile_name],
            )
            item = {
                "pid": 404,
                "command": pc.shlex.join(identity["argv"]),
                "profile_dir": str(profile_dir),
                "profile_name": profile_name,
                "profile_path": str(profile_path),
                "profile_sha256": pc._sha256_file(profile_path),
                "tunnel_id": "tunnel-existing",
                "process_identity": identity,
                "expected_socket": str(pc.HISTORICAL_M0_BRIDGE_SOCKET),
            }
            self.assertEqual(
                pc._validate_receipt_tunnel_profile(
                    item,
                    expected_socket=pc.HISTORICAL_M0_BRIDGE_SOCKET,
                    label="completed rollback historical tunnel",
                )["tunnel_id"],
                "tunnel-existing",
            )
            for key, value in (
                ("profile_sha256", "0" * 64),
                ("profile_path", str(root / "other.yaml")),
                ("profile_name", "other"),
            ):
                with self.subTest(key=key):
                    tampered = {**item, key: value}
                    with self.assertRaisesRegex(RuntimeError, "profile|changed|incomplete"):
                        pc._validate_receipt_tunnel_profile(
                            tampered,
                            expected_socket=pc.HISTORICAL_M0_BRIDGE_SOCKET,
                            label="completed rollback historical tunnel",
                        )

    def test_release_upgrade_retry_accepts_already_stopped_old_component_pids(self):
        state = {
            "bridge_pid": 801,
            "bridge_process_identity": self._identity(801, ["/private/bridge"]),
            "host_pid": 802,
            "host_process_identity": self._identity(802, ["/private/host"]),
            "catdesk_supervisor_pid": 803,
            "catdesk_supervisor_process_identity": self._identity(803, ["/private/catdesk"]),
            "sentinel": "preserve-prior-state",
        }
        layout = {"bridge_socket": Path("/tmp/nonexistent-bridge.sock")}
        with patch.object(pc, "_process_identity_matches", return_value=False), patch.object(
            pc, "_process_is_running", return_value=False
        ), patch.object(pc, "_stop_verified_local_runtime") as stop:
            pc._stop_prior_local_runtime_for_release_upgrade(state, layout)
        retried_state = stop.call_args.args[0]
        self.assertEqual(retried_state["sentinel"], "preserve-prior-state")
        for key in (
            "bridge_pid", "bridge_process_identity", "host_pid", "host_process_identity",
            "catdesk_supervisor_pid", "catdesk_supervisor_process_identity",
        ):
            self.assertNotIn(key, retried_state)

    def test_cross_release_active_tunnel_without_receipt_refuses_before_stopping(self):
        manifest = self._manifest()
        upgraded = {**manifest, "release_revision": "b" * 40, "endpoint_generation": 8}
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                upgraded, runtime_root=root / "runtime", bridge_socket=root / "bridge.sock"
            )
            prior = {
                "release_revision": manifest["release_revision"],
                "endpoint_generation": manifest["endpoint_generation"],
                "tunnel_pid": 717,
                "tunnel_id": "tunnel-production",
                "tunnel_process_identity": self._identity(717, ["/opt/tunnel-client", "run"]),
            }
            pc._write_json_private(layout["runtime_state"], prior)
            with patch.object(pc, "validate_installed_release"), patch.object(
                pc, "production_status", return_value={"local_runtime_ready": False, "tunnel_ready": False}
            ), patch.object(pc, "_stop_prior_local_runtime_for_release_upgrade") as forbidden_stop:
                with self.assertRaisesRegex(RuntimeError, "active tunnel.*rollback receipt"):
                    pc.start_local_runtime(upgraded, layout)
            forbidden_stop.assert_not_called()

    def test_cross_release_pure_local_prior_without_receipt_is_upgradeable(self):
        manifest = self._manifest()
        upgraded = {**manifest, "release_revision": "b" * 40, "endpoint_generation": 8}
        prior = {
            "release_revision": manifest["release_revision"],
            "endpoint_generation": manifest["endpoint_generation"],
            "bridge_pid": 801,
            "host_pid": 802,
            "catdesk_supervisor_pid": 803,
        }
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                upgraded, runtime_root=root / "runtime", bridge_socket=root / "bridge.sock"
            )
            self.assertEqual(pc._preserved_activation_state(upgraded, layout, prior), {})

    def test_private_json_write_is_atomic_across_write_flush_fsync_replace_and_dir_fsync_failures(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            target = root / "runtime.json"
            old_value = {"phase": "old", "sentinel": "complete"}
            new_value = {"phase": "new", "sentinel": "complete"}
            pc._write_json_private(target, old_value)
            old_bytes = target.read_bytes()
            real_fdopen = os.fdopen
            real_fsync = os.fsync

            class FailingFile:
                def __init__(self, handle, stage):
                    self.handle = handle
                    self.stage = stage

                def write(self, value):
                    if self.stage == "write":
                        self.handle.write(value[: max(1, len(value) // 3)])
                        raise OSError("injected write failure")
                    return self.handle.write(value)

                def flush(self):
                    if self.stage == "flush":
                        raise OSError("injected flush failure")
                    return self.handle.flush()

                def fileno(self):
                    return self.handle.fileno()

                def close(self):
                    return self.handle.close()

            for stage in ("write", "flush"):
                with self.subTest(stage=stage):
                    def failing_fdopen(fd, *args, _stage=stage, **kwargs):
                        return FailingFile(real_fdopen(fd, *args, **kwargs), _stage)

                    with patch.object(pc.os, "fdopen", side_effect=failing_fdopen):
                        with self.assertRaisesRegex(OSError, stage):
                            pc._write_json_private(target, new_value)
                    self.assertEqual(target.read_bytes(), old_bytes)
                    self.assertEqual(json.loads(target.read_text(encoding="utf-8")), old_value)
                    self.assertEqual(list(root.glob(".runtime.json.*.tmp")), [])

            def fail_file_fsync(fd):
                if stat.S_ISREG(os.fstat(fd).st_mode):
                    raise OSError("injected file fsync failure")
                return real_fsync(fd)

            with patch.object(pc.os, "fsync", side_effect=fail_file_fsync):
                with self.assertRaisesRegex(OSError, "file fsync"):
                    pc._write_json_private(target, new_value)
            self.assertEqual(target.read_bytes(), old_bytes)
            self.assertEqual(list(root.glob(".runtime.json.*.tmp")), [])

            with patch.object(pc.os, "replace", side_effect=OSError("injected replace failure")):
                with self.assertRaisesRegex(OSError, "replace"):
                    pc._write_json_private(target, new_value)
            self.assertEqual(target.read_bytes(), old_bytes)
            self.assertEqual(list(root.glob(".runtime.json.*.tmp")), [])

            def fail_directory_fsync(fd):
                if stat.S_ISDIR(os.fstat(fd).st_mode):
                    raise OSError("injected directory fsync failure")
                return real_fsync(fd)

            with patch.object(pc.os, "fsync", side_effect=fail_directory_fsync):
                with self.assertRaisesRegex(RuntimeError, "persistence state is unknown"):
                    pc._write_json_private(target, new_value)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), new_value)
            self.assertEqual(list(root.glob(".runtime.json.*.tmp")), [])

            reentry_value = {"phase": "reentered", "sentinel": "complete"}
            pc._write_json_private(target, reentry_value)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), reentry_value)

    def test_build_gate_checks_full_porcelain_and_git_archive_excludes_untracked_inputs(self):
        clean = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with patch.object(pc.subprocess, "run", return_value=clean) as run:
            pc._require_clean_build_repo(Path("/private/candidate"))
        self.assertIn("--porcelain=v1", run.call_args.args[0])
        self.assertIn("--untracked-files=all", run.call_args.args[0])

        unknown = subprocess.CompletedProcess([], 0, stdout="?? experiments/web-host-attestation/injected.mjs\n", stderr="")
        with patch.object(pc.subprocess, "run", return_value=unknown):
            with self.assertRaisesRegex(RuntimeError, "untracked|dirty"):
                pc._require_clean_build_repo(Path("/private/candidate"))

        allowed = subprocess.CompletedProcess(
            [],
            0,
            stdout=(
                "?? scripts/__pycache__/production_connector.cpython-311.pyc\n"
                "?? tests/__pycache__/test_production_connector.cpython-311.pyc\n"
                "?? experiments/web-host-attestation/chrome-host/host-mcp-adapter.mjs.orig\n"
                "?? experiments/web-host-attestation/chrome-host/host-mcp-adapter.mjs.rej\n"
            ),
            stderr="",
        )
        with patch.object(pc.subprocess, "run", return_value=allowed):
            pc._require_clean_build_repo(Path("/private/candidate"))

        tracked = subprocess.CompletedProcess([], 0, stdout=" M crates/local-tool-bridge/src/main.rs\n", stderr="")
        with patch.object(pc.subprocess, "run", return_value=tracked):
            with self.assertRaisesRegex(RuntimeError, "tracked|dirty"):
                pc._require_clean_build_repo(Path("/private/candidate"))

        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            repo = root / "repo"
            snapshot = root / "snapshot"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            (repo / "tracked.txt").write_text("tracked", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
            commit_env = {
                **os.environ,
                "GIT_AUTHOR_NAME": "test",
                "GIT_AUTHOR_EMAIL": "test@example.invalid",
                "GIT_COMMITTER_NAME": "test",
                "GIT_COMMITTER_EMAIL": "test@example.invalid",
            }
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fixture"], check=True, env=commit_env)
            revision = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            (repo / "untracked.txt").write_text("must not enter release", encoding="utf-8")
            pc._export_git_snapshot(repo, revision, snapshot)
            self.assertEqual((snapshot / "tracked.txt").read_text(encoding="utf-8"), "tracked")
            self.assertFalse((snapshot / "untracked.txt").exists())

    def test_tunnel_client_discovery_refuses_ambiguity_and_accepts_only_explicit_file(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            first = root / "v1" / "tunnel-client"
            second = root / "v2" / "tunnel-client"
            for path in (first, second):
                path.parent.mkdir(parents=True)
                path.write_text("binary", encoding="utf-8")
                path.chmod(0o700)
            with patch.object(pc.glob, "glob", return_value=[str(first), str(second)]), patch.dict(
                pc.os.environ, {}, clear=True
            ):
                with self.assertRaisesRegex(RuntimeError, "multiple|explicit|ambiguous"):
                    pc._find_tunnel_client()
            self.assertEqual(pc._find_tunnel_client(explicit=first), first.resolve())

    def test_process_identity_record_is_complete_and_pid_reuse_fails_closed(self):
        identity = {
            "pid": 17,
            "uid": os.geteuid(),
            "lstart": "Thu Sep 11 12:00:00 2026",
            "start_timestamp": 1789099200.0,
            "pgid": 17,
            "exe_realpath": "/opt/tunnel-client",
            "exe_inode": 123,
            "exe_sha256": "a" * 64,
            "argv": ["/opt/tunnel-client", "run"],
        }
        self.assertEqual(pc._validate_process_identity_record(identity), identity)
        for key in tuple(identity):
            with self.subTest(missing=key), self.assertRaisesRegex(RuntimeError, "identity|missing"):
                pc._validate_process_identity_record({name: value for name, value in identity.items() if name != key})
        with patch.object(pc, "_capture_process_identity", return_value=dict(identity)):
            self.assertTrue(pc._process_identity_matches(identity))
        with patch.object(pc, "_capture_process_identity", return_value={**identity, "start_timestamp": 1789099201.0}):
            self.assertFalse(pc._process_identity_matches(identity))

    def test_feishu_release_cli_flags_forward_to_manifest_contract(self):
        manifest = self._manifest()
        setup_argv = [
            "manifest",
            "--release-revision",
            REVISION,
            "--endpoint-generation",
            "7",
            "--feishu-admin",
        ]
        with patch.object(pc, "release_manifest", return_value=manifest) as release, patch(
            "builtins.print"
        ):
            self.assertEqual(pc.main(setup_argv), 0)
        release.assert_called_once_with(
            release_revision=REVISION,
            endpoint_generation=7,
            feishu_admin_enabled=True,
            feishu_cli_executable=None,
            feishu_cli_version=None,
        )

        pinned_argv = [
            "manifest",
            "--release-revision",
            REVISION,
            "--endpoint-generation",
            "8",
            "--feishu-cli",
            "/private/provider/lark-cli",
            "--feishu-cli-version",
            "1.0.96",
        ]
        with patch.object(pc, "release_manifest", return_value=manifest) as release, patch(
            "builtins.print"
        ):
            self.assertEqual(pc.main(pinned_argv), 0)
        release.assert_called_once_with(
            release_revision=REVISION,
            endpoint_generation=8,
            feishu_admin_enabled=False,
            feishu_cli_executable=Path("/private/provider/lark-cli"),
            feishu_cli_version="1.0.96",
        )

    def test_production_process_backend_env_is_paired_and_fail_closed(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest, runtime_root=root / "runtime", bridge_socket=root / "bridge.sock"
            )
            env = pc.production_component_env(
                manifest, layout, catdesk_slug="private-slug"
            )
            self.assertEqual(
                env["bridge"]["LAB_PROCESS_BACKEND_URL"],
                f"http://127.0.0.1:{pc.PRODUCTION_PROCESS_RUNTIME_PORT}/mcp",
            )
            self.assertEqual(
                env["bridge"]["LAB_PROCESS_BACKEND_KEY_FILE"],
                str(layout["process_runtime_backend_key"]),
            )
            self.assertEqual(
                env["process_runtime"]["PROCESS_RUNTIME_BIND"],
                f"127.0.0.1:{pc.PRODUCTION_PROCESS_RUNTIME_PORT}",
            )
            pc._require_paired_backend_env(
                env["bridge"],
                url_key="LAB_PROCESS_BACKEND_URL",
                key_file_key="LAB_PROCESS_BACKEND_KEY_FILE",
                label="process",
            )
            with self.assertRaisesRegex(RuntimeError, "together"):
                pc._require_paired_backend_env(
                    {"LAB_PROCESS_BACKEND_URL": "http://127.0.0.1:33442/mcp"},
                    url_key="LAB_PROCESS_BACKEND_URL",
                    key_file_key="LAB_PROCESS_BACKEND_KEY_FILE",
                    label="process",
                )
            with self.assertRaisesRegex(RuntimeError, "together"):
                pc._require_paired_backend_env(
                    {"LAB_PROCESS_BACKEND_KEY_FILE": str(layout["process_runtime_backend_key"])},
                    url_key="LAB_PROCESS_BACKEND_URL",
                    key_file_key="LAB_PROCESS_BACKEND_KEY_FILE",
                    label="process",
                )
            with self.assertRaisesRegex(RuntimeError, "must be configured"):
                pc._require_paired_backend_env(
                    {},
                    url_key="LAB_PROCESS_BACKEND_URL",
                    key_file_key="LAB_PROCESS_BACKEND_KEY_FILE",
                    label="process",
                )
            with self.assertRaisesRegex(RuntimeError, "numeric IP"):
                pc._numeric_loopback_bind("localhost:33442")
            with self.assertRaisesRegex(RuntimeError, "numeric loopback"):
                pc._numeric_loopback_bind("0.0.0.0:33442")

    def test_start_local_fail_closes_when_process_backend_env_is_unpaired(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory(dir="/tmp") as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest, runtime_root=root / "runtime", bridge_socket=root / "bridge.sock"
            )
            for key, value in (
                ("release_catdesk_config", "mcpSlug = 'private'\n"),
                ("release_catdesk_backend_key", "k" * 64),
            ):
                layout[key].parent.mkdir(parents=True, exist_ok=True)
                layout[key].write_text(value, encoding="utf-8")
                layout[key].chmod(0o600)
            installed = json.loads(json.dumps(manifest))
            installed["artifacts_sha256"] = {"bin/process-runtime": "a" * 64}
            with patch.object(
                pc, "validate_installed_release", return_value=installed
            ), patch.object(
                pc, "production_status", return_value={"local_runtime_ready": False, "tunnel_ready": False}
            ), patch.object(pc, "_tcp_ready", return_value=False), patch.object(
                pc, "_catdesk_slug", return_value="private"
            ), patch.object(
                pc,
                "production_component_env",
                return_value={
                    "host": {},
                    "bridge": {"LAB_PROCESS_BACKEND_URL": "http://127.0.0.1:33442/mcp"},
                    "process_runtime": {"PROCESS_RUNTIME_BIND": "127.0.0.1:33442"},
                },
            ), patch.object(pc, "_spawn_logged") as spawn:
                with self.assertRaisesRegex(RuntimeError, "together"):
                    pc.start_local_runtime(manifest, layout)
            spawn.assert_not_called()

    def _start_local_bridge_spawn_env(self, layout, manifest, installed, *, host_socket=None):
        layout["bridge_binary"].parent.mkdir(parents=True, exist_ok=True)
        layout["bridge_binary"].write_text("bridge", encoding="utf-8")
        layout["bridge_binary"].chmod(0o700)
        layout["process_runtime_binary"].parent.mkdir(parents=True, exist_ok=True)
        layout["process_runtime_binary"].write_text("process-runtime", encoding="utf-8")
        layout["process_runtime_binary"].chmod(0o700)
        for key, value in (
            ("release_catdesk_config", "mcpSlug = 'private'\n"),
            ("release_catdesk_backend_key", "k" * 64),
        ):
            layout[key].parent.mkdir(parents=True, exist_ok=True)
            layout[key].write_text(value, encoding="utf-8")
            layout[key].chmod(0o600)
        kernel_python = layout["runtime_root"] / "kernel-python"
        kernel_python.write_text("python", encoding="utf-8")
        kernel_python.chmod(0o700)
        release_node = layout["node_binary"]
        release_node.parent.mkdir(parents=True, exist_ok=True)
        release_node.write_text("release-node", encoding="utf-8")
        release_node.chmod(0o700)
        processes = [MagicMock(pid=901 + index) for index in range(5)]
        identities = [
            self._identity(process.pid, [f"/private/process-{process.pid}"])
            for process in processes
        ]
        receipt_env = {
            "LAB_HOST_ADAPTER_SOCKET": str(layout["host_socket"]),
            "LAB_RUNTIME_HOST_TOOL_HOOK_SOCKET": str(layout["runtime_hook_socket"]),
            "LAB_PROCESS_BACKEND_URL": "http://127.0.0.1:33442/mcp",
            "LAB_PROCESS_BACKEND_KEY_FILE": str(layout["process_runtime_backend_key"]),
        }
        listener = None
        if host_socket == "listening":
            layout["host_socket"].parent.mkdir(parents=True, exist_ok=True)
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(layout["host_socket"]))
            listener.listen(1)
            os.chmod(layout["host_socket"], 0o600)
        elif host_socket == "stale":
            layout["host_socket"].parent.mkdir(parents=True, exist_ok=True)
            stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            stale.bind(str(layout["host_socket"]))
            os.chmod(layout["host_socket"], 0o600)
            stale.close()
        try:
            with patch.object(
                pc, "validate_installed_release", return_value=installed
            ), patch.object(
                pc,
                "production_status",
                side_effect=[
                    {"local_runtime_ready": False, "tunnel_ready": False},
                    {"local_runtime_ready": False, "tunnel_ready": False},
                ],
            ), patch.object(pc, "_tcp_ready", return_value=False), patch.object(
                pc, "_catdesk_slug", return_value="private"
            ), patch.object(
                pc,
                "production_component_env",
                return_value={
                    "host": {"LAB_HOST_ADAPTER_SOCKET": str(layout["host_socket"])},
                    "bridge": dict(receipt_env),
                    "process_runtime": {"PROCESS_RUNTIME_BIND": "127.0.0.1:33442"},
                },
            ), patch.object(
                pc, "_kernel_python_executable", return_value=kernel_python
            ), patch.object(pc, "_spawn_logged", side_effect=processes) as spawn, patch.object(
                pc, "_capture_process_identity", side_effect=identities
            ), patch.object(pc, "_wait_for_tcp"), patch.object(
                pc,
                "_wait_for_runtime_hook",
                return_value={
                    "path": str(layout["runtime_hook_socket"]),
                    "uid": os.geteuid(),
                    "mode": 0o600,
                    "device": 1,
                    "inode": 2,
                },
            ), patch.object(
                pc, "_wait_for_bridge", return_value=sorted(pc.REQUIRED_TOOLS)
            ), patch.object(
                pc,
                "_validate_runtime_dependencies",
                return_value={
                    "node": {"path": str(release_node)},
                    "python": {"path": str(kernel_python)},
                },
            ), patch.object(pc, "_stop_verified_process_identity"):
                with self.assertRaisesRegex(RuntimeError, "post-start status"):
                    pc.start_local_runtime(manifest, layout)
            self.assertEqual(spawn.call_count, 5)
            return spawn.call_args_list[4].kwargs["env"]
        finally:
            if listener is not None:
                listener.close()

    def test_host_adapter_socket_listening_requires_owned_live_unix_socket(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as raw_root:
            path = Path(raw_root) / "host.sock"
            self.assertFalse(pc._host_adapter_socket_is_listening(path))
            path.write_text("not-a-socket\n", encoding="utf-8")
            self.assertFalse(pc._host_adapter_socket_is_listening(path))
            path.unlink()
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                listener.bind(str(path))
                listener.listen(1)
                os.chmod(path, 0o600)
                self.assertTrue(pc._host_adapter_socket_is_listening(path))
                os.chmod(path, 0o666)
                self.assertFalse(pc._host_adapter_socket_is_listening(path))
                os.chmod(path, 0o600)
            finally:
                listener.close()
            self.assertFalse(pc._host_adapter_socket_is_listening(path))

    def test_start_local_omits_host_receipt_env_when_host_socket_is_not_listening(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory(dir="/tmp") as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest, runtime_root=root / "runtime", bridge_socket=root / "bridge.sock"
            )
            installed = json.loads(json.dumps(manifest))
            installed["artifacts_sha256"] = {"bin/process-runtime": "a" * 64}
            missing = self._start_local_bridge_spawn_env(
                layout, manifest, installed, host_socket=None
            )
            self.assertNotIn("LAB_HOST_ADAPTER_SOCKET", missing)
            self.assertNotIn("LAB_RUNTIME_HOST_TOOL_HOOK_SOCKET", missing)
            self.assertEqual(
                missing["LAB_PROCESS_BACKEND_URL"], "http://127.0.0.1:33442/mcp"
            )
            self.assertEqual(
                missing["LAB_PROCESS_BACKEND_KEY_FILE"],
                str(layout["process_runtime_backend_key"]),
            )
            self.assertFalse(layout["host_socket"].exists())

            stale_root = root / "stale"
            stale_layout = pc.production_layout(
                manifest,
                runtime_root=stale_root / "runtime",
                bridge_socket=stale_root / "bridge.sock",
            )
            stale = self._start_local_bridge_spawn_env(
                stale_layout, manifest, installed, host_socket="stale"
            )
            self.assertNotIn("LAB_HOST_ADAPTER_SOCKET", stale)
            self.assertNotIn("LAB_RUNTIME_HOST_TOOL_HOOK_SOCKET", stale)
            self.assertEqual(
                stale["LAB_PROCESS_BACKEND_URL"], "http://127.0.0.1:33442/mcp"
            )

    def test_start_local_injects_host_receipt_env_only_when_host_socket_is_listening(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory(dir="/tmp") as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest, runtime_root=root / "runtime", bridge_socket=root / "bridge.sock"
            )
            installed = json.loads(json.dumps(manifest))
            installed["artifacts_sha256"] = {"bin/process-runtime": "a" * 64}
            env = self._start_local_bridge_spawn_env(
                layout, manifest, installed, host_socket="listening"
            )
            self.assertEqual(env["LAB_HOST_ADAPTER_SOCKET"], str(layout["host_socket"]))
            self.assertEqual(
                env["LAB_RUNTIME_HOST_TOOL_HOOK_SOCKET"],
                str(layout["runtime_hook_socket"]),
            )
            self.assertEqual(
                env["LAB_PROCESS_BACKEND_URL"], "http://127.0.0.1:33442/mcp"
            )

    def test_status_exit_is_nonzero_until_tunnel_is_ready(self):
        manifest = self._manifest()
        argv = ["status", "--release-revision", REVISION, "--endpoint-generation", "7"]
        with patch.object(pc, "release_manifest", return_value=manifest), patch.object(
            pc, "production_layout", return_value={}
        ), patch.object(
            pc,
            "production_status",
            return_value={"local_runtime_ready": True, "tunnel_ready": False},
        ), patch("builtins.print"):
            self.assertEqual(pc.main(argv), 2)
        with patch.object(pc, "release_manifest", return_value=manifest), patch.object(
            pc, "production_layout", return_value={}
        ), patch.object(
            pc,
            "production_status",
            return_value={"local_runtime_ready": True, "tunnel_ready": True},
        ), patch("builtins.print"):
            self.assertEqual(pc.main(argv), 0)

    def test_rollback_tunnel_cli_routes_only_to_canonical_receipt_function(self):
        manifest = self._manifest()
        layout = {"rollback_receipt": Path("/private/runtime/activation-rollback.json")}
        argv = [
            "rollback-tunnel",
            "--release-revision",
            REVISION,
            "--endpoint-generation",
            "7",
        ]
        with patch.object(
            pc, "_node_runtime_dependency", side_effect=AssertionError("rollback must not resolve Node")
        ) as forbidden_node, patch.object(
            pc, "production_layout", return_value=layout
        ), patch.object(
            pc,
            "rollback_production_activation",
            return_value={"rolled_back": True, "already_rolled_back": False},
        ) as rollback, patch("builtins.print"):
            self.assertEqual(pc.main(argv), 0)
        forbidden_node.assert_not_called()
        requested = rollback.call_args.args[0]
        self.assertEqual(requested["release_revision"], manifest["release_revision"])
        self.assertEqual(requested["endpoint_generation"], manifest["endpoint_generation"])
        rollback.assert_called_once_with(requested, layout, receipt_path=None)

    def test_adopt_current_rollback_cli_routes_to_read_only_observation_api(self):
        manifest = self._manifest()
        layout = {"runtime_root": Path("/private/runtime")}
        fallback = Path("/private/fallback-health-url")
        argv = [
            "adopt-current-rollback",
            "--release-revision",
            REVISION,
            "--endpoint-generation",
            "7",
            "--fallback-health-url-file",
            str(fallback),
        ]
        with patch.object(pc, "release_manifest", return_value=manifest), patch.object(
            pc, "production_layout", return_value=layout
        ), patch.object(
            pc,
            "adopt_current_production_rollback",
            return_value={"adopted": True},
        ) as adopt, patch("builtins.print"):
            self.assertEqual(pc.main(argv), 0)
        adopt.assert_called_once_with(
            manifest,
            layout,
            native_user_data_dirs=[],
            fallback_health_url_file=fallback,
        )

    def test_process_command_match_requires_exact_executable_and_required_arguments(self):
        command = "/private/release/bin/local-tool-bridge --serve production"
        self.assertTrue(
            pc.process_command_matches(
                command,
                Path("/private/release/bin/local-tool-bridge"),
                ["--serve", "production"],
            )
        )
        self.assertFalse(
            pc.process_command_matches(
                command,
                Path("/other/release/bin/local-tool-bridge"),
                ["--serve", "production"],
            )
        )
        self.assertFalse(
            pc.process_command_matches(
                command,
                Path("/private/release/bin/local-tool-bridge"),
                ["--serve", "staging"],
            )
        )

    def test_start_local_parallel_generation_does_not_stop_or_steal_live_binds(self):
        manifest = self._manifest(generation=16)
        isolated_ports = {
            "catdesk_port": 43331,
            "host_mcp_port": 43321,
            "process_runtime_port": 43442,
        }
        with tempfile.TemporaryDirectory(dir="/tmp") as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest,
                runtime_root=root / "runtime",
                bridge_socket=root / "bridge.sock",
                **isolated_ports,
            )
            self.assertEqual(layout["catdesk_port"], 43331)
            self.assertEqual(layout["host_mcp_port"], 43321)
            self.assertEqual(layout["process_runtime_port"], 43442)
            self.assertNotEqual(layout["bridge_socket"], pc.PRODUCTION_BRIDGE_SOCKET)
            env = pc.production_component_env(
                manifest, layout, catdesk_slug="private-slug"
            )
            self.assertEqual(env["host"]["LAB_HOST_MCP_PORT"], "43321")
            self.assertEqual(
                env["bridge"]["LAB_BRIDGE_BACKEND_URL"],
                "http://127.0.0.1:43331/private-slug/mcp",
            )
            self.assertEqual(
                env["bridge"]["LAB_HOST_BACKEND_URL"],
                "http://127.0.0.1:43321/mcp",
            )
            self.assertEqual(
                env["bridge"]["LAB_PROCESS_BACKEND_URL"],
                "http://127.0.0.1:43442/mcp",
            )
            self.assertEqual(
                env["process_runtime"]["PROCESS_RUNTIME_BIND"],
                "127.0.0.1:43442",
            )
            self.assertIn("'PORT': '43331'", pc._catdesk_runner_source(manifest, layout))
            prior = {
                "release_revision": "b" * 40,
                "endpoint_generation": 15,
                "bridge_pid": 12057,
                "host_pid": 60989,
                "process_runtime_pid": 61002,
                "catdesk_supervisor_pid": 60946,
            }
            layout["runtime_root"].mkdir(parents=True, exist_ok=True)
            pc._write_json_private(layout["runtime_state"], prior)

            def tcp_ready(_host, port):
                return port in {
                    pc.PRODUCTION_CATDESK_PORT,
                    pc.PRODUCTION_HOST_MCP_PORT,
                    pc.PRODUCTION_PROCESS_RUNTIME_PORT,
                }

            with patch.object(pc, "validate_installed_release", return_value=manifest), patch.object(
                pc,
                "production_status",
                return_value={"local_runtime_ready": False, "tunnel_ready": False},
            ), patch.object(pc, "_tcp_ready", side_effect=tcp_ready), patch.object(
                pc, "_stop_prior_local_runtime_for_release_upgrade"
            ) as stop_prior, patch.object(
                pc, "_stop_verified_local_runtime"
            ) as stop_verified, patch.object(
                pc,
                "_materialize_catdesk_runtime",
                side_effect=RuntimeError("stop after isolated occupancy"),
            ) as materialize:
                with self.assertRaisesRegex(RuntimeError, "stop after isolated occupancy"):
                    pc.start_local_runtime(manifest, layout)
            stop_prior.assert_not_called()
            stop_verified.assert_not_called()
            materialize.assert_called_once_with(layout)

    def _canonical_jobs_upgrade_fixture(self, raw_root):
        prior_manifest = self._manifest(generation=15)
        upgraded = {
            **prior_manifest,
            "release_revision": "b" * 40,
            "endpoint_generation": 16,
        }
        root = Path(raw_root)
        runtime_root = root / "runtime"
        bridge_socket = root / "bridge.sock"
        layout = pc.production_layout(
            upgraded, runtime_root=runtime_root, bridge_socket=bridge_socket
        )
        prior_layout = pc.production_layout(
            prior_manifest, runtime_root=runtime_root, bridge_socket=bridge_socket
        )
        layout["runtime_root"].mkdir(parents=True, exist_ok=True)
        layout["bridge_binary"].parent.mkdir(parents=True, exist_ok=True)
        layout["bridge_binary"].write_text("bridge", encoding="utf-8")
        layout["bridge_binary"].chmod(0o700)
        layout["process_runtime_binary"].write_text("process-runtime", encoding="utf-8")
        layout["process_runtime_binary"].chmod(0o700)
        layout["process_runtime_backend_key"].parent.mkdir(parents=True, exist_ok=True)
        layout["process_runtime_backend_key"].write_text("n" * 64, encoding="utf-8")
        layout["process_runtime_backend_key"].chmod(0o600)
        prior_layout["catdesk_config"].parent.mkdir(parents=True, exist_ok=True)
        prior_layout["catdesk_config"].write_text("mcpSlug = 'private'\n", encoding="utf-8")
        prior_layout["catdesk_config"].chmod(0o600)
        prior_layout["catdesk_backend_key"].write_text("c" * 64, encoding="utf-8")
        prior_layout["catdesk_backend_key"].chmod(0o600)
        prior_layout["host_backend_key"].parent.mkdir(parents=True, exist_ok=True)
        prior_layout["host_backend_key"].write_text("h" * 64, encoding="utf-8")
        prior_layout["host_backend_key"].chmod(0o600)
        prior_layout["release_manifest"].parent.mkdir(parents=True, exist_ok=True)
        pc._write_json_private(prior_layout["release_manifest"], prior_manifest)
        hook_identity = {
            "path": str(prior_layout["runtime_hook_socket"]),
            "uid": os.geteuid(),
            "mode": 0o600,
            "device": 1,
            "inode": 2,
        }
        prior = {
            "release_revision": prior_manifest["release_revision"],
            "endpoint_generation": prior_manifest["endpoint_generation"],
            "workspace": prior_manifest["workspace"],
            "tunnel_profile_name": pc.TUNNEL_PROFILE_NAME,
            "catdesk_supervisor_pid": 60946,
            "host_pid": 60989,
            "runtime_hook_pid": 60995,
            "process_runtime_pid": 61002,
            "bridge_pid": 12057,
            "catdesk_supervisor_process_identity": self._identity(
                60946, ["/private/catdesk-supervisor"]
            ),
            "host_process_identity": self._identity(60989, ["/private/host"]),
            "runtime_hook_process_identity": self._identity(60995, ["/private/runtime-hook"]),
            "process_runtime_process_identity": self._identity(
                61002, ["/private/process-runtime"]
            ),
            "bridge_process_identity": self._identity(12057, ["/private/bridge"]),
            "runtime_hook_socket_identity": hook_identity,
        }
        pc._write_json_private(layout["runtime_state"], prior)
        installed = json.loads(json.dumps(upgraded))
        installed["artifacts_sha256"] = {"bin/process-runtime": "a" * 64}
        return upgraded, layout, prior_layout, prior, installed, hook_identity

    def test_canonical_release_upgrade_reuses_live_catdesk_host_and_only_replaces_jobs_runtime(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as raw_root:
            upgraded, layout, prior_layout, prior, installed, hook_identity = (
                self._canonical_jobs_upgrade_fixture(raw_root)
            )
            processes = [MagicMock(pid=9101), MagicMock(pid=9102)]
            identities = [
                self._identity(9101, [str(layout["process_runtime_binary"].resolve())]),
                self._identity(9102, [str(layout["bridge_binary"].resolve())]),
            ]
            stopped = []

            def tcp_ready(_host, port):
                return port in {pc.PRODUCTION_CATDESK_PORT, pc.PRODUCTION_HOST_MCP_PORT}

            with patch.object(pc, "PRODUCTION_RUNTIME_ROOT", layout["runtime_root"]), patch.object(
                pc, "PRODUCTION_BRIDGE_SOCKET", layout["bridge_socket"]
            ), patch.object(
                pc, "validate_installed_release", return_value=installed
            ), patch.object(
                pc,
                "production_status",
                side_effect=[
                    {"local_runtime_ready": False, "tunnel_ready": True},
                    {
                        "local_runtime_ready": True,
                        "tunnel_ready": True,
                        "catdesk_supervisor_pid": 60946,
                        "host_pid": 60989,
                        "process_runtime_pid": 9101,
                        "bridge_pid": 9102,
                    },
                ],
            ), patch.object(pc, "_tcp_ready", side_effect=tcp_ready), patch.object(
                pc, "_process_identity_matches", return_value=True
            ), patch.object(
                pc, "_runtime_hook_socket_is_live", return_value=True
            ), patch.object(
                pc, "_host_adapter_socket_is_listening", return_value=True
            ), patch.object(
                pc, "_stop_prior_local_runtime_for_release_upgrade"
            ) as stop_prior, patch.object(
                pc, "_stop_verified_local_runtime"
            ) as stop_verified, patch.object(
                pc, "_materialize_catdesk_runtime"
            ) as materialize, patch.object(
                pc,
                "_stop_verified_process_identity",
                side_effect=lambda identity, process_group=False, process=None: stopped.append(
                    identity["pid"]
                ),
            ), patch.object(
                pc, "_spawn_logged", side_effect=processes
            ) as spawn, patch.object(
                pc, "_capture_process_identity", side_effect=identities
            ), patch.object(pc, "_wait_for_tcp"), patch.object(
                pc, "_wait_for_bridge", return_value=sorted(pc.REQUIRED_TOOLS)
            ), patch.object(pc, "_wait_for_runtime_hook") as wait_hook:
                result = pc.start_local_runtime(upgraded, layout)

            stop_prior.assert_not_called()
            stop_verified.assert_not_called()
            materialize.assert_not_called()
            wait_hook.assert_not_called()
            self.assertEqual(stopped, [12057, 61002])
            self.assertEqual(spawn.call_count, 2)
            self.assertEqual(
                spawn.call_args_list[0].args[0][0],
                str(layout["process_runtime_binary"].resolve()),
            )
            self.assertEqual(
                spawn.call_args_list[1].args[0][0],
                str(layout["bridge_binary"].resolve()),
            )
            bridge_env = spawn.call_args_list[1].kwargs["env"]
            self.assertEqual(
                bridge_env["LAB_BRIDGE_BACKEND_KEY_FILE"],
                str(prior_layout["catdesk_backend_key"]),
            )
            self.assertEqual(
                bridge_env["LAB_HOST_BACKEND_KEY_FILE"],
                str(prior_layout["host_backend_key"]),
            )
            self.assertEqual(
                bridge_env["LAB_HOST_ADAPTER_SOCKET"],
                str(prior_layout["host_socket"]),
            )
            self.assertEqual(
                bridge_env["LAB_RUNTIME_HOST_TOOL_HOOK_SOCKET"],
                str(prior_layout["runtime_hook_socket"]),
            )
            self.assertEqual(
                bridge_env["LAB_PROCESS_BACKEND_KEY_FILE"],
                str(layout["process_runtime_backend_key"]),
            )
            self.assertEqual(
                bridge_env["LAB_PRODUCTION_RELEASE_REVISION"],
                prior["release_revision"],
            )
            self.assertEqual(
                bridge_env["LAB_PRODUCTION_ENDPOINT_GENERATION"],
                str(prior["endpoint_generation"]),
            )
            self.assertEqual(
                bridge_env["LAB_PRODUCTION_RELEASE_MANIFEST_FILE"],
                str(prior_layout["release_manifest"]),
            )
            state = json.loads(layout["runtime_state"].read_text(encoding="utf-8"))
            self.assertEqual(state["catdesk_supervisor_pid"], 60946)
            self.assertEqual(state["host_pid"], 60989)
            self.assertEqual(state["runtime_hook_pid"], 60995)
            self.assertEqual(state["process_runtime_pid"], 9101)
            self.assertEqual(state["bridge_pid"], 9102)
            self.assertEqual(state["runtime_hook_socket_identity"], hook_identity)
            self.assertEqual(
                state["reused_live_backends"]["source_release_revision"],
                prior["release_revision"],
            )
            self.assertEqual(state["reused_live_backends"]["source_endpoint_generation"], 15)
            self.assertEqual(result["catdesk_supervisor_pid"], 60946)
            self.assertEqual(result["host_pid"], 60989)

    def test_canonical_release_upgrade_fail_closes_without_stopping_live_catdesk_host(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as raw_root:
            upgraded, layout, _prior_layout, prior, installed, _hook = (
                self._canonical_jobs_upgrade_fixture(raw_root)
            )

            def identity_matches(identity):
                return identity.get("pid") != prior["catdesk_supervisor_pid"]

            with patch.object(pc, "PRODUCTION_RUNTIME_ROOT", layout["runtime_root"]), patch.object(
                pc, "PRODUCTION_BRIDGE_SOCKET", layout["bridge_socket"]
            ), patch.object(
                pc, "validate_installed_release", return_value=installed
            ), patch.object(
                pc,
                "production_status",
                return_value={"local_runtime_ready": False, "tunnel_ready": True},
            ), patch.object(pc, "_tcp_ready", return_value=True), patch.object(
                pc, "_process_identity_matches", side_effect=identity_matches
            ), patch.object(
                pc, "_stop_prior_local_runtime_for_release_upgrade"
            ) as stop_prior, patch.object(
                pc, "_stop_verified_local_runtime"
            ) as stop_verified, patch.object(
                pc, "_stop_verified_process_identity"
            ) as stop_identity, patch.object(pc, "_spawn_logged") as spawn:
                with self.assertRaisesRegex(
                    RuntimeError, "cannot reuse live CatDesk|CatDesk.*changed"
                ):
                    pc.start_local_runtime(upgraded, layout)
            stop_prior.assert_not_called()
            stop_verified.assert_not_called()
            stop_identity.assert_not_called()
            spawn.assert_not_called()
            self.assertEqual(
                json.loads(layout["runtime_state"].read_text(encoding="utf-8")),
                prior,
            )


if __name__ == "__main__":
    unittest.main()
