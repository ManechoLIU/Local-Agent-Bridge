#!/usr/bin/env python3
"""Stage and bind one allowlisted production Chrome extension component patch.

Supports the v1 submit-page component and the background+registry overlay
variant that copies a verified v1 tree, preserves submit-page.js, and replaces
background.js and current-entry-registry.js from the same explicit Git commit.
The utility never edits Chrome preferences or restarts Chrome, Bridge, tunnel,
or Host processes. An operator must switch the unpacked extension through the
normal Chrome UI before `rebind` (or switch back before `rollback`).
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import sys
import time
import uuid

import production_connector as pc

PATCHED_RELATIVE = "submit-page.js"
REGISTRY_RELATIVE = "current-entry-registry.js"
BACKGROUND_RELATIVE = "background.js"
PATCH_ROOT_NAME = "extension-component-patches"


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _private_bytes(path: Path, label: str) -> bytes:
    value = pc._read_private_bytes_if_present(path, label)
    if value is None:
        raise RuntimeError(f"{label} is missing")
    return value


def _expected_sha256(value: str, label: str) -> str:
    normalized = str(value or "").strip().lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise RuntimeError(f"{label} must be an explicit SHA-256 digest")
    return normalized


@contextmanager
def _bounded_install_lock(runtime_root: Path):
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)

    def expired(_signal, _frame):
        raise RuntimeError("production install lock wait exceeded 10 seconds")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 10)
    lock = pc._production_install_lock(runtime_root)
    acquired = False
    try:
        lock.__enter__()
        acquired = True
        signal.setitimer(signal.ITIMER_REAL, 0)
        yield
    finally:
        if acquired:
            lock.__exit__(None, None, None)
        signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        signal.signal(signal.SIGALRM, previous_handler)


def _copy_owned_extension(
    source: Path,
    destination: Path,
    *,
    label: str = "production release extension",
) -> None:
    pc._validate_owned_tree_source(source, label)
    destination.mkdir(mode=0o700)
    for member in sorted(source.rglob("*")):
        relative = member.relative_to(source)
        target = destination / relative
        info = member.lstat()
        if stat.S_ISDIR(info.st_mode) and not member.is_symlink():
            target.mkdir(mode=0o700)
            continue
        pc._validate_owned_file_source(member, f"{label} member")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        source_fd = os.open(member, flags)
        try:
            opened = os.fstat(source_fd)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_uid != os.geteuid()
                or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
            ):
                raise RuntimeError("production release extension changed during copy")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(source_fd, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            os.close(source_fd)
        target_fd = os.open(
            target,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(target_fd, "wb") as stream:
            stream.write(b"".join(chunks))
            stream.flush()
            os.fsync(stream.fileno())


def _seal(root: Path) -> None:
    for item in sorted(root.rglob("*"), key=lambda path: len(path.parts), reverse=True):
        item.chmod(0o500 if item.is_dir() else 0o400)
    root.chmod(0o500)


def load_context(runtime_root: Path) -> dict[str, object]:
    root = Path(runtime_root).expanduser().resolve(strict=True)
    runtime_path = root / "runtime.json"
    runtime_raw = _private_bytes(runtime_path, "production runtime state")
    try:
        state = json.loads(runtime_raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("production runtime state is invalid JSON") from error
    if not isinstance(state, dict):
        raise RuntimeError("production runtime state must be a JSON object")
    reused = state.get("reused_live_backends")
    if not isinstance(reused, dict):
        raise RuntimeError("extension component patch requires reused production backends")
    source_revision = pc._release_revision(reused.get("source_release_revision"))
    source_generation = pc._endpoint_generation(reused.get("source_endpoint_generation"))
    source_release = root / "releases" / source_revision / f"generation-{source_generation}"
    source_manifest_path = source_release / "release.json"
    if reused.get("release_manifest") != str(source_manifest_path):
        raise RuntimeError("reused production source manifest path mismatch")
    source_manifest_raw = _private_bytes(source_manifest_path, "source production release")
    try:
        source_manifest = json.loads(source_manifest_raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("source production release is invalid JSON") from error
    pc._validate_manifest_contract(source_manifest)
    if (
        source_manifest.get("release_revision") != source_revision
        or source_manifest.get("endpoint_generation") != source_generation
    ):
        raise RuntimeError("reused production source release fence mismatch")
    layout = pc.production_layout(source_manifest, runtime_root=root)
    # generation-34 predates whole-release directory sealing. Its complete
    # artifact hashes remain authoritative; the component tree itself is
    # separately required to be sealed before binding.
    pc.validate_installed_release(layout, require_sealed=False)
    source_extension = layout["host_dir"] / "chrome-host" / "extension"
    source_hashes = pc._runtime_extension_tree_hashes(
        source_extension, label="production release extension"
    )
    artifacts = source_manifest.get("artifacts_sha256")
    prefix = "host/chrome-host/extension/"
    pinned_hashes = {
        relative.removeprefix(prefix): digest
        for relative, digest in artifacts.items()
        if isinstance(relative, str) and relative.startswith(prefix)
    } if isinstance(artifacts, dict) else {}
    if source_hashes != pinned_hashes:
        raise RuntimeError("production release extension differs from its release manifest")
    return {
        "root": root,
        "runtime_path": runtime_path,
        "runtime_sha256": _sha_bytes(runtime_raw),
        "state": state,
        "layout": layout,
        "manifest": source_manifest,
        "manifest_path": source_manifest_path,
        "manifest_sha256": _sha_bytes(source_manifest_raw),
        "source_extension": source_extension,
        "source_hashes": source_hashes,
    }


def _read_reviewed_candidate(
    context: dict[str, object],
    candidate: Path,
    *,
    expected_source_sha256: str,
    expected_candidate_sha256: str,
) -> bytes:
    source_hash = _expected_sha256(expected_source_sha256, "source submit-page.js SHA-256")
    candidate_hash = _expected_sha256(expected_candidate_sha256, "candidate submit-page.js SHA-256")
    source_hashes = context["source_hashes"]
    assert isinstance(source_hashes, dict)
    if source_hashes.get(PATCHED_RELATIVE) != source_hash:
        raise RuntimeError("explicit source submit-page.js hash does not match the release")
    candidate = Path(candidate).expanduser()
    pc._validate_owned_file_source(candidate, "reviewed submit-page.js candidate")
    before = candidate.lstat()
    payload = candidate.read_bytes()
    after = candidate.lstat()
    if (before.st_dev, before.st_ino, before.st_size) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
    ):
        raise RuntimeError("reviewed submit-page.js candidate changed while reading")
    if _sha_bytes(payload) != candidate_hash:
        raise RuntimeError("explicit candidate submit-page.js hash mismatch")
    if candidate_hash == source_hash:
        raise RuntimeError("submit-page.js candidate does not change the source release")
    return payload


def _read_reviewed_owned_bytes(candidate: Path, label: str) -> bytes:
    candidate = Path(candidate).expanduser()
    pc._validate_owned_file_source(candidate, label)
    before = candidate.lstat()
    payload = candidate.read_bytes()
    after = candidate.lstat()
    if (before.st_dev, before.st_ino, before.st_size) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
    ):
        raise RuntimeError(f"{label} changed while reading")
    return payload


def _read_reviewed_registry_overlay(
    context: dict[str, object],
    candidate: Path,
    *,
    prior_component_manifest: Path,
    prior_component_manifest_sha256: str,
    git_repository: Path,
    git_commit: str,
    git_blob_oid: str,
    expected_registry_sha256: str,
    candidate_background: Path,
    background_git_blob_oid: str,
    expected_background_sha256: str,
) -> tuple[bytes, bytes, dict[str, object], dict[str, str], dict[str, str]]:
    prior_hash = _expected_sha256(
        prior_component_manifest_sha256, "prior component manifest SHA-256"
    )
    root = context["root"]
    layout = context["layout"]
    manifest = context["manifest"]
    source_hashes = context["source_hashes"]
    assert isinstance(root, Path) and isinstance(layout, dict)
    assert isinstance(manifest, dict) and isinstance(source_hashes, dict)
    prior_path = Path(prior_component_manifest).expanduser()
    if prior_path.is_symlink():
        raise RuntimeError("prior extension component manifest must not be a symlink")
    prior_path = prior_path.resolve(strict=True)
    if prior_path.parent.parent != root / PATCH_ROOT_NAME:
        raise RuntimeError("prior extension component is outside the production patch root")
    prior_provenance = pc._validate_runtime_extension_component_patch(
        layout,
        manifest,
        extension_dir=prior_path.parent / "extension",
        component_manifest=prior_path,
    )
    if (
        prior_provenance.get("schema_version") == pc.RUNTIME_EXTENSION_REGISTRY_OVERLAY_SCHEMA
        or prior_provenance.get("manifest_sha256") != prior_hash
        or prior_provenance.get("allowed_relative_paths") != [PATCHED_RELATIVE]
    ):
        raise RuntimeError(
            "registry overlay prior component must be the verified v1 submit-page component"
        )
    payload = _read_reviewed_owned_bytes(
        candidate, "reviewed current-entry-registry.js candidate"
    )
    background_payload = _read_reviewed_owned_bytes(
        candidate_background, "reviewed background.js candidate"
    )
    git_provenance = pc._validate_registry_overlay_git_provenance(
        repository=git_repository,
        commit=git_commit,
        blob_oid=git_blob_oid,
        expected_sha256=expected_registry_sha256,
        current_bytes=payload,
        relative=REGISTRY_RELATIVE,
    )
    background_git = pc._validate_registry_overlay_git_provenance(
        repository=git_repository,
        commit=git_commit,
        blob_oid=background_git_blob_oid,
        expected_sha256=expected_background_sha256,
        current_bytes=background_payload,
        relative=BACKGROUND_RELATIVE,
    )
    if (
        git_provenance["repository"] != background_git["repository"]
        or git_provenance["commit"] != background_git["commit"]
    ):
        raise RuntimeError("registry overlay candidate provenance mismatch")
    if git_provenance["sha256"] == source_hashes.get(REGISTRY_RELATIVE):
        raise RuntimeError(
            "current-entry-registry.js candidate does not change the source release"
        )
    if git_provenance["sha256"] == prior_provenance["artifact_hashes"].get(REGISTRY_RELATIVE):
        raise RuntimeError(
            "current-entry-registry.js candidate does not change the prior verified component"
        )
    if background_git["sha256"] == source_hashes.get(BACKGROUND_RELATIVE):
        raise RuntimeError("background.js candidate does not change the source release")
    if background_git["sha256"] == prior_provenance["artifact_hashes"].get(
        BACKGROUND_RELATIVE
    ):
        raise RuntimeError(
            "background.js candidate does not change the prior verified component"
        )
    return payload, background_payload, prior_provenance, git_provenance, background_git


def verify_patch(context: dict[str, object], patch_dir: Path) -> tuple[Path, dict[str, object]]:
    patch_root = Path(patch_dir).expanduser()
    if patch_root.is_symlink():
        raise RuntimeError("extension component patch path must not be a symlink")
    patch_root = patch_root.resolve(strict=True)
    root = context["root"]
    assert isinstance(root, Path)
    if patch_root.parent != root / PATCH_ROOT_NAME:
        raise RuntimeError("extension component patch is outside the production patch root")
    if {item.name for item in patch_root.iterdir()} != {"extension", "component-manifest.json"}:
        raise RuntimeError("extension component patch has unlisted root artifacts")
    manifest_path = patch_root / "component-manifest.json"
    layout = context["layout"]
    manifest = context["manifest"]
    assert isinstance(layout, dict) and isinstance(manifest, dict)
    provenance = pc._validate_runtime_extension_component_patch(
        layout,
        manifest,
        extension_dir=patch_root / "extension",
        component_manifest=manifest_path,
    )
    for item in (patch_root, *patch_root.rglob("*")):
        if stat.S_IMODE(item.lstat().st_mode) & 0o222:
            raise RuntimeError("extension component patch is not sealed")
    return patch_root, provenance


def check(
    runtime_root: Path,
    *,
    candidate: Path | None = None,
    patch_dir: Path | None = None,
    expected_source_sha256: str | None = None,
    expected_candidate_sha256: str | None = None,
    prior_component_manifest: Path | None = None,
    prior_component_manifest_sha256: str | None = None,
    git_repository: Path | None = None,
    git_commit: str | None = None,
    git_blob_oid: str | None = None,
    expected_registry_sha256: str | None = None,
    candidate_background: Path | None = None,
    background_git_blob_oid: str | None = None,
    expected_background_sha256: str | None = None,
) -> dict[str, object]:
    context = load_context(runtime_root)
    if patch_dir is not None:
        patch, provenance = verify_patch(context, patch_dir)
        return {
            "ok": True,
            "action": "check",
            "patch_dir": str(patch),
            "component_manifest_sha256": provenance["manifest_sha256"],
            "production_written": False,
        }
    overlay_inputs = (
        prior_component_manifest,
        prior_component_manifest_sha256,
        git_repository,
        git_commit,
        git_blob_oid,
        expected_registry_sha256,
        candidate_background,
        background_git_blob_oid,
        expected_background_sha256,
    )
    if any(value is not None for value in overlay_inputs):
        if candidate is None or any(value is None for value in overlay_inputs):
            raise RuntimeError(
                "registry overlay check requires the candidate, background, prior component, and git provenance"
            )
        payload, background_payload, prior_provenance, git_provenance, background_git = (
            _read_reviewed_registry_overlay(
                context,
                candidate,
                prior_component_manifest=prior_component_manifest,
                prior_component_manifest_sha256=prior_component_manifest_sha256,
                git_repository=git_repository,
                git_commit=git_commit,
                git_blob_oid=git_blob_oid,
                expected_registry_sha256=expected_registry_sha256,
                candidate_background=candidate_background,
                background_git_blob_oid=background_git_blob_oid,
                expected_background_sha256=expected_background_sha256,
            )
        )
        return {
            "ok": True,
            "action": "check",
            "candidate_registry_sha256": _sha_bytes(payload),
            "candidate_background_sha256": _sha_bytes(background_payload),
            "prior_component_manifest_sha256": prior_provenance["manifest_sha256"],
            "git_blob_oid": git_provenance["blob_oid"],
            "background_git_blob_oid": background_git["blob_oid"],
            "allowed_relative_paths": list(pc.RUNTIME_EXTENSION_REGISTRY_OVERLAY_ALLOWLIST),
            "production_written": False,
        }
    if candidate is None or expected_source_sha256 is None or expected_candidate_sha256 is None:
        raise RuntimeError("candidate check requires explicit source and candidate SHA-256 digests")
    payload = _read_reviewed_candidate(
        context,
        candidate,
        expected_source_sha256=expected_source_sha256,
        expected_candidate_sha256=expected_candidate_sha256,
    )
    return {
        "ok": True,
        "action": "check",
        "source_submit_sha256": expected_source_sha256,
        "candidate_submit_sha256": _sha_bytes(payload),
        "allowed_relative_paths": [PATCHED_RELATIVE],
        "production_written": False,
    }


def stage_patch(
    runtime_root: Path,
    candidate: Path,
    *,
    expected_source_sha256: str,
    expected_candidate_sha256: str,
) -> Path:
    with _bounded_install_lock(runtime_root):
        context = load_context(runtime_root)
        payload = _read_reviewed_candidate(
            context,
            candidate,
            expected_source_sha256=expected_source_sha256,
            expected_candidate_sha256=expected_candidate_sha256,
        )
        root = context["root"]
        source_extension = context["source_extension"]
        source_hashes = context["source_hashes"]
        layout = context["layout"]
        manifest = context["manifest"]
        assert isinstance(root, Path) and isinstance(source_extension, Path)
        assert isinstance(source_hashes, dict) and isinstance(layout, dict)
        assert isinstance(manifest, dict)
        patches = root / PATCH_ROOT_NAME
        pc._private_dir(patches)
        patch = patches / f"extension-{uuid.uuid4()}"
        try:
            patch.mkdir(mode=0o700)
            extension = patch / "extension"
            _copy_owned_extension(source_extension, extension)
            target = extension / PATCHED_RELATIVE
            target.write_bytes(payload)
            target.chmod(0o600)
            artifact_hashes = pc._runtime_extension_tree_hashes(
                extension, label="staged Runtime Host extension component"
            )
            changed = sorted(
                relative
                for relative in source_hashes
                if source_hashes[relative] != artifact_hashes.get(relative)
            )
            if set(source_hashes) != set(artifact_hashes) or changed != [PATCHED_RELATIVE]:
                raise RuntimeError("extension component changes exceed submit-page.js allowlist")
            record = {
                "schema_version": pc.RUNTIME_EXTENSION_COMPONENT_SCHEMA,
                "component": "chrome-extension-submit-page",
                "created_at_unix_ms": int(time.time() * 1000),
                "patch_dir": str(patch),
                "extension_dir": str(extension),
                "source_release_dir": str(layout["release_dir"].resolve(strict=True)),
                "source_release_manifest": str(layout["release_manifest"].resolve(strict=True)),
                "source_release_manifest_sha256": context["manifest_sha256"],
                "source_release_revision": manifest["release_revision"],
                "source_endpoint_generation": manifest["endpoint_generation"],
                "allowed_relative_paths": [PATCHED_RELATIVE],
                "changed_relative_paths": [PATCHED_RELATIVE],
                "source_hashes": source_hashes,
                "artifact_hashes": artifact_hashes,
            }
            pc._write_json_private(patch / "component-manifest.json", record)
            _seal(patch)
            verify_patch(context, patch)
            return patch
        except Exception:
            if patch.exists() and not patch.is_symlink():
                for item in (patch, *patch.rglob("*")):
                    try:
                        item.chmod(0o700 if item.is_dir() else 0o600)
                    except OSError:
                        pass
                shutil.rmtree(patch)
            raise


def stage_registry_overlay(
    runtime_root: Path,
    candidate: Path,
    *,
    prior_component_manifest: Path,
    prior_component_manifest_sha256: str,
    git_repository: Path,
    git_commit: str,
    git_blob_oid: str,
    expected_registry_sha256: str,
    candidate_background: Path,
    background_git_blob_oid: str,
    expected_background_sha256: str,
) -> Path:
    with _bounded_install_lock(runtime_root):
        context = load_context(runtime_root)
        payload, background_payload, prior_provenance, git_provenance, background_git = (
            _read_reviewed_registry_overlay(
                context,
                candidate,
                prior_component_manifest=prior_component_manifest,
                prior_component_manifest_sha256=prior_component_manifest_sha256,
                git_repository=git_repository,
                git_commit=git_commit,
                git_blob_oid=git_blob_oid,
                expected_registry_sha256=expected_registry_sha256,
                candidate_background=candidate_background,
                background_git_blob_oid=background_git_blob_oid,
                expected_background_sha256=expected_background_sha256,
            )
        )
        root = context["root"]
        source_hashes = context["source_hashes"]
        layout = context["layout"]
        manifest = context["manifest"]
        assert isinstance(root, Path) and isinstance(source_hashes, dict)
        assert isinstance(layout, dict) and isinstance(manifest, dict)
        binding_cas = _require_active_overlay_prior(layout, manifest, prior_provenance)
        prior_extension = Path(str(prior_provenance["manifest_path"])).parent / "extension"
        patches = root / PATCH_ROOT_NAME
        pc._private_dir(patches)
        patch = patches / f"extension-{uuid.uuid4()}"
        try:
            patch.mkdir(mode=0o700)
            extension = patch / "extension"
            _copy_owned_extension(
                prior_extension,
                extension,
                label="verified extension component",
            )
            target = extension / REGISTRY_RELATIVE
            target.write_bytes(payload)
            target.chmod(0o600)
            background_target = extension / BACKGROUND_RELATIVE
            background_target.write_bytes(background_payload)
            background_target.chmod(0o600)
            artifact_hashes = pc._runtime_extension_tree_hashes(
                extension, label="staged Runtime Host extension registry overlay"
            )
            prior_hashes = prior_provenance["artifact_hashes"]
            assert isinstance(prior_hashes, dict)
            changed = sorted(
                relative
                for relative in source_hashes
                if source_hashes[relative] != artifact_hashes.get(relative)
            )
            changed_from_prior = sorted(
                relative
                for relative in prior_hashes
                if prior_hashes[relative] != artifact_hashes.get(relative)
            )
            if set(source_hashes) != set(artifact_hashes) or set(prior_hashes) != set(
                artifact_hashes
            ):
                raise RuntimeError("extension component file set mismatch")
            if changed != list(pc.RUNTIME_EXTENSION_REGISTRY_OVERLAY_ALLOWLIST):
                raise RuntimeError(
                    "registry overlay changes must be exactly background.js, current-entry-registry.js and submit-page.js versus the original release"
                )
            if changed_from_prior != list(pc.RUNTIME_EXTENSION_REGISTRY_OVERLAY_PRIOR_CHANGED):
                raise RuntimeError(
                    "registry overlay may change only background.js and current-entry-registry.js versus the prior verified component"
                )
            if artifact_hashes.get(PATCHED_RELATIVE) != prior_hashes.get(PATCHED_RELATIVE):
                raise RuntimeError(
                    "registry overlay must preserve the verified submit-page.js component"
                )
            if artifact_hashes.get(PATCHED_RELATIVE) == source_hashes.get(PATCHED_RELATIVE):
                raise RuntimeError(
                    "registry overlay must not revert submit-page.js to the original release"
                )
            if artifact_hashes.get(REGISTRY_RELATIVE) != git_provenance["sha256"]:
                raise RuntimeError("staged current-entry-registry.js hash mismatch")
            if artifact_hashes.get(BACKGROUND_RELATIVE) != background_git["sha256"]:
                raise RuntimeError("staged background.js hash mismatch")
            record = {
                "schema_version": pc.RUNTIME_EXTENSION_REGISTRY_OVERLAY_SCHEMA,
                "component": pc.RUNTIME_EXTENSION_REGISTRY_OVERLAY_COMPONENT,
                "created_at_unix_ms": int(time.time() * 1000),
                "patch_dir": str(patch),
                "extension_dir": str(extension),
                "source_release_dir": str(layout["release_dir"].resolve(strict=True)),
                "source_release_manifest": str(layout["release_manifest"].resolve(strict=True)),
                "source_release_manifest_sha256": context["manifest_sha256"],
                "source_release_revision": manifest["release_revision"],
                "source_endpoint_generation": manifest["endpoint_generation"],
                "allowed_relative_paths": list(pc.RUNTIME_EXTENSION_REGISTRY_OVERLAY_ALLOWLIST),
                "changed_relative_paths": list(pc.RUNTIME_EXTENSION_REGISTRY_OVERLAY_ALLOWLIST),
                "source_hashes": source_hashes,
                "artifact_hashes": artifact_hashes,
                "prior_component_manifest": str(prior_provenance["manifest_path"]),
                "prior_component_manifest_sha256": prior_provenance["manifest_sha256"],
                "preserved_submit_page_sha256": prior_hashes[PATCHED_RELATIVE],
                "candidate_git_repository": git_provenance["repository"],
                "candidate_git_commit": git_provenance["commit"],
                "candidate_git_blob_oid": git_provenance["blob_oid"],
                "candidate_registry_sha256": git_provenance["sha256"],
                "candidate_background_git_blob_oid": background_git["blob_oid"],
                "candidate_background_sha256": background_git["sha256"],
            }
            pc._write_json_private(patch / "component-manifest.json", record)
            _seal(patch)
            verify_patch(context, patch)
            _require_active_overlay_prior(
                layout,
                manifest,
                prior_provenance,
                expected_binding_sha256=binding_cas,
            )
            return patch
        except Exception:
            if patch.exists() and not patch.is_symlink():
                for item in (patch, *patch.rglob("*")):
                    try:
                        item.chmod(0o700 if item.is_dir() else 0o600)
                    except OSError:
                        pass
                shutil.rmtree(patch)
            raise


def _require_browser_inputs(
    native_user_data_dirs: tuple[Path, ...], browser_executables: tuple[Path, ...]
) -> None:
    if not native_user_data_dirs or not browser_executables:
        raise RuntimeError("rebind/rollback requires explicit Chrome profile and executable pins")


def _configured_extension_dirs(native_user_data_dirs: tuple[Path, ...]) -> set[Path]:
    return {
        pc._configured_extension_source(Path(profile).expanduser().resolve(strict=True))[1]
        for profile in native_user_data_dirs
    }


def _assert_runtime_unchanged(context: dict[str, object]) -> None:
    runtime_path = context["runtime_path"]
    expected_sha256 = context["runtime_sha256"]
    assert isinstance(runtime_path, Path) and isinstance(expected_sha256, str)
    observed = _sha_bytes(_private_bytes(runtime_path, "production runtime state CAS"))
    if observed != expected_sha256:
        raise RuntimeError(
            "production runtime changed before extension binding publication"
        )


def _require_active_overlay_prior(
    layout: dict[str, Path],
    manifest: dict[str, object],
    prior_provenance: dict[str, object],
    *,
    expected_binding_sha256: str | None = None,
) -> str:
    """Prove the live private binding is exactly the overlay's v1 prior.

    Uses the bounded component-binding proof so Chrome may already have loaded
    a newly staged overlay path. Binding byte CAS rejects drift between the
    first proof and publication.
    """
    value, raw = pc._read_authenticated_runtime_extension_binding(layout, manifest)
    digest = _sha_bytes(raw)
    if expected_binding_sha256 is not None and digest != expected_binding_sha256:
        raise RuntimeError(
            "Runtime Host extension binding changed before overlay publication"
        )
    component = value.get("component_patch")
    if (
        not isinstance(component, dict)
        or component.get("manifest_path") != prior_provenance.get("manifest_path")
        or component.get("manifest_sha256") != prior_provenance.get("manifest_sha256")
        or component.get("artifact_hashes") != prior_provenance.get("artifact_hashes")
    ):
        raise RuntimeError(
            "registry overlay prior does not match the active Runtime Host extension binding"
        )
    return digest


def rebind(
    runtime_root: Path,
    patch_dir: Path,
    *,
    home_dir: Path,
    native_user_data_dirs: tuple[Path, ...],
    browser_executables: tuple[Path, ...],
) -> dict[str, object]:
    _require_browser_inputs(native_user_data_dirs, browser_executables)
    with _bounded_install_lock(runtime_root):
        context = load_context(runtime_root)
        patch, provenance = verify_patch(context, patch_dir)
        expected_extension = (patch / "extension").resolve(strict=True)
        configured = _configured_extension_dirs(native_user_data_dirs)
        if configured != {expected_extension}:
            raise RuntimeError("Chrome has not loaded the staged extension component path")
        layout = context["layout"]
        manifest = context["manifest"]
        assert isinstance(layout, dict) and isinstance(manifest, dict)
        overlay_prior = None
        overlay_binding_cas = None
        if provenance.get("schema_version") == pc.RUNTIME_EXTENSION_REGISTRY_OVERLAY_SCHEMA:
            prior_manifest = Path(str(provenance["prior_component_manifest"]))
            overlay_prior = pc._validate_runtime_extension_component_patch(
                layout,
                manifest,
                extension_dir=prior_manifest.parent / "extension",
                component_manifest=prior_manifest,
            )
            overlay_binding_cas = _require_active_overlay_prior(
                layout, manifest, overlay_prior
            )
        _assert_runtime_unchanged(context)
        if overlay_prior is not None:
            _require_active_overlay_prior(
                layout,
                manifest,
                overlay_prior,
                expected_binding_sha256=overlay_binding_cas,
            )
        installed = pc._install_native_messaging_manifests_unlocked(
            layout,
            manifest,
            home_dir=home_dir,
            native_user_data_dirs=native_user_data_dirs,
            browser_executables=browser_executables,
            extension_component_manifest=patch / "component-manifest.json",
        )
        binding = pc._validate_runtime_extension_binding(layout, manifest)
        _assert_runtime_unchanged(context)
        if binding.get("component_patch") != provenance:
            raise RuntimeError("extension component binding readback mismatch")
    return {
        "ok": True,
        "action": "rebind",
        "patch_dir": str(patch),
        "extension_dir": str(expected_extension),
        "binding": str(layout["host_extension_binding"]),
        "native_manifests": {name: str(path) for name, path in installed.items()},
        "processes_restarted": False,
    }


def rollback(
    runtime_root: Path,
    patch_dir: Path,
    *,
    home_dir: Path,
    native_user_data_dirs: tuple[Path, ...],
    browser_executables: tuple[Path, ...],
) -> dict[str, object]:
    _require_browser_inputs(native_user_data_dirs, browser_executables)
    with _bounded_install_lock(runtime_root):
        context = load_context(runtime_root)
        patch, provenance = verify_patch(context, patch_dir)
        layout = context["layout"]
        manifest = context["manifest"]
        source_extension = context["source_extension"]
        assert isinstance(layout, dict) and isinstance(manifest, dict)
        assert isinstance(source_extension, Path)
        configured = _configured_extension_dirs(native_user_data_dirs)
        overlay = (
            provenance.get("schema_version") == pc.RUNTIME_EXTENSION_REGISTRY_OVERLAY_SCHEMA
        )
        if overlay:
            prior_manifest = Path(str(provenance["prior_component_manifest"]))
            restored_extension = (prior_manifest.parent / "extension").resolve(strict=True)
            if configured != {restored_extension}:
                raise RuntimeError(
                    "Chrome has not restored the prior verified extension component path"
                )
        else:
            restored_extension = source_extension.resolve(strict=True)
            if configured != {restored_extension}:
                raise RuntimeError("Chrome has not restored the immutable release extension path")
        _assert_runtime_unchanged(context)
        installed = pc._install_native_messaging_manifests_unlocked(
            layout,
            manifest,
            home_dir=home_dir,
            native_user_data_dirs=native_user_data_dirs,
            browser_executables=browser_executables,
            rollback_extension_component_manifest=patch / "component-manifest.json",
        )
        binding = pc._validate_runtime_extension_binding(layout, manifest)
        _assert_runtime_unchanged(context)
        if overlay:
            prior_provenance = pc._validate_runtime_extension_component_patch(
                layout,
                manifest,
                extension_dir=restored_extension,
                component_manifest=Path(str(provenance["prior_component_manifest"])),
            )
            if binding.get("component_patch") != prior_provenance:
                raise RuntimeError("extension component rollback binding readback mismatch")
        elif "component_patch" in binding:
            raise RuntimeError("extension component rollback binding readback mismatch")
    return {
        "ok": True,
        "action": "rollback",
        "patch_dir": str(patch),
        "extension_dir": str(restored_extension),
        "binding": str(layout["host_extension_binding"]),
        "native_manifests": {name: str(path) for name, path in installed.items()},
        "processes_restarted": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "stage", "rebind", "rollback"))
    parser.add_argument("--runtime-root", type=Path, default=pc.PRODUCTION_RUNTIME_ROOT)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--patch-dir", type=Path)
    parser.add_argument("--source-submit-sha256")
    parser.add_argument("--candidate-submit-sha256")
    parser.add_argument("--prior-component-manifest", type=Path)
    parser.add_argument("--prior-component-manifest-sha256")
    parser.add_argument("--git-repository", type=Path)
    parser.add_argument("--git-commit")
    parser.add_argument("--git-blob-oid")
    parser.add_argument("--candidate-registry-sha256")
    parser.add_argument("--candidate-background", type=Path)
    parser.add_argument("--background-git-blob-oid")
    parser.add_argument("--candidate-background-sha256")
    parser.add_argument("--home-dir", type=Path, default=Path.home())
    parser.add_argument("--native-user-data-dir", type=Path, action="append", default=[])
    parser.add_argument("--browser-executable", type=Path, action="append", default=[])
    args = parser.parse_args(argv)
    overlay_inputs = (
        args.prior_component_manifest,
        args.prior_component_manifest_sha256,
        args.git_repository,
        args.git_commit,
        args.git_blob_oid,
        args.candidate_registry_sha256,
        args.candidate_background,
        args.background_git_blob_oid,
        args.candidate_background_sha256,
    )
    overlay_requested = any(value is not None for value in overlay_inputs)
    if args.action == "check":
        result = check(
            args.runtime_root,
            candidate=args.candidate,
            patch_dir=args.patch_dir,
            expected_source_sha256=args.source_submit_sha256,
            expected_candidate_sha256=args.candidate_submit_sha256,
            prior_component_manifest=args.prior_component_manifest,
            prior_component_manifest_sha256=args.prior_component_manifest_sha256,
            git_repository=args.git_repository,
            git_commit=args.git_commit,
            git_blob_oid=args.git_blob_oid,
            expected_registry_sha256=args.candidate_registry_sha256,
            candidate_background=args.candidate_background,
            background_git_blob_oid=args.background_git_blob_oid,
            expected_background_sha256=args.candidate_background_sha256,
        )
    elif args.action == "stage":
        if overlay_requested:
            if args.candidate is None or any(value is None for value in overlay_inputs):
                parser.error(
                    "registry overlay stage requires --candidate, --candidate-background, prior component, and git provenance"
                )
            patch = stage_registry_overlay(
                args.runtime_root,
                args.candidate,
                prior_component_manifest=args.prior_component_manifest,
                prior_component_manifest_sha256=args.prior_component_manifest_sha256,
                git_repository=args.git_repository,
                git_commit=args.git_commit,
                git_blob_oid=args.git_blob_oid,
                expected_registry_sha256=args.candidate_registry_sha256,
                candidate_background=args.candidate_background,
                background_git_blob_oid=args.background_git_blob_oid,
                expected_background_sha256=args.candidate_background_sha256,
            )
        else:
            if args.candidate is None or args.source_submit_sha256 is None or args.candidate_submit_sha256 is None:
                parser.error("stage requires --candidate and both explicit SHA-256 values")
            patch = stage_patch(
                args.runtime_root,
                args.candidate,
                expected_source_sha256=args.source_submit_sha256,
                expected_candidate_sha256=args.candidate_submit_sha256,
            )
        result = {"ok": True, "action": "stage", "patch_dir": str(patch), "processes_restarted": False}
    else:
        if args.patch_dir is None:
            parser.error(f"{args.action} requires --patch-dir")
        operation = rebind if args.action == "rebind" else rollback
        result = operation(
            args.runtime_root,
            args.patch_dir,
            home_dir=args.home_dir,
            native_user_data_dirs=tuple(args.native_user_data_dir),
            browser_executables=tuple(args.browser_executable),
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
