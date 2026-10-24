#!/usr/bin/env python3
"""Paired Bridge/Runtime-hook maintenance for the production control path.

The production release identity remains unchanged.  This utility stages two
artifacts from independent immutable Git commits and swaps only the Bridge and
Runtime Host tool hook under the canonical production install lock.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import socket
import shutil
import signal
import tempfile
import uuid

import production_connector as pc
import repair_production_host as host_repair


PROVENANCE = "control_component_patch"
PATCH_COMPONENT = "bridge-runtime-hook-pair"
PATCH_ROOT = "component-patches"
AAR_INSTALL_MANIFEST = ".adaptive-delivery-install.json"
DEFAULT_AAR_SKILL_ROOT = Path.home() / ".agents" / "skills" / "adaptive-delivery"
BRIDGE_CHAIN_ENV = {
    "LAB_HOST_ADAPTER_SOCKET": "host_socket",
    "LAB_RUNTIME_HOST_TOOL_HOOK_SOCKET": "hook_socket",
}
PAIRED_RUNTIME_FIELDS = frozenset(
    {
        "bridge_pid",
        "bridge_process_identity",
        "bridge_socket_identity",
        "runtime_hook_pid",
        "runtime_hook_process_identity",
        "runtime_hook_socket_identity",
        PROVENANCE,
    }
)


class PairStopError(RuntimeError):
    def __init__(self, message: str, *, fully_stopped: bool):
        super().__init__(message)
        self.fully_stopped = fully_stopped


class PairStartError(RuntimeError):
    def __init__(self, message: str, *, cleanup_complete: bool):
        super().__init__(message)
        self.cleanup_complete = cleanup_complete


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_sha(value: object) -> str:
    return _sha(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _preserved_runtime_state(state: dict) -> dict:
    return {key: value for key, value in state.items() if key not in PAIRED_RUNTIME_FIELDS}


def _private_bytes(path: Path, label: str) -> bytes:
    value = pc._read_private_bytes_if_present(path, label)
    if value is None:
        raise RuntimeError(f"{label} is missing")
    return value


def _file_hashes(
    root: Path,
    *,
    immutable: bool = False,
    allow_legacy_readonly_root: bool = False,
) -> dict[str, str]:
    pc._validate_owned_tree_source(root, "control component tree")
    hashes: dict[str, str] = {}
    for member in (root, *sorted(root.rglob("*"))):
        mode = stat.S_IMODE(member.lstat().st_mode)
        legacy_root = allow_legacy_readonly_root and member == root and mode == 0o555
        if (mode & 0o077 and not legacy_root) or (immutable and mode & 0o222):
            raise RuntimeError("control component tree is not private and immutable")
        if member.is_file():
            hashes[str(member.relative_to(root))] = pc._sha256_file(member)
    return hashes


def _hook_hashes(
    root: Path,
    *,
    immutable: bool = False,
    allow_legacy_readonly_root: bool = False,
) -> dict[str, str]:
    observed = _file_hashes(
        root,
        immutable=immutable,
        allow_legacy_readonly_root=allow_legacy_readonly_root,
    )
    expected = set(pc.RUNTIME_HOST_TOOL_HOOK_BUNDLE_RELATIVE_PATHS)
    if set(observed) != expected:
        raise RuntimeError("Runtime Host tool hook bundle is not dependency-complete")
    return {f"runtime-host-tool-hook/{key}": value for key, value in observed.items()}


def _full_hook_hashes(
    root: Path, revision: str, *, immutable: bool = False
) -> tuple[dict[str, str], dict[str, str], str]:
    observed = _file_hashes(root, immutable=immutable)
    manifest_digest = observed.get(AAR_INSTALL_MANIFEST)
    if manifest_digest is None:
        raise RuntimeError("Runtime hook full source install manifest is missing")
    manifest_raw = _private_bytes(
        root / AAR_INSTALL_MANIFEST, "Runtime hook full source install manifest"
    )
    _, expected_files = _parse_aar_install_manifest(manifest_raw, revision)
    if set(observed) != set(expected_files) | {AAR_INSTALL_MANIFEST}:
        raise RuntimeError("Runtime hook full source tree differs from manifest whitelist")
    for relative, expected in expected_files.items():
        if observed.get(relative) != expected:
            raise RuntimeError(f"Runtime hook full source hash mismatch: {relative}")
    if manifest_digest != _sha(manifest_raw):
        raise RuntimeError("Runtime hook install manifest hash mismatch")
    return (
        {f"runtime-host-tool-hook/{key}": value for key, value in observed.items()},
        expected_files,
        manifest_digest,
    )


def _private_tree_snapshot(root: Path) -> dict[str, dict[str, object]]:
    root = Path(root).resolve(strict=True)
    pc._validate_owned_tree_source(root, "private Bridge state tree")
    snapshot: dict[str, dict[str, object]] = {}
    for member in (root, *sorted(root.rglob("*"))):
        info = member.lstat()
        mode = stat.S_IMODE(info.st_mode)
        if mode & 0o077:
            raise RuntimeError("private Bridge state tree permissions are unsafe")
        relative = "." if member == root else str(member.relative_to(root))
        if member.is_dir():
            snapshot[relative] = {"kind": "directory", "mode": mode}
        elif member.is_file():
            snapshot[relative] = {
                "kind": "file",
                "mode": mode,
                "sha256": pc._sha256_file(member),
            }
        else:
            raise RuntimeError("private Bridge state tree contains an unsupported member")
    return snapshot


def _prepare_side_bridge_state(
    context: dict,
    bridge_environment: dict[str, str],
    side_root: Path,
) -> tuple[dict[str, str], dict[str, dict[str, object]]]:
    production_state = Path(bridge_environment.get("LAB_BRIDGE_STATE_DIR", ""))
    expected_state = context["current_layout"]["bridge_state_dir"]
    if production_state != expected_state:
        raise RuntimeError("Bridge production state directory mismatch")
    pc._bridge_capability_lease_status(context["current_layout"], context["state"])
    before = _private_tree_snapshot(production_state)
    if "authority-state.json" not in before:
        raise RuntimeError("Bridge authority state is missing; refusing side smoke")
    side_root.mkdir(parents=True, mode=0o700)
    side_state = side_root / "bridge-state"
    host_repair.copy_owned_host_contents(production_state, side_state)
    if _private_tree_snapshot(side_state) != before:
        raise RuntimeError("Bridge side state snapshot differs from production source")
    side_environment = dict(bridge_environment)
    side_environment["LAB_BRIDGE_STATE_DIR"] = str(side_state)
    return side_environment, before


def _prepare_stage_modes(patch: Path) -> None:
    for item in (patch, *patch.rglob("*")):
        if item.is_symlink():
            raise RuntimeError("control component staging tree may not contain symlinks")
        if item.is_dir():
            item.chmod(0o700)
        elif item.is_file():
            item.chmod(0o600)
    (patch / "bin" / "local-tool-bridge").chmod(0o700)
    (
        patch
        / "runtime-host-tool-hook"
        / pc.RUNTIME_HOST_TOOL_HOOK_ENTRYPOINT_RELATIVE_PATH
    ).chmod(0o700)


def _seal_patch(patch: Path) -> None:
    bridge = patch / "bin" / "local-tool-bridge"
    hook_entry = (
        patch
        / "runtime-host-tool-hook"
        / pc.RUNTIME_HOST_TOOL_HOOK_ENTRYPOINT_RELATIVE_PATH
    )
    for item in sorted(patch.rglob("*"), key=lambda value: len(value.parts), reverse=True):
        if item.is_dir():
            item.chmod(0o500)
        elif item.is_file():
            item.chmod(0o500 if item in (bridge, hook_entry) else 0o400)
    patch.chmod(0o500)


def _validate_commit(repo: Path, commit: str, label: str) -> tuple[Path, str]:
    repository = Path(repo).expanduser().resolve(strict=True)
    revision = pc._release_revision(commit)
    completed = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "--verify", f"{revision}^{{commit}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or completed.stdout.strip() != revision:
        raise RuntimeError(f"exact {label} source commit is unavailable")
    return repository, revision


def _git_revision_file_hashes(repo: Path, revision: str) -> dict[str, str]:
    completed = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-rz", "--full-tree", revision],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("cannot enumerate exact AAR source revision")
    hashes: dict[str, str] = {}
    for record in completed.stdout.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_relative = record.split(b"\t", 1)
            mode, kind, object_id = metadata.decode("ascii").split(" ", 2)
            relative = raw_relative.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as error:
            raise RuntimeError("exact AAR source revision tree is malformed") from error
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise RuntimeError(f"unsupported exact AAR source member: {relative}")
        blob = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "blob", object_id],
            capture_output=True,
            check=False,
        )
        if blob.returncode != 0:
            raise RuntimeError(f"cannot read exact AAR source member: {relative}")
        hashes[relative] = _sha(blob.stdout)
    if not hashes:
        raise RuntimeError("exact AAR source revision is empty")
    return hashes


def _parse_aar_install_manifest(raw: bytes, revision: str) -> tuple[dict, dict[str, str]]:
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("Adaptive Delivery install manifest is invalid JSON") from error
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") not in {1, 2}
        or manifest.get("revision") != revision
    ):
        raise RuntimeError("Adaptive Delivery install manifest revision mismatch")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise RuntimeError("Adaptive Delivery install manifest files map is missing")
    normalized: dict[str, str] = {}
    for raw_relative, digest in files.items():
        if not isinstance(raw_relative, str) or not raw_relative:
            raise RuntimeError("Adaptive Delivery install manifest file path is invalid")
        relative = Path(raw_relative)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or str(relative) != raw_relative
            or raw_relative == AAR_INSTALL_MANIFEST
        ):
            raise RuntimeError("Adaptive Delivery install manifest file path is invalid")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise RuntimeError("Adaptive Delivery install manifest file hash is invalid")
        normalized[raw_relative] = digest
    return manifest, normalized


def _validate_installed_aar_skill(
    skill_root: Path, aar_repo: Path, aar_commit: str
) -> dict:
    root = Path(skill_root).expanduser().resolve(strict=True)
    repository, revision = _validate_commit(aar_repo, aar_commit, "AAR")
    pc._validate_owned_tree_source(root, "installed Adaptive Delivery Core")
    manifest_path = root / AAR_INSTALL_MANIFEST
    manifest_raw = _private_bytes(manifest_path, "Adaptive Delivery install manifest")
    _, normalized = _parse_aar_install_manifest(manifest_raw, revision)
    source_hashes = _git_revision_file_hashes(repository, revision)
    if set(normalized) != set(source_hashes):
        raise RuntimeError("Adaptive Delivery install manifest file map differs from exact commit")
    for relative, expected in sorted(normalized.items()):
        if source_hashes[relative] != expected:
            raise RuntimeError(f"Adaptive Delivery source hash mismatch: {relative}")
        installed = root / relative
        pc._validate_owned_file_source(installed, "installed Adaptive Delivery file")
        if pc._sha256_file(installed) != expected:
            raise RuntimeError(f"Adaptive Delivery installed file hash mismatch: {relative}")
    return {
        "root": root,
        "revision": revision,
        "manifest_raw": manifest_raw,
        "manifest_sha256": _sha(manifest_raw),
        "files": normalized,
    }


def materialize_exact_sources(
    lab_repo: Path,
    lab_commit: str,
    aar_repo: Path,
    aar_commit: str,
    aar_skill_root: Path,
    destination: Path,
) -> tuple[Path, Path]:
    """Build/export only bytes from the two exact commits."""
    lab_repo, lab_commit = _validate_commit(lab_repo, lab_commit, "LAB")
    aar_repo, aar_commit = _validate_commit(aar_repo, aar_commit, "AAR")
    with tempfile.TemporaryDirectory(prefix="lab-control-source-") as temporary:
        snapshot = Path(temporary) / "lab"
        pc._export_git_snapshot(lab_repo, lab_commit, snapshot)
        completed = subprocess.run(
            ["cargo", "build", "--release"],
            cwd=snapshot / "crates" / "local-tool-bridge",
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "control component Bridge build failed: " + completed.stderr.strip()[-600:]
            )
        built = snapshot / "crates" / "local-tool-bridge" / "target" / "release" / "local-tool-bridge"
        pc._validate_owned_file_source(built, "control component Bridge build")
        bridge = destination / "bin" / "local-tool-bridge"
        bridge.parent.mkdir(parents=True, mode=0o700)
        bridge.write_bytes(built.read_bytes())
        bridge.chmod(0o700)

    hook = destination / "runtime-host-tool-hook"
    manifest_raw = _private_bytes(
        Path(aar_skill_root) / AAR_INSTALL_MANIFEST,
        "installed Adaptive Delivery manifest",
    )
    _parse_aar_install_manifest(manifest_raw, aar_commit)
    pc._export_git_snapshot(aar_repo, aar_commit, hook)
    manifest_target = hook / AAR_INSTALL_MANIFEST
    manifest_target.write_bytes(manifest_raw)
    manifest_target.chmod(0o600)
    return bridge, hook


def _safe_environment(value: dict[str, str], label: str) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise RuntimeError(f"{label} environment is missing")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str) or not key:
            raise RuntimeError(f"{label} environment is invalid")
        upper = key.upper()
        if any(marker in upper for marker in ("PASSWORD", "TOKEN", "API_KEY", "SECRET_VALUE")):
            raise RuntimeError(f"{label} environment contains an inline secret")
        if key not in {"HOME", "PATH", "PYTHONDONTWRITEBYTECODE"} and not key.startswith("LAB_"):
            raise RuntimeError(f"{label} environment contains an unapproved variable: {key}")
        result[key] = item
    return result


def _host_socket_identity(path: Path) -> dict:
    identity = pc._bridge_socket_identity(path)
    parent = path.parent.lstat()
    if (
        path.parent.is_symlink()
        or not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.geteuid()
        or stat.S_IMODE(parent.st_mode) != 0o700
    ):
        raise RuntimeError("production Host adapter socket parent is unsafe")
    return identity


class Operations:
    identity_matches = staticmethod(pc._process_identity_matches)
    environment = staticmethod(host_repair.process_environment)
    host_socket_identity = staticmethod(_host_socket_identity)
    host_socket_is_listening = staticmethod(pc._host_adapter_socket_is_listening)
    runtime_hook_socket_is_live = staticmethod(pc._runtime_hook_socket_is_live)

    @staticmethod
    def _remove_socket(path: Path, expected: object, observer) -> None:
        if not path.exists() and not path.is_symlink():
            return
        observed = observer(path)
        if not isinstance(expected, dict) or observed != expected:
            raise RuntimeError("control component socket identity changed")
        path.unlink()

    @staticmethod
    def _smoke_hook_fail_closed(path: Path) -> None:
        request = {
            "request_id": "control-component-smoke",
            "protocol": "invalid-control-component-protocol",
            "op": "pre",
        }
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(2.0)
            probe.connect(str(path))
            probe.sendall(json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n")
            payload = bytearray()
            while b"\n" not in payload and len(payload) <= 65536:
                chunk = probe.recv(4096)
                if not chunk:
                    break
                payload.extend(chunk)
        try:
            response = json.loads(bytes(payload).split(b"\n", 1)[0])
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError("Runtime hook fail-closed smoke returned invalid JSON") from error
        if (
            not isinstance(response, dict)
            or response.get("request_id") != request["request_id"]
            or response.get("ok") is not False
            or not isinstance(response.get("error"), str)
        ):
            raise RuntimeError("Runtime hook fail-closed smoke was not rejected")

    def _cleanup_started(
        self,
        *,
        bridge_identity,
        bridge_process,
        bridge_socket: Path,
        bridge_socket_identity,
        hook_identity,
        hook_process,
        hook_socket: Path,
        hook_socket_identity,
    ) -> tuple[bool, list[str]]:
        errors: list[str] = []
        unknown_spawned = False
        live_by_label: dict[str, bool] = {}
        for label, identity, process in (
            ("Bridge", bridge_identity, bridge_process),
            ("Runtime hook", hook_identity, hook_process),
        ):
            if not isinstance(identity, dict):
                if process is not None:
                    unknown_spawned = True
                    errors.append(f"{label} exact identity was not captured")
                live_by_label[label] = process is not None
                continue
            if self.identity_matches(identity):
                try:
                    pc._stop_verified_process_identity(
                        identity, process=process, process_group=False
                    )
                except Exception as error:
                    errors.append(f"{label} stop failed: {error}")
            live_by_label[label] = self.identity_matches(identity)
            if live_by_label[label]:
                errors.append(f"{label} remains live after cleanup")

        sockets = (
            ("Bridge", bridge_socket, bridge_socket_identity, pc._bridge_socket_identity),
            ("Runtime hook", hook_socket, hook_socket_identity, pc._runtime_hook_socket_identity),
        )
        for label, path, identity, observer in sockets:
            if live_by_label.get(label, False):
                continue
            if identity is None:
                if path.exists() or path.is_symlink():
                    errors.append(f"{label} socket exists without a captured identity")
                continue
            try:
                self._remove_socket(path, identity, observer)
            except Exception as error:
                errors.append(f"{label} socket cleanup failed: {error}")
        live = any(
            isinstance(identity, dict) and self.identity_matches(identity)
            for identity in (bridge_identity, hook_identity)
        )
        blocked = any(path.exists() or path.is_symlink() for _, path, _, _ in sockets)
        return not unknown_spawned and not live and not blocked, errors

    @staticmethod
    def _bridge_check_config(command, environment, *, cwd: Path) -> None:
        process = subprocess.Popen(
            [*command, "--check-config"],
            cwd=cwd,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, _stderr = process.communicate(timeout=3.0)
        except subprocess.TimeoutExpired as error:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate(timeout=1.0)
            raise RuntimeError("Bridge check-config timed out") from error
        if process.returncode != 0:
            raise RuntimeError("Bridge check-config failed")
        try:
            result = json.loads(stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError("Bridge check-config returned invalid JSON") from error
        expected = {
            "check_config": True,
            "production_mode": True,
            "listening": False,
        }
        if result != expected:
            raise RuntimeError("Bridge check-config result is not the fixed production contract")

    def preflight_pair(self, bridge_command, hook_command, bridge_env, hook_env, paths, label):
        """Smoke the target Hook and validate the new Bridge without binding its socket."""
        hook_socket = Path(paths["hook_socket"])
        if hook_socket.exists() or hook_socket.is_symlink():
            raise RuntimeError("control component preflight Hook socket already exists")
        pc._private_dir(hook_socket.parent)
        logs = Path(paths["logs"])
        hook_process = None
        hook_identity = None
        hook_socket_identity = None
        failure = None
        cleanup_errors: list[str] = []
        try:
            hook_process = pc._spawn_logged(
                list(hook_command),
                logs / f"control-{label}-hook.out",
                logs / f"control-{label}-hook.err",
                cwd=Path(hook_command[1]).parent.parent,
                env=dict(hook_env),
            )
            hook_identity = pc._capture_process_identity(
                hook_process.pid,
                expected_executable=Path(hook_command[0]),
                expected_argv=list(hook_command),
            )
            hook_socket_identity = pc._wait_for_runtime_hook(
                hook_process, {"runtime_hook_socket": hook_socket}
            )
            self._smoke_hook_fail_closed(hook_socket)
            self._bridge_check_config(
                list(bridge_command),
                bridge_env,
                cwd=Path(bridge_env["LAB_PRODUCTION_WORKSPACE"]),
            )
        except Exception as error:
            failure = error
        finally:
            if not isinstance(hook_identity, dict):
                if hook_process is not None:
                    cleanup_errors.append("Runtime hook exact identity was not captured")
            else:
                if self.identity_matches(hook_identity):
                    try:
                        pc._stop_verified_process_identity(
                            hook_identity, process=hook_process, process_group=False
                        )
                    except Exception as error:
                        cleanup_errors.append(f"Runtime hook stop failed: {error}")
                if self.identity_matches(hook_identity):
                    cleanup_errors.append("Runtime hook remains live after cleanup")
                elif hook_socket_identity is not None:
                    try:
                        self._remove_socket(
                            hook_socket,
                            hook_socket_identity,
                            pc._runtime_hook_socket_identity,
                        )
                    except Exception as error:
                        cleanup_errors.append(f"Runtime hook socket cleanup failed: {error}")
                elif hook_socket.exists() or hook_socket.is_symlink():
                    cleanup_errors.append("Runtime hook socket exists without a captured identity")
        if failure is not None or cleanup_errors:
            detail = f"; cleanup: {'; '.join(cleanup_errors)}" if cleanup_errors else ""
            raise PairStartError(
                f"{failure or 'control component preflight cleanup failed'}{detail}",
                cleanup_complete=not cleanup_errors,
            ) from failure
        return {"check_config": True, "production_mode": True, "listening": False}

    def start_pair(self, bridge_command, hook_command, bridge_env, hook_env, paths, label):
        bridge_socket = Path(paths["bridge_socket"])
        hook_socket = Path(paths["hook_socket"])
        for path in (bridge_socket, hook_socket):
            if path.exists() or path.is_symlink():
                raise RuntimeError("control component start socket already exists")
            pc._private_dir(path.parent)
        logs = Path(paths["logs"])
        hook_process = None
        bridge_process = None
        hook_identity = None
        bridge_identity = None
        hook_socket_identity = None
        bridge_socket_identity = None
        try:
            hook_process = pc._spawn_logged(
                list(hook_command),
                logs / f"control-{label}-hook.out",
                logs / f"control-{label}-hook.err",
                cwd=Path(hook_command[1]).parent.parent,
                env=dict(hook_env),
            )
            hook_identity = pc._capture_process_identity(
                hook_process.pid,
                expected_executable=Path(hook_command[0]),
                expected_argv=list(hook_command),
            )
            hook_socket_identity = pc._wait_for_runtime_hook(
                hook_process, {"runtime_hook_socket": hook_socket}
            )
            self._smoke_hook_fail_closed(hook_socket)
            bridge_process = pc._spawn_logged(
                list(bridge_command),
                logs / f"control-{label}-bridge.out",
                logs / f"control-{label}-bridge.err",
                cwd=Path(bridge_env["LAB_PRODUCTION_WORKSPACE"]),
                env=dict(bridge_env),
            )
            bridge_identity = pc._capture_process_identity(
                bridge_process.pid,
                expected_executable=Path(bridge_command[0]),
                expected_argv=list(bridge_command),
            )
            probe_layout = {"bridge_socket": bridge_socket}
            tools = pc._wait_for_bridge(bridge_process, probe_layout)
            bridge_socket_identity = pc._bridge_socket_identity(bridge_socket)
            return {
                "bridge_identity": bridge_identity,
                "hook_identity": hook_identity,
                "bridge_socket_identity": bridge_socket_identity,
                "hook_socket_identity": hook_socket_identity,
                "tools": tools,
            }
        except Exception as error:
            cleanup_complete, cleanup_errors = self._cleanup_started(
                bridge_identity=bridge_identity,
                bridge_process=bridge_process,
                bridge_socket=bridge_socket,
                bridge_socket_identity=bridge_socket_identity,
                hook_identity=hook_identity,
                hook_process=hook_process,
                hook_socket=hook_socket,
                hook_socket_identity=hook_socket_identity,
            )
            detail = f"; cleanup: {'; '.join(cleanup_errors)}" if cleanup_errors else ""
            raise PairStartError(
                f"{error}{detail}", cleanup_complete=cleanup_complete
            ) from error

    def stop_pair(self, bridge, hook, paths):
        if pc._bridge_socket_identity(Path(paths["bridge_socket"])) != paths["bridge_socket_identity"]:
            raise RuntimeError("control component Bridge socket identity changed")
        if pc._runtime_hook_socket_identity(Path(paths["hook_socket"])) != paths["hook_socket_identity"]:
            raise RuntimeError("control component Runtime hook socket identity changed")
        if not self.identity_matches(bridge) or not self.identity_matches(hook):
            raise RuntimeError("paired control process identity changed before stop")
        errors: list[str] = []
        for label, identity in (("Bridge", bridge), ("Runtime hook", hook)):
            try:
                pc._stop_verified_process_identity(identity, process_group=False)
            except Exception as error:
                errors.append(f"{label} stop failed: {error}")
        # One bounded second pass handles the concrete partial-stop case: the
        # Bridge exited but the first Hook stop failed. Never start a second
        # pair while either exact old identity remains live.
        for label, identity in (("Bridge", bridge), ("Runtime hook", hook)):
            if self.identity_matches(identity):
                try:
                    pc._stop_verified_process_identity(identity, process_group=False)
                except Exception as error:
                    errors.append(f"{label} retry stop failed: {error}")
        fully_stopped = not self.identity_matches(bridge) and not self.identity_matches(hook)
        if fully_stopped:
            try:
                self._remove_socket(
                    Path(paths["bridge_socket"]),
                    paths["bridge_socket_identity"],
                    pc._bridge_socket_identity,
                )
                self._remove_socket(
                    Path(paths["hook_socket"]),
                    paths["hook_socket_identity"],
                    pc._runtime_hook_socket_identity,
                )
            except Exception as error:
                fully_stopped = False
                errors.append(f"socket cleanup failed: {error}")
        if errors:
            raise PairStopError("; ".join(errors), fully_stopped=fully_stopped)
        if not fully_stopped:
            raise PairStopError(
                "paired control processes remain live after bounded stop",
                fully_stopped=False,
            )

    def stop_started_pair(self, pair, paths):
        stop_paths = dict(paths)
        stop_paths["bridge_socket_identity"] = pair["bridge_socket_identity"]
        stop_paths["hook_socket_identity"] = pair["hook_socket_identity"]
        self.stop_pair(pair["bridge_identity"], pair["hook_identity"], stop_paths)


def _release_layout(root: Path, manifest: dict, *, bridge_socket: Path) -> dict:
    return pc.production_layout(manifest, runtime_root=root, bridge_socket=bridge_socket)


def load_context(runtime_root: Path, ops: Operations) -> dict:
    root = Path(runtime_root).expanduser().resolve(strict=True)
    runtime_path = root / "runtime.json"
    raw = _private_bytes(runtime_path, "production runtime state")
    state = json.loads(raw)
    if not isinstance(state, dict):
        raise RuntimeError("production runtime state must be a JSON object")
    bridge_socket_raw = (state.get("bridge_socket_identity") or {}).get("path")
    if not isinstance(bridge_socket_raw, str) or not Path(bridge_socket_raw).is_absolute():
        raise RuntimeError("production Bridge socket identity is missing")
    current_release = root / "releases" / pc._release_revision(state.get("release_revision")) / f"generation-{pc._endpoint_generation(state.get('endpoint_generation'))}"
    current_manifest_raw = _private_bytes(current_release / "release.json", "current release manifest")
    current_manifest = json.loads(current_manifest_raw)
    if (
        state.get("release_manifest") != str(current_release / "release.json")
        or state.get("workspace") != current_manifest.get("workspace")
        or state.get("tunnel_profile_name") != pc.TUNNEL_PROFILE_NAME
    ):
        raise RuntimeError("current production release identity mismatch")
    current_layout = _release_layout(root, current_manifest, bridge_socket=Path(bridge_socket_raw))
    pc.validate_installed_release(current_layout)
    reused = state.get("reused_live_backends")
    if not isinstance(reused, dict):
        raise RuntimeError("control component patch requires reused_live_backends")
    source_release = root / "releases" / pc._release_revision(reused.get("source_release_revision")) / f"generation-{pc._endpoint_generation(reused.get('source_endpoint_generation'))}"
    if reused.get("release_manifest") != str(source_release / "release.json"):
        raise RuntimeError("reused control source manifest path mismatch")
    source_manifest_raw = _private_bytes(source_release / "release.json", "control source release manifest")
    source_manifest = json.loads(source_manifest_raw)
    hook_socket_raw = reused.get("runtime_hook_socket") or (state.get("runtime_hook_socket_identity") or {}).get("path")
    if not isinstance(hook_socket_raw, str) or not Path(hook_socket_raw).is_absolute():
        raise RuntimeError("production Runtime hook socket identity is missing")
    source_layout = _release_layout(root, source_manifest, bridge_socket=Path(bridge_socket_raw))
    source_layout["runtime_hook_socket"] = Path(hook_socket_raw)
    host_socket_raw = reused.get("host_socket")
    if not isinstance(host_socket_raw, str) or not Path(host_socket_raw).is_absolute():
        raise RuntimeError("production Host adapter socket identity is missing")
    host_socket = Path(host_socket_raw)
    canonical_source_layout = _release_layout(
        root, source_manifest, bridge_socket=Path(bridge_socket_raw)
    )
    if (
        host_socket != canonical_source_layout["host_socket"]
        or Path(hook_socket_raw) != canonical_source_layout["runtime_hook_socket"]
    ):
        raise RuntimeError("reused control socket fence differs from canonical source release")
    # The reused generation may predate whole-release sealing. Reuse the
    # canonical legacy component validator: all artifact hashes still match,
    # and `secrets` is the only directory allowed to remain writable.
    pc._validate_legacy_component_source_release(source_layout, source_manifest)
    artifacts = source_manifest.get("artifacts_sha256") or {}
    source_hook_hashes = _hook_hashes(
        source_layout["runtime_hook_dir"],
        immutable=True,
        allow_legacy_readonly_root=True,
    )
    expected_hook_hashes = {
        key: value for key, value in artifacts.items() if key.startswith("runtime-host-tool-hook/")
    }
    if source_hook_hashes != expected_hook_hashes:
        raise RuntimeError("source Runtime hook bundle differs from immutable release manifest")
    current_artifacts = current_manifest.get("artifacts_sha256") or {}
    source_bridge = current_layout["bridge_binary"]
    if pc._sha256_file(source_bridge) != current_artifacts.get("bin/local-tool-bridge"):
        raise RuntimeError("source Bridge differs from immutable release manifest")
    bridge_identity = state.get("bridge_process_identity")
    hook_identity = state.get("runtime_hook_process_identity")
    if (
        not isinstance(bridge_identity, dict)
        or bridge_identity.get("pid") != state.get("bridge_pid")
        or not isinstance(hook_identity, dict)
        or hook_identity.get("pid") != state.get("runtime_hook_pid")
    ):
        raise RuntimeError("paired control process identities are incomplete")
    pc._validate_process_identity_record(bridge_identity)
    pc._validate_process_identity_record(hook_identity)
    if not ops.identity_matches(bridge_identity) or not ops.identity_matches(hook_identity):
        raise RuntimeError("paired control process identity mismatch")
    host_identity = state.get("host_process_identity")
    if (
        not isinstance(host_identity, dict)
        or host_identity.get("pid") != state.get("host_pid")
    ):
        raise RuntimeError("production Host process identity is incomplete")
    pc._validate_process_identity_record(host_identity)
    if (
        host_identity.get("uid") != os.geteuid()
        or hook_identity.get("uid") != os.geteuid()
        or not ops.identity_matches(host_identity)
    ):
        raise RuntimeError("reused Host/Runtime hook process identity is unsafe")
    host_socket_identity = ops.host_socket_identity(host_socket)
    if (
        host_socket_identity.get("path") != str(host_socket)
        or host_socket_identity.get("uid") != os.geteuid()
        or host_socket_identity.get("mode") != 0o600
        or not ops.host_socket_is_listening(host_socket)
    ):
        raise RuntimeError("reused Host adapter socket is not a safe live canonical socket")
    hook_socket_identity = state.get("runtime_hook_socket_identity")
    if (
        not isinstance(hook_socket_identity, dict)
        or hook_socket_identity.get("path") != str(hook_socket_raw)
        or hook_socket_identity.get("uid") != os.geteuid()
        or hook_socket_identity.get("mode") != 0o600
        or not ops.runtime_hook_socket_is_live(Path(hook_socket_raw), hook_socket_identity)
    ):
        raise RuntimeError("reused Runtime hook socket is not a safe live canonical socket")
    source_fence = {
        "LAB_PRODUCTION_RELEASE_MANIFEST_FILE": str(source_release / "release.json"),
        "LAB_PRODUCTION_RELEASE_REVISION": source_manifest["release_revision"],
        "LAB_PRODUCTION_ENDPOINT_GENERATION": str(source_manifest["endpoint_generation"]),
    }
    host_environment = ops.environment(host_identity)
    if (
        host_environment.get("LAB_HOST_ADAPTER_SOCKET") != str(host_socket)
        or any(host_environment.get(key) != value for key, value in source_fence.items())
    ):
        raise RuntimeError("production Host environment differs from reused source fence")
    if PROVENANCE not in state:
        hook_entry = source_layout["runtime_hook_entrypoint"]
        python = Path((source_manifest.get("runtime_dependencies") or {}).get("python", {}).get("path", ""))
        expected_hook_command = [str(python), str(hook_entry), "--serve-unix-socket", str(source_layout["runtime_hook_socket"])]
        if bridge_identity.get("argv") != [str(source_bridge)] or hook_identity.get("argv") != expected_hook_command:
            raise RuntimeError("unpatched control pair does not match immutable release commands")
    return {
        "root": root,
        "path": runtime_path,
        "raw": raw,
        "state": state,
        "current_release": current_release,
        "current_manifest": current_manifest,
        "current_manifest_sha256": _sha(current_manifest_raw),
        "source_release": source_release,
        "source_manifest": source_manifest,
        "source_manifest_sha256": _sha(source_manifest_raw),
        "current_layout": current_layout,
        "source_layout": source_layout,
        "source_bridge": source_bridge,
        "source_hook": source_layout["runtime_hook_dir"],
        "source_hashes": {"bin/local-tool-bridge": pc._sha256_file(source_bridge), **source_hook_hashes},
        "bridge_identity": bridge_identity,
        "hook_identity": hook_identity,
        "host_identity": host_identity,
        "host_socket": host_socket,
        "host_socket_identity": host_socket_identity,
        "source_fence_environment": source_fence,
        "bridge_socket": Path(bridge_socket_raw),
        "hook_socket": Path(hook_socket_raw),
    }


def _assert_unchanged(context: dict, ops: Operations) -> None:
    if _private_bytes(context["path"], "production runtime state") != context["raw"]:
        raise RuntimeError("production runtime bytes changed; refusing control maintenance")
    if not ops.identity_matches(context["bridge_identity"]) or not ops.identity_matches(context["hook_identity"]):
        raise RuntimeError("paired control process identity changed")


def _environment_snapshot(context: dict, ops: Operations) -> tuple[dict[str, str], dict[str, str]]:
    bridge = _safe_environment(ops.environment(context["bridge_identity"]), "Bridge")
    hook = _safe_environment(ops.environment(context["hook_identity"]), "Runtime hook")
    if bridge.get("LAB_BRIDGE_SOCKET") != str(context["bridge_socket"]):
        raise RuntimeError("Bridge environment socket mismatch")
    if bridge.get("LAB_BRIDGE_STATE_DIR") != str(context["current_layout"]["bridge_state_dir"]):
        raise RuntimeError("Bridge state directory mismatch")
    for key, value in context["source_fence_environment"].items():
        if bridge.get(key) != value:
            raise RuntimeError(f"Bridge source fence mismatch: {key}")
    for key, context_key in BRIDGE_CHAIN_ENV.items():
        if key in bridge and bridge[key] != str(context[context_key]):
            raise RuntimeError(f"Bridge chain environment mismatch: {key}")
    for state_key, env_key in (
        ("bridge_execution_session_id", "LAB_BRIDGE_EXECUTION_SESSION_ID"),
        ("bridge_capability_id", "LAB_BRIDGE_CAPABILITY_ID"),
    ):
        if bridge.get(env_key) != context["state"].get(state_key):
            raise RuntimeError(f"Bridge environment state mismatch: {env_key}")
    return bridge, hook


def _bridge_environment_after(context: dict, before: dict[str, str]) -> tuple[dict[str, str], dict]:
    after = dict(before)
    transition = {}
    for key, context_key in BRIDGE_CHAIN_ENV.items():
        expected = str(context[context_key])
        if key in before and before[key] != expected:
            raise RuntimeError(f"Bridge chain environment mismatch: {key}")
        transition[key] = {
            "before": before[key] if key in before else "missing",
            "after": expected,
        }
        after[key] = expected
    return after, transition


def stage_patch(
    runtime_root: Path,
    *,
    lab_repo: Path,
    lab_commit: str,
    aar_repo: Path,
    aar_commit: str,
    aar_skill_root: Path = DEFAULT_AAR_SKILL_ROOT,
    ops=None,
    materialize=materialize_exact_sources,
    validate_aar_skill=_validate_installed_aar_skill,
) -> Path:
    ops = ops or Operations()
    lab_commit = pc._release_revision(lab_commit)
    aar_commit = pc._release_revision(aar_commit)
    if materialize is materialize_exact_sources:
        lab_repo, lab_commit = _validate_commit(lab_repo, lab_commit, "LAB")
        aar_repo, aar_commit = _validate_commit(aar_repo, aar_commit, "AAR")
    with host_repair.bounded_install_lock(runtime_root):
        context = load_context(runtime_root, ops)
        if PROVENANCE in context["state"]:
            raise RuntimeError("an active paired control component patch already exists")
        installed_aar = validate_aar_skill(
            Path(aar_skill_root), Path(aar_repo), aar_commit
        )
        bridge_env_before, hook_env = _environment_snapshot(context, ops)
        bridge_env_after, bridge_environment_transition = _bridge_environment_after(
            context, bridge_env_before
        )
        _assert_unchanged(context, ops)
        patch_root = context["root"] / PATCH_ROOT
        pc._private_dir(patch_root)
        patch = patch_root / f"control-{uuid.uuid4()}"
        patch.mkdir(mode=0o700)
        bridge, hook = materialize(
            Path(lab_repo).expanduser().resolve(strict=True),
            lab_commit,
            Path(aar_repo).expanduser().resolve(strict=True),
            aar_commit,
            installed_aar["root"],
            patch,
        )
        if bridge != patch / "bin" / "local-tool-bridge" or hook != patch / "runtime-host-tool-hook":
            raise RuntimeError("control source materializer returned an unexpected layout")
        _prepare_stage_modes(patch)
        hook_artifact_hashes, hook_files, hook_manifest_sha256 = _full_hook_hashes(
            hook, aar_commit
        )
        if (
            hook_files != installed_aar["files"]
            or hook_manifest_sha256 != installed_aar["manifest_sha256"]
        ):
            raise RuntimeError("materialized Runtime hook source differs from installed Core")
        artifact_hashes = {
            "bin/local-tool-bridge": pc._sha256_file(bridge),
            **hook_artifact_hashes,
        }
        pc._write_json_private(patch / "bridge-environment-before.json", bridge_env_before)
        pc._write_json_private(patch / "bridge-environment-after.json", bridge_env_after)
        pc._write_json_private(patch / "hook-environment.json", hook_env)
        pc._write_text_private(patch / "runtime-before.json", context["raw"].decode("utf-8"), 0o600)
        record = {
            "schema_version": 2,
            "component": PATCH_COMPONENT,
            "patch_dir": str(patch),
            "sources": {
                "lab_commit": lab_commit,
                "aar_commit": aar_commit,
                "aar_install_manifest_sha256": hook_manifest_sha256,
            },
            "source_repositories": {
                "lab": str(Path(lab_repo).expanduser().resolve(strict=True)),
                "aar": str(Path(aar_repo).expanduser().resolve(strict=True)),
                "aar_skill_root": str(installed_aar["root"]),
            },
            "current_manifest_sha256": context["current_manifest_sha256"],
            "source_manifest_sha256": context["source_manifest_sha256"],
            "source_hashes": context["source_hashes"],
            "artifact_hashes": artifact_hashes,
            "runtime_before_sha256": _sha(context["raw"]),
            "preserved_runtime_state_sha256": _json_sha(
                _preserved_runtime_state(context["state"])
            ),
            "bridge_environment_transition": bridge_environment_transition,
            "bridge_environment_before_sha256": pc._sha256_file(
                patch / "bridge-environment-before.json"
            ),
            "bridge_environment_after_sha256": pc._sha256_file(
                patch / "bridge-environment-after.json"
            ),
            "hook_environment_sha256": pc._sha256_file(patch / "hook-environment.json"),
            "old_bridge_identity": context["bridge_identity"],
            "old_hook_identity": context["hook_identity"],
        }
        pc._write_json_private(patch / "manifest.json", record)
        _assert_unchanged(context, ops)
        _seal_patch(patch)
        return patch


def verify_patch(context: dict, patch: Path):
    if Path(patch).is_symlink():
        raise RuntimeError("control component patch may not be a symlink")
    patch = Path(patch).resolve(strict=True)
    if patch.parent != context["root"] / PATCH_ROOT:
        raise RuntimeError("control component patch is outside the production patch root")
    pc._validate_owned_tree_source(patch, "control component patch")
    expected_entries = {
        "bin", "runtime-host-tool-hook", "bridge-environment-before.json",
        "bridge-environment-after.json",
        "hook-environment.json", "runtime-before.json", "manifest.json",
    }
    if {item.name for item in patch.iterdir()} != expected_entries:
        raise RuntimeError("control component patch contains unlisted artifacts")
    for item in (patch, *patch.rglob("*")):
        if stat.S_IMODE(item.lstat().st_mode) & 0o277:
            raise RuntimeError("control component patch is not sealed")
    record = json.loads(_private_bytes(patch / "manifest.json", "control component manifest"))
    if (
        record.get("schema_version") != 2
        or record.get("component") != PATCH_COMPONENT
        or record.get("patch_dir") != str(patch)
        or record.get("current_manifest_sha256") != context["current_manifest_sha256"]
        or record.get("source_manifest_sha256") != context["source_manifest_sha256"]
        or record.get("source_hashes") != context["source_hashes"]
        or record.get("preserved_runtime_state_sha256")
        != _json_sha(_preserved_runtime_state(context["state"]))
    ):
        raise RuntimeError("control component source provenance mismatch")
    sources = record.get("sources")
    if not isinstance(sources, dict):
        raise RuntimeError("control component source commits are missing")
    pc._release_revision(sources.get("lab_commit"))
    aar_commit = pc._release_revision(sources.get("aar_commit"))
    hook_hashes, _, hook_manifest_sha256 = _full_hook_hashes(
        patch / "runtime-host-tool-hook", aar_commit, immutable=True
    )
    if sources.get("aar_install_manifest_sha256") != hook_manifest_sha256:
        raise RuntimeError("control component AAR install manifest provenance mismatch")
    observed = {
        "bin/local-tool-bridge": pc._sha256_file(patch / "bin" / "local-tool-bridge"),
        **hook_hashes,
    }
    if observed != record.get("artifact_hashes"):
        raise RuntimeError("control component artifact hashes mismatch")
    executable_paths = (
        patch / "bin" / "local-tool-bridge",
        patch / "runtime-host-tool-hook" / pc.RUNTIME_HOST_TOOL_HOOK_ENTRYPOINT_RELATIVE_PATH,
    )
    if any(stat.S_IMODE(path.stat().st_mode) != 0o500 for path in executable_paths):
        raise RuntimeError("control component executable modes mismatch")
    runtime_before = _private_bytes(patch / "runtime-before.json", "control rollback state")
    if _sha(runtime_before) != record.get("runtime_before_sha256"):
        raise RuntimeError("control rollback state hash mismatch")
    bridge_env_before_raw = _private_bytes(
        patch / "bridge-environment-before.json", "Bridge environment before patch"
    )
    bridge_env_after_raw = _private_bytes(
        patch / "bridge-environment-after.json", "Bridge environment after patch"
    )
    hook_env_raw = _private_bytes(patch / "hook-environment.json", "Runtime hook environment")
    if (
        pc._sha256_file(patch / "bridge-environment-before.json")
        != record.get("bridge_environment_before_sha256")
        or pc._sha256_file(patch / "bridge-environment-after.json")
        != record.get("bridge_environment_after_sha256")
        or pc._sha256_file(patch / "hook-environment.json")
        != record.get("hook_environment_sha256")
    ):
        raise RuntimeError("control component environment hash mismatch")
    bridge_env_before = _safe_environment(
        json.loads(bridge_env_before_raw), "Bridge environment before patch"
    )
    bridge_env_after = _safe_environment(
        json.loads(bridge_env_after_raw), "Bridge environment after patch"
    )
    hook_env = _safe_environment(json.loads(hook_env_raw), "Runtime hook")
    expected_after, expected_transition = _bridge_environment_after(
        context, bridge_env_before
    )
    if (
        bridge_env_after != expected_after
        or record.get("bridge_environment_transition") != expected_transition
    ):
        raise RuntimeError("control component Bridge environment transition mismatch")
    return patch, record, runtime_before, bridge_env_before, bridge_env_after, hook_env


def _provenance(patch: Path, record: dict) -> dict:
    return {
        "manifest": str(patch / "manifest.json"),
        "manifest_sha256": pc._sha256_file(patch / "manifest.json"),
        "lab_commit": record["sources"]["lab_commit"],
        "aar_commit": record["sources"]["aar_commit"],
        "aar_install_manifest_sha256": record["sources"]["aar_install_manifest_sha256"],
        "bridge_sha256": record["artifact_hashes"]["bin/local-tool-bridge"],
        "hook_bundle_sha256": {
            key: value for key, value in record["artifact_hashes"].items()
            if key.startswith("runtime-host-tool-hook/")
        },
    }


def _paths(context: dict, *, bridge_socket=None, hook_socket=None) -> dict:
    return {
        "bridge_socket": Path(bridge_socket or context["bridge_socket"]),
        "hook_socket": Path(hook_socket or context["hook_socket"]),
        "bridge_socket_identity": context["state"].get("bridge_socket_identity"),
        "hook_socket_identity": context["state"].get("runtime_hook_socket_identity"),
        "logs": context["root"] / "logs",
    }


def _commands(context: dict, patch: Path, *, patched: bool, hook_socket: Path) -> tuple[list[str], list[str]]:
    if patched:
        bridge = patch / "bin" / "local-tool-bridge"
        hook = patch / "runtime-host-tool-hook" / pc.RUNTIME_HOST_TOOL_HOOK_ENTRYPOINT_RELATIVE_PATH
        python = Path(context["hook_identity"]["argv"][0])
    else:
        bridge = context["source_bridge"]
        hook = context["source_hook"] / pc.RUNTIME_HOST_TOOL_HOOK_ENTRYPOINT_RELATIVE_PATH
        python = Path(context["source_manifest"].get("runtime_dependencies", {}).get("python", {}).get("path", ""))
    return [str(bridge)], [str(python), str(hook), "--serve-unix-socket", str(hook_socket)]


def _assert_active_pair_commands(context: dict, patch: Path, *, patched: bool) -> None:
    bridge_command, hook_command = _commands(
        context, patch, patched=patched, hook_socket=context["hook_socket"]
    )
    if (
        context["bridge_identity"].get("argv") != bridge_command
        or context["hook_identity"].get("argv") != hook_command
    ):
        raise RuntimeError("active paired control process commands mismatch")


def _start_pair(ops, context, patch, patched, bridge_env, hook_env, paths, label):
    bridge_env = dict(bridge_env)
    bridge_env["LAB_BRIDGE_SOCKET"] = str(paths["bridge_socket"])
    if "LAB_RUNTIME_HOST_TOOL_HOOK_SOCKET" in bridge_env:
        bridge_env["LAB_RUNTIME_HOST_TOOL_HOOK_SOCKET"] = str(paths["hook_socket"])
    bridge_command, hook_command = _commands(
        context, patch, patched=patched, hook_socket=Path(paths["hook_socket"])
    )
    return ops.start_pair(bridge_command, hook_command, bridge_env, hook_env, paths, label)


def _publish(context: dict, expected: bytes, pair: dict, provenance: dict | None) -> bytes:
    if _private_bytes(context["path"], "production runtime state") != expected:
        raise RuntimeError("production runtime CAS failed")
    state = json.loads(expected)
    state["bridge_pid"] = pair["bridge_identity"]["pid"]
    state["bridge_process_identity"] = pair["bridge_identity"]
    state["bridge_socket_identity"] = pair["bridge_socket_identity"]
    state["runtime_hook_pid"] = pair["hook_identity"]["pid"]
    state["runtime_hook_process_identity"] = pair["hook_identity"]
    state["runtime_hook_socket_identity"] = pair["hook_socket_identity"]
    if provenance is None:
        state.pop(PROVENANCE, None)
    else:
        state[PROVENANCE] = provenance
    pc._write_json_private(context["path"], state)
    observed = _private_bytes(context["path"], "production runtime state")
    if json.loads(observed) != state:
        raise RuntimeError("production runtime control pair readback failed")
    if _preserved_runtime_state(json.loads(observed)) != _preserved_runtime_state(
        json.loads(expected)
    ):
        raise RuntimeError("production runtime preserved component state changed")
    return observed


def execute_patch(runtime_root: Path, patch: Path, *, rollback=False, ops=None):
    ops = ops or Operations()
    with host_repair.bounded_install_lock(runtime_root):
        context = load_context(runtime_root, ops)
        (
            patch,
            record,
            runtime_before,
            stored_bridge_env_before,
            stored_bridge_env_after,
            stored_hook_env,
        ) = verify_patch(context, patch)
        expected_provenance = _provenance(patch, record)
        active = context["state"].get(PROVENANCE)
        if rollback:
            if active != expected_provenance:
                raise RuntimeError("active paired provenance mismatch; refusing partial rollback")
            _assert_active_pair_commands(context, patch, patched=True)
            target_patched = False
        else:
            if active is not None or context["raw"] != runtime_before:
                raise RuntimeError("control component apply baseline mismatch")
            target_patched = True
        prior_bridge_env, prior_hook_env = _environment_snapshot(context, ops)
        expected_live_bridge_env = (
            stored_bridge_env_after if rollback else stored_bridge_env_before
        )
        if (
            prior_bridge_env != expected_live_bridge_env
            or prior_hook_env != stored_hook_env
        ):
            raise RuntimeError("active control process environment differs from sealed patch record")
        target_bridge_env = (
            stored_bridge_env_before if rollback else stored_bridge_env_after
        )

        side_root = context["root"] / "logs" / f"control-smoke-{uuid.uuid4().hex[:10]}"
        side_paths = _paths(
            context,
            bridge_socket=context["bridge_socket"],
            hook_socket=side_root / "h.sock",
        )
        side_bridge_env, production_state_before = _prepare_side_bridge_state(
            context, target_bridge_env, side_root
        )
        side_bridge_env["LAB_BRIDGE_SOCKET"] = str(context["bridge_socket"])
        if "LAB_RUNTIME_HOST_TOOL_HOOK_SOCKET" in side_bridge_env:
            side_bridge_env["LAB_RUNTIME_HOST_TOOL_HOOK_SOCKET"] = str(side_paths["hook_socket"])
        check_bridge_command, _ = _commands(
            context, patch, patched=True, hook_socket=Path(side_paths["hook_socket"])
        )
        _, check_hook_command = _commands(
            context, patch, patched=target_patched, hook_socket=Path(side_paths["hook_socket"])
        )
        preflight_complete = False
        try:
            ops.preflight_pair(
                check_bridge_command, check_hook_command,
                side_bridge_env, stored_hook_env, side_paths, "side-preflight",
            )
            preflight_complete = True
        except PairStartError as error:
            preflight_complete = error.cleanup_complete
            raise
        finally:
            if _private_tree_snapshot(context["current_layout"]["bridge_state_dir"]) != production_state_before:
                raise RuntimeError("production Bridge state changed during side smoke")
            if preflight_complete:
                shutil.rmtree(side_root)
        _assert_unchanged(context, ops)

        production_paths = _paths(context)
        prior_provenance = active
        target_pair = None
        pair_quiesced = False
        try:
            try:
                ops.stop_pair(
                    context["bridge_identity"], context["hook_identity"], production_paths
                )
                pair_quiesced = True
            except PairStopError as stop_error:
                pair_quiesced = stop_error.fully_stopped
                raise
            target_pair = _start_pair(
                ops, context, patch, target_patched,
                target_bridge_env, stored_hook_env, production_paths,
                "rollback" if rollback else "apply",
            )
            if set(target_pair.get("tools") or []) != set(context["state"].get("tools") or []):
                raise RuntimeError("control component Bridge tool surface changed")
            written = _publish(
                context,
                context["raw"],
                target_pair,
                None if rollback else expected_provenance,
            )
            return {
                "ok": True,
                "action": "rollback" if rollback else "apply",
                "bridge_pid": target_pair["bridge_identity"]["pid"],
                "runtime_hook_pid": target_pair["hook_identity"]["pid"],
                "runtime_sha256": _sha(written),
            }
        except Exception as error:
            safe_to_restore = pair_quiesced
            if target_pair is not None:
                try:
                    ops.stop_started_pair(target_pair, production_paths)
                    safe_to_restore = True
                except PairStopError as cleanup_error:
                    safe_to_restore = cleanup_error.fully_stopped
                except Exception:
                    safe_to_restore = False
            elif isinstance(error, PairStartError):
                safe_to_restore = pair_quiesced and error.cleanup_complete
            sockets_clear = all(
                not Path(production_paths[key]).exists()
                and not Path(production_paths[key]).is_symlink()
                for key in ("bridge_socket", "hook_socket")
            )
            safe_to_restore = safe_to_restore and sockets_clear
            if not safe_to_restore:
                bridge_live = ops.identity_matches(context["bridge_identity"])
                hook_live = ops.identity_matches(context["hook_identity"])
                runtime_unchanged = (
                    _private_bytes(context["path"], "production runtime state")
                    == context["raw"]
                )
                raise RuntimeError(
                    f"{error}; control pair recovery is fail-closed: "
                    f"old_bridge_live={bridge_live}, old_hook_live={hook_live}, "
                    f"production_sockets_clear={sockets_clear}, "
                    f"runtime_state_unchanged={runtime_unchanged}"
                ) from error
            restored = _start_pair(
                ops, context, patch, not target_patched,
                prior_bridge_env, prior_hook_env, production_paths, "restore",
            )
            _publish(context, context["raw"], restored, prior_provenance)
            raise RuntimeError(f"{error}; prior control pair restored with fresh identities") from error


def check(runtime_root: Path, *, patch: Path | None = None, ops=None) -> dict:
    ops = ops or Operations()
    context = load_context(runtime_root, ops)
    active = context["state"].get(PROVENANCE)
    if patch is not None:
        resolved, record, *_ = verify_patch(context, patch)
        expected = _provenance(resolved, record)
        if active is not None and active != expected:
            raise RuntimeError("active paired provenance mismatch")
        if active is not None:
            _assert_active_pair_commands(context, resolved, patched=True)
    elif active is not None:
        manifest = active.get("manifest") if isinstance(active, dict) else None
        if not isinstance(manifest, str):
            raise RuntimeError("active paired provenance mismatch")
        resolved, record, *_ = verify_patch(context, Path(manifest).parent)
        if active != _provenance(resolved, record):
            raise RuntimeError("active paired provenance mismatch")
        _assert_active_pair_commands(context, resolved, patched=True)
    return {
        "ok": True,
        "active": active is not None,
        "bridge_pid": context["state"]["bridge_pid"],
        "runtime_hook_pid": context["state"]["runtime_hook_pid"],
        "runtime_sha256": _sha(context["raw"]),
        "production_written": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Paired production Bridge/Runtime-hook repair")
    parser.add_argument("action", choices=("check", "stage", "apply", "rollback"))
    parser.add_argument("--runtime-root", type=Path, default=pc.PRODUCTION_RUNTIME_ROOT)
    parser.add_argument("--patch-dir", type=Path)
    parser.add_argument("--lab-repo", type=Path)
    parser.add_argument("--lab-commit")
    parser.add_argument("--aar-repo", type=Path)
    parser.add_argument("--aar-commit")
    parser.add_argument("--aar-skill-root", type=Path, default=DEFAULT_AAR_SKILL_ROOT)
    args = parser.parse_args(argv)
    if args.action == "stage":
        if None in (args.lab_repo, args.lab_commit, args.aar_repo, args.aar_commit):
            parser.error("stage requires --lab-repo/--lab-commit and --aar-repo/--aar-commit")
        result = {
            "ok": True,
            "action": "stage",
            "patch_dir": str(
                stage_patch(
                    args.runtime_root,
                    lab_repo=args.lab_repo,
                    lab_commit=args.lab_commit,
                    aar_repo=args.aar_repo,
                    aar_commit=args.aar_commit,
                    aar_skill_root=args.aar_skill_root,
                )
            ),
            "processes_restarted": False,
        }
    elif args.action == "check":
        result = check(args.runtime_root, patch=args.patch_dir)
    else:
        if args.patch_dir is None:
            parser.error(f"{args.action} requires --patch-dir")
        result = execute_patch(
            args.runtime_root, args.patch_dir, rollback=args.action == "rollback"
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
