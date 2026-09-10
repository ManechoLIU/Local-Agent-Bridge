import json
import os
import stat
import struct
import subprocess
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

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

    def _release_sources(self, root: Path) -> dict[str, Path]:
        sources_root = root / "sources"
        host = sources_root / "host"
        chrome_host = host / "chrome-host"
        (chrome_host / "extension").mkdir(parents=True)
        (chrome_host / "native-host.mjs").write_text("#!/usr/bin/env node\n", encoding="utf-8")
        (chrome_host / "host-mcp-adapter.mjs").write_text("#!/usr/bin/env node\n", encoding="utf-8")
        (chrome_host / "extension" / "manifest.json").write_text(
            json.dumps({"version": "0.1.1"}), encoding="utf-8"
        )
        for name in ("bridge", "catdesk"):
            path = sources_root / name
            path.write_bytes(name.encode() * 32)
            path.chmod(0o700)
        for name, content in (
            ("catdesk-config", b"mcpSlug = 'private-slug'\n"),
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
            "catdesk_binary": sources_root / "catdesk",
            "catdesk_config": sources_root / "catdesk-config",
            "catdesk_backend_key": sources_root / "catdesk-key",
            "host_attestation_key": sources_root / "attestation-key",
            "host_source_dir": host,
            "tunnel_profile": tunnel,
        }

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
        self.assertIn("read", manifest["required_tools"])
        self.assertIn("run_command", manifest["required_tools"])
        self.assertIn("discover_current_entry", manifest["required_tools"])
        self.assertEqual(manifest["tunnel"]["profile_name"], "local-agent-bridge-production")
        self.assertIn("local-agent-bridge-poc", manifest["tunnel"]["legacy_profile_names_forbidden"])
        self.assertIn("Local Bridge Identity Probe", manifest["historical_connectors_forbidden"])

    def test_release_manifest_rejects_invalid_revision_or_generation(self):
        with self.assertRaises(RuntimeError):
            pc.release_manifest(pc.canonical_workspace(), release_revision="dirty", endpoint_generation=1)
        with self.assertRaises(RuntimeError):
            pc.release_manifest(pc.canonical_workspace(), release_revision=REVISION, endpoint_generation=0)

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
            self.assertEqual(env["bridge"]["LAB_PRODUCTION_WORKSPACE"], str(pc.canonical_workspace()))
            self.assertEqual(env["bridge"]["LAB_PRODUCTION_TUNNEL_PROFILE_NAME"], pc.TUNNEL_PROFILE_NAME)
            self.assertIn("host_attestation", env["bridge"]["LAB_BRIDGE_CAPABILITIES"])

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

    def test_install_release_bundle_is_private_immutable_and_hash_verified(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            sources_root = root / "sources"
            host = sources_root / "host"
            chrome_host = host / "chrome-host"
            (chrome_host / "extension").mkdir(parents=True)
            (chrome_host / "native-host.mjs").write_text("#!/usr/bin/env node\n", encoding="utf-8")
            (chrome_host / "host-mcp-adapter.mjs").write_text("#!/usr/bin/env node\n", encoding="utf-8")
            (chrome_host / "extension" / "manifest.json").write_text(
                json.dumps({"version": "0.1.1"}), encoding="utf-8"
            )
            for name in ("bridge", "catdesk"):
                path = sources_root / name
                path.write_bytes(name.encode() * 32)
                path.chmod(0o700)
            for name, content in (
                ("catdesk-config", b"mcpSlug = 'private-slug'\n"),
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
            layout = pc.production_layout(
                manifest,
                runtime_root=root / "runtime",
                bridge_socket=root / "socket" / "bridge.sock",
            )
            sources = {
                "bridge_binary": sources_root / "bridge",
                "catdesk_binary": sources_root / "catdesk",
                "catdesk_config": sources_root / "catdesk-config",
                "catdesk_backend_key": sources_root / "catdesk-key",
                "host_attestation_key": sources_root / "attestation-key",
                "host_source_dir": host,
                "tunnel_profile": tunnel,
            }
            installed = pc.install_release_bundle(manifest, layout, sources)
            self.assertEqual(installed, layout)
            pc.validate_installed_release(layout)
            self.assertEqual(stat.S_IMODE(layout["release_manifest"].stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(layout["host_backend_key"].stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(layout["tunnel_profile"].stat().st_mode), 0o600)
            self.assertTrue(os.access(layout["bridge_binary"], os.X_OK))
            self.assertTrue(os.access(layout["native_host_wrapper"], os.X_OK))
            installed_manifest = json.loads(layout["release_manifest"].read_text(encoding="utf-8"))
            node = installed_manifest["runtime_dependencies"]["node"]
            self.assertTrue(Path(node["path"]).is_absolute())
            self.assertEqual(node["sha256"], pc._sha256_file(Path(node["path"])))
            self.assertIn(f"exec {node['path']}", layout["native_host_wrapper"].read_text(encoding="utf-8"))

            with self.assertRaisesRegex(RuntimeError, "immutable|already exists"):
                pc.install_release_bundle(manifest, layout, sources)

            extra = layout["release_dir"] / "unlisted-evidence.txt"
            extra.write_text("not in release manifest", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "extra|unlisted|artifact"):
                pc.validate_installed_release(layout)
            extra.unlink()

            layout["bridge_binary"].write_bytes(b"tampered")
            with self.assertRaisesRegex(RuntimeError, "hash|artifact"):
                pc.validate_installed_release(layout)

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
            installed_release = json.loads(json.dumps(manifest))
            installed_release["artifacts_sha256"] = {
                str(layout["native_host_wrapper"].relative_to(layout["release_dir"])): pc._sha256_file(
                    layout["native_host_wrapper"]
                )
            }
            extension_manifest = layout["host_dir"] / "chrome-host" / "extension" / "manifest.json"
            extension_manifest.parent.mkdir(parents=True)
            extension_manifest.write_text(json.dumps({"version": "0.1.1"}), encoding="utf-8")
            extension_manifest.chmod(0o600)

            custom_dirs = [
                root / ".chatgpt-web-mcp" / "chrome-profile",
                root / ".chatgpt-web-mcp" / "chrome-for-testing-profile",
                root / ".chatgpt-web-mcp" / "chrome-profile-current-entry-v2",
            ]
            with patch.object(pc, "validate_installed_release", return_value=installed_release):
                installed = pc.install_native_messaging_manifests(
                    layout,
                    manifest,
                    home_dir=root / "home",
                    native_user_data_dirs=custom_dirs,
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

            with patch.object(pc, "validate_installed_release", return_value=installed_release):
                pc.validate_native_messaging_manifests(
                    layout,
                    manifest,
                    home_dir=root / "home",
                    native_user_data_dirs=custom_dirs,
                )
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

            with self.assertRaisesRegex(RuntimeError, "absolute"):
                pc._native_messaging_paths(
                    root / "home",
                    native_user_data_dirs=[Path("relative-chrome-profile")],
                )

    def test_default_release_sources_and_head_fence_are_canonical_and_fail_closed(self):
        sources = pc.default_release_sources()
        self.assertEqual(
            sources["bridge_binary"],
            pc.CANONICAL_PROJECT_ROOT / "crates" / "local-tool-bridge" / "target" / "release" / "local-tool-bridge",
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

    def test_tunnel_operational_gate_requires_loopback_exact_status_and_fresh_polling_log(self):
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
                json.dumps({"timestamp": 1001.0, "message": "control-plane polling operational"}) + "\n",
                encoding="utf-8",
            )
            layout["tunnel_log"].chmod(0o600)
            response = MagicMock()
            response.status = 200
            response.read.return_value = json.dumps({
                "control_plane_tunnel_id": "tunnel-production",
                "channels": {"main": {"enabled": True, "probe_status": "ok"}},
            }).encode("utf-8")
            response.__enter__.return_value = response
            with patch.object(pc.urllib.request, "urlopen", return_value=response) as urlopen:
                observed = pc._verify_tunnel_operational(
                    layout,
                    expected_tunnel_id="tunnel-production",
                    started_at=1000.0,
                )
            self.assertTrue(observed["ok"])
            self.assertEqual(urlopen.call_args.args[0].full_url, "http://127.0.0.1:49152/api/status")

            layout["tunnel_health_url"].write_text("https://api.openai.com/health\n", encoding="utf-8")
            with patch.object(pc.urllib.request, "urlopen") as forbidden_request:
                with self.assertRaisesRegex(RuntimeError, "loopback"):
                    pc._verify_tunnel_operational(
                        layout,
                        expected_tunnel_id="tunnel-production",
                        started_at=1000.0,
                    )
            forbidden_request.assert_not_called()

            layout["tunnel_health_url"].write_text("http://127.0.0.1:49152\n", encoding="utf-8")
            layout["tunnel_log"].write_text(
                json.dumps({"timestamp": 999.0, "message": "control-plane polling operational"}) + "\n",
                encoding="utf-8",
            )
            with patch.object(pc.urllib.request, "urlopen", return_value=response):
                with self.assertRaisesRegex(RuntimeError, "started_at|fresh|polling"):
                    pc._verify_tunnel_operational(
                        layout,
                        expected_tunnel_id="tunnel-production",
                        started_at=1000.0,
                    )

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
            with patch.object(
                pc.urllib.request,
                "urlopen",
                return_value=health_response,
            ) as urlopen, patch.object(
                pc, "_tcp_ready", return_value=True
            ) as tcp_ready, patch.object(
                pc, "_mcp_streamable_tools_list", return_value=tools_status
            ) as handshake:
                guard = pc._verify_ai_bridge_fallback(health_file)
            self.assertTrue(guard["ready"])
            self.assertEqual(guard["compute_host"], "127.0.0.1")
            self.assertEqual(guard["compute_port"], 52342)
            self.assertEqual(guard["tools"], ["bridge_get_status", "computer", "shell_command"])
            self.assertEqual(urlopen.call_count, 1)
            handshake.assert_called_once_with("http://127.0.0.1:52342/mcp/private-slug")
            tcp_ready.assert_called_once_with("127.0.0.1", 52342)

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
            stop.assert_called_once_with(identity)
            self.assertEqual(json.loads(layout["runtime_state"].read_text(encoding="utf-8")), original)

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

    def test_start_local_post_status_failure_cleans_new_processes_and_preserves_verified_tunnel(self):
        manifest = self._manifest()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            layout = pc.production_layout(
                manifest, runtime_root=root / "runtime", bridge_socket=root / "bridge.sock"
            )
            layout["runtime_state"].parent.mkdir(parents=True)
            prior = {
                "release_revision": REVISION,
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
            node = root / "node"
            node.write_text("node", encoding="utf-8")
            node.chmod(0o700)
            processes = [MagicMock(pid=801), MagicMock(pid=802), MagicMock(pid=803)]
            identities = [
                self._identity(process.pid, [f"/private/process-{process.pid}"])
                for process in processes
            ]
            with patch.object(pc, "validate_installed_release"), patch.object(
                pc,
                "production_status",
                side_effect=[
                    {"local_runtime_ready": False, "tunnel_ready": True},
                    {"local_runtime_ready": False, "tunnel_ready": True},
                ],
            ), patch.object(pc, "_stop_verified_local_runtime"), patch.object(
                pc, "_tcp_ready", return_value=False
            ), patch.object(pc, "_catdesk_slug", return_value="private"), patch.object(
                pc, "production_component_env", return_value={"host": {}, "bridge": {}}
            ), patch.object(
                pc, "_spawn_logged", side_effect=processes
            ), patch.object(
                pc, "_capture_process_identity", side_effect=identities
            ), patch.object(pc, "_wait_for_tcp"), patch.object(
                pc, "_wait_for_bridge", return_value=sorted(pc.REQUIRED_TOOLS)
            ), patch.object(
                pc, "_validate_runtime_dependencies", return_value={"node": {"path": str(node)}}
            ), patch.object(pc, "_stop_verified_process_identity") as stop:
                with self.assertRaisesRegex(RuntimeError, "post-start status"):
                    pc.start_local_runtime(manifest, layout)
            self.assertEqual(stop.call_count, 3)
            state = json.loads(layout["runtime_state"].read_text(encoding="utf-8"))
            self.assertEqual(state["tunnel_pid"], 717)
            self.assertEqual(state["tunnel_process_identity"], prior["tunnel_process_identity"])
            self.assertNotIn("bridge_pid", state)

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


if __name__ == "__main__":
    unittest.main()
