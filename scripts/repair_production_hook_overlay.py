#!/usr/bin/env python3
"""Install one exact Runtime Host hook overlay without replacing shared Core."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import uuid

import production_connector as pc
import repair_production_control as control
import repair_production_host as host_repair


PROVENANCE = "runtime_hook_overlay_patch"
PATCH_COMPONENT = "runtime-hook-overlay"
PATCH_ROOT = "component-patches"
OVERLAY_RELATIVE = Path("scripts/runtime_host_tool_hook.py")
LAUNCHER_NAME = "launch-runtime-hook-overlay.py"

LAUNCHER_BYTES = b"""#!/usr/bin/env python3
from pathlib import Path
import runpy
import sys

root = Path(__file__).resolve().parent
base_scripts = root / "base-runtime-host-tool-hook" / "scripts"
sys.path.insert(0, str(base_scripts))
runpy.run_path(str(root / "overlay" / "runtime_host_tool_hook.py"), run_name="__main__")
"""


class HookStopError(RuntimeError):
    def __init__(self, message: str, *, fully_stopped: bool):
        super().__init__(message)
        self.fully_stopped = fully_stopped


class HookStartError(RuntimeError):
    def __init__(self, message: str, *, cleanup_complete: bool):
        super().__init__(message)
        self.cleanup_complete = cleanup_complete


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_sha(value: object) -> str:
    return _sha(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _private_bytes(path: Path, label: str) -> bytes:
    return control._private_bytes(path, label)


def _preserved_runtime_state(state: dict) -> dict:
    ignored = {
        "runtime_hook_pid",
        "runtime_hook_process_identity",
        "runtime_hook_socket_identity",
        PROVENANCE,
    }
    return {key: value for key, value in state.items() if key not in ignored}


def _require_sha256(value: str | None, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise RuntimeError(f"{label} must be an explicit SHA-256 digest")
    return value


def _runtime_reconciliation(
    context: dict,
    previous_patch: Path,
    *,
    expected_runtime_sha256: str | None = None,
    expected_proof: dict | None = None,
) -> dict:
    if expected_runtime_sha256 is not None:
        expected_runtime_sha256 = _require_sha256(
            expected_runtime_sha256, "runtime reconciliation SHA-256"
        )
        if _sha(context["raw"]) != expected_runtime_sha256:
            raise RuntimeError("runtime reconciliation SHA-256 mismatch")
    record = json.loads(
        _private_bytes(
            previous_patch / "manifest.json", "previous Runtime hook overlay manifest"
        )
    )
    runtime_before = _private_bytes(
        previous_patch / "runtime-before.json", "previous overlay rollback state"
    )
    if record.get("schema_version") != 1 or _sha(runtime_before) != record.get(
        "runtime_before_sha256"
    ):
        raise RuntimeError("previous Runtime hook overlay baseline is invalid")
    before_state = json.loads(runtime_before)
    before_preserved = _preserved_runtime_state(before_state)
    current_preserved = _preserved_runtime_state(context["state"])
    before_hash = _json_sha(before_preserved)
    current_hash = _json_sha(current_preserved)
    if before_hash != record.get("preserved_runtime_state_sha256"):
        raise RuntimeError("previous Runtime hook overlay preserved state is invalid")
    changed = {
        key
        for key in set(before_preserved) | set(current_preserved)
        if key not in before_preserved
        or key not in current_preserved
        or before_preserved[key] != current_preserved[key]
    }
    allowed = {"tunnel_pid", "tunnel_process_identity"}
    if not changed.issubset(allowed):
        raise RuntimeError(
            "runtime reconciliation contains unsupported runtime fields: "
            + ",".join(sorted(changed - allowed))
        )
    current_pid = current_preserved.get("tunnel_pid")
    current_identity = current_preserved.get("tunnel_process_identity")
    if (
        not isinstance(current_pid, int)
        or current_pid < 1
        or not isinstance(current_identity, dict)
        or current_identity.get("pid") != current_pid
    ):
        raise RuntimeError("runtime reconciliation tunnel identity is incomplete")
    pc._validate_process_identity_record(current_identity)
    status = pc.production_tunnel_status(
        context["current_manifest"], context["current_layout"]
    )
    if (
        status.get("ready") is not True
        or status.get("pid") != current_pid
        or status.get("generation") != context["state"].get("endpoint_generation")
        or status.get("tunnel_id") != context["state"].get("tunnel_id")
        or status.get("profile_sha256")
        != context["state"].get("tunnel_profile_sha256")
        or not isinstance(status.get("operational"), dict)
    ):
        raise RuntimeError("runtime reconciliation tunnel is not canonical and operational")
    old_identity = before_preserved.get("tunnel_process_identity")
    proof = {
        "previous_preserved_runtime_state_sha256": before_hash,
        "current_preserved_runtime_state_sha256": current_hash,
        "changed_fields": {
            key: {
                "before_sha256": _json_sha(before_preserved.get(key)),
                "after_sha256": _json_sha(current_preserved.get(key)),
            }
            for key in sorted(changed)
        },
        "tunnel": {
            "previous_pid": before_preserved.get("tunnel_pid"),
            "current_pid": current_pid,
            "previous_identity_sha256": _json_sha(old_identity),
            "current_identity_sha256": _json_sha(current_identity),
            "tunnel_id": status["tunnel_id"],
            "profile_sha256": status["profile_sha256"],
            "generation": status["generation"],
            "canonical_ready": True,
            "operational_verified": True,
        },
    }
    if expected_proof is not None and proof != expected_proof:
        raise RuntimeError("sealed runtime reconciliation proof mismatch")
    return proof


def _exact_git_file(repo: Path, commit: str) -> tuple[Path, str, bytes, str]:
    repository = Path(repo).expanduser().resolve(strict=True)
    revision = pc._release_revision(commit)
    resolved = str(
        pc._git_offline_run(
            repository, "rev-parse", "--verify", f"{revision}^{{commit}}"
        )
    ).strip()
    if resolved != revision:
        raise RuntimeError("exact AAR overlay source commit is unavailable")
    spec = f"{revision}:{OVERLAY_RELATIVE.as_posix()}"
    blob_oid = str(pc._git_offline_run(repository, "rev-parse", "--verify", spec)).strip()
    content = pc._git_offline_run(repository, "cat-file", "blob", blob_oid, binary=True)
    if not blob_oid or not isinstance(content, bytes):
        raise RuntimeError("exact AAR overlay source blob is unavailable")
    return repository, revision, content, blob_oid


def _exact_git_tree(repo: Path, commit: str) -> tuple[Path, str, dict[str, str]]:
    repository = Path(repo).expanduser().resolve(strict=True)
    revision = pc._release_revision(commit)
    resolved = str(
        pc._git_offline_run(
            repository, "rev-parse", "--verify", f"{revision}^{{commit}}"
        )
    ).strip()
    if resolved != revision:
        raise RuntimeError("exact AAR base source commit is unavailable")
    listing = pc._git_offline_run(
        repository, "ls-tree", "-rz", "--full-tree", revision, binary=True
    )
    hashes: dict[str, str] = {}
    for entry in listing.split(b"\0"):
        if not entry:
            continue
        try:
            metadata, raw_relative = entry.split(b"\t", 1)
            mode, kind, object_id = metadata.decode("ascii").split(" ", 2)
            relative = raw_relative.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as error:
            raise RuntimeError("exact AAR base source tree is malformed") from error
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise RuntimeError(f"unsupported exact AAR base member: {relative}")
        payload = pc._git_offline_run(
            repository, "cat-file", "blob", object_id, binary=True
        )
        hashes[relative] = _sha(payload)
    if not hashes:
        raise RuntimeError("exact AAR base source tree is empty")
    return repository, revision, hashes


def _installed_base_source(root: Path, repo: Path, commit: str) -> dict:
    installed = Path(root).expanduser().resolve(strict=True)
    repository, revision, git_hashes = _exact_git_tree(repo, commit)
    pc._validate_owned_tree_source(installed, "installed Adaptive Delivery Core")
    manifest_raw = _private_bytes(
        installed / control.AAR_INSTALL_MANIFEST,
        "installed Adaptive Delivery manifest",
    )
    manifest, manifest_files = control._parse_aar_install_manifest(
        manifest_raw, revision
    )
    source_root = manifest.get("source_root")
    if (
        not isinstance(source_root, str)
        or source_root != str(repository)
    ):
        raise RuntimeError(
            "installed Adaptive Delivery source root differs from exact repository"
        )
    if manifest_files != git_hashes:
        raise RuntimeError("installed Adaptive Delivery manifest differs from exact commit")
    observed: dict[str, str] = {}
    for member in sorted(installed.rglob("*")):
        if member.is_symlink() or (not member.is_dir() and not member.is_file()):
            raise RuntimeError("installed Adaptive Delivery tree contains unsupported members")
        if member.is_file():
            pc._validate_owned_file_source(member, "installed Adaptive Delivery file")
            observed[str(member.relative_to(installed))] = pc._sha256_file(member)
    expected = {**manifest_files, control.AAR_INSTALL_MANIFEST: _sha(manifest_raw)}
    extras = set(observed) - set(expected)
    unsupported_extras = {
        relative
        for relative in extras
        if not (
            Path(relative).suffix == ".pyc"
            and "__pycache__" in Path(relative).parts
        )
    }
    if set(expected) - set(observed) or unsupported_extras:
        raise RuntimeError("installed Adaptive Delivery tree differs from manifest whitelist")
    for relative, digest in expected.items():
        if observed.get(relative) != digest:
            raise RuntimeError(f"installed file hash mismatch: {relative}")
    return {
        "root": installed,
        "repository": repository,
        "revision": revision,
        "manifest_sha256": _sha(manifest_raw),
        "files": expected,
    }


def _copy_installed_base(source: dict, destination: Path) -> None:
    destination.mkdir(mode=0o700)
    for relative in sorted(source["files"]):
        origin = source["root"] / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.write_bytes(origin.read_bytes())


def _require_descendant(repository: Path, previous: str, revision: str) -> None:
    try:
        pc._git_offline_run(
            repository, "merge-base", "--is-ancestor", previous, revision
        )
    except RuntimeError as error:
        raise RuntimeError("new AAR base is not a descendant of the active base") from error


def _context_without_overlay(context: dict) -> dict:
    normalized = dict(context)
    state = dict(context["state"])
    state.pop(PROVENANCE, None)
    normalized["state"] = state
    return normalized


def _context_with_previous_tunnel(context: dict, previous_patch: Path) -> dict:
    before = json.loads(
        _private_bytes(
            previous_patch / "runtime-before.json", "previous overlay rollback state"
        )
    )
    if not isinstance(before, dict):
        raise RuntimeError("previous overlay rollback state is invalid")
    normalized = dict(context)
    state = dict(context["state"])
    for key in ("tunnel_pid", "tunnel_process_identity"):
        if key in before:
            state[key] = before[key]
        else:
            state.pop(key, None)
    normalized["state"] = state
    return normalized


def _active_base(context: dict) -> tuple[Path, dict, list[str]]:
    active = context["state"].get(control.PROVENANCE)
    if not isinstance(active, dict) or not isinstance(active.get("manifest"), str):
        raise RuntimeError("Runtime hook overlay requires the active sealed control patch")
    normalized = _context_without_overlay(context)
    patch, record, *_ = control.verify_patch(
        normalized, Path(active["manifest"]).parent
    )
    if active != control._provenance(patch, record):
        raise RuntimeError("active control patch provenance mismatch")
    expected_bridge = [str(patch / "bin" / "local-tool-bridge")]
    if context["bridge_identity"].get("argv") != expected_bridge:
        raise RuntimeError("active Bridge does not match the sealed control patch")
    python = str(context["hook_identity"].get("argv", [""])[0])
    if not python or not Path(python).is_absolute():
        raise RuntimeError("active Runtime hook interpreter is invalid")
    hook_command = [
        python,
        str(
            patch
            / "runtime-host-tool-hook"
            / pc.RUNTIME_HOST_TOOL_HOOK_ENTRYPOINT_RELATIVE_PATH
        ),
        "--serve-unix-socket",
        str(context["hook_socket"]),
    ]
    return patch, record, hook_command


def _prepare_modes(patch: Path) -> None:
    for item in (patch, *patch.rglob("*")):
        if item.is_symlink():
            raise RuntimeError("Runtime hook overlay patch may not contain symlinks")
        if item.is_dir():
            item.chmod(0o700)
        elif item.is_file():
            item.chmod(0o600)
    (patch / LAUNCHER_NAME).chmod(0o700)


def _seal(patch: Path) -> None:
    launcher = patch / LAUNCHER_NAME
    base_entry = (
        patch
        / "base-runtime-host-tool-hook"
        / pc.RUNTIME_HOST_TOOL_HOOK_ENTRYPOINT_RELATIVE_PATH
    )
    for item in sorted(patch.rglob("*"), key=lambda value: len(value.parts), reverse=True):
        if item.is_dir():
            item.chmod(0o500)
        elif item.is_file():
            item.chmod(0o500 if item in (launcher, base_entry) else 0o400)
    patch.chmod(0o500)


def _base_hashes(root: Path, *, immutable: bool) -> dict[str, str]:
    return control._file_hashes(root, immutable=immutable)


def _overlay_command(context: dict, patch: Path, socket_path: Path) -> list[str]:
    python = str(context["hook_identity"]["argv"][0])
    return [python, str(patch / LAUNCHER_NAME), "--serve-unix-socket", str(socket_path)]


class Operations(control.Operations):
    def stop_hook(self, identity: dict, socket_path: Path, socket_identity: dict) -> None:
        if pc._runtime_hook_socket_identity(socket_path) != socket_identity:
            raise HookStopError("Runtime hook socket identity changed", fully_stopped=False)
        if not self.identity_matches(identity):
            raise HookStopError("Runtime hook process identity changed", fully_stopped=False)
        errors = []
        try:
            pc._stop_verified_process_identity(identity, process_group=False)
        except Exception as error:
            errors.append(f"Runtime hook stop failed: {error}")
        live = self.identity_matches(identity)
        if live:
            errors.append("Runtime hook remains live after stop")
        if not live:
            try:
                self._remove_socket(
                    socket_path, socket_identity, pc._runtime_hook_socket_identity
                )
            except Exception as error:
                errors.append(f"Runtime hook socket cleanup failed: {error}")
        fully_stopped = not live and not socket_path.exists() and not socket_path.is_symlink()
        if errors or not fully_stopped:
            raise HookStopError(
                "; ".join(errors) or "Runtime hook cleanup is incomplete",
                fully_stopped=fully_stopped,
            )

    def start_hook(self, command, environment, socket_path: Path, logs: Path, label: str) -> dict:
        if socket_path.exists() or socket_path.is_symlink():
            raise HookStartError("Runtime hook start socket already exists", cleanup_complete=False)
        pc._private_dir(socket_path.parent)
        process = None
        identity = None
        socket_identity = None
        failure = None
        cleanup_errors = []
        try:
            process = pc._spawn_logged(
                list(command),
                logs / f"hook-overlay-{label}.out",
                logs / f"hook-overlay-{label}.err",
                cwd=Path(command[1]).parent.parent,
                env=dict(environment),
            )
            identity = pc._capture_process_identity(
                process.pid,
                expected_executable=Path(command[0]),
                expected_argv=list(command),
            )
            socket_identity = pc._wait_for_runtime_hook(
                process, {"runtime_hook_socket": socket_path}
            )
            self._smoke_hook_fail_closed(socket_path)
            return {
                "hook_identity": identity,
                "hook_socket_identity": socket_identity,
            }
        except Exception as error:
            failure = error
        if not isinstance(identity, dict):
            if process is not None:
                cleanup_errors.append("Runtime hook exact identity was not captured")
        else:
            if self.identity_matches(identity):
                try:
                    pc._stop_verified_process_identity(
                        identity, process=process, process_group=False
                    )
                except Exception as error:
                    cleanup_errors.append(f"Runtime hook stop failed: {error}")
            if self.identity_matches(identity):
                cleanup_errors.append("Runtime hook remains live after cleanup")
            elif socket_identity is not None:
                try:
                    self._remove_socket(
                        socket_path,
                        socket_identity,
                        pc._runtime_hook_socket_identity,
                    )
                except Exception as error:
                    cleanup_errors.append(f"Runtime hook socket cleanup failed: {error}")
            elif socket_path.exists() or socket_path.is_symlink():
                cleanup_errors.append("Runtime hook socket exists without a captured identity")
        detail = f"; cleanup: {'; '.join(cleanup_errors)}" if cleanup_errors else ""
        raise HookStartError(
            f"{failure}{detail}", cleanup_complete=not cleanup_errors
        ) from failure

    def stop_started_hook(self, started: dict, socket_path: Path) -> None:
        self.stop_hook(
            started["hook_identity"], socket_path, started["hook_socket_identity"]
        )


def _stage_upgrade_patch(
    runtime_root: Path,
    *,
    installed_base: Path,
    base_repo: Path,
    base_commit: str,
    reconcile_runtime_sha256: str,
    ops,
) -> Path:
    with host_repair.bounded_install_lock(runtime_root):
        context = control.load_context(runtime_root, ops)
        active = context["state"].get(PROVENANCE)
        if not isinstance(active, dict) or not isinstance(active.get("manifest"), str):
            raise RuntimeError("Runtime hook base upgrade requires an active sealed overlay")
        previous_patch = Path(active["manifest"]).parent.resolve(strict=True)
        if pc._sha256_file(previous_patch / "manifest.json") != active.get(
            "manifest_sha256"
        ):
            raise RuntimeError("active Runtime hook overlay provenance mismatch")
        reconciliation = _runtime_reconciliation(
            context,
            previous_patch,
            expected_runtime_sha256=reconcile_runtime_sha256,
        )
        previous_patch, previous_record, *_ = verify_patch(
            context,
            previous_patch,
            runtime_reconciliation=reconciliation,
        )
        if previous_record.get("schema_version") != 1:
            raise RuntimeError("Runtime hook base upgrade requires the original overlay schema")
        if active != _provenance(previous_patch, previous_record):
            raise RuntimeError("active Runtime hook overlay provenance mismatch")
        previous_command = _overlay_command(
            context, previous_patch, context["hook_socket"]
        )
        if context["hook_identity"].get("argv") != previous_command:
            raise RuntimeError("active Runtime hook overlay command mismatch")
        installed = _installed_base_source(installed_base, base_repo, base_commit)
        _require_descendant(
            installed["repository"],
            previous_record["base"]["aar_revision"],
            installed["revision"],
        )
        hook_environment = control._safe_environment(
            ops.environment(context["hook_identity"]), "Runtime hook"
        )
        control._assert_unchanged(context, ops)
        patch_root = context["root"] / PATCH_ROOT
        pc._private_dir(patch_root)
        patch = patch_root / f"hook-overlay-{uuid.uuid4()}"
        patch.mkdir(mode=0o700)
        try:
            base_target = patch / "base-runtime-host-tool-hook"
            _copy_installed_base(installed, base_target)
            overlay_target = patch / "overlay" / "runtime_host_tool_hook.py"
            overlay_target.parent.mkdir(mode=0o700)
            overlay_target.write_bytes(
                (previous_patch / "overlay" / "runtime_host_tool_hook.py").read_bytes()
            )
            (patch / LAUNCHER_NAME).write_bytes(LAUNCHER_BYTES)
            _prepare_modes(patch)
            if _base_hashes(base_target, immutable=False) != installed["files"]:
                raise RuntimeError("copied Runtime hook base differs from installed Core")
            pc._write_json_private(patch / "hook-environment.json", hook_environment)
            pc._write_text_private(
                patch / "runtime-before.json", context["raw"].decode("utf-8"), 0o600
            )
            record = {
                "schema_version": 2,
                "component": PATCH_COMPONENT,
                "patch_dir": str(patch),
                "base": {
                    "control_manifest": previous_record["base"]["control_manifest"],
                    "control_manifest_sha256": previous_record["base"][
                        "control_manifest_sha256"
                    ],
                    "repository": str(installed["repository"]),
                    "installed_root": str(installed["root"]),
                    "previous_aar_revision": previous_record["base"]["aar_revision"],
                    "aar_revision": installed["revision"],
                    "install_manifest_sha256": installed["manifest_sha256"],
                    "files": installed["files"],
                },
                "overlay": previous_record["overlay"],
                "previous_overlay": {
                    "manifest": str(previous_patch / "manifest.json"),
                    "manifest_sha256": pc._sha256_file(previous_patch / "manifest.json"),
                    "provenance": active,
                },
                "runtime_reconciliation": reconciliation,
                "launcher_sha256": _sha(LAUNCHER_BYTES),
                "hook_environment_sha256": pc._sha256_file(
                    patch / "hook-environment.json"
                ),
                "runtime_before_sha256": _sha(context["raw"]),
                "preserved_runtime_state_sha256": _json_sha(
                    _preserved_runtime_state(context["state"])
                ),
                "old_hook_identity": context["hook_identity"],
                "old_hook_socket_identity": context["state"][
                    "runtime_hook_socket_identity"
                ],
                "old_hook_command": previous_command,
            }
            pc._write_json_private(patch / "manifest.json", record)
            control._assert_unchanged(context, ops)
            _seal(patch)
            return patch
        except Exception:
            if patch.exists():
                for item in (patch, *patch.rglob("*")):
                    try:
                        item.chmod(0o700 if item.is_dir() else 0o600)
                    except OSError:
                        pass
                shutil.rmtree(patch)
            raise


def stage_patch(
    runtime_root: Path,
    *,
    aar_repo: Path | None = None,
    aar_commit: str | None = None,
    installed_base: Path | None = None,
    base_repo: Path | None = None,
    base_commit: str | None = None,
    reconcile_runtime_sha256: str | None = None,
    ops=None,
) -> Path:
    ops = ops or Operations()
    upgrading = any(
        value is not None for value in (installed_base, base_repo, base_commit)
    )
    if upgrading:
        if None in (installed_base, base_repo, base_commit):
            raise RuntimeError(
                "base upgrade requires installed_base, base_repo, and base_commit"
            )
        if aar_repo is not None or aar_commit is not None:
            raise RuntimeError("base upgrade derives the overlay from active provenance")
        if reconcile_runtime_sha256 is None:
            raise RuntimeError(
                "base upgrade requires an explicit runtime reconciliation SHA-256"
            )
        return _stage_upgrade_patch(
            runtime_root,
            installed_base=installed_base,
            base_repo=base_repo,
            base_commit=base_commit,
            reconcile_runtime_sha256=reconcile_runtime_sha256,
            ops=ops,
        )
    if aar_repo is None or aar_commit is None:
        raise RuntimeError("initial overlay stage requires aar_repo and aar_commit")
    if reconcile_runtime_sha256 is not None:
        raise RuntimeError("initial overlay stage does not accept runtime reconciliation")
    repository, revision, overlay_bytes, blob_oid = _exact_git_file(
        aar_repo, aar_commit
    )
    with host_repair.bounded_install_lock(runtime_root):
        context = control.load_context(runtime_root, ops)
        if PROVENANCE in context["state"]:
            raise RuntimeError("an active Runtime hook overlay already exists")
        base_patch, base_record, base_command = _active_base(context)
        if context["hook_identity"].get("argv") != base_command:
            raise RuntimeError("active Runtime hook does not match the sealed base tree")
        hook_environment = control._safe_environment(
            ops.environment(context["hook_identity"]), "Runtime hook"
        )
        control._assert_unchanged(context, ops)
        patch_root = context["root"] / PATCH_ROOT
        pc._private_dir(patch_root)
        patch = patch_root / f"hook-overlay-{uuid.uuid4()}"
        patch.mkdir(mode=0o700)
        base_source = base_patch / "runtime-host-tool-hook"
        base_target = patch / "base-runtime-host-tool-hook"
        shutil.copytree(base_source, base_target)
        overlay_target = patch / "overlay" / "runtime_host_tool_hook.py"
        overlay_target.parent.mkdir(mode=0o700)
        overlay_target.write_bytes(overlay_bytes)
        launcher = patch / LAUNCHER_NAME
        launcher.write_bytes(LAUNCHER_BYTES)
        _prepare_modes(patch)
        base_hashes = _base_hashes(base_target, immutable=False)
        expected_base_hashes = {
            key.removeprefix("runtime-host-tool-hook/"): value
            for key, value in base_record["artifact_hashes"].items()
            if key.startswith("runtime-host-tool-hook/")
        }
        if base_hashes != expected_base_hashes:
            raise RuntimeError("copied Runtime hook base differs from the sealed control patch")
        pc._write_json_private(patch / "hook-environment.json", hook_environment)
        pc._write_text_private(
            patch / "runtime-before.json", context["raw"].decode("utf-8"), 0o600
        )
        record = {
            "schema_version": 1,
            "component": PATCH_COMPONENT,
            "patch_dir": str(patch),
            "base": {
                "control_manifest": str(base_patch / "manifest.json"),
                "control_manifest_sha256": pc._sha256_file(base_patch / "manifest.json"),
                "aar_revision": base_record["sources"]["aar_commit"],
                "install_manifest_sha256": base_record["sources"]["aar_install_manifest_sha256"],
                "files": expected_base_hashes,
            },
            "overlay": {
                "repository": str(repository),
                "aar_commit": revision,
                "git_blob_oid": blob_oid,
                "sha256": _sha(overlay_bytes),
            },
            "launcher_sha256": _sha(LAUNCHER_BYTES),
            "hook_environment_sha256": pc._sha256_file(patch / "hook-environment.json"),
            "runtime_before_sha256": _sha(context["raw"]),
            "preserved_runtime_state_sha256": _json_sha(
                _preserved_runtime_state(context["state"])
            ),
            "old_hook_identity": context["hook_identity"],
            "old_hook_socket_identity": context["state"]["runtime_hook_socket_identity"],
            "old_hook_command": base_command,
        }
        pc._write_json_private(patch / "manifest.json", record)
        control._assert_unchanged(context, ops)
        _seal(patch)
        return patch


def verify_patch(
    context: dict,
    patch: Path,
    *,
    runtime_reconciliation: dict | None = None,
):
    if Path(patch).is_symlink():
        raise RuntimeError("Runtime hook overlay patch may not be a symlink")
    patch = Path(patch).resolve(strict=True)
    if patch.parent != context["root"] / PATCH_ROOT:
        raise RuntimeError("Runtime hook overlay is outside the production patch root")
    pc._validate_owned_tree_source(patch, "Runtime hook overlay patch")
    expected = {
        "base-runtime-host-tool-hook",
        "overlay",
        LAUNCHER_NAME,
        "hook-environment.json",
        "runtime-before.json",
        "manifest.json",
    }
    if {item.name for item in patch.iterdir()} != expected:
        raise RuntimeError("Runtime hook overlay contains unlisted artifacts")
    for item in (patch, *patch.rglob("*")):
        if stat.S_IMODE(item.lstat().st_mode) & 0o277:
            raise RuntimeError("Runtime hook overlay is not sealed")
    record = json.loads(_private_bytes(patch / "manifest.json", "Runtime hook overlay manifest"))
    schema = record.get("schema_version")
    current_preserved_hash = _json_sha(_preserved_runtime_state(context["state"]))
    expected_preserved_hash = (
        runtime_reconciliation.get("previous_preserved_runtime_state_sha256")
        if isinstance(runtime_reconciliation, dict)
        else current_preserved_hash
    )
    if (
        schema not in {1, 2}
        or record.get("component") != PATCH_COMPONENT
        or record.get("patch_dir") != str(patch)
        or record.get("preserved_runtime_state_sha256")
        != expected_preserved_hash
    ):
        raise RuntimeError("Runtime hook overlay provenance mismatch")
    if runtime_reconciliation is not None and (
        schema != 1
        or runtime_reconciliation.get("current_preserved_runtime_state_sha256")
        != current_preserved_hash
    ):
        raise RuntimeError("Runtime hook overlay reconciliation provenance mismatch")
    active_base_context = context
    if runtime_reconciliation is not None:
        active_base_context = _context_with_previous_tunnel(context, patch)
    elif schema == 2:
        previous_manifest_value = (record.get("previous_overlay") or {}).get(
            "manifest"
        )
        if not isinstance(previous_manifest_value, str):
            raise RuntimeError("Runtime hook previous overlay provenance is missing")
        active_base_context = _context_with_previous_tunnel(
            context, Path(previous_manifest_value).parent
        )
    base_patch, base_record, base_command = _active_base(active_base_context)
    base = record.get("base")
    rollback_provenance = None
    if schema == 1:
        expected_base = {
            "control_manifest": str(base_patch / "manifest.json"),
            "control_manifest_sha256": pc._sha256_file(base_patch / "manifest.json"),
            "aar_revision": base_record["sources"]["aar_commit"],
            "install_manifest_sha256": base_record["sources"][
                "aar_install_manifest_sha256"
            ],
            "files": {
                key.removeprefix("runtime-host-tool-hook/"): value
                for key, value in base_record["artifact_hashes"].items()
                if key.startswith("runtime-host-tool-hook/")
            },
        }
        if base != expected_base:
            raise RuntimeError("Runtime hook overlay base provenance mismatch")
    else:
        if not isinstance(base, dict):
            raise RuntimeError("Runtime hook overlay base provenance mismatch")
        if (
            base.get("control_manifest") != str(base_patch / "manifest.json")
            or base.get("control_manifest_sha256")
            != pc._sha256_file(base_patch / "manifest.json")
            or base.get("previous_aar_revision")
            != base_record["sources"]["aar_commit"]
        ):
            raise RuntimeError("Runtime hook overlay base provenance mismatch")
        repository, revision, git_hashes = _exact_git_tree(
            Path(str(base.get("repository") or "")),
            str(base.get("aar_revision") or ""),
        )
        if str(repository) != base.get("repository") or revision != base.get(
            "aar_revision"
        ):
            raise RuntimeError("Runtime hook overlay base Git provenance mismatch")
        _require_descendant(repository, base["previous_aar_revision"], revision)
        sealed_manifest = _private_bytes(
            patch / "base-runtime-host-tool-hook" / control.AAR_INSTALL_MANIFEST,
            "Runtime hook overlay base install manifest",
        )
        sealed_manifest_record, manifest_files = control._parse_aar_install_manifest(
            sealed_manifest, revision
        )
        if (
            sealed_manifest_record.get("source_root") != str(repository)
            or manifest_files != git_hashes
            or base.get("install_manifest_sha256") != _sha(sealed_manifest)
            or base.get("files")
            != {**git_hashes, control.AAR_INSTALL_MANIFEST: _sha(sealed_manifest)}
        ):
            raise RuntimeError("Runtime hook overlay base Git provenance mismatch")
        previous = record.get("previous_overlay")
        if not isinstance(previous, dict) or not isinstance(
            previous.get("manifest"), str
        ):
            raise RuntimeError("Runtime hook previous overlay provenance is missing")
        previous_manifest = Path(previous["manifest"])
        reconciliation = record.get("runtime_reconciliation")
        if not isinstance(reconciliation, dict):
            raise RuntimeError("Runtime hook reconciliation proof is missing")
        _runtime_reconciliation(
            context,
            previous_manifest.parent,
            expected_proof=reconciliation,
        )
        previous_patch, previous_record, *_ = verify_patch(
            context,
            previous_manifest.parent,
            runtime_reconciliation=reconciliation,
        )
        if previous_record.get("schema_version") != 1:
            raise RuntimeError("Runtime hook previous overlay schema mismatch")
        rollback_provenance = _provenance(previous_patch, previous_record)
        if (
            previous_manifest != previous_patch / "manifest.json"
            or previous.get("manifest_sha256") != pc._sha256_file(previous_manifest)
            or previous.get("provenance") != rollback_provenance
        ):
            raise RuntimeError("Runtime hook previous overlay provenance mismatch")
        base_command = _overlay_command(context, previous_patch, context["hook_socket"])
    if _base_hashes(patch / "base-runtime-host-tool-hook", immutable=True) != base["files"]:
        raise RuntimeError("Runtime hook overlay base tree hash mismatch")
    overlay = record.get("overlay")
    overlay_path = patch / "overlay" / "runtime_host_tool_hook.py"
    if (
        not isinstance(overlay, dict)
        or pc._release_revision(overlay.get("aar_commit")) != overlay.get("aar_commit")
        or pc._sha256_file(overlay_path) != overlay.get("sha256")
    ):
        raise RuntimeError("Runtime hook overlay source hash mismatch")
    if control._file_hashes(patch / "overlay", immutable=True) != {
        "runtime_host_tool_hook.py": overlay["sha256"]
    }:
        raise RuntimeError("Runtime hook overlay source tree is not exact")
    source_repo, source_revision, source_bytes, source_blob_oid = _exact_git_file(
        Path(str(overlay.get("repository") or "")), overlay["aar_commit"]
    )
    if (
        str(source_repo) != overlay.get("repository")
        or source_revision != overlay["aar_commit"]
        or source_blob_oid != overlay.get("git_blob_oid")
        or source_bytes != overlay_path.read_bytes()
    ):
        raise RuntimeError("Runtime hook overlay Git source provenance mismatch")
    if schema == 2 and record.get("overlay") != previous_record.get("overlay"):
        raise RuntimeError("Runtime hook overlay differs from previous sealed overlay")
    if (patch / LAUNCHER_NAME).read_bytes() != LAUNCHER_BYTES or record.get(
        "launcher_sha256"
    ) != _sha(LAUNCHER_BYTES):
        raise RuntimeError("Runtime hook overlay launcher mismatch")
    executable_paths = (
        patch / LAUNCHER_NAME,
        patch
        / "base-runtime-host-tool-hook"
        / pc.RUNTIME_HOST_TOOL_HOOK_ENTRYPOINT_RELATIVE_PATH,
    )
    if any(stat.S_IMODE(path.stat().st_mode) != 0o500 for path in executable_paths):
        raise RuntimeError("Runtime hook overlay executable mode mismatch")
    runtime_before = _private_bytes(patch / "runtime-before.json", "overlay rollback state")
    if _sha(runtime_before) != record.get("runtime_before_sha256"):
        raise RuntimeError("Runtime hook overlay rollback state hash mismatch")
    before_state = json.loads(runtime_before)
    if (
        not isinstance(before_state, dict)
        or record.get("old_hook_identity")
        != before_state.get("runtime_hook_process_identity")
        or record.get("old_hook_socket_identity")
        != before_state.get("runtime_hook_socket_identity")
        or record.get("old_hook_command")
        != (before_state.get("runtime_hook_process_identity") or {}).get("argv")
    ):
        raise RuntimeError("Runtime hook overlay rollback identity mismatch")
    hook_environment = control._safe_environment(
        json.loads(_private_bytes(patch / "hook-environment.json", "overlay Hook environment")),
        "Runtime hook",
    )
    if pc._sha256_file(patch / "hook-environment.json") != record.get(
        "hook_environment_sha256"
    ):
        raise RuntimeError("Runtime hook overlay environment hash mismatch")
    if record.get("old_hook_command") != base_command:
        raise RuntimeError("Runtime hook overlay rollback command mismatch")
    return (
        patch,
        record,
        runtime_before,
        hook_environment,
        base_command,
        rollback_provenance,
    )


def _provenance(patch: Path, record: dict) -> dict:
    return {
        "manifest": str(patch / "manifest.json"),
        "manifest_sha256": pc._sha256_file(patch / "manifest.json"),
        "base_control_manifest_sha256": record["base"]["control_manifest_sha256"],
        "base_aar_revision": record["base"]["aar_revision"],
        "base_files": record["base"]["files"],
        "overlay_aar_commit": record["overlay"]["aar_commit"],
        "overlay_git_blob_oid": record["overlay"]["git_blob_oid"],
        "overlay_sha256": record["overlay"]["sha256"],
        "launcher_sha256": record["launcher_sha256"],
    }


def _publish(context: dict, expected: bytes, started: dict, provenance: dict | None) -> bytes:
    if _private_bytes(context["path"], "production runtime state") != expected:
        raise RuntimeError("production runtime CAS failed")
    state = json.loads(expected)
    state["runtime_hook_pid"] = started["hook_identity"]["pid"]
    state["runtime_hook_process_identity"] = started["hook_identity"]
    state["runtime_hook_socket_identity"] = started["hook_socket_identity"]
    if provenance is None:
        state.pop(PROVENANCE, None)
    else:
        state[PROVENANCE] = provenance
    pc._write_json_private(context["path"], state)
    observed = _private_bytes(context["path"], "production runtime state")
    if json.loads(observed) != state:
        raise RuntimeError("Runtime hook overlay state readback failed")
    if _preserved_runtime_state(json.loads(observed)) != _preserved_runtime_state(
        json.loads(expected)
    ):
        raise RuntimeError("Runtime hook overlay changed unrelated runtime state")
    return observed


def execute_patch(runtime_root: Path, patch: Path, *, rollback=False, ops=None) -> dict:
    ops = ops or Operations()
    with host_repair.bounded_install_lock(runtime_root):
        context = control.load_context(runtime_root, ops)
        (
            patch,
            record,
            runtime_before,
            hook_env,
            base_command,
            rollback_provenance,
        ) = verify_patch(context, patch)
        expected_provenance = _provenance(patch, record)
        active = context["state"].get(PROVENANCE)
        overlay_command = _overlay_command(context, patch, context["hook_socket"])
        if rollback:
            if active != expected_provenance:
                raise RuntimeError("active Runtime hook overlay provenance mismatch")
            if context["hook_identity"].get("argv") != overlay_command:
                raise RuntimeError("active Runtime hook overlay command mismatch")
            target_command = base_command
        else:
            if active != rollback_provenance or context["raw"] != runtime_before:
                raise RuntimeError("Runtime hook overlay apply baseline mismatch")
            if context["hook_identity"].get("argv") != base_command:
                raise RuntimeError("active Runtime hook base command mismatch")
            target_command = overlay_command
        if control._safe_environment(
            ops.environment(context["hook_identity"]), "Runtime hook"
        ) != hook_env:
            raise RuntimeError("active Runtime hook environment differs from sealed overlay")
        control._assert_unchanged(context, ops)

        side_root = Path(tempfile.mkdtemp(prefix="lab-ho-", dir="/tmp"))
        side_root.chmod(0o700)
        side_socket = side_root / "hook.sock"
        side_started = None
        side_clean = False
        side_command = list(target_command)
        side_command[-1] = str(side_socket)
        try:
            side_started = ops.start_hook(
                side_command, hook_env, side_socket, side_root, "side-smoke"
            )
            ops.stop_started_hook(side_started, side_socket)
            side_clean = True
        except HookStopError as error:
            side_clean = error.fully_stopped
            raise
        except HookStartError as error:
            side_clean = error.cleanup_complete
            raise
        finally:
            if side_clean:
                shutil.rmtree(side_root)
        control._assert_unchanged(context, ops)

        prior_provenance = active
        old_stopped = False
        target_started = None
        try:
            try:
                ops.stop_hook(
                    context["hook_identity"],
                    context["hook_socket"],
                    context["state"]["runtime_hook_socket_identity"],
                )
                old_stopped = True
            except HookStopError as stop_error:
                old_stopped = stop_error.fully_stopped
                raise
            if not ops.identity_matches(context["bridge_identity"]):
                raise RuntimeError("Bridge identity changed while replacing Runtime hook")
            target_started = ops.start_hook(
                target_command,
                hook_env,
                context["hook_socket"],
                context["root"] / "logs",
                "rollback" if rollback else "apply",
            )
            if not ops.identity_matches(context["bridge_identity"]):
                raise RuntimeError("Bridge identity changed after Runtime hook replacement")
            written = _publish(
                context,
                context["raw"],
                target_started,
                rollback_provenance if rollback else expected_provenance,
            )
            return {
                "ok": True,
                "action": "rollback" if rollback else "apply",
                "runtime_hook_pid": target_started["hook_identity"]["pid"],
                "bridge_pid": context["bridge_identity"]["pid"],
                "runtime_sha256": _sha(written),
            }
        except Exception as error:
            safe_to_restore = old_stopped
            if target_started is not None:
                try:
                    ops.stop_started_hook(target_started, context["hook_socket"])
                except HookStopError as cleanup_error:
                    safe_to_restore = cleanup_error.fully_stopped
                except Exception:
                    safe_to_restore = False
            elif isinstance(error, HookStartError):
                safe_to_restore = old_stopped and error.cleanup_complete
            socket_clear = (
                not context["hook_socket"].exists()
                and not context["hook_socket"].is_symlink()
            )
            runtime_unchanged = (
                _private_bytes(context["path"], "production runtime state")
                == context["raw"]
            )
            safe_to_restore = safe_to_restore and socket_clear and runtime_unchanged
            if not safe_to_restore:
                raise RuntimeError(
                    f"{error}; Runtime hook recovery is fail-closed: "
                    f"bridge_live={ops.identity_matches(context['bridge_identity'])}, "
                    f"old_hook_live={ops.identity_matches(context['hook_identity'])}, "
                    f"hook_socket_clear={socket_clear}, "
                    f"runtime_state_unchanged={runtime_unchanged}"
                ) from error
            restore_command = overlay_command if rollback else base_command
            restored = ops.start_hook(
                restore_command,
                hook_env,
                context["hook_socket"],
                context["root"] / "logs",
                "restore",
            )
            _publish(context, context["raw"], restored, prior_provenance)
            raise RuntimeError(
                f"{error}; prior Runtime hook restored with a fresh identity"
            ) from error


def check(
    runtime_root: Path,
    *,
    patch: Path | None = None,
    reconcile_runtime_sha256: str | None = None,
    ops=None,
) -> dict:
    ops = ops or Operations()
    context = control.load_context(runtime_root, ops)
    active = context["state"].get(PROVENANCE)
    resolved = None
    record = None
    def checked(candidate: Path):
        if reconcile_runtime_sha256 is None:
            return verify_patch(context, candidate)
        reconciliation = _runtime_reconciliation(
            context,
            Path(candidate).resolve(strict=True),
            expected_runtime_sha256=reconcile_runtime_sha256,
        )
        return verify_patch(
            context,
            candidate,
            runtime_reconciliation=reconciliation,
        )

    if patch is not None:
        resolved, record, *_ = checked(patch)
        expected = _provenance(resolved, record)
        if active is not None and active != expected:
            raise RuntimeError("active Runtime hook overlay provenance mismatch")
    elif active is not None:
        manifest = active.get("manifest") if isinstance(active, dict) else None
        if not isinstance(manifest, str):
            raise RuntimeError("active Runtime hook overlay provenance mismatch")
        resolved, record, *_ = checked(Path(manifest).parent)
        if active != _provenance(resolved, record):
            raise RuntimeError("active Runtime hook overlay provenance mismatch")
    if active is not None:
        expected_command = _overlay_command(context, resolved, context["hook_socket"])
        canonical_guard = (
            resolved
            / "base-runtime-host-tool-hook"
            / "scripts"
            / "control_event_guard.py"
        )
    else:
        base_patch, _, expected_command = _active_base(context)
        canonical_guard = (
            base_patch
            / "runtime-host-tool-hook"
            / "scripts"
            / "control_event_guard.py"
        )
    if context["hook_identity"].get("argv") != expected_command:
        raise RuntimeError("active Runtime hook command does not match overlay state")
    if not canonical_guard.is_file():
        raise RuntimeError("Runtime hook canonical guard is missing")
    return {
        "ok": True,
        "active": active is not None,
        "bridge_pid": context["bridge_identity"]["pid"],
        "runtime_hook_pid": context["hook_identity"]["pid"],
        "canonical_guard": str(canonical_guard),
        "runtime_sha256": _sha(context["raw"]),
        "production_written": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Production Runtime Hook overlay repair")
    parser.add_argument("action", choices=("check", "stage", "apply", "rollback"))
    parser.add_argument("--runtime-root", type=Path, default=pc.PRODUCTION_RUNTIME_ROOT)
    parser.add_argument("--patch-dir", type=Path)
    parser.add_argument("--aar-repo", type=Path)
    parser.add_argument("--aar-commit")
    parser.add_argument("--installed-base", type=Path)
    parser.add_argument("--base-repo", type=Path)
    parser.add_argument("--base-commit")
    parser.add_argument("--reconcile-runtime-sha256")
    args = parser.parse_args(argv)
    if args.action == "stage":
        upgrading = any(
            value is not None
            for value in (args.installed_base, args.base_repo, args.base_commit)
        )
        if upgrading and None in (
            args.installed_base,
            args.base_repo,
            args.base_commit,
        ):
            parser.error(
                "base upgrade requires --installed-base, --base-repo, and --base-commit"
            )
        if not upgrading and (args.aar_repo is None or args.aar_commit is None):
            parser.error("initial stage requires --aar-repo and --aar-commit")
        if upgrading and (args.aar_repo is not None or args.aar_commit is not None):
            parser.error("base upgrade derives the overlay from active provenance")
        if upgrading and args.reconcile_runtime_sha256 is None:
            parser.error(
                "base upgrade requires --reconcile-runtime-sha256"
            )
        if not upgrading and args.reconcile_runtime_sha256 is not None:
            parser.error(
                "initial stage does not accept --reconcile-runtime-sha256"
            )
        result = {
            "ok": True,
            "action": "stage",
            "patch_dir": str(
                stage_patch(
                    args.runtime_root,
                    aar_repo=args.aar_repo,
                    aar_commit=args.aar_commit,
                    installed_base=args.installed_base,
                    base_repo=args.base_repo,
                    base_commit=args.base_commit,
                    reconcile_runtime_sha256=args.reconcile_runtime_sha256,
                )
            ),
            "processes_restarted": False,
        }
    elif args.action == "check":
        result = check(
            args.runtime_root,
            patch=args.patch_dir,
            reconcile_runtime_sha256=args.reconcile_runtime_sha256,
        )
    else:
        if args.reconcile_runtime_sha256 is not None:
            parser.error(
                f"{args.action} does not accept --reconcile-runtime-sha256"
            )
        if args.patch_dir is None:
            parser.error(f"{args.action} requires --patch-dir")
        result = execute_patch(
            args.runtime_root,
            args.patch_dir,
            rollback=args.action == "rollback",
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
