import json
import os
from contextlib import contextmanager
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import production_connector as pc
import repair_production_extension as repair


class ProductionExtensionComponentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="extension-patch-")
        self.addCleanup(self.cleanup)
        self.root = Path(self.temp.name).resolve() / "production"
        self.root.mkdir(mode=0o700)
        self.revision = "a" * 40
        self.manifest = {
            "connector_id": pc.CONNECTOR_ID,
            "contract_version": pc.CONTRACT_VERSION,
            "fail_closed": True,
            "workspace": str(pc.canonical_workspace()),
            "release_revision": self.revision,
            "endpoint_generation": 34,
            "tunnel": {"profile_name": pc.TUNNEL_PROFILE_NAME},
        }
        self.layout = pc.production_layout(self.manifest, runtime_root=self.root)
        self.extension = self.layout["host_dir"] / "chrome-host" / "extension"
        self.extension.mkdir(parents=True, mode=0o700)
        members = set(pc.RUNTIME_EXTENSION_EXECUTION_RELATIVE_PATHS) | {
            "foreground-transaction.js"
        }
        for relative in sorted(members):
            path = self.extension / relative
            if relative == "manifest.json":
                path.write_text(json.dumps({"version": "0.1.2", "key": "test-key"}), encoding="utf-8")
            else:
                path.write_text(f"// source {relative}\n", encoding="utf-8")
            path.chmod(0o400)
        for directory in reversed([item for item in self.extension.rglob("*") if item.is_dir()]):
            directory.chmod(0o500)
        self.extension.chmod(0o500)
        self.manifest["artifacts_sha256"] = {
            "host/chrome-host/extension/" + str(path.relative_to(self.extension)):
                pc._sha256_file(path)
            for path in self.extension.rglob("*")
            if path.is_file()
        }
        self._write_json(self.layout["release_manifest"], self.manifest)
        self._write_json(
            self.root / "runtime.json",
            {
                "release_revision": "b" * 40,
                "endpoint_generation": 35,
                "workspace": self.manifest["workspace"],
                "tunnel_profile_name": pc.TUNNEL_PROFILE_NAME,
                "reused_live_backends": {
                    "source_release_revision": self.revision,
                    "source_endpoint_generation": 34,
                    "release_manifest": str(self.layout["release_manifest"]),
                },
                "host_component_patch": {"must": "remain"},
            },
        )
        self.candidate = Path(self.temp.name) / "submit-page.js"
        self.candidate.write_text("// reviewed DOM compatibility patch\n", encoding="utf-8")
        self.candidate.chmod(0o600)
        self.source_sha = pc._sha256_file(self.extension / repair.PATCHED_RELATIVE)
        self.candidate_sha = pc._sha256_file(self.candidate)
        self.validation_mocks = [
            patch.object(pc, "_validate_manifest_contract"),
            patch.object(pc, "validate_installed_release", return_value=self.manifest),
        ]
        for mock in self.validation_mocks:
            mock.start()
            self.addCleanup(mock.stop)

    def cleanup(self):
        root = Path(self.temp.name)
        if root.exists():
            for item in sorted(root.rglob("*"), key=lambda path: len(path.parts), reverse=True):
                try:
                    item.chmod(0o700 if item.is_dir() else 0o600)
                except OSError:
                    pass
        self.temp.cleanup()

    @staticmethod
    def _write_json(path: Path, value) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(json.dumps(value), encoding="utf-8")
        path.chmod(0o600)

    def _stage(self) -> Path:
        return repair.stage_patch(
            self.root,
            self.candidate,
            expected_source_sha256=self.source_sha,
            expected_candidate_sha256=self.candidate_sha,
        )

    def _pin_overlay_commit(self, commit: str) -> None:
        patcher = patch.object(pc, "RUNTIME_EXTENSION_REGISTRY_OVERLAY_GIT_COMMIT", commit)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _registry_git(
        self, payload: bytes | None = None, background: bytes | None = None
    ) -> dict[str, object]:
        payload = payload or b"// reviewed current-entry-registry overlay\n"
        background_payload = background or b"// reviewed background overlay\n"
        repo = Path(self.temp.name) / f"registry-git-{os.urandom(8).hex()}"
        relative = Path(pc.RUNTIME_EXTENSION_REGISTRY_OVERLAY_GIT_PATH)
        background_relative = Path(pc.RUNTIME_EXTENSION_REGISTRY_OVERLAY_BACKGROUND_GIT_PATH)
        tracked = repo / relative
        tracked.parent.mkdir(parents=True, exist_ok=True)
        tracked.write_bytes(payload)
        tracked.chmod(0o600)
        background_tracked = repo / background_relative
        background_tracked.parent.mkdir(parents=True, exist_ok=True)
        background_tracked.write_bytes(background_payload)
        background_tracked.chmod(0o600)
        env = os.environ.copy()
        env.update(
            {
                "GIT_AUTHOR_NAME": "overlay-test",
                "GIT_AUTHOR_EMAIL": "overlay-test@example.com",
                "GIT_COMMITTER_NAME": "overlay-test",
                "GIT_COMMITTER_EMAIL": "overlay-test@example.com",
                "GIT_TERMINAL_PROMPT": "0",
            }
        )
        subprocess.run(
            ["git", "init", str(repo)],
            check=True,
            capture_output=True,
            env=env,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "add",
                relative.as_posix(),
                background_relative.as_posix(),
            ],
            check=True,
            capture_output=True,
            env=env,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.name=overlay-test",
                "-c",
                "user.email=overlay-test@example.com",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-m",
                "registry overlay candidate",
            ],
            check=True,
            capture_output=True,
            env=env,
        )
        commit = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        ).stdout.strip()
        blob = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", f"HEAD:{relative.as_posix()}"],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        ).stdout.strip()
        background_blob = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "rev-parse",
                f"HEAD:{background_relative.as_posix()}",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        ).stdout.strip()
        candidate = Path(self.temp.name) / f"current-entry-registry-{os.urandom(8).hex()}.js"
        candidate.write_bytes(payload)
        candidate.chmod(0o600)
        background_candidate = Path(self.temp.name) / (
            f"background-{os.urandom(8).hex()}.js"
        )
        background_candidate.write_bytes(background_payload)
        background_candidate.chmod(0o600)
        return {
            "repo": repo,
            "commit": commit,
            "blob": blob,
            "sha256": pc._sha256_file(candidate),
            "payload": payload,
            "candidate": candidate,
            "background_blob": background_blob,
            "background_sha256": pc._sha256_file(background_candidate),
            "background_payload": background_payload,
            "background_candidate": background_candidate,
        }

    def _overlay_kwargs(self, prior: Path, git: dict[str, object]) -> dict[str, object]:
        return {
            "prior_component_manifest": prior / "component-manifest.json",
            "prior_component_manifest_sha256": pc._sha256_file(
                prior / "component-manifest.json"
            ),
            "git_repository": Path(str(git["repo"])),
            "git_commit": str(git["commit"]),
            "git_blob_oid": str(git["blob"]),
            "expected_registry_sha256": str(git["sha256"]),
            "candidate_background": Path(str(git["background_candidate"])),
            "background_git_blob_oid": str(git["background_blob"]),
            "expected_background_sha256": str(git["background_sha256"]),
        }

    def _stage_variant(self, source_text: str) -> Path:
        candidate = Path(self.temp.name) / f"submit-{os.urandom(8).hex()}.js"
        candidate.write_text(source_text, encoding="utf-8")
        candidate.chmod(0o600)
        return repair.stage_patch(
            self.root,
            candidate,
            expected_source_sha256=self.source_sha,
            expected_candidate_sha256=pc._sha256_file(candidate),
        )

    def _bind_component(self, patch_dir: Path) -> dict[str, object]:
        profile = self._profile(patch_dir / "extension")
        chrome = self._chrome()
        binding = pc._build_runtime_extension_binding(
            self.layout,
            self.manifest,
            home_dir=Path(self.temp.name),
            native_user_data_dirs=(profile,),
            browser_executables=(chrome,),
            extension_component_manifest=patch_dir / "component-manifest.json",
        )
        self._write_json(self.layout["host_extension_binding"], binding)
        return binding

    def _stage_overlay(
        self, prior: Path | None = None, git: dict[str, object] | None = None
    ) -> tuple[Path, Path, dict[str, object]]:
        prior = prior or self._stage()
        self._bind_component(prior)
        git = git or self._registry_git()
        self._pin_overlay_commit(str(git["commit"]))
        overlay = repair.stage_registry_overlay(
            self.root,
            Path(str(git["candidate"])),
            **self._overlay_kwargs(prior, git),
        )
        return overlay, prior, git

    def _profile(self, extension_dir: Path) -> Path:
        profile = Path(self.temp.name) / "chrome-profile"
        preferences = profile / "Default" / "Secure Preferences"
        self._write_json(
            preferences,
            {
                "extensions": {
                    "settings": {
                        pc.HOST_EXTENSION_ID: {"path": str(extension_dir.resolve(strict=True))}
                    }
                }
            },
        )
        return profile

    def _chrome(self) -> Path:
        chrome = Path(self.temp.name) / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome"
        chrome.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        chrome.write_bytes(b"fake chrome executable")
        chrome.chmod(0o700)
        return chrome

    def test_check_is_read_only_and_stage_seals_full_tree_with_one_changed_file(self):
        runtime_before = (self.root / "runtime.json").read_bytes()
        checked = repair.check(
            self.root,
            candidate=self.candidate,
            expected_source_sha256=self.source_sha,
            expected_candidate_sha256=self.candidate_sha,
        )
        self.assertTrue(checked["ok"])
        self.assertFalse(checked["production_written"])
        self.assertFalse((self.root / repair.PATCH_ROOT_NAME).exists())
        patch_dir = self._stage()
        self.assertEqual((self.root / "runtime.json").read_bytes(), runtime_before)
        record = json.loads((patch_dir / "component-manifest.json").read_text())
        self.assertEqual(record["changed_relative_paths"], [repair.PATCHED_RELATIVE])
        self.assertEqual(set(record["source_hashes"]), set(record["artifact_hashes"]))
        self.assertEqual(
            record["artifact_hashes"][repair.PATCHED_RELATIVE], self.candidate_sha
        )
        self.assertEqual(
            record["source_hashes"][repair.PATCHED_RELATIVE], self.source_sha
        )
        for item in (patch_dir, *patch_dir.rglob("*")):
            self.assertEqual(stat.S_IMODE(item.lstat().st_mode) & 0o222, 0)
        verified, _ = repair.verify_patch(repair.load_context(self.root), patch_dir)
        self.assertEqual(verified, patch_dir)

    def test_explicit_candidate_hash_mismatch_is_rejected_before_staging(self):
        with self.assertRaisesRegex(RuntimeError, "candidate.*hash mismatch"):
            repair.stage_patch(
                self.root,
                self.candidate,
                expected_source_sha256=self.source_sha,
                expected_candidate_sha256="0" * 64,
            )
        patch_root = self.root / repair.PATCH_ROOT_NAME
        self.assertFalse(patch_root.exists())

    def test_unallowlisted_component_tamper_is_rejected(self):
        patch_dir = self._stage()
        other = patch_dir / "extension" / "background.js"
        other.chmod(0o600)
        other.write_text("// unallowlisted tamper\n", encoding="utf-8")
        other.chmod(0o400)
        with self.assertRaisesRegex(RuntimeError, "component tree hash mismatch"):
            repair.verify_patch(repair.load_context(self.root), patch_dir)

    def test_component_aware_binding_keeps_v1_schema_and_validates_provenance(self):
        patch_dir = self._stage()
        profile = self._profile(patch_dir / "extension")
        chrome = self._chrome()
        binding = pc._build_runtime_extension_binding(
            self.layout,
            self.manifest,
            home_dir=Path(self.temp.name),
            native_user_data_dirs=(profile,),
            browser_executables=(chrome,),
            extension_component_manifest=patch_dir / "component-manifest.json",
        )
        self.assertEqual(binding["schema_version"], "runtime_host_extension_binding_v1")
        self.assertIn("component_patch", binding)
        self._write_json(self.layout["host_extension_binding"], binding)
        validated = pc._validate_runtime_extension_binding(self.layout, self.manifest)
        self.assertEqual(validated, binding)
        self.assertEqual(
            set(validated["bundle_sha256"]),
            {
                str((patch_dir / "extension" / relative).resolve())
                for relative in pc.RUNTIME_EXTENSION_EXECUTION_RELATIVE_PATHS
            },
        )

    def test_rebind_uses_existing_transactional_installer_without_process_actions(self):
        patch_dir = self._stage()
        profile = self._profile(patch_dir / "extension")
        chrome = self._chrome()
        context = repair.load_context(self.root)
        _, provenance = repair.verify_patch(context, patch_dir)
        runtime_before = (self.root / "runtime.json").read_bytes()
        lock_state = {"held": False}

        @contextmanager
        def held_lock(_runtime_root):
            self.assertFalse(lock_state["held"])
            lock_state["held"] = True
            try:
                yield
            finally:
                lock_state["held"] = False

        def install_while_locked(*_args, **_kwargs):
            self.assertTrue(lock_state["held"])
            return {"custom": Path(self.temp.name) / "native.json"}

        with patch.object(
            pc,
            "_install_native_messaging_manifests_unlocked",
            side_effect=install_while_locked,
        ) as install, patch.object(
            pc,
            "_validate_runtime_extension_binding",
            return_value={"component_patch": provenance},
        ), patch.object(
            repair,
            "_bounded_install_lock",
            side_effect=held_lock,
        ):
            result = repair.rebind(
                self.root,
                patch_dir,
                home_dir=Path(self.temp.name),
                native_user_data_dirs=(profile,),
                browser_executables=(chrome,),
            )
        self.assertTrue(result["ok"])
        self.assertFalse(result["processes_restarted"])
        self.assertEqual((self.root / "runtime.json").read_bytes(), runtime_before)
        self.assertEqual(
            install.call_args.kwargs["extension_component_manifest"],
            patch_dir / "component-manifest.json",
        )
        self.assertFalse(lock_state["held"])

    def test_rebind_runtime_drift_after_profile_check_performs_no_publication(self):
        patch_dir = self._stage()
        profile = self._profile(patch_dir / "extension")
        chrome = self._chrome()

        def drift_after_context(_profiles):
            runtime_path = self.root / "runtime.json"
            state = json.loads(runtime_path.read_text(encoding="utf-8"))
            state["concurrent_runtime_switch"] = True
            self._write_json(runtime_path, state)
            return {(patch_dir / "extension").resolve(strict=True)}

        with patch.object(
            repair,
            "_configured_extension_dirs",
            side_effect=drift_after_context,
        ), patch.object(
            pc,
            "_install_native_messaging_manifests_unlocked",
        ) as install:
            with self.assertRaisesRegex(RuntimeError, "runtime changed before"):
                repair.rebind(
                    self.root,
                    patch_dir,
                    home_dir=Path(self.temp.name),
                    native_user_data_dirs=(profile,),
                    browser_executables=(chrome,),
                )
        install.assert_not_called()
        self.assertFalse(self.layout["host_extension_binding"].exists())
        self.assertFalse(
            pc._runtime_verifier_config_path(Path(self.temp.name)).exists()
        )

    def test_rollback_requires_release_path_and_removes_component_binding(self):
        patch_dir = self._stage()
        context = repair.load_context(self.root)
        _, provenance = repair.verify_patch(context, patch_dir)
        self._write_json(
            self.layout["host_extension_binding"],
            {"component_patch": provenance},
        )
        profile = self._profile(self.extension)
        chrome = self._chrome()
        with patch.object(
            pc,
            "_install_native_messaging_manifests_unlocked",
            return_value={"custom": Path(self.temp.name) / "native.json"},
        ) as install, patch.object(
            pc,
            "_validate_runtime_extension_binding",
            return_value={"schema_version": "runtime_host_extension_binding_v1"},
        ):
            result = repair.rollback(
                self.root,
                patch_dir,
                home_dir=Path(self.temp.name),
                native_user_data_dirs=(profile,),
                browser_executables=(chrome,),
            )
        self.assertTrue(result["ok"])
        self.assertEqual(
            install.call_args.kwargs["rollback_extension_component_manifest"],
            patch_dir / "component-manifest.json",
        )

    def test_registry_overlay_check_is_read_only_and_preserves_submit_page(self):
        prior = self._stage()
        git = self._registry_git()
        self._pin_overlay_commit(str(git["commit"]))
        runtime_before = (self.root / "runtime.json").read_bytes()
        checked = repair.check(
            self.root,
            candidate=Path(str(git["candidate"])),
            **self._overlay_kwargs(prior, git),
        )
        self.assertTrue(checked["ok"])
        self.assertFalse(checked["production_written"])
        overlay, _, _ = self._stage_overlay(prior=prior, git=git)
        self.assertEqual((self.root / "runtime.json").read_bytes(), runtime_before)
        self.assertEqual(
            (overlay / "extension" / repair.PATCHED_RELATIVE).read_bytes(),
            (prior / "extension" / repair.PATCHED_RELATIVE).read_bytes(),
        )
        self.assertEqual(
            pc._sha256_file(overlay / "extension" / repair.REGISTRY_RELATIVE),
            git["sha256"],
        )
        self.assertEqual(
            pc._sha256_file(overlay / "extension" / repair.BACKGROUND_RELATIVE),
            git["background_sha256"],
        )
        self.assertEqual(
            (overlay / "extension" / "trusted-input.js").read_bytes(),
            (self.extension / "trusted-input.js").read_bytes(),
        )
        record = json.loads((overlay / "component-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(record["schema_version"], pc.RUNTIME_EXTENSION_REGISTRY_OVERLAY_SCHEMA)
        self.assertEqual(
            record["changed_relative_paths"],
            list(pc.RUNTIME_EXTENSION_REGISTRY_OVERLAY_ALLOWLIST),
        )
        self.assertEqual(
            record["prior_component_manifest_sha256"],
            pc._sha256_file(prior / "component-manifest.json"),
        )
        verified, provenance = repair.verify_patch(repair.load_context(self.root), overlay)
        self.assertEqual(verified, overlay)
        self.assertEqual(
            provenance["preserved_submit_page_sha256"],
            self.candidate_sha,
        )

    def test_registry_overlay_rejects_submit_page_reverted_to_g34(self):
        overlay, _, _ = self._stage_overlay()
        submit = overlay / "extension" / repair.PATCHED_RELATIVE
        submit.chmod(0o600)
        submit.write_bytes((self.extension / repair.PATCHED_RELATIVE).read_bytes())
        submit.chmod(0o400)
        with self.assertRaisesRegex(
            RuntimeError, "preserve the verified submit-page.js"
        ):
            repair.verify_patch(repair.load_context(self.root), overlay)

    def test_registry_overlay_rejects_third_extra_or_missing_file(self):
        overlay, _, _ = self._stage_overlay()
        third = overlay / "extension" / "observation.js"
        third.chmod(0o600)
        third.write_text("// third difference\n", encoding="utf-8")
        third.chmod(0o400)
        with self.assertRaisesRegex(
            RuntimeError, "tree hash mismatch|allowlist mismatch"
        ):
            repair.verify_patch(repair.load_context(self.root), overlay)

        overlay, _, _ = self._stage_overlay()
        extension = overlay / "extension"
        extension.chmod(0o700)
        extra = extension / "extra.js"
        extra.write_text("// extra file\n", encoding="utf-8")
        extra.chmod(0o400)
        extension.chmod(0o500)
        with self.assertRaisesRegex(
            RuntimeError, "tree hash mismatch|file set mismatch|unlisted"
        ):
            repair.verify_patch(repair.load_context(self.root), overlay)

        overlay, _, _ = self._stage_overlay()
        extension = overlay / "extension"
        extension.chmod(0o700)
        missing = extension / "foreground-transaction.js"
        missing.chmod(0o600)
        missing.unlink()
        extension.chmod(0o500)
        with self.assertRaisesRegex(
            RuntimeError, "tree hash mismatch|file set mismatch|incomplete"
        ):
            repair.verify_patch(repair.load_context(self.root), overlay)

    def test_registry_overlay_rejects_prior_hash_tampering(self):
        overlay, _, _ = self._stage_overlay()
        manifest_path = overlay / "component-manifest.json"
        manifest_path.chmod(0o600)
        record = json.loads(manifest_path.read_text(encoding="utf-8"))
        record["prior_component_manifest_sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(record), encoding="utf-8")
        manifest_path.chmod(0o400)
        with self.assertRaisesRegex(RuntimeError, "prior component hash"):
            repair.verify_patch(repair.load_context(self.root), overlay)

    def test_registry_overlay_rejects_git_blob_mismatch_when_file_sha_matches(self):
        prior = self._stage()
        self._bind_component(prior)
        git = self._registry_git()
        self._pin_overlay_commit(str(git["commit"]))
        kwargs = self._overlay_kwargs(prior, git)
        with self.assertRaisesRegex(RuntimeError, "git blob mismatch"):
            repair.stage_registry_overlay(
                self.root,
                Path(str(git["candidate"])),
                **{**kwargs, "git_blob_oid": "0" * 40},
            )
        with self.assertRaisesRegex(RuntimeError, "git blob mismatch"):
            repair.stage_registry_overlay(
                self.root,
                Path(str(git["candidate"])),
                **{**kwargs, "background_git_blob_oid": "0" * 40},
            )
        with self.assertRaisesRegex(RuntimeError, "git commit mismatch"):
            repair.check(
                self.root,
                candidate=Path(str(git["candidate"])),
                **{**kwargs, "git_commit": "1" * 40},
            )

    def test_registry_overlay_rejects_stale_prior_and_binding_drift_before_rebind(self):
        component_a = self._stage_variant("// old submit-page component A\n")
        component_b = self._stage_variant("// current submit-page component B\n")
        binding_a = self._bind_component(component_a)
        binding_b = self._bind_component(component_b)
        self.assertNotEqual(
            binding_a["component_patch"]["manifest_path"],
            binding_b["component_patch"]["manifest_path"],
        )
        self.assertNotEqual(
            binding_a["component_patch"]["artifact_hashes"][repair.PATCHED_RELATIVE],
            binding_b["component_patch"]["artifact_hashes"][repair.PATCHED_RELATIVE],
        )
        git = self._registry_git()
        self._pin_overlay_commit(str(git["commit"]))
        overlay_kwargs = self._overlay_kwargs(component_a, git)
        with self.assertRaisesRegex(
            RuntimeError, "prior does not match the active Runtime Host extension binding"
        ):
            repair.stage_registry_overlay(
                self.root, Path(str(git["candidate"])), **overlay_kwargs
            )
        overlay = repair.stage_registry_overlay(
            self.root,
            Path(str(git["candidate"])),
            **self._overlay_kwargs(component_b, git),
        )
        overlay_profile = self._profile(overlay / "extension")
        chrome = self._chrome()
        self._write_json(self.layout["host_extension_binding"], binding_a)
        with patch.object(
            pc, "_install_native_messaging_manifests_unlocked"
        ) as install:
            with self.assertRaisesRegex(
                RuntimeError,
                "prior does not match the active Runtime Host extension binding",
            ):
                repair.rebind(
                    self.root,
                    overlay,
                    home_dir=Path(self.temp.name),
                    native_user_data_dirs=(overlay_profile,),
                    browser_executables=(chrome,),
                )
        install.assert_not_called()
        published = json.loads(
            self.layout["host_extension_binding"].read_text(encoding="utf-8")
        )
        self.assertEqual(
            published["component_patch"]["manifest_path"],
            binding_a["component_patch"]["manifest_path"],
        )
        self.assertEqual(
            published["component_patch"]["artifact_hashes"],
            binding_a["component_patch"]["artifact_hashes"],
        )
        self.assertFalse(
            pc._runtime_verifier_config_path(Path(self.temp.name)).exists()
        )

    def test_registry_overlay_rejects_git_replace_ref_substituted_bytes(self):
        prior = self._stage()
        self._bind_component(prior)
        original = self._registry_git(b"// original current-entry-registry overlay\n")
        substitute = b"// substituted current-entry-registry via replace-ref\n"
        repo = Path(str(original["repo"]))
        env = os.environ.copy()
        env.update(
            {
                "GIT_AUTHOR_NAME": "overlay-test",
                "GIT_AUTHOR_EMAIL": "overlay-test@example.com",
                "GIT_COMMITTER_NAME": "overlay-test",
                "GIT_COMMITTER_EMAIL": "overlay-test@example.com",
                "GIT_TERMINAL_PROMPT": "0",
            }
        )
        hashed = subprocess.run(
            ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
            input=substitute,
            check=True,
            capture_output=True,
            env=env,
        )
        replacement_blob = hashed.stdout.decode("ascii").strip()
        subprocess.run(
            ["git", "-C", str(repo), "replace", str(original["blob"]), replacement_blob],
            check=True,
            capture_output=True,
            env=env,
        )
        naive = subprocess.run(
            ["git", "-C", str(repo), "show", str(original["blob"])],
            check=True,
            capture_output=True,
            env=env,
        )
        self.assertEqual(naive.stdout, substitute)
        self._pin_overlay_commit(str(original["commit"]))
        substitute_candidate = Path(self.temp.name) / (
            f"replaced-registry-{os.urandom(8).hex()}.js"
        )
        substitute_candidate.write_bytes(substitute)
        substitute_candidate.chmod(0o600)
        with patch.dict(
            os.environ,
            {
                "GIT_NO_REPLACE_OBJECTS": "0",
                "GIT_REPLACE_REF_BASE": "refs/replace",
            },
        ):
            # Git treats any defined NO_REPLACE value (including "0") as disabled.
            # Exercise the vulnerable ordinary unset environment causally.
            os.environ.pop("GIT_NO_REPLACE_OBJECTS", None)
            kwargs = self._overlay_kwargs(prior, original)
            with self.assertRaisesRegex(
                RuntimeError, "git object does not match the current file"
            ):
                repair.stage_registry_overlay(
                    self.root,
                    substitute_candidate,
                    **{
                        **kwargs,
                        "expected_registry_sha256": pc._sha256_file(
                            substitute_candidate
                        ),
                    },
                )
            overlay = repair.stage_registry_overlay(
                self.root,
                Path(str(original["candidate"])),
                **kwargs,
            )
        self.assertEqual(
            (overlay / "extension" / repair.REGISTRY_RELATIVE).read_bytes(),
            original["payload"],
        )
        self.assertEqual(
            (overlay / "extension" / repair.BACKGROUND_RELATIVE).read_bytes(),
            original["background_payload"],
        )

    def test_registry_overlay_rejects_missing_or_mismatching_background(self):
        prior = self._stage()
        git = self._registry_git()
        self._pin_overlay_commit(str(git["commit"]))
        kwargs = self._overlay_kwargs(prior, git)
        kwargs.pop("candidate_background")
        with self.assertRaisesRegex(RuntimeError, "background"):
            repair.check(
                self.root,
                candidate=Path(str(git["candidate"])),
                **kwargs,
            )
        prior_bound = self._stage()
        self._bind_component(prior_bound)
        git = self._registry_git()
        self._pin_overlay_commit(str(git["commit"]))
        with self.assertRaisesRegex(RuntimeError, "background.js.*hash mismatch"):
            repair.stage_registry_overlay(
                self.root,
                Path(str(git["candidate"])),
                **{
                    **self._overlay_kwargs(prior_bound, git),
                    "expected_background_sha256": "0" * 64,
                },
            )
        overlay, _, _ = self._stage_overlay()
        background = overlay / "extension" / repair.BACKGROUND_RELATIVE
        background.chmod(0o600)
        background.write_text("// mismatched background\n", encoding="utf-8")
        background.chmod(0o400)
        with self.assertRaisesRegex(
            RuntimeError, "tree hash mismatch|provenance mismatch|hash mismatch"
        ):
            repair.verify_patch(repair.load_context(self.root), overlay)
        overlay, _, _ = self._stage_overlay()
        extension = overlay / "extension"
        extension.chmod(0o700)
        missing = extension / repair.BACKGROUND_RELATIVE
        missing.chmod(0o600)
        missing.unlink()
        extension.chmod(0o500)
        with self.assertRaisesRegex(
            RuntimeError, "tree hash mismatch|file set mismatch|missing"
        ):
            repair.verify_patch(repair.load_context(self.root), overlay)
        overlay, _, _ = self._stage_overlay()
        manifest_path = overlay / "component-manifest.json"
        manifest_path.chmod(0o600)
        record = json.loads(manifest_path.read_text(encoding="utf-8"))
        record["schema_version"] = pc.RUNTIME_EXTENSION_REGISTRY_OVERLAY_INCOMPLETE_SCHEMA
        manifest_path.write_text(json.dumps(record), encoding="utf-8")
        manifest_path.chmod(0o400)
        with self.assertRaisesRegex(
            RuntimeError, "incomplete registry overlay without background.js"
        ):
            repair.verify_patch(repair.load_context(self.root), overlay)

    def test_registry_overlay_rebind_runtime_drift_performs_no_publication(self):
        overlay, _, _ = self._stage_overlay()
        binding_before = self.layout["host_extension_binding"].read_bytes()
        profile = self._profile(overlay / "extension")
        chrome = self._chrome()

        def drift_after_context(_profiles):
            runtime_path = self.root / "runtime.json"
            state = json.loads(runtime_path.read_text(encoding="utf-8"))
            state["concurrent_runtime_switch"] = True
            self._write_json(runtime_path, state)
            return {(overlay / "extension").resolve(strict=True)}

        with patch.object(
            repair,
            "_configured_extension_dirs",
            side_effect=drift_after_context,
        ), patch.object(
            pc,
            "_install_native_messaging_manifests_unlocked",
        ) as install:
            with self.assertRaisesRegex(RuntimeError, "runtime changed before"):
                repair.rebind(
                    self.root,
                    overlay,
                    home_dir=Path(self.temp.name),
                    native_user_data_dirs=(profile,),
                    browser_executables=(chrome,),
                )
        install.assert_not_called()
        self.assertEqual(
            self.layout["host_extension_binding"].read_bytes(), binding_before
        )
        self.assertFalse(
            pc._runtime_verifier_config_path(Path(self.temp.name)).exists()
        )

    def test_registry_overlay_rollback_restores_prior_v1_component(self):
        overlay, prior, _ = self._stage_overlay()
        context = repair.load_context(self.root)
        _, overlay_provenance = repair.verify_patch(context, overlay)
        _, prior_provenance = repair.verify_patch(context, prior)
        self.assertEqual(
            pc._runtime_extension_component_rollback_restore_manifest(overlay_provenance),
            Path(prior_provenance["manifest_path"]),
        )
        self.assertIsNone(
            pc._runtime_extension_component_rollback_restore_manifest(prior_provenance)
        )
        profile = self._profile(overlay / "extension")
        chrome = self._chrome()
        binding = pc._build_runtime_extension_binding(
            self.layout,
            self.manifest,
            home_dir=Path(self.temp.name),
            native_user_data_dirs=(profile,),
            browser_executables=(chrome,),
            extension_component_manifest=overlay / "component-manifest.json",
        )
        self.assertEqual(binding["schema_version"], "runtime_host_extension_binding_v1")
        self.assertEqual(
            binding["component_patch"]["schema_version"],
            pc.RUNTIME_EXTENSION_REGISTRY_OVERLAY_SCHEMA,
        )
        self._write_json(self.layout["host_extension_binding"], binding)
        restored_profile = self._profile(prior / "extension")
        with patch.object(
            pc,
            "_install_native_messaging_manifests_unlocked",
            return_value={"custom": Path(self.temp.name) / "native.json"},
        ) as install, patch.object(
            pc,
            "_validate_runtime_extension_binding",
            return_value={"component_patch": prior_provenance},
        ):
            result = repair.rollback(
                self.root,
                overlay,
                home_dir=Path(self.temp.name),
                native_user_data_dirs=(restored_profile,),
                browser_executables=(chrome,),
            )
        self.assertTrue(result["ok"])
        self.assertEqual(
            result["extension_dir"],
            str((prior / "extension").resolve(strict=True)),
        )
        self.assertEqual(
            install.call_args.kwargs["rollback_extension_component_manifest"],
            overlay / "component-manifest.json",
        )


if __name__ == "__main__":
    unittest.main()
