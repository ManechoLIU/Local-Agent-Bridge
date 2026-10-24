import copy
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import production_connector as pc
import repair_production_control as repair


class InstalledAarSkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="installed-aar-skill-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.skill = Path(self.temp.name) / "installed"
        self.repo.mkdir(); self.skill.mkdir()
        files = {
            "SKILL.md": b"# adaptive delivery\n",
            "scripts/runtime_host_tool_hook.py": b"# hook\n",
            "scripts/rule_handshake.py": b"# handshake\n",
        }
        for relative, payload in files.items():
            source = self.repo / relative
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(payload)
            target = self.skill / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture"],
            cwd=self.repo,
            check=True,
        )
        self.revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, check=True, capture_output=True, text=True
        ).stdout.strip()
        self.files = {
            relative: hashlib.sha256(payload).hexdigest()
            for relative, payload in files.items()
        }
        self.manifest = {
            "schema_version": 1,
            "revision": self.revision,
            "canonical_release_source": None,
            "files": self.files,
        }
        self._write_manifest(self.manifest)

    def _write_manifest(self, value):
        path = self.skill / repair.AAR_INSTALL_MANIFEST
        path.write_text(json.dumps(value), encoding="utf-8")
        path.chmod(0o600)

    def test_installed_skill_must_exactly_match_commit_and_manifest_file_map(self):
        validated = repair._validate_installed_aar_skill(
            self.skill, self.repo, self.revision
        )
        self.assertEqual(validated["files"], self.files)
        self.assertEqual(validated["revision"], self.revision)
        self.assertEqual(
            validated["manifest_sha256"],
            pc._sha256_file(self.skill / repair.AAR_INSTALL_MANIFEST),
        )

    def test_installed_skill_rejects_missing_extra_revision_and_hash_drift(self):
        cases = []
        missing = dict(self.manifest); missing["files"] = dict(self.files)
        missing["files"].pop("SKILL.md")
        cases.append(("missing", missing, None, "file map differs"))
        extra = dict(self.manifest); extra["files"] = {**self.files, "extra.py": "0" * 64}
        cases.append(("extra", extra, None, "file map differs"))
        revision = dict(self.manifest); revision["revision"] = "f" * 40
        cases.append(("revision", revision, None, "revision mismatch"))
        hashes = dict(self.manifest); hashes["files"] = {**self.files, "SKILL.md": "0" * 64}
        cases.append(("hash", hashes, None, "hash mismatch"))
        cases.append(("installed-bytes", self.manifest, ("SKILL.md", b"changed\n"), "hash mismatch"))
        for label, manifest, mutation, message in cases:
            with self.subTest(label=label):
                self._write_manifest(manifest)
                original = (self.skill / "SKILL.md").read_bytes()
                if mutation:
                    (self.skill / mutation[0]).write_bytes(mutation[1])
                try:
                    with self.assertRaisesRegex(RuntimeError, message):
                        repair._validate_installed_aar_skill(
                            self.skill, self.repo, self.revision
                        )
                finally:
                    (self.skill / "SKILL.md").write_bytes(original)
                    self._write_manifest(self.manifest)


class FakeControlProcesses:
    def __init__(self, bridge, hook, bridge_env, hook_env):
        self.live = {bridge["pid"]: copy.deepcopy(bridge), hook["pid"]: copy.deepcopy(hook)}
        self.environments = {
            bridge["pid"]: dict(bridge_env),
            hook["pid"]: dict(hook_env),
        }
        self.events = []
        self.next_pid = 500
        self.on_side_smoke = None
        self.fail_label = None
        self.partial_stop_once = False
        self.unrecoverable_partial_once = False

    def identity_matches(self, identity):
        return self.live.get(identity["pid"]) == identity

    def environment(self, identity):
        if not self.identity_matches(identity):
            raise RuntimeError("fake identity changed")
        return dict(self.environments[identity["pid"]])

    @staticmethod
    def host_socket_identity(path):
        return {"path": str(path), "uid": os.geteuid(), "mode": 0o600, "inode": 900}

    @staticmethod
    def host_socket_is_listening(_path):
        return True

    @staticmethod
    def runtime_hook_socket_is_live(_path, identity):
        return isinstance(identity, dict)

    def start_pair(self, bridge_command, hook_command, bridge_env, hook_env, paths, label):
        self.next_pid += 1
        hook = self._identity(self.next_pid, hook_command)
        self.next_pid += 1
        bridge = self._identity(self.next_pid, bridge_command)
        self.live[hook["pid"]] = hook
        self.live[bridge["pid"]] = bridge
        self.environments[hook["pid"]] = dict(hook_env)
        self.environments[bridge["pid"]] = dict(bridge_env)
        self.events.append(("start_pair", label, bridge["pid"], hook["pid"], dict(paths), dict(bridge_env)))
        if label == "side-smoke" and self.on_side_smoke:
            self.on_side_smoke()
        if label == self.fail_label:
            self.fail_label = None
            del self.live[bridge["pid"]]
            del self.live[hook["pid"]]
            raise RuntimeError("fake pair smoke failed")
        return {
            "bridge_identity": copy.deepcopy(bridge),
            "hook_identity": copy.deepcopy(hook),
            "bridge_socket_identity": {
                "path": str(paths["bridge_socket"]), "uid": os.geteuid(), "mode": 0o600,
                "inode": bridge["pid"],
            },
            "hook_socket_identity": {
                "path": str(paths["hook_socket"]), "uid": os.geteuid(), "mode": 0o600,
                "inode": hook["pid"],
            },
            "tools": ["run_command", "read_file"],
        }

    def preflight_pair(self, bridge_command, hook_command, bridge_env, hook_env, paths, label):
        self.events.append((
            "preflight_pair", label, list(bridge_command), list(hook_command),
            dict(paths), dict(bridge_env), dict(hook_env),
        ))
        if self.on_side_smoke:
            self.on_side_smoke()
        if label == self.fail_label:
            self.fail_label = None
            raise repair.PairStartError("fake preflight failed", cleanup_complete=True)
        return {"check_config": True, "production_mode": True, "listening": False}

    def stop_pair(self, bridge, hook, paths):
        if not self.identity_matches(bridge) or not self.identity_matches(hook):
            raise RuntimeError("fake identity changed")
        self.events.append(("stop_pair", bridge["pid"], hook["pid"], dict(paths)))
        if self.unrecoverable_partial_once and bridge["pid"] == 200:
            self.unrecoverable_partial_once = False
            del self.live[bridge["pid"]]
            raise repair.PairStopError("injected live Hook survivor", fully_stopped=False)
        del self.live[bridge["pid"]]
        del self.live[hook["pid"]]
        if self.partial_stop_once and bridge["pid"] == 200:
            self.partial_stop_once = False
            raise repair.PairStopError("injected partial stop recovered to fully stopped", fully_stopped=True)

    def stop_started_pair(self, pair, paths):
        bridge = pair["bridge_identity"]
        hook = pair["hook_identity"]
        if self.identity_matches(bridge) and self.identity_matches(hook):
            self.stop_pair(bridge, hook, paths)

    def _identity(self, pid, command):
        executable = Path(command[0])
        return {
            "pid": pid,
            "uid": os.geteuid(),
            "pgid": pid,
            "lstart": f"fake-{pid}",
            "start_timestamp": float(pid),
            "exe_realpath": str(executable),
            "exe_inode": pid,
            "exe_sha256": pc._sha256_file(executable),
            "argv": list(command),
        }


class ProductionControlComponentTests(unittest.TestCase):
    LAB_COMMIT = "c" * 40
    AAR_COMMIT = "d" * 40

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="control-patch-")
        self.addCleanup(self.cleanup)
        self.root = Path(self.temp.name).resolve() / "production"
        self.root.mkdir(mode=0o700)
        self.current = self.root / "releases" / ("b" * 40) / "generation-35"
        self.source = self.root / "releases" / ("a" * 40) / "generation-34"
        self.current_bridge = self.current / "bin" / "local-tool-bridge"
        self.current_bridge.parent.mkdir(parents=True, mode=0o700)
        self.current_bridge.write_bytes(b"old bridge")
        self.current_bridge.chmod(0o500)
        self.python = self.source / "bin" / "python-runtime"
        self.python.parent.mkdir(parents=True, mode=0o700)
        self.python.write_bytes(b"old python")
        self.python.chmod(0o500)
        self.source_hook = self.source / "runtime-host-tool-hook"
        for relative in pc.RUNTIME_HOST_TOOL_HOOK_BUNDLE_RELATIVE_PATHS:
            target = self.source_hook / relative
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.write_text(f"# old {relative}\n", encoding="utf-8")
            target.chmod(0o500 if relative == pc.RUNTIME_HOST_TOOL_HOOK_ENTRYPOINT_RELATIVE_PATH else 0o400)
        for directory in reversed([item for item in self.source_hook.rglob("*") if item.is_dir()]):
            directory.chmod(0o500)
        # generation-34 has a legacy 0555 bundle root; all descendants remain
        # private and sealed, and the full release hash validator runs first.
        self.source_hook.chmod(0o555)
        self.current_manifest = self._manifest("b" * 40, 35)
        self.current_manifest["artifacts_sha256"] = {
            "bin/local-tool-bridge": pc._sha256_file(self.current_bridge)
        }
        self.source_manifest = self._manifest("a" * 40, 34)
        self.source_manifest["artifacts_sha256"] = {
            "bin/python-runtime": pc._sha256_file(self.python),
            **{
                "runtime-host-tool-hook/" + relative: pc._sha256_file(self.source_hook / relative)
                for relative in pc.RUNTIME_HOST_TOOL_HOOK_BUNDLE_RELATIVE_PATHS
            },
        }
        self.source_manifest["runtime_dependencies"] = {
            "python": {"path": str(self.python), "sha256": pc._sha256_file(self.python)}
        }
        self._write(self.current / "release.json", self.current_manifest)
        self._write(self.source / "release.json", self.source_manifest)
        self.bridge_socket = self.root / "bridge.sock"
        source_layout = pc.production_layout(
            self.source_manifest,
            runtime_root=self.root,
            bridge_socket=self.bridge_socket,
        )
        self.hook_socket = source_layout["runtime_hook_socket"]
        self.host_socket = source_layout["host_socket"]
        self.bridge_identity = self._identity(200, [str(self.current_bridge)])
        hook_entry = self.source_hook / pc.RUNTIME_HOST_TOOL_HOOK_ENTRYPOINT_RELATIVE_PATH
        self.hook_identity = self._identity(
            100,
            [str(self.python), str(hook_entry), "--serve-unix-socket", str(self.hook_socket)],
        )
        self.host_identity = self._identity(302, [str(self.python), "host-adapter"])
        self.state = {
            "release_revision": "b" * 40,
            "endpoint_generation": 35,
            "workspace": self.current_manifest["workspace"],
            "tunnel_profile_name": pc.TUNNEL_PROFILE_NAME,
            "release_manifest": str(self.current / "release.json"),
            "bridge_pid": 200,
            "bridge_process_identity": self.bridge_identity,
            "bridge_socket_identity": {"path": str(self.bridge_socket), "inode": 1},
            "runtime_hook_pid": 100,
            "runtime_hook_process_identity": self.hook_identity,
            "runtime_hook_socket_identity": {
                "path": str(self.hook_socket), "uid": os.geteuid(), "mode": 0o600, "inode": 2
            },
            "bridge_execution_session_id": "session-stays",
            "bridge_capability_id": "capability-stays",
            "tools": ["run_command", "read_file"],
            "process_runtime_pid": 300,
            "process_runtime_process_identity": {"pid": 300, "keep": True},
            "computer_runtime_pid": 301,
            "host_pid": 302,
            "host_process_identity": self.host_identity,
            "host_component_patch": {"keep": "host"},
            "extension_component_patch": {"keep": "extension"},
            "tunnel_process_identity": {"keep": "tunnel"},
            "reused_live_backends": {
                "source_release_revision": "a" * 40,
                "source_endpoint_generation": 34,
                "release_manifest": str(self.source / "release.json"),
                "host_socket": str(self.host_socket),
                "runtime_hook_socket": str(self.hook_socket),
            },
        }
        self.state_path = self.root / "runtime.json"
        self._write(self.state_path, self.state)
        self.bridge_state_dir = pc.production_layout(
            self.current_manifest,
            runtime_root=self.root,
            bridge_socket=self.bridge_socket,
        )["bridge_state_dir"]
        self.bridge_state_dir.mkdir(parents=True, mode=0o700)
        self._write(self.bridge_state_dir / "authority-state.json", {"authority": "must-not-change"})
        self._write(self.bridge_state_dir / "tool-receipt-journal.json", {"receipts": ["must-stay"]})
        self.bridge_env = {
            "HOME": str(Path.home()), "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1",
            "LAB_BRIDGE_SOCKET": str(self.bridge_socket),
            "LAB_BRIDGE_EXECUTION_SESSION_ID": "session-stays",
            "LAB_BRIDGE_CAPABILITY_ID": "capability-stays",
            "LAB_BRIDGE_STATE_DIR": str(self.bridge_state_dir),
            "LAB_PRODUCTION_RELEASE_MANIFEST_FILE": str(self.source / "release.json"),
            "LAB_PRODUCTION_RELEASE_REVISION": "a" * 40,
            "LAB_PRODUCTION_ENDPOINT_GENERATION": "34",
        }
        self.hook_env = {
            "HOME": str(Path.home()), "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1",
            "LAB_PRODUCTION_RELEASE_MANIFEST_FILE": str(self.source / "release.json"),
            "LAB_PRODUCTION_RELEASE_REVISION": "a" * 40,
            "LAB_PRODUCTION_ENDPOINT_GENERATION": "34",
        }
        self.host_env = {
            **self.hook_env,
            "LAB_HOST_ADAPTER_SOCKET": str(self.host_socket),
        }
        self.ops = FakeControlProcesses(self.bridge_identity, self.hook_identity, self.bridge_env, self.hook_env)
        self.ops.live[self.host_identity["pid"]] = copy.deepcopy(self.host_identity)
        self.ops.environments[self.host_identity["pid"]] = dict(self.host_env)
        self.lab_repo = Path(self.temp.name) / "lab-source"
        self.aar_repo = Path(self.temp.name) / "aar-source"
        self.lab_repo.mkdir(); self.aar_repo.mkdir()
        self.aar_skill_root = Path(self.temp.name) / "installed-aar"
        self.aar_skill_root.mkdir()
        self.aar_files = {
            relative: f"# new {self.AAR_COMMIT} {relative}\n".encode("utf-8")
            for relative in pc.RUNTIME_HOST_TOOL_HOOK_BUNDLE_RELATIVE_PATHS
        }
        self.aar_files.update(
            {
                "SKILL.md": b"# full installed Adaptive Delivery Core\n",
                "tests/full_tree_fixture.txt": b"full source member\n",
            }
        )
        for relative, payload in self.aar_files.items():
            target = self.aar_skill_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        self.aar_file_hashes = {
            relative: hashlib.sha256(payload).hexdigest()
            for relative, payload in self.aar_files.items()
        }
        self.aar_manifest_raw = json.dumps(
            {
                "schema_version": 1,
                "revision": self.AAR_COMMIT,
                "canonical_release_source": None,
                "files": self.aar_file_hashes,
            },
            sort_keys=True,
        ).encode("utf-8")
        (self.aar_skill_root / repair.AAR_INSTALL_MANIFEST).write_bytes(self.aar_manifest_raw)
        (self.aar_skill_root / repair.AAR_INSTALL_MANIFEST).chmod(0o600)
        self.mocks = [
            patch.object(pc, "_validate_manifest_contract"),
            patch.object(pc, "validate_installed_release", return_value=self.current_manifest),
            patch.object(pc, "_validate_legacy_component_source_release", return_value=self.source_manifest),
            patch.object(pc, "_bridge_capability_lease_status", return_value={"ready": True}),
            patch.object(pc, "_validate_process_identity_record", side_effect=lambda value: value),
        ]
        for mock in self.mocks:
            mock.start(); self.addCleanup(mock.stop)

    def cleanup(self):
        root = Path(self.temp.name)
        if root.exists():
            for item in sorted(root.rglob("*"), key=lambda value: len(value.parts), reverse=True):
                try:
                    item.chmod(0o700 if item.is_dir() else 0o600)
                except OSError:
                    pass
        self.temp.cleanup()

    def _manifest(self, revision, generation):
        return {
            "connector_id": pc.CONNECTOR_ID,
            "contract_version": pc.CONTRACT_VERSION,
            "fail_closed": True,
            "workspace": str(pc.canonical_workspace()),
            "release_revision": revision,
            "endpoint_generation": generation,
            "tunnel": {"profile_name": pc.TUNNEL_PROFILE_NAME},
        }

    def _identity(self, pid, argv):
        executable = Path(argv[0])
        return {
            "pid": pid, "uid": os.geteuid(), "pgid": pid, "lstart": f"old-{pid}",
            "start_timestamp": float(pid), "exe_realpath": str(executable), "exe_inode": pid,
            "exe_sha256": pc._sha256_file(executable), "argv": list(argv),
        }

    @staticmethod
    def _write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(json.dumps(value), encoding="utf-8")
        path.chmod(0o600)

    def validate_aar_skill(self, skill_root, aar_repo, aar_commit):
        self.assertEqual(Path(skill_root).resolve(), self.aar_skill_root.resolve())
        self.assertEqual((Path(aar_repo).resolve(), aar_commit), (self.aar_repo.resolve(), self.AAR_COMMIT))
        return {
            "root": self.aar_skill_root.resolve(),
            "revision": self.AAR_COMMIT,
            "manifest_raw": self.aar_manifest_raw,
            "manifest_sha256": hashlib.sha256(self.aar_manifest_raw).hexdigest(),
            "files": self.aar_file_hashes,
        }

    def materialize(self, lab_repo, lab_commit, aar_repo, aar_commit, aar_skill_root, destination):
        self.assertEqual((lab_repo, lab_commit), (self.lab_repo.resolve(), self.LAB_COMMIT))
        self.assertEqual((aar_repo, aar_commit), (self.aar_repo.resolve(), self.AAR_COMMIT))
        self.assertEqual(aar_skill_root, self.aar_skill_root.resolve())
        bridge = destination / "bin" / "local-tool-bridge"
        bridge.parent.mkdir(parents=True, mode=0o700)
        bridge.write_bytes(b"new bridge from exact LAB commit")
        bridge.chmod(0o700)
        hook = destination / "runtime-host-tool-hook"
        for relative, payload in self.aar_files.items():
            target = hook / relative
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.write_bytes(payload)
            target.chmod(0o700 if relative == pc.RUNTIME_HOST_TOOL_HOOK_ENTRYPOINT_RELATIVE_PATH else 0o600)
        (hook / repair.AAR_INSTALL_MANIFEST).write_bytes(self.aar_manifest_raw)
        (hook / repair.AAR_INSTALL_MANIFEST).chmod(0o600)
        return bridge, hook

    def stage(self):
        return repair.stage_patch(
            self.root,
            lab_repo=self.lab_repo,
            lab_commit=self.LAB_COMMIT,
            aar_repo=self.aar_repo,
            aar_commit=self.AAR_COMMIT,
            aar_skill_root=self.aar_skill_root,
            ops=self.ops,
            materialize=self.materialize,
            validate_aar_skill=self.validate_aar_skill,
        )

    def test_stage_seals_paired_artifacts_with_independent_commit_provenance(self):
        before = self.state_path.read_bytes()
        staged = self.stage()
        self.assertEqual(self.state_path.read_bytes(), before)
        record = json.loads((staged / "manifest.json").read_text())
        self.assertEqual(record["sources"]["lab_commit"], self.LAB_COMMIT)
        self.assertEqual(record["sources"]["aar_commit"], self.AAR_COMMIT)
        self.assertEqual(
            set(record["artifact_hashes"]),
            {
                "bin/local-tool-bridge",
                *{"runtime-host-tool-hook/" + item for item in self.aar_file_hashes},
                "runtime-host-tool-hook/" + repair.AAR_INSTALL_MANIFEST,
            },
        )
        self.assertEqual(
            record["sources"]["aar_install_manifest_sha256"],
            hashlib.sha256(self.aar_manifest_raw).hexdigest(),
        )
        self.assertGreater(len(self.aar_file_hashes), len(pc.RUNTIME_HOST_TOOL_HOOK_BUNDLE_RELATIVE_PATHS))
        self.assertEqual(
            (staged / "runtime-host-tool-hook" / repair.AAR_INSTALL_MANIFEST).read_bytes(),
            self.aar_manifest_raw,
        )
        for item in (staged, *staged.rglob("*")):
            self.assertEqual(stat.S_IMODE(item.lstat().st_mode) & 0o222, 0)
        self.assertEqual(stat.S_IMODE((staged / "bin" / "local-tool-bridge").stat().st_mode), 0o500)
        hook_entry = staged / "runtime-host-tool-hook" / pc.RUNTIME_HOST_TOOL_HOOK_ENTRYPOINT_RELATIVE_PATH
        self.assertEqual(stat.S_IMODE(hook_entry.stat().st_mode), 0o500)

    def test_stage_records_missing_to_canonical_bridge_chain_environment(self):
        staged = self.stage()
        before = json.loads((staged / "bridge-environment-before.json").read_text())
        after = json.loads((staged / "bridge-environment-after.json").read_text())
        record = json.loads((staged / "manifest.json").read_text())
        self.assertNotIn("LAB_HOST_ADAPTER_SOCKET", before)
        self.assertNotIn("LAB_RUNTIME_HOST_TOOL_HOOK_SOCKET", before)
        self.assertEqual(after["LAB_HOST_ADAPTER_SOCKET"], str(self.host_socket))
        self.assertEqual(after["LAB_RUNTIME_HOST_TOOL_HOOK_SOCKET"], str(self.hook_socket))
        self.assertEqual(
            record["bridge_environment_transition"],
            {
                "LAB_HOST_ADAPTER_SOCKET": {
                    "before": "missing",
                    "after": str(self.host_socket),
                },
                "LAB_RUNTIME_HOST_TOOL_HOOK_SOCKET": {
                    "before": "missing",
                    "after": str(self.hook_socket),
                },
            },
        )
        self.assertEqual(
            record["bridge_environment_before_sha256"],
            pc._sha256_file(staged / "bridge-environment-before.json"),
        )
        self.assertEqual(
            record["bridge_environment_after_sha256"],
            pc._sha256_file(staged / "bridge-environment-after.json"),
        )

    def test_stage_refuses_incorrect_existing_bridge_chain_environment(self):
        self.ops.environments[200]["LAB_HOST_ADAPTER_SOCKET"] = "/tmp/wrong-host.sock"
        with self.assertRaisesRegex(RuntimeError, "Bridge chain environment mismatch"):
            self.stage()
        self.assertFalse((self.root / repair.PATCH_ROOT).exists())

    def test_stage_refuses_present_empty_bridge_chain_environment(self):
        self.ops.environments[200]["LAB_RUNTIME_HOST_TOOL_HOOK_SOCKET"] = ""
        with self.assertRaisesRegex(RuntimeError, "Bridge chain environment mismatch"):
            self.stage()
        self.assertFalse((self.root / repair.PATCH_ROOT).exists())

    def test_stage_refuses_reused_socket_outside_canonical_source_fence(self):
        state = dict(self.state)
        state["reused_live_backends"] = {
            **self.state["reused_live_backends"],
            "host_socket": str(self.root / "wrong-host.sock"),
        }
        self._write(self.state_path, state)
        with self.assertRaisesRegex(RuntimeError, "socket fence differs"):
            self.stage()

    def test_stage_refuses_host_environment_from_different_release_fence(self):
        self.ops.environments[302]["LAB_PRODUCTION_ENDPOINT_GENERATION"] = "35"
        with self.assertRaisesRegex(RuntimeError, "Host environment differs"):
            self.stage()

    def test_stage_refuses_bridge_environment_from_different_release_fence(self):
        self.ops.environments[200]["LAB_PRODUCTION_ENDPOINT_GENERATION"] = "35"
        with self.assertRaisesRegex(RuntimeError, "Bridge source fence mismatch"):
            self.stage()

    def test_stage_refuses_materialized_full_tree_with_missing_manifest_file(self):
        def incomplete(*args):
            bridge, hook = self.materialize(*args)
            (hook / "SKILL.md").unlink()
            return bridge, hook

        with self.assertRaisesRegex(RuntimeError, "manifest whitelist"):
            repair.stage_patch(
                self.root,
                lab_repo=self.lab_repo,
                lab_commit=self.LAB_COMMIT,
                aar_repo=self.aar_repo,
                aar_commit=self.AAR_COMMIT,
                aar_skill_root=self.aar_skill_root,
                ops=self.ops,
                materialize=incomplete,
                validate_aar_skill=self.validate_aar_skill,
            )

    def test_patch_verification_rejects_extra_full_tree_member_before_process_action(self):
        staged = self.stage()
        hook = staged / "runtime-host-tool-hook"
        hook.chmod(0o700)
        extra = hook / "unlisted.py"
        extra.write_text("unlisted\n")
        extra.chmod(0o400)
        hook.chmod(0o500)
        events_before = list(self.ops.events)
        with self.assertRaisesRegex(RuntimeError, "manifest whitelist"):
            repair.execute_patch(self.root, staged, ops=self.ops)
        self.assertEqual(self.ops.events, events_before)

    def test_apply_and_rollback_replace_only_bridge_and_hook_as_a_pair(self):
        staged = self.stage()
        applied = repair.execute_patch(self.root, staged, ops=self.ops)
        self.assertTrue(applied["ok"])
        state = json.loads(self.state_path.read_text())
        for key, value in self.state.items():
            if key not in repair.PAIRED_RUNTIME_FIELDS:
                self.assertEqual(state[key], value)
        self.assertIn(repair.PROVENANCE, state)
        self.assertEqual(
            state[repair.PROVENANCE]["aar_install_manifest_sha256"],
            hashlib.sha256(self.aar_manifest_raw).hexdigest(),
        )
        self.assertNotEqual(state["bridge_pid"], 200)
        self.assertNotEqual(state["runtime_hook_pid"], 100)
        apply_event = next(event for event in self.ops.events if event[:2] == ("start_pair", "apply"))
        self.assertEqual(apply_event[5]["LAB_HOST_ADAPTER_SOCKET"], str(self.host_socket))
        self.assertEqual(apply_event[5]["LAB_RUNTIME_HOST_TOOL_HOOK_SOCKET"], str(self.hook_socket))
        rolled = repair.execute_patch(self.root, staged, rollback=True, ops=self.ops)
        restored = json.loads(self.state_path.read_text())
        self.assertTrue(rolled["ok"])
        self.assertNotIn(repair.PROVENANCE, restored)
        for key, value in self.state.items():
            if key not in repair.PAIRED_RUNTIME_FIELDS:
                self.assertEqual(restored[key], value)
        self.assertEqual(restored["bridge_process_identity"]["argv"], self.bridge_identity["argv"])
        self.assertEqual(restored["runtime_hook_process_identity"]["argv"], self.hook_identity["argv"])
        self.assertNotEqual(restored["bridge_pid"], 200)
        self.assertNotEqual(restored["runtime_hook_pid"], 100)
        rollback_event = next(event for event in self.ops.events if event[:2] == ("start_pair", "rollback"))
        self.assertNotIn("LAB_HOST_ADAPTER_SOCKET", rollback_event[5])
        self.assertNotIn("LAB_RUNTIME_HOST_TOOL_HOOK_SOCKET", rollback_event[5])

    def test_runtime_drift_after_side_smoke_stops_no_production_process(self):
        staged = self.stage()
        def drift():
            self._write(self.state_path, {**self.state, "concurrent": True})
        self.ops.on_side_smoke = drift
        with self.assertRaisesRegex(RuntimeError, "runtime bytes changed"):
            repair.execute_patch(self.root, staged, ops=self.ops)
        production_stops = [event for event in self.ops.events if event[0] == "stop_pair" and event[1] == 200]
        self.assertEqual(production_stops, [])

    def test_side_smoke_uses_private_state_copy_and_leaves_production_state_unchanged(self):
        staged = self.stage()
        before = repair._private_tree_snapshot(self.bridge_state_dir)

        def mutate_side_copy():
            event = next(item for item in reversed(self.ops.events) if item[:2] == ("preflight_pair", "side-preflight"))
            side_state = Path(event[5]["LAB_BRIDGE_STATE_DIR"])
            self.assertNotEqual(side_state, self.bridge_state_dir)
            self.assertEqual(
                json.loads((side_state / "authority-state.json").read_text()),
                {"authority": "must-not-change"},
            )
            self.assertEqual(event[5]["LAB_HOST_ADAPTER_SOCKET"], str(self.host_socket))
            self.assertEqual(
                event[5]["LAB_RUNTIME_HOST_TOOL_HOOK_SOCKET"],
                str(event[4]["hook_socket"]),
            )
            self._write(side_state / "authority-state.json", {"authority": "side-only-write"})

        self.ops.on_side_smoke = mutate_side_copy
        repair.execute_patch(self.root, staged, ops=self.ops)
        self.assertEqual(repair._private_tree_snapshot(self.bridge_state_dir), before)

    def test_side_preflight_uses_new_bridge_check_config_without_second_bridge(self):
        staged = self.stage()
        repair.execute_patch(self.root, staged, ops=self.ops)
        event = next(item for item in self.ops.events if item[:2] == ("preflight_pair", "side-preflight"))
        self.assertEqual(event[2], [str(staged / "bin" / "local-tool-bridge")])
        self.assertEqual(event[5]["LAB_BRIDGE_SOCKET"], str(self.bridge_socket))
        self.assertNotEqual(Path(event[5]["LAB_BRIDGE_STATE_DIR"]), self.bridge_state_dir)
        self.assertFalse(any(item[:2] == ("start_pair", "side-smoke") for item in self.ops.events))

    def test_side_preflight_failure_stops_no_production_process(self):
        staged = self.stage()
        for reason in ("nonzero", "invalid JSON", "timed out"):
            with self.subTest(reason=reason):
                events_before = list(self.ops.events)
                with patch.object(
                    self.ops,
                    "preflight_pair",
                    side_effect=repair.PairStartError(reason, cleanup_complete=True),
                ):
                    with self.assertRaisesRegex(RuntimeError, reason):
                        repair.execute_patch(self.root, staged, ops=self.ops)
                new_events = self.ops.events[len(events_before):]
                self.assertFalse(any(event[0] == "stop_pair" for event in new_events))

    def test_rollback_refuses_host_or_extension_provenance_drift_without_stop(self):
        staged = self.stage()
        repair.execute_patch(self.root, staged, ops=self.ops)
        state = json.loads(self.state_path.read_text())
        state["host_component_patch"] = {"keep": "drifted"}
        self._write(self.state_path, state)
        events_before = list(self.ops.events)
        with self.assertRaisesRegex(RuntimeError, "source provenance mismatch"):
            repair.execute_patch(self.root, staged, rollback=True, ops=self.ops)
        self.assertEqual(self.ops.events, events_before)

    def test_partial_or_tampered_active_provenance_refuses_rollback_without_stop(self):
        staged = self.stage()
        repair.execute_patch(self.root, staged, ops=self.ops)
        state = json.loads(self.state_path.read_text())
        state[repair.PROVENANCE]["aar_commit"] = "e" * 40
        self._write(self.state_path, state)
        events_before = list(self.ops.events)
        with self.assertRaisesRegex(RuntimeError, "paired provenance"):
            repair.execute_patch(self.root, staged, rollback=True, ops=self.ops)
        self.assertEqual(self.ops.events, events_before)

    def test_one_sided_active_command_drift_refuses_paired_rollback_without_stop(self):
        staged = self.stage()
        repair.execute_patch(self.root, staged, ops=self.ops)
        state = json.loads(self.state_path.read_text())
        bridge_identity = state["bridge_process_identity"]
        bridge_identity["argv"] = [str(self.current_bridge)]
        self.ops.live[bridge_identity["pid"]]["argv"] = [str(self.current_bridge)]
        self._write(self.state_path, state)
        events_before = list(self.ops.events)
        with self.assertRaisesRegex(RuntimeError, "paired control process commands"):
            repair.execute_patch(self.root, staged, rollback=True, ops=self.ops)
        self.assertEqual(self.ops.events, events_before)

    def test_apply_failure_restores_old_pair_with_real_new_pids(self):
        staged = self.stage()
        self.ops.fail_label = "apply"
        with self.assertRaisesRegex(RuntimeError, "prior control pair restored"):
            repair.execute_patch(self.root, staged, ops=self.ops)
        restored = json.loads(self.state_path.read_text())
        self.assertNotEqual(restored["bridge_pid"], 200)
        self.assertNotEqual(restored["runtime_hook_pid"], 100)
        self.assertEqual(restored["bridge_process_identity"]["argv"], self.bridge_identity["argv"])
        self.assertEqual(restored["runtime_hook_process_identity"]["argv"], self.hook_identity["argv"])
        self.assertNotIn(repair.PROVENANCE, restored)

    def test_partial_stop_failure_restores_old_pair_with_fresh_identities(self):
        staged = self.stage()
        self.ops.partial_stop_once = True
        with self.assertRaisesRegex(RuntimeError, "prior control pair restored"):
            repair.execute_patch(self.root, staged, ops=self.ops)
        restored = json.loads(self.state_path.read_text())
        self.assertNotEqual(restored["bridge_pid"], 200)
        self.assertNotEqual(restored["runtime_hook_pid"], 100)
        self.assertEqual(restored["bridge_process_identity"]["argv"], self.bridge_identity["argv"])
        self.assertEqual(restored["runtime_hook_process_identity"]["argv"], self.hook_identity["argv"])

    def test_unrecoverable_partial_stop_reports_exact_fail_closed_readback(self):
        staged = self.stage()
        self.ops.unrecoverable_partial_once = True
        with self.assertRaisesRegex(
            RuntimeError,
            "old_bridge_live=False, old_hook_live=True.*runtime_state_unchanged=True",
        ):
            repair.execute_patch(self.root, staged, ops=self.ops)
        self.assertEqual(self.state_path.read_bytes(), json.dumps(self.state).encode("utf-8"))
        restores = [event for event in self.ops.events if event[:2] == ("start_pair", "restore")]
        self.assertEqual(restores, [])

    def test_artifact_tamper_fails_before_any_process_operation(self):
        staged = self.stage()
        target = staged / "runtime-host-tool-hook" / "scripts" / "control_event_guard.py"
        target.chmod(0o600); target.write_text("tampered\n"); target.chmod(0o400)
        events_before = list(self.ops.events)
        with self.assertRaisesRegex(RuntimeError, "hash mismatch"):
            repair.execute_patch(self.root, staged, ops=self.ops)
        self.assertEqual(self.ops.events, events_before)

    def test_missing_execute_mode_fails_before_any_process_operation(self):
        staged = self.stage()
        bridge = staged / "bin" / "local-tool-bridge"
        bridge.chmod(0o400)
        with self.assertRaisesRegex(RuntimeError, "executable modes"):
            repair.execute_patch(self.root, staged, ops=self.ops)
        self.assertEqual(self.ops.events, [])

    def test_context_cas_stop_start_and_readback_all_run_under_install_lock(self):
        staged = self.stage()
        held = {"value": False}
        original_start_pair = self.ops.start_pair
        original_stop_pair = self.ops.stop_pair
        original_preflight_pair = self.ops.preflight_pair

        @contextmanager
        def lock(_runtime_root):
            self.assertFalse(held["value"])
            held["value"] = True
            try:
                yield
            finally:
                held["value"] = False

        def start_pair(*args, **kwargs):
            self.assertTrue(held["value"])
            return original_start_pair(*args, **kwargs)

        def stop_pair(*args, **kwargs):
            self.assertTrue(held["value"])
            return original_stop_pair(*args, **kwargs)

        def preflight_pair(*args, **kwargs):
            self.assertTrue(held["value"])
            return original_preflight_pair(*args, **kwargs)

        self.ops.start_pair = start_pair
        self.ops.stop_pair = stop_pair
        self.ops.preflight_pair = preflight_pair
        with patch.object(repair.host_repair, "bounded_install_lock", side_effect=lock):
            repair.execute_patch(self.root, staged, ops=self.ops)
        self.assertFalse(held["value"])


class RealOperationsFaultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="control-ops-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.root.chmod(0o700)

    def _listener(self, name):
        path = self.root / name
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        path.chmod(0o600)
        self.addCleanup(listener.close)
        return path

    def _executable(self, name, body):
        path = self.root / name
        path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
        path.chmod(0o700)
        return path

    def test_bridge_check_config_requires_exact_fixed_json_and_bounded_exit(self):
        success = self._executable(
            "check-success",
            "import json\nprint(json.dumps({'check_config': True, 'production_mode': True, 'listening': False}))\n",
        )
        repair.Operations._bridge_check_config([str(success)], {}, cwd=self.root)

        malformed = self._executable("check-malformed", "print('not-json')\n")
        with self.assertRaisesRegex(RuntimeError, "invalid JSON"):
            repair.Operations._bridge_check_config([str(malformed)], {}, cwd=self.root)

        wrong = self._executable(
            "check-wrong",
            "import json\nprint(json.dumps({'check_config': True, 'production_mode': True, 'listening': True}))\n",
        )
        with self.assertRaisesRegex(RuntimeError, "fixed production contract"):
            repair.Operations._bridge_check_config([str(wrong)], {}, cwd=self.root)

        failed = self._executable("check-failed", "raise SystemExit(7)\n")
        with self.assertRaisesRegex(RuntimeError, "check-config failed"):
            repair.Operations._bridge_check_config([str(failed)], {}, cwd=self.root)

        timed_out = self._executable("check-timeout", "import time\ntime.sleep(10)\n")
        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            repair.Operations._bridge_check_config([str(timed_out)], {}, cwd=self.root)
        self.assertLess(time.monotonic() - started, 4.5)

    def test_preflight_bridge_failure_stops_verified_side_hook_before_return(self):
        hook_socket = self.root / "side-hook.sock"
        process = type("P", (), {"pid": 71})()
        identity = {"pid": 71, "argv": ["python", "hook"]}
        live = {71}
        listener = None

        def wait_hook(_process, _layout):
            nonlocal listener
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(hook_socket))
            hook_socket.chmod(0o600)
            self.addCleanup(listener.close)
            return pc._runtime_hook_socket_identity(hook_socket)

        def stop(verified, **_kwargs):
            live.remove(verified["pid"])

        ops = repair.Operations()
        ops.identity_matches = lambda verified: verified["pid"] in live
        with patch.object(pc, "_spawn_logged", return_value=process), patch.object(
            pc, "_capture_process_identity", return_value=identity
        ), patch.object(
            pc, "_wait_for_runtime_hook", side_effect=wait_hook
        ), patch.object(
            ops, "_smoke_hook_fail_closed"
        ), patch.object(
            ops, "_bridge_check_config", side_effect=RuntimeError("injected check failure")
        ), patch.object(
            pc, "_stop_verified_process_identity", side_effect=stop
        ):
            with self.assertRaisesRegex(repair.PairStartError, "injected check failure") as raised:
                ops.preflight_pair(
                    ["bridge"],
                    ["python", "hook", "--serve-unix-socket", str(hook_socket)],
                    {"LAB_PRODUCTION_WORKSPACE": str(self.root)},
                    {},
                    {"hook_socket": hook_socket, "logs": self.root},
                    "side-preflight",
                )
        self.assertTrue(raised.exception.cleanup_complete)
        self.assertEqual(live, set())
        self.assertFalse(hook_socket.exists())

    def test_real_hook_fail_closed_smoke_uses_unix_socket_protocol(self):
        path = self.root / "smoke.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        listener.listen(1)
        path.chmod(0o600)

        def serve():
            connection, _ = listener.accept()
            with connection:
                request = json.loads(connection.recv(4096).split(b"\n", 1)[0])
                connection.sendall(
                    json.dumps(
                        {
                            "request_id": request["request_id"],
                            "ok": False,
                            "error": "Runtime Host hook protocol is unsupported",
                        }
                    ).encode("utf-8")
                    + b"\n"
                )

        thread = threading.Thread(target=serve)
        thread.start()
        try:
            repair.Operations._smoke_hook_fail_closed(path)
        finally:
            thread.join(timeout=2)
            listener.close()
        self.assertFalse(thread.is_alive())

    def test_real_operations_retries_hook_stop_then_reports_fully_stopped_for_restore(self):
        bridge_socket = self._listener("bridge.sock")
        hook_socket = self._listener("hook.sock")
        bridge = {"pid": 10}
        hook = {"pid": 11}
        live = {10, 11}
        attempts = []

        def stop(identity, **_kwargs):
            attempts.append(identity["pid"])
            if identity["pid"] == 11 and attempts.count(11) == 1:
                raise RuntimeError("injected first hook stop failure")
            live.remove(identity["pid"])

        ops = repair.Operations()
        ops.identity_matches = lambda identity: identity["pid"] in live
        paths = {
            "bridge_socket": bridge_socket,
            "hook_socket": hook_socket,
            "bridge_socket_identity": pc._bridge_socket_identity(bridge_socket),
            "hook_socket_identity": pc._runtime_hook_socket_identity(hook_socket),
        }
        with patch.object(pc, "_stop_verified_process_identity", side_effect=stop):
            with self.assertRaises(repair.PairStopError) as raised:
                ops.stop_pair(bridge, hook, paths)
        self.assertTrue(raised.exception.fully_stopped)
        self.assertEqual(attempts, [10, 11, 11])
        self.assertEqual(live, set())
        self.assertFalse(bridge_socket.exists())
        self.assertFalse(hook_socket.exists())

    def test_real_operations_keeps_sockets_when_hook_survives_bounded_stop(self):
        bridge_socket = self._listener("bridge-live.sock")
        hook_socket = self._listener("hook-live.sock")
        bridge = {"pid": 30}
        hook = {"pid": 31}
        live = {30, 31}

        def stop(identity, **_kwargs):
            if identity["pid"] == 31:
                raise RuntimeError("injected persistent hook stop failure")
            live.remove(identity["pid"])

        ops = repair.Operations()
        ops.identity_matches = lambda identity: identity["pid"] in live
        paths = {
            "bridge_socket": bridge_socket,
            "hook_socket": hook_socket,
            "bridge_socket_identity": pc._bridge_socket_identity(bridge_socket),
            "hook_socket_identity": pc._runtime_hook_socket_identity(hook_socket),
        }
        with patch.object(pc, "_stop_verified_process_identity", side_effect=stop):
            with self.assertRaises(repair.PairStopError) as raised:
                ops.stop_pair(bridge, hook, paths)
        self.assertFalse(raised.exception.fully_stopped)
        self.assertEqual(live, {31})
        self.assertTrue(bridge_socket.exists())
        self.assertTrue(hook_socket.exists())

    def test_real_operations_start_failure_never_unlinks_unverified_foreign_socket(self):
        bridge_socket = self.root / "bridge.sock"
        hook_socket = self.root / "hook.sock"
        hook_listener = None
        foreign = None
        processes = [type("P", (), {"pid": 20})(), type("P", (), {"pid": 21})()]
        identities = [
            {"pid": 20, "argv": ["python", "hook"]},
            {"pid": 21, "argv": ["bridge"]},
        ]
        live = {20, 21}

        def wait_hook(_process, _layout):
            nonlocal hook_listener
            hook_listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            hook_listener.bind(str(hook_socket))
            hook_socket.chmod(0o600)
            self.addCleanup(hook_listener.close)
            return pc._runtime_hook_socket_identity(hook_socket)

        def wait_bridge(_process, _layout):
            nonlocal foreign
            foreign = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            foreign.bind(str(bridge_socket))
            bridge_socket.chmod(0o600)
            self.addCleanup(foreign.close)
            raise RuntimeError("injected Bridge readiness failure")

        def stop(identity, **_kwargs):
            live.remove(identity["pid"])

        ops = repair.Operations()
        ops.identity_matches = lambda identity: identity["pid"] in live
        with patch.object(pc, "_spawn_logged", side_effect=processes), patch.object(
            pc, "_capture_process_identity", side_effect=identities
        ), patch.object(
            pc, "_wait_for_runtime_hook", side_effect=wait_hook
        ), patch.object(
            ops, "_smoke_hook_fail_closed"
        ), patch.object(
            pc, "_wait_for_bridge", side_effect=wait_bridge
        ), patch.object(
            pc, "_stop_verified_process_identity", side_effect=stop
        ):
            with self.assertRaisesRegex(repair.PairStartError, "readiness failure") as raised:
                ops.start_pair(
                    ["bridge"],
                    ["python", "hook", "--serve-unix-socket", str(hook_socket)],
                    {"LAB_PRODUCTION_WORKSPACE": str(self.root)},
                    {},
                    {"bridge_socket": bridge_socket, "hook_socket": hook_socket, "logs": self.root},
                    "fault",
                )
        self.assertFalse(raised.exception.cleanup_complete)
        self.assertTrue(bridge_socket.exists())
        self.assertFalse(hook_socket.exists())
        self.assertEqual(live, set())

    def test_real_operations_start_failure_with_spawned_unknown_process_is_incomplete(self):
        bridge_socket = self.root / "bridge-unknown.sock"
        hook_socket = self.root / "hook-unknown.sock"
        process = type("P", (), {"pid": 40})()

        ops = repair.Operations()
        with patch.object(pc, "_spawn_logged", return_value=process), patch.object(
            pc, "_capture_process_identity", side_effect=RuntimeError("injected identity capture failure")
        ):
            with self.assertRaisesRegex(repair.PairStartError, "identity capture failure") as raised:
                ops.start_pair(
                    ["bridge"],
                    ["python", "hook", "--serve-unix-socket", str(hook_socket)],
                    {"LAB_PRODUCTION_WORKSPACE": str(self.root)},
                    {},
                    {"bridge_socket": bridge_socket, "hook_socket": hook_socket, "logs": self.root},
                    "unknown-process",
                )
        self.assertFalse(raised.exception.cleanup_complete)
        self.assertIn("exact identity was not captured", str(raised.exception))

    def test_real_operations_cleanup_keeps_socket_for_known_live_process(self):
        bridge_socket = self._listener("bridge-cleanup.sock")
        hook_socket = self._listener("hook-cleanup.sock")
        bridge = {"pid": 50}
        hook = {"pid": 51}
        live = {50, 51}

        def stop(identity, **_kwargs):
            if identity["pid"] == 51:
                raise RuntimeError("injected hook stop failure")
            live.remove(identity["pid"])

        ops = repair.Operations()
        ops.identity_matches = lambda identity: identity["pid"] in live
        with patch.object(pc, "_stop_verified_process_identity", side_effect=stop):
            complete, errors = ops._cleanup_started(
                bridge_identity=bridge,
                bridge_process=None,
                bridge_socket=bridge_socket,
                bridge_socket_identity=pc._bridge_socket_identity(bridge_socket),
                hook_identity=hook,
                hook_process=None,
                hook_socket=hook_socket,
                hook_socket_identity=pc._runtime_hook_socket_identity(hook_socket),
            )
        self.assertFalse(complete)
        self.assertTrue(any("Runtime hook remains live" in error for error in errors))
        self.assertFalse(bridge_socket.exists())
        self.assertTrue(hook_socket.exists())
        self.assertEqual(live, {51})

if __name__ == "__main__":
    unittest.main()
