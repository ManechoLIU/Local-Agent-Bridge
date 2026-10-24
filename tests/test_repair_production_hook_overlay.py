import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import production_connector as pc
import repair_production_control as control
import repair_production_hook_overlay as overlay
from tests import test_repair_production_control as control_tests


class FakeOverlayOperations:
    def __init__(self, base):
        self.base = base
        self.events = []
        self.fail_label = None
        self.on_side_smoke = None

    def identity_matches(self, identity):
        return self.base.identity_matches(identity)

    def environment(self, identity):
        return self.base.environment(identity)

    def host_socket_identity(self, path):
        return self.base.host_socket_identity(path)

    def host_socket_is_listening(self, path):
        return self.base.host_socket_is_listening(path)

    def runtime_hook_socket_is_live(self, path, identity):
        return self.base.runtime_hook_socket_is_live(path, identity)

    def start_hook(self, command, environment, socket_path, logs, label):
        Path(logs).mkdir(parents=True, exist_ok=True)
        self.events.append(("start_hook", label, list(command), dict(environment), str(socket_path)))
        if label == "side-smoke" and self.on_side_smoke:
            self.on_side_smoke()
        if self.fail_label == label:
            self.fail_label = None
            raise overlay.HookStartError("fake Hook start failure", cleanup_complete=True)
        self.base.next_pid += 1
        identity = self.base._identity(self.base.next_pid, command)
        self.base.live[identity["pid"]] = copy.deepcopy(identity)
        self.base.environments[identity["pid"]] = dict(environment)
        return {
            "hook_identity": copy.deepcopy(identity),
            "hook_socket_identity": {
                "path": str(socket_path),
                "uid": os.geteuid(),
                "mode": 0o600,
                "inode": identity["pid"],
            },
        }

    def stop_hook(self, identity, socket_path, socket_identity):
        self.events.append(("stop_hook", identity["pid"], str(socket_path), dict(socket_identity)))
        if not self.identity_matches(identity):
            raise overlay.HookStopError("fake Hook identity changed", fully_stopped=False)
        del self.base.live[identity["pid"]]

    def stop_started_hook(self, started, socket_path):
        self.stop_hook(
            started["hook_identity"], socket_path, started["hook_socket_identity"]
        )


class ProductionHookOverlayTests(unittest.TestCase):
    def setUp(self):
        fixture = control_tests.ProductionControlComponentTests(
            methodName="test_stage_seals_paired_artifacts_with_independent_commit_provenance"
        )
        fixture.setUp()
        self.fixture = fixture
        self.addCleanup(fixture.doCleanups)
        subprocess.run(["git", "init", "-q", str(fixture.aar_repo)], check=True)
        for relative, payload in fixture.aar_files.items():
            target = fixture.aar_repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        subprocess.run(["git", "-C", str(fixture.aar_repo), "add", "."], check=True)
        subprocess.run(
            [
                "git", "-C", str(fixture.aar_repo),
                "-c", "user.name=Base Test", "-c", "user.email=base@test.invalid",
                "commit", "-q", "-m", "old installed base",
            ],
            check=True,
        )
        fixture.AAR_COMMIT = subprocess.check_output(
            ["git", "-C", str(fixture.aar_repo), "rev-parse", "HEAD"], text=True
        ).strip()
        fixture.aar_manifest_raw = json.dumps(
            {
                "schema_version": 1,
                "revision": fixture.AAR_COMMIT,
                "canonical_release_source": None,
                "files": fixture.aar_file_hashes,
            },
            sort_keys=True,
        ).encode("utf-8")
        (fixture.aar_skill_root / control.AAR_INSTALL_MANIFEST).write_bytes(
            fixture.aar_manifest_raw
        )
        fixture.state.update(
            {
                "tunnel_pid": 400,
                "tunnel_id": "fixture-tunnel",
                "tunnel_profile_sha256": "f" * 64,
                "tunnel_process_identity": fixture._identity(
                    400, [str(fixture.current_bridge), "fake-tunnel"]
                ),
            }
        )
        fixture._write(fixture.state_path, fixture.state)
        self.control_patch = fixture.stage()
        control.execute_patch(fixture.root, self.control_patch, ops=fixture.ops)
        self.ops = FakeOverlayOperations(fixture.ops)
        self.tunnel_status_patch = patch.object(
            pc,
            "production_tunnel_status",
            side_effect=self.canonical_tunnel_status,
        )
        self.tunnel_status_patch.start()
        self.addCleanup(self.tunnel_status_patch.stop)
        self.overlay_repo = Path(fixture.temp.name) / "overlay-source"
        self.overlay_repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.overlay_repo)], check=True)
        source = self.overlay_repo / overlay.OVERLAY_RELATIVE
        source.parent.mkdir(parents=True)
        source.write_text(
            "# exact overlay fixture\n"
            "import control_event_guard\n"
            "EXPECTED_GUARD = control_event_guard.__file__\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "-C", str(self.overlay_repo), "add", "."], check=True)
        subprocess.run(
            [
                "git", "-C", str(self.overlay_repo),
                "-c", "user.name=Overlay Test", "-c", "user.email=overlay@test.invalid",
                "commit", "-q", "-m", "overlay fixture",
            ],
            check=True,
        )
        self.overlay_commit = subprocess.check_output(
            ["git", "-C", str(self.overlay_repo), "rev-parse", "HEAD"], text=True
        ).strip()
        self.ops.events.clear()

    def stage(self):
        return overlay.stage_patch(
            self.fixture.root,
            aar_repo=self.overlay_repo,
            aar_commit=self.overlay_commit,
            ops=self.ops,
        )

    def install_descendant_base(self, *, repo=None, label="new"):
        repo = Path(repo or self.fixture.aar_repo)
        guard = repo / "scripts/control_event_guard.py"
        guard.write_bytes(guard.read_bytes() + f"# {label} base\n".encode())
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(
            [
                "git", "-C", str(repo),
                "-c", "user.name=Base Test", "-c", "user.email=base@test.invalid",
                "commit", "-q", "-m", f"{label} installed base",
            ],
            check=True,
        )
        revision = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        installed = Path(self.fixture.temp.name) / f"installed-{label}"
        installed.mkdir(mode=0o700)
        files = {}
        listed = subprocess.check_output(
            ["git", "-C", str(repo), "ls-files", "-z"]
        ).split(b"\0")
        for raw_relative in listed:
            if not raw_relative:
                continue
            relative = raw_relative.decode("utf-8")
            payload = subprocess.check_output(
                ["git", "-C", str(repo), "show", f"{revision}:{relative}"]
            )
            target = installed / relative
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.write_bytes(payload)
            files[relative] = hashlib.sha256(payload).hexdigest()
        manifest = {
            "schema_version": 2,
            "revision": revision,
            "source_root": str(repo.resolve()),
            "previous_revision": self.fixture.AAR_COMMIT,
            "upgrade_lineage": {
                "previous_revision": self.fixture.AAR_COMMIT,
                "revision": revision,
                "status": "linear",
            },
            "files": files,
        }
        install_manifest = installed / control.AAR_INSTALL_MANIFEST
        install_manifest.write_text(
            json.dumps(manifest, sort_keys=True), encoding="utf-8"
        )
        install_manifest.chmod(0o600)
        return installed, repo, revision

    def canonical_tunnel_status(self, _manifest, _layout):
        state = json.loads(self.fixture.state_path.read_text())
        return {
            "ready": True,
            "pid": state["tunnel_pid"],
            "reason": None,
            "generation": state["endpoint_generation"],
            "tunnel_id": state.get("tunnel_id", "fixture-tunnel"),
            "profile_sha256": state.get("tunnel_profile_sha256", "f" * 64),
            "operational": {"verified": True},
        }

    def stage_upgrade(self, installed, repo, revision, *, runtime_sha256=None):
        if runtime_sha256 is None:
            runtime_sha256 = hashlib.sha256(
                self.fixture.state_path.read_bytes()
            ).hexdigest()
        return overlay.stage_patch(
            self.fixture.root,
            installed_base=installed,
            base_repo=repo,
            base_commit=revision,
            reconcile_runtime_sha256=runtime_sha256,
            ops=self.ops,
        )

    def test_stage_copies_exact_sealed_base_and_records_overlay_blob_without_runtime_write(self):
        before = self.fixture.state_path.read_bytes()
        staged = self.stage()
        self.assertEqual(self.fixture.state_path.read_bytes(), before)
        self.assertEqual(self.ops.events, [])
        record = json.loads((staged / "manifest.json").read_text())
        control_record = json.loads((self.control_patch / "manifest.json").read_text())
        self.assertEqual(record["overlay"]["aar_commit"], self.overlay_commit)
        self.assertEqual(
            record["overlay"]["sha256"],
            pc._sha256_file(staged / "overlay" / "runtime_host_tool_hook.py"),
        )
        self.assertEqual(
            record["base"]["files"],
            {
                key.removeprefix("runtime-host-tool-hook/"): value
                for key, value in control_record["artifact_hashes"].items()
                if key.startswith("runtime-host-tool-hook/")
            },
        )
        self.assertIn(control.AAR_INSTALL_MANIFEST, record["base"]["files"])
        self.assertEqual((staged / overlay.LAUNCHER_NAME).read_bytes(), overlay.LAUNCHER_BYTES)
        for item in (staged, *staged.rglob("*")):
            self.assertEqual(stat.S_IMODE(item.lstat().st_mode) & 0o222, 0)

    def test_apply_check_and_rollback_replace_only_hook_and_preserve_control_patch(self):
        staged = self.stage()
        before = json.loads(self.fixture.state_path.read_text())
        bridge_identity = copy.deepcopy(before["bridge_process_identity"])
        control_provenance = copy.deepcopy(before[control.PROVENANCE])
        result = overlay.execute_patch(self.fixture.root, staged, ops=self.ops)
        applied = json.loads(self.fixture.state_path.read_text())
        self.assertTrue(result["ok"])
        self.assertEqual(applied["bridge_process_identity"], bridge_identity)
        self.assertEqual(applied[control.PROVENANCE], control_provenance)
        self.assertIn(overlay.PROVENANCE, applied)
        self.assertEqual(
            applied["runtime_hook_process_identity"]["argv"][1],
            str(staged / overlay.LAUNCHER_NAME),
        )
        checked = overlay.check(self.fixture.root, patch=staged, ops=self.ops)
        self.assertTrue(checked["active"])
        self.assertEqual(
            checked["canonical_guard"],
            str(staged / "base-runtime-host-tool-hook/scripts/control_event_guard.py"),
        )
        rolled = overlay.execute_patch(
            self.fixture.root, staged, rollback=True, ops=self.ops
        )
        restored = json.loads(self.fixture.state_path.read_text())
        self.assertTrue(rolled["ok"])
        self.assertNotIn(overlay.PROVENANCE, restored)
        self.assertEqual(restored["bridge_process_identity"], bridge_identity)
        self.assertEqual(restored[control.PROVENANCE], control_provenance)
        self.assertEqual(
            restored["runtime_hook_process_identity"]["argv"][1],
            str(self.control_patch / "runtime-host-tool-hook" / pc.RUNTIME_HOST_TOOL_HOOK_ENTRYPOINT_RELATIVE_PATH),
        )
        self.assertNotEqual(restored["runtime_hook_pid"], before["runtime_hook_pid"])
        self.assertTrue(all(event[0] in {"start_hook", "stop_hook"} for event in self.ops.events))

    def test_side_smoke_failure_stops_no_canonical_hook_or_bridge(self):
        staged = self.stage()
        before = json.loads(self.fixture.state_path.read_text())
        self.ops.fail_label = "side-smoke"
        with self.assertRaisesRegex(overlay.HookStartError, "fake Hook start failure"):
            overlay.execute_patch(self.fixture.root, staged, ops=self.ops)
        after = json.loads(self.fixture.state_path.read_text())
        self.assertEqual(after, before)
        canonical_stops = [
            event for event in self.ops.events
            if event[0] == "stop_hook" and event[2] == str(self.fixture.hook_socket)
        ]
        self.assertEqual(canonical_stops, [])
        self.assertTrue(self.ops.identity_matches(before["bridge_process_identity"]))
        self.assertTrue(self.ops.identity_matches(before["runtime_hook_process_identity"]))

    def test_side_smoke_uses_short_private_tmp_socket_path(self):
        staged = self.stage()
        overlay.execute_patch(self.fixture.root, staged, ops=self.ops)
        side = next(event for event in self.ops.events if event[:2] == ("start_hook", "side-smoke"))
        socket_path = Path(side[4])
        self.assertEqual(socket_path.parent.parent, Path("/tmp"))
        self.assertTrue(socket_path.parent.name.startswith("lab-ho-"))
        self.assertLess(len(os.fsencode(socket_path)), 104)

    def test_runtime_cas_drift_after_side_smoke_refuses_before_canonical_stop(self):
        staged = self.stage()
        before = json.loads(self.fixture.state_path.read_text())

        def drift():
            self.fixture._write(self.fixture.state_path, {**before, "concurrent": True})

        self.ops.on_side_smoke = drift
        with self.assertRaisesRegex(RuntimeError, "runtime bytes changed"):
            overlay.execute_patch(self.fixture.root, staged, ops=self.ops)
        self.assertFalse(
            any(
                event[0] == "stop_hook" and event[2] == str(self.fixture.hook_socket)
                for event in self.ops.events
            )
        )
        self.assertTrue(self.ops.identity_matches(before["bridge_process_identity"]))
        self.assertTrue(self.ops.identity_matches(before["runtime_hook_process_identity"]))

    def test_failed_canonical_overlay_start_restores_base_hook_with_fresh_pid(self):
        staged = self.stage()
        before = json.loads(self.fixture.state_path.read_text())
        self.ops.fail_label = "apply"
        with self.assertRaisesRegex(RuntimeError, "prior Runtime hook restored"):
            overlay.execute_patch(self.fixture.root, staged, ops=self.ops)
        restored = json.loads(self.fixture.state_path.read_text())
        self.assertEqual(
            restored["bridge_process_identity"], before["bridge_process_identity"]
        )
        self.assertNotEqual(restored["runtime_hook_pid"], before["runtime_hook_pid"])
        self.assertNotIn(overlay.PROVENANCE, restored)
        self.assertEqual(
            restored["runtime_hook_process_identity"]["argv"][1],
            str(self.control_patch / "runtime-host-tool-hook" / pc.RUNTIME_HOST_TOOL_HOOK_ENTRYPOINT_RELATIVE_PATH),
        )

    def test_tampered_overlay_or_base_fails_before_process_operation(self):
        for relative in (
            Path("overlay/runtime_host_tool_hook.py"),
            Path("base-runtime-host-tool-hook/scripts/control_event_guard.py"),
        ):
            with self.subTest(relative=relative):
                staged = self.stage()
                target = staged / relative
                target.chmod(0o600)
                target.write_bytes(target.read_bytes() + b"# tampered\n")
                target.chmod(0o400)
                events_before = list(self.ops.events)
                with self.assertRaisesRegex(RuntimeError, "hash mismatch"):
                    overlay.execute_patch(self.fixture.root, staged, ops=self.ops)
                self.assertEqual(self.ops.events, events_before)

    def test_overlay_and_self_reported_sha_tamper_still_fails_git_source_provenance(self):
        staged = self.stage()
        source = staged / "overlay" / "runtime_host_tool_hook.py"
        manifest_path = staged / "manifest.json"
        source.chmod(0o600)
        source.write_bytes(source.read_bytes() + b"# synchronized tamper\n")
        source.chmod(0o400)
        manifest_path.chmod(0o600)
        record = json.loads(manifest_path.read_text())
        record["overlay"]["sha256"] = pc._sha256_file(source)
        manifest_path.write_text(json.dumps(record), encoding="utf-8")
        manifest_path.chmod(0o400)
        events_before = list(self.ops.events)
        with self.assertRaisesRegex(RuntimeError, "Git source provenance mismatch"):
            overlay.execute_patch(self.fixture.root, staged, ops=self.ops)
        self.assertEqual(self.ops.events, events_before)

    def test_overlay_extra_file_is_rejected_before_process_operation(self):
        staged = self.stage()
        overlay_dir = staged / "overlay"
        overlay_dir.chmod(0o700)
        extra = overlay_dir / "extra.py"
        extra.write_text("# unlisted\n", encoding="utf-8")
        extra.chmod(0o400)
        overlay_dir.chmod(0o500)
        events_before = list(self.ops.events)
        with self.assertRaisesRegex(RuntimeError, "source tree is not exact"):
            overlay.execute_patch(self.fixture.root, staged, ops=self.ops)
        self.assertEqual(self.ops.events, events_before)

    def test_wrong_active_overlay_provenance_refuses_rollback_without_stop(self):
        staged = self.stage()
        overlay.execute_patch(self.fixture.root, staged, ops=self.ops)
        state = json.loads(self.fixture.state_path.read_text())
        state[overlay.PROVENANCE]["overlay_sha256"] = "0" * 64
        self.fixture._write(self.fixture.state_path, state)
        events_before = list(self.ops.events)
        with self.assertRaisesRegex(RuntimeError, "provenance mismatch"):
            overlay.execute_patch(
                self.fixture.root, staged, rollback=True, ops=self.ops
            )
        self.assertEqual(self.ops.events, events_before)

    def test_upgrade_switches_to_exact_installed_base_and_rolls_back_to_prior_overlay(self):
        prior_patch = self.stage()
        overlay.execute_patch(self.fixture.root, prior_patch, ops=self.ops)
        prior_state = json.loads(self.fixture.state_path.read_text())
        prior_provenance = copy.deepcopy(prior_state[overlay.PROVENANCE])
        bridge_identity = copy.deepcopy(prior_state["bridge_process_identity"])
        reconciled = copy.deepcopy(prior_state)
        reconciled["tunnel_pid"] = 44444
        reconciled["tunnel_process_identity"] = {
            **reconciled["tunnel_process_identity"],
            "pid": 44444,
            "pgid": 44444,
            "lstart": "reconciled-tunnel",
            "start_timestamp": 44444.0,
        }
        self.fixture._write(self.fixture.state_path, reconciled)
        repo = Path(self.fixture.temp.name) / "isolated-base-candidate"
        subprocess.run(
            ["git", "clone", "-q", str(self.fixture.aar_repo), str(repo)], check=True
        )
        installed, repo, revision = self.install_descendant_base(repo=repo)
        derived_cache = installed / "scripts/__pycache__/control_event_guard.cpython-311.pyc"
        derived_cache.parent.mkdir(parents=True)
        derived_cache.write_bytes(b"derived cache is not sealed source")
        runtime_before_stage = self.fixture.state_path.read_bytes()

        upgrade = self.stage_upgrade(installed, repo, revision)

        self.assertEqual(self.fixture.state_path.read_bytes(), runtime_before_stage)
        record = json.loads((upgrade / "manifest.json").read_text())
        prior_record = json.loads((prior_patch / "manifest.json").read_text())
        self.assertEqual(record["schema_version"], 2)
        self.assertEqual(record["base"]["aar_revision"], revision)
        self.assertEqual(record["overlay"], prior_record["overlay"])
        self.assertEqual(record["previous_overlay"]["provenance"], prior_provenance)
        self.assertEqual(
            set(record["runtime_reconciliation"]["changed_fields"]),
            {"tunnel_pid", "tunnel_process_identity"},
        )
        self.assertFalse(
            (upgrade / "base-runtime-host-tool-hook/scripts/__pycache__").exists()
        )

        applied = overlay.execute_patch(self.fixture.root, upgrade, ops=self.ops)
        active = json.loads(self.fixture.state_path.read_text())
        self.assertTrue(applied["ok"])
        self.assertEqual(active["bridge_process_identity"], bridge_identity)
        self.assertEqual(
            active["runtime_hook_process_identity"]["argv"][1],
            str(upgrade / overlay.LAUNCHER_NAME),
        )
        checked = overlay.check(self.fixture.root, patch=upgrade, ops=self.ops)
        self.assertEqual(
            checked["canonical_guard"],
            str(upgrade / "base-runtime-host-tool-hook/scripts/control_event_guard.py"),
        )

        rolled = overlay.execute_patch(
            self.fixture.root, upgrade, rollback=True, ops=self.ops
        )
        restored = json.loads(self.fixture.state_path.read_text())
        self.assertTrue(rolled["ok"])
        self.assertEqual(restored[overlay.PROVENANCE], prior_provenance)
        self.assertEqual(restored["bridge_process_identity"], bridge_identity)
        self.assertEqual(
            restored["runtime_hook_process_identity"]["argv"][1],
            str(prior_patch / overlay.LAUNCHER_NAME),
        )
        with self.assertRaisesRegex(RuntimeError, "provenance mismatch"):
            overlay.check(self.fixture.root, ops=self.ops)
        reconciled_check = overlay.check(
            self.fixture.root,
            reconcile_runtime_sha256=hashlib.sha256(
                self.fixture.state_path.read_bytes()
            ).hexdigest(),
            ops=self.ops,
        )
        self.assertTrue(reconciled_check["active"])
        self.assertEqual(
            reconciled_check["canonical_guard"],
            str(prior_patch / "base-runtime-host-tool-hook/scripts/control_event_guard.py"),
        )

    def test_upgrade_reconciliation_requires_exact_runtime_sha_and_only_tunnel_drift(self):
        prior_patch = self.stage()
        overlay.execute_patch(self.fixture.root, prior_patch, ops=self.ops)
        installed, repo, revision = self.install_descendant_base()
        before = json.loads(self.fixture.state_path.read_text())
        events_before = list(self.ops.events)

        with self.assertRaisesRegex(RuntimeError, "reconciliation SHA-256"):
            overlay.stage_patch(
                self.fixture.root,
                installed_base=installed,
                base_repo=repo,
                base_commit=revision,
                ops=self.ops,
            )
        with self.assertRaisesRegex(RuntimeError, "runtime reconciliation SHA-256 mismatch"):
            self.stage_upgrade(installed, repo, revision, runtime_sha256="0" * 64)

        for key, value in (
            ("tools", [*before["tools"], "unauthorized-drift"]),
            ("new_null_field", None),
        ):
            with self.subTest(key=key):
                drifted = {**before, key: value}
                self.fixture._write(self.fixture.state_path, drifted)
                with self.assertRaisesRegex(RuntimeError, "unsupported runtime fields"):
                    self.stage_upgrade(installed, repo, revision)
                self.assertEqual(
                    self.fixture.state_path.read_bytes(), json.dumps(drifted).encode()
                )
        self.assertEqual(self.ops.events, events_before)

    def test_upgrade_rejects_tampered_installed_base_and_foreign_repository_without_write(self):
        prior_patch = self.stage()
        overlay.execute_patch(self.fixture.root, prior_patch, ops=self.ops)
        installed, repo, revision = self.install_descendant_base()
        before = self.fixture.state_path.read_bytes()
        events_before = list(self.ops.events)
        target = installed / "scripts/control_event_guard.py"
        target.write_bytes(target.read_bytes() + b"# installed tamper\n")
        with self.assertRaisesRegex(RuntimeError, "installed file hash mismatch"):
            self.stage_upgrade(installed, repo, revision)
        self.assertEqual(self.fixture.state_path.read_bytes(), before)
        self.assertEqual(self.ops.events, events_before)

        foreign = Path(self.fixture.temp.name) / "foreign-base"
        subprocess.run(["git", "clone", "-q", str(repo), str(foreign)], check=True)
        foreign_git = foreign / ".git"
        # A different repository path is outside the recorded old-base lineage,
        # even when it happens to contain the same object ids.
        target.write_bytes(
            subprocess.check_output(
                ["git", "-C", str(repo), "show", f"{revision}:scripts/control_event_guard.py"]
            )
        )
        with self.assertRaisesRegex(RuntimeError, "source root differs"):
            self.stage_upgrade(installed, foreign, revision)
        self.assertTrue(foreign_git.is_dir())
        self.assertEqual(self.fixture.state_path.read_bytes(), before)
        self.assertEqual(self.ops.events, events_before)

    def test_upgrade_base_tamper_and_runtime_drift_fail_before_canonical_stop(self):
        prior_patch = self.stage()
        overlay.execute_patch(self.fixture.root, prior_patch, ops=self.ops)
        installed, repo, revision = self.install_descendant_base()
        upgrade = self.stage_upgrade(installed, repo, revision)
        base_guard = upgrade / "base-runtime-host-tool-hook/scripts/control_event_guard.py"
        base_guard.chmod(0o600)
        base_guard.write_bytes(base_guard.read_bytes() + b"# sealed tamper\n")
        base_guard.chmod(0o400)
        events_before = list(self.ops.events)
        with self.assertRaisesRegex(RuntimeError, "base tree hash mismatch"):
            overlay.execute_patch(self.fixture.root, upgrade, ops=self.ops)
        self.assertEqual(self.ops.events, events_before)

        # Restage from the still-active prior overlay and inject runtime drift
        # only after the isolated side Hook has passed its smoke check.
        upgrade = self.stage_upgrade(installed, repo, revision)
        before = json.loads(self.fixture.state_path.read_text())

        def drift():
            self.fixture._write(self.fixture.state_path, {**before, "concurrent": True})

        self.ops.on_side_smoke = drift
        with self.assertRaisesRegex(RuntimeError, "runtime bytes changed"):
            overlay.execute_patch(self.fixture.root, upgrade, ops=self.ops)
        canonical_stops = [
            event for event in self.ops.events[len(events_before):]
            if event[0] == "stop_hook" and event[2] == str(self.fixture.hook_socket)
        ]
        self.assertEqual(canonical_stops, [])
        self.assertTrue(self.ops.identity_matches(before["bridge_process_identity"]))
        self.assertTrue(self.ops.identity_matches(before["runtime_hook_process_identity"]))

    def test_upgrade_ignores_git_replace_ref_when_validating_exact_base_commit(self):
        prior_patch = self.stage()
        overlay.execute_patch(self.fixture.root, prior_patch, ops=self.ops)
        installed, repo, revision = self.install_descendant_base()
        guard = repo / "scripts/control_event_guard.py"
        guard.write_bytes(guard.read_bytes() + b"# replacement object bytes\n")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(
            [
                "git", "-C", str(repo),
                "-c", "user.name=Base Test", "-c", "user.email=base@test.invalid",
                "commit", "-q", "-m", "replacement object",
            ],
            check=True,
        )
        replacement = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        subprocess.run(
            ["git", "-C", str(repo), "replace", revision, replacement], check=True
        )
        malicious = subprocess.check_output(
            ["git", "-C", str(repo), "show", f"{revision}:scripts/control_event_guard.py"]
        )
        target = installed / "scripts/control_event_guard.py"
        target.write_bytes(malicious)
        manifest_path = installed / control.AAR_INSTALL_MANIFEST
        manifest_path.chmod(0o600)
        manifest = json.loads(manifest_path.read_text())
        manifest["files"]["scripts/control_event_guard.py"] = hashlib.sha256(
            malicious
        ).hexdigest()
        manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
        manifest_path.chmod(0o600)
        before = self.fixture.state_path.read_bytes()
        events_before = list(self.ops.events)

        with self.assertRaisesRegex(RuntimeError, "manifest differs from exact commit"):
            self.stage_upgrade(installed, repo, revision)

        self.assertEqual(self.fixture.state_path.read_bytes(), before)
        self.assertEqual(self.ops.events, events_before)


if __name__ == "__main__":
    unittest.main()
