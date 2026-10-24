#!/usr/bin/env python3
"""Host-only maintenance for an already reused production backend.

No release publication, secret rotation, browser binding, or other process is
performed. `check` is read-only; install stages an immutable component patch.
Every process operation requires an exact kernel identity and runtime-byte CAS.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import struct
import subprocess
import sys
import signal
from contextlib import contextmanager
import urllib.request
import uuid

import production_connector as pc

ADAPTER = "chrome-host/host-mcp-adapter.mjs"
PROVENANCE = "host_component_patch"
OLD_FAILURE = "      return json(503, {jsonrpc: '2.0', id, error: {code: -32002, message: String(error?.message || error)}});"
NEW_FAILURE = """      // A Host identity failure is a tool-level error, not a transport outage.
      // Preserve the production fence so ingress can return the reason instead
      // of collapsing it into an opaque downstream 5xx. Never include a
      // current_entry in a failure receipt: Controller recovery stays closed.
      const message = String(error?.message || error);
      const error_code = /^HOST_CURRENT_ENTRY_UNAVAILABLE(?::|$)/.test(message)
        ? 'HOST_CURRENT_ENTRY_UNAVAILABLE' : 'HOST_ATTESTATION_FAILED';
      const structuredContent = {error_code, message};
      return json(200, {
        jsonrpc: '2.0', id,
        result: {
          content: [{type: 'text', text: JSON.stringify(structuredContent)}],
          structuredContent,
          isError: true,
          _meta: {'local-agent-bridge/production-fence': productionFence}
        }
      });"""
OLD_CHMOD = "  chmodSync(path, 0o600);"
NEW_CHMOD = """  // Immutable production releases can be owner-readable but chmod-denied.
  // The lstat already enforces a safe private mode; do not mutate the file
  // just to read it. On some hardened installations chmod throws EPERM."""


@contextmanager
def bounded_install_lock(runtime_root: Path):
    # Reuse the canonical lock while bounding its otherwise blocking flock.
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


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def approved_patch(original: bytes) -> bytes:
    text = original.decode("utf-8")
    for old, new in (
        ("import { chmodSync, lstatSync, readFileSync } from 'node:fs';", "import { lstatSync, readFileSync } from 'node:fs';"),
        (OLD_FAILURE, NEW_FAILURE), (OLD_CHMOD, NEW_CHMOD),
    ):
        if text.count(old) != 1:
            raise RuntimeError("source Host does not match the reviewed patch preimage")
        text = text.replace(old, new, 1)
    return text.encode("utf-8")


def private_bytes(filename: Path, label: str) -> bytes:
    value = pc._read_private_bytes_if_present(filename, label)
    if value is None:
        raise RuntimeError(f"{label} is missing")
    return value


def tree_hashes(root: Path, *, immutable: bool = False, private: bool = True) -> dict[str, str]:
    pc._validate_owned_tree_source(root, "Host component tree")
    hashes = {}
    for item in (root, *sorted(root.rglob("*"))):
        mode = stat.S_IMODE(item.lstat().st_mode)
        if (private and mode & 0o077) or (immutable and mode & 0o222):
            raise RuntimeError("Host component tree is not private/immutable")
        if item.is_file():
            hashes[str(item.relative_to(root))] = pc._sha256_file(item)
    if ADAPTER not in hashes:
        raise RuntimeError("Host component adapter missing")
    return hashes


def copy_owned_host_contents(source: Path, destination: Path) -> None:
    """Copy only validated bytes, never immutable flags, xattrs, or metadata.

    Source release members can carry macOS uchg. copytree/copy2 propagates that
    flag before a destination chmod, making the new staging tree unwritable.
    Fresh private files preserve the release bytes without inheriting its seal.
    """
    pc._validate_owned_tree_source(source, "Host component source")
    destination.mkdir(mode=0o700)
    for member in sorted(source.rglob("*")):
        relative = member.relative_to(source)
        target = destination / relative
        info = member.lstat()
        if stat.S_ISDIR(info.st_mode) and not member.is_symlink():
            target.mkdir(mode=0o700)
            continue
        pc._validate_owned_file_source(member, "Host component source member")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        source_fd = os.open(member, flags)
        try:
            opened = os.fstat(source_fd)
            if not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.geteuid() or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise RuntimeError("Host component source changed during content copy")
            with os.fdopen(source_fd, "rb", closefd=False) as stream:
                payload = stream.read()
        finally:
            os.close(source_fd)
        target_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(target_fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())


def seal(root: Path) -> None:
    for item in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        item.chmod(0o500 if item.is_dir() else 0o400)
    root.chmod(0o500)


def _parse_process_environment_entries(entries) -> dict[str, str]:
    environment = {}
    for entry in entries:
        if not entry:
            break
        if b"=" not in entry:
            raise RuntimeError("invalid Host kernel environment entry")
        key, value = entry.split(b"=", 1)
        name = os.fsdecode(key)
        if not name or name in environment:
            raise RuntimeError("ambiguous Host kernel environment")
        environment[name] = os.fsdecode(value)
    return environment


def process_environment(identity: dict) -> dict[str, str]:
    """Read the kernel argv/env block; never parse ps text or print values."""
    if not pc._process_identity_matches(identity):
        raise RuntimeError("Host process identity drift before environment snapshot")
    pid = identity["pid"]
    if sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        mib = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN, KERN_PROCARGS2
        argmax = ctypes.c_int()
        argmax_size = ctypes.c_size_t(ctypes.sizeof(argmax))
        if libc.sysctlbyname(b"kern.argmax", ctypes.byref(argmax), ctypes.byref(argmax_size), None, 0) != 0 or not 4096 <= argmax.value <= 4 * 1024 * 1024:
            raise RuntimeError("cannot bound the Host kernel environment snapshot")
        size = ctypes.c_size_t(argmax.value)
        buffer = ctypes.create_string_buffer(size.value)
        if libc.sysctl(mib, 3, buffer, ctypes.byref(size), None, 0) != 0:
            raise RuntimeError("cannot capture the Host kernel environment")
        raw = buffer.raw[:size.value]
        argc = struct.unpack_from("=i", raw)[0]
        if argc < 1 or argc > 65536:
            raise RuntimeError("invalid Host kernel argument block")
        offset = raw.index(b"\0", 4) + 1
        while offset < len(raw) and raw[offset] == 0:
            offset += 1
        arguments = []
        for _ in range(argc):
            end = raw.index(b"\0", offset)
            arguments.append(os.fsdecode(raw[offset:end])); offset = end + 1
        if arguments != identity["argv"]:
            raise RuntimeError("Host kernel argv differs from verified process identity")
        entries = raw[offset:].split(b"\0")
    elif sys.platform.startswith("linux"):
        entries = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    else:
        raise RuntimeError("exact Host environment capture unsupported on this platform")
    environment = _parse_process_environment_entries(entries)
    if not pc._process_identity_matches(identity):
        raise RuntimeError("Host process identity drift after environment snapshot")
    return environment


class Operations:
    """Only these bounded operations touch a process; tests replace this class."""
    identity_matches = staticmethod(pc._process_identity_matches)
    environment = staticmethod(process_environment)

    def start(self, command, cwd, environment, logs, label):
        process = pc._spawn_logged(command, logs / f"{label}.out", logs / f"{label}.err", cwd=cwd, env=environment)
        try:
            identity = pc._capture_process_identity(process.pid, expected_executable=Path(command[0]), expected_argv=command)
            pc._wait_for_tcp(process, int(environment["LAB_HOST_MCP_PORT"]), "Host component patch")
            listener = pc._unique_loopback_listener_identity("127.0.0.1", int(environment["LAB_HOST_MCP_PORT"]), "Host component patch")
            if listener != identity:
                raise RuntimeError("Host listener exact identity mismatch")
            return identity
        except Exception:
            # Only a captured exact identity may be stopped; never kill by PID alone.
            try:
                identity = pc._capture_process_identity(process.pid, expected_executable=Path(command[0]), expected_argv=command)
                pc._stop_verified_process_identity(identity, process=process)
            except Exception:
                pass
            raise

    def stop(self, identity):
        pc._stop_verified_process_identity(identity)

    def smoke(self, environment, fence, *, require_error=True):
        port = int(environment["LAB_HOST_MCP_PORT"])
        key = private_bytes(Path(environment["LAB_HOST_MCP_BACKEND_KEY_FILE"]), "Host backend key").decode().strip()
        def request(method, params=None):
            payload = {"jsonrpc": "2.0", "id": "host-component-maintenance", "method": method}
            if params is not None:
                payload["params"] = params
            req = urllib.request.Request(f"http://127.0.0.1:{port}/mcp", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "X-Lab-Backend-Key": key}, method="POST")
            reply = pc._request_json(req, "Host-only loopback smoke")
            if reply.get("jsonrpc") != "2.0" or reply.get("id") != payload["id"] or "error" in reply:
                raise RuntimeError("Host loopback smoke returned an invalid MCP envelope")
            return reply
        listed = request("tools/list")
        tools = listed.get("result", {}).get("tools")
        if not isinstance(tools, list) or len(tools) != 1 or tools[0].get("name") != "discover_current_entry" or tools[0].get("_meta", {}).get("local-agent-bridge/production-fence") != fence:
            raise RuntimeError("Host tools/list production fence mismatch")
        # Generations are signed caller fences, not an authorization test. Use
        # ordinary 1/1 only to exercise transport and the real verifier boundary.
        reply = request("tools/call", {"name": "discover_current_entry", "arguments": {"target_generation": 1, "ownership_generation": 1}})
        result = reply.get("result", {})
        if not isinstance(result, dict) or result.get("_meta", {}).get("local-agent-bridge/production-fence") != fence:
            raise RuntimeError("Host tools/call production fence mismatch")
        structured = result.get("structuredContent")
        if not isinstance(structured, dict):
            raise RuntimeError("Host smoke structured result is missing")
        if result.get("isError") is True:
            def has_current_entry(value):
                if isinstance(value, dict):
                    return "current_entry" in value or any(has_current_entry(v) for v in value.values())
                if isinstance(value, list):
                    return any(has_current_entry(v) for v in value)
                return False
            text_content = []
            for block in result.get("content", []):
                if block.get("type") == "text":
                    try:
                        text_content.append(json.loads(block.get("text", "")))
                    except (TypeError, json.JSONDecodeError):
                        raise RuntimeError("Host error smoke content is malformed")
            if has_current_entry(result) or has_current_entry(text_content) or structured.get("error_code") not in ("HOST_CURRENT_ENTRY_UNAVAILABLE", "HOST_ATTESTATION_FAILED") or not isinstance(structured.get("message"), str) or not structured["message"].strip():
                raise RuntimeError("Host error smoke did not return a fenced MCP isError")
            if require_error:
                missing = Path(environment["LAB_HOST_ADAPTER_SOCKET"])
                if missing.exists() or missing.is_symlink() or "ENOENT" not in structured["message"] or str(missing) not in structured["message"]:
                    raise RuntimeError("Host side smoke did not prove real ENOENT on the absent socket")
            return "fenced_error"
        if require_error or result.get("isError") is not False or "error_code" in structured or not isinstance(structured.get("current_entry"), dict):
            raise RuntimeError("Host smoke did not return a trusted current-entry success or fenced error")
        # Reuse the original immutable release verifier rather than copying its
        # HMAC, freshness, provenance, or generation rules into this utility.
        release = Path(environment["LAB_PRODUCTION_RELEASE_MANIFEST_FILE"]).parent
        private_bytes(Path(environment["LAB_HOST_ATTESTATION_KEY_FILE"]), "Host attestation key")
        verifier = """
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
const source = resolve(dirname(process.env.LAB_PRODUCTION_RELEASE_MANIFEST_FILE), 'host', 'attestation.mjs');
const { verifyCurrentEntryEvidence } = await import(pathToFileURL(source).href);
verifyCurrentEntryEvidence({secret: readFileSync(process.env.LAB_HOST_ATTESTATION_KEY_FILE), evidence: JSON.parse(readFileSync(0, 'utf8')), expectedTargetGeneration: 1, expectedOwnershipGeneration: 1});
"""
        verified = subprocess.run([str(release / "bin" / "node-runtime"), "--input-type=module", "-e", verifier], input=json.dumps(structured["current_entry"]), text=True, capture_output=True, env=environment, timeout=5, check=False)
        if verified.returncode != 0:
            raise RuntimeError("Host success smoke current-entry proof authentication failed")
        return "verified_current_entry"


def load_context(runtime_root: Path, ops: Operations) -> dict:
    runtime_root = runtime_root.resolve(strict=True)
    runtime_path = runtime_root / "runtime.json"
    original = private_bytes(runtime_path, "production runtime state")
    state = json.loads(original)
    if not isinstance(state, dict):
        raise RuntimeError("production runtime state must be a JSON object")
    reused = state.get("reused_live_backends")
    if not isinstance(reused, dict):
        raise RuntimeError("Host-only patch requires existing reused_live_backends")
    source_release = runtime_root / "releases" / pc._release_revision(reused.get("source_release_revision")) / f"generation-{pc._endpoint_generation(reused.get('source_endpoint_generation'))}"
    manifest_path = source_release / "release.json"
    if reused.get("release_manifest") != str(manifest_path):
        raise RuntimeError("reused source manifest path mismatch")
    manifest_bytes = private_bytes(manifest_path, "source production release")
    manifest = json.loads(manifest_bytes)
    pc._validate_manifest_contract(manifest)
    if manifest["release_revision"] != reused["source_release_revision"] or manifest["endpoint_generation"] != reused["source_endpoint_generation"]:
        raise RuntimeError("reused source release fence mismatch")
    current_release = runtime_root / "releases" / pc._release_revision(state.get("release_revision")) / f"generation-{pc._endpoint_generation(state.get('endpoint_generation'))}"
    current_manifest = json.loads(private_bytes(current_release / "release.json", "current production release"))
    current_layout = pc.production_layout(current_manifest, runtime_root=runtime_root)
    pc.validate_installed_release(current_layout)
    if state.get("workspace") != current_manifest["workspace"] or state.get("tunnel_profile_name") != pc.TUNNEL_PROFILE_NAME:
        raise RuntimeError("current runtime production identity mismatch")
    identity = state.get("host_process_identity")
    if not isinstance(identity, dict) or identity.get("pid") != state.get("host_pid") or not ops.identity_matches(identity):
        raise RuntimeError("Host exact process identity mismatch")
    pc._validate_process_identity_record(identity)
    source_host = source_release / "host"
    old_hashes = tree_hashes(source_host, immutable=True, private=False)
    artifacts = manifest.get("artifacts_sha256", {})
    expected_host = {k.removeprefix("host/"): v for k, v in artifacts.items() if k.startswith("host/")}
    if old_hashes != expected_host:
        raise RuntimeError("source Host tree differs from immutable release manifest")
    executable = source_release / "bin" / "node-runtime"
    if identity["exe_realpath"] != str(executable) or pc._sha256_file(executable) != artifacts.get("bin/node-runtime"):
        raise RuntimeError("Host node runtime does not match source release artifact")
    if PROVENANCE not in state and identity["argv"] != [str(executable), str(source_host / ADAPTER)]:
        raise RuntimeError("Host complete argv does not match immutable source adapter")
    fence = {"connector_id": pc.CONNECTOR_ID, "contract_version": pc.CONTRACT_VERSION, "release_revision": manifest["release_revision"], "endpoint_generation": manifest["endpoint_generation"], "workspace": manifest["workspace"], "tunnel_profile_name": pc.TUNNEL_PROFILE_NAME}
    return {"root": runtime_root, "path": runtime_path, "raw": original, "state": state, "source": source_release, "source_host": source_host, "manifest": manifest, "manifest_sha256": sha(manifest_bytes), "hashes": old_hashes, "identity": identity, "executable": executable, "fence": fence}


def assert_unchanged(context, ops):
    if private_bytes(context["path"], "production runtime state") != context["raw"]:
        raise RuntimeError("production runtime bytes changed; refusing Host maintenance")
    if not ops.identity_matches(context["identity"]):
        raise RuntimeError("Host exact process identity changed; refusing Host maintenance")


def validate_environment(context, environment):
    source = context["source"]
    manifest = context["manifest"]
    layout = pc.production_layout(manifest, runtime_root=context["root"])
    expected = pc.production_component_env(manifest, layout, catdesk_slug="unused")["host"]
    for key, value in expected.items():
        if environment.get(key) != value:
            raise RuntimeError(f"Host environment source/fence mismatch: {key}")
    return environment


def stage_patch(runtime_root: Path, candidate: Path, *, ops=None) -> Path:
    ops = ops or Operations()
    with bounded_install_lock(runtime_root):
        context = load_context(runtime_root, ops)
        if PROVENANCE in context["state"]:
            raise RuntimeError("an active Host component patch already exists")
        pc._validate_owned_file_source(candidate, "reviewed Host adapter")
        patched = approved_patch((context["source_host"] / ADAPTER).read_bytes())
        if candidate.read_bytes() != patched:
            raise RuntimeError("candidate changes exceed the reviewed Host adapter patch allowlist")
        environment = validate_environment(context, ops.environment(context["identity"]))
        assert_unchanged(context, ops)
        root = context["root"] / "component-patches"
        pc._private_dir(root)
        patch = root / f"host-{uuid.uuid4()}"
        patch.mkdir(mode=0o700)
        copy_owned_host_contents(context["source_host"], patch / "host")
        (patch / "host" / ADAPTER).write_bytes(patched)
        new_hashes = tree_hashes(patch / "host")
        changed = [name for name in new_hashes if new_hashes[name] != context["hashes"].get(name)]
        if changed != [ADAPTER] or set(new_hashes) != set(context["hashes"]):
            raise RuntimeError("component patch changes exceed the Host adapter allowlist")
        pc._write_json_private(patch / "environment.json", environment)
        pc._write_text_private(patch / "runtime-before.json", context["raw"].decode(), 0o600)
        record = {"schema_version": 1, "component": "host-mcp-diagnostic", "patch_dir": str(patch), "source_release": str(context["source"]), "source_manifest_sha256": context["manifest_sha256"], "source_fence": context["fence"], "source_hashes": context["hashes"], "artifact_hashes": new_hashes, "old_identity": context["identity"], "runtime_before_sha256": sha(context["raw"]), "environment_sha256": pc._sha256_file(patch / "environment.json"), "adapter_relative": ADAPTER}
        pc._write_json_private(patch / "manifest.json", record)
        assert_unchanged(context, ops)
        seal(patch)
        return patch


def verify_patch(context, patch: Path):
    if patch.is_symlink():
        raise RuntimeError("component patch path must not be a symlink")
    patch = patch.resolve(strict=True)
    if patch.parent != context["root"] / "component-patches":
        raise RuntimeError("component patch is outside the private production patch root")
    pc._validate_owned_tree_source(patch, "Host patch")
    expected_root_entries = {"host", "manifest.json", "environment.json", "runtime-before.json"}
    if {item.name for item in patch.iterdir()} != expected_root_entries:
        raise RuntimeError("Host component patch contains unlisted root artifacts")
    for item in (patch, *patch.rglob("*")):
        if stat.S_IMODE(item.lstat().st_mode) & 0o277:
            raise RuntimeError("Host component patch is not sealed private and immutable")
    record = json.loads(private_bytes(patch / "manifest.json", "Host patch manifest"))
    if record.get("schema_version") != 1 or record.get("component") != "host-mcp-diagnostic" or record.get("patch_dir") != str(patch) or record.get("source_release") != str(context["source"]) or record.get("source_manifest_sha256") != context["manifest_sha256"] or record.get("source_fence") != context["fence"] or record.get("source_hashes") != context["hashes"]:
        raise RuntimeError("Host patch source/fence provenance mismatch")
    expected = dict(context["hashes"])
    expected[ADAPTER] = sha(approved_patch((context["source_host"] / ADAPTER).read_bytes()))
    if record.get("artifact_hashes") != expected or tree_hashes(patch / "host", immutable=True) != expected:
        raise RuntimeError("Host component artifact hashes/allowlist mismatch")
    original = private_bytes(patch / "runtime-before.json", "Host runtime rollback snapshot")
    if sha(original) != record.get("runtime_before_sha256"):
        raise RuntimeError("Host rollback runtime snapshot hash mismatch")
    environment = private_bytes(patch / "environment.json", "Host environment snapshot")
    if sha(environment) != record.get("environment_sha256"):
        raise RuntimeError("Host environment snapshot hash mismatch")
    validate_environment(context, json.loads(environment))
    return patch, record, original, json.loads(environment)


def publish_runtime(context, expected: bytes, identity: dict, provenance=None):
    if private_bytes(context["path"], "production runtime state") != expected:
        raise RuntimeError("production runtime CAS failed")
    state = json.loads(expected)
    state["host_pid"] = identity["pid"]
    state["host_process_identity"] = identity
    if provenance is None:
        state.pop(PROVENANCE, None)
    else:
        state[PROVENANCE] = provenance
    pc._write_json_private(context["path"], state)
    observed = private_bytes(context["path"], "production runtime state")
    if json.loads(observed) != state:
        raise RuntimeError("production runtime CAS readback failed")
    return observed


def execute_patch(runtime_root: Path, patch: Path, *, rollback=False, ops=None):
    ops = ops or Operations()
    with bounded_install_lock(runtime_root):
        context = load_context(runtime_root, ops)
        patch, record, original, environment = verify_patch(context, patch)
        provenance = context["state"].get(PROVENANCE)
        expected_provenance = {"manifest": str(patch / "manifest.json"), "manifest_sha256": pc._sha256_file(patch / "manifest.json"), "source_fence": context["fence"], "adapter_sha256": record["artifact_hashes"][ADAPTER]}
        if rollback:
            if provenance != expected_provenance or context["identity"]["argv"] != [str(context["executable"]), str(patch / "host" / ADAPTER)]:
                raise RuntimeError("active Host patch provenance/identity mismatch")
            target = context["source_host"] / ADAPTER
        else:
            if provenance is not None or context["raw"] != original or context["identity"] != record["old_identity"]:
                raise RuntimeError("runtime changed since Host patch staging")
            target = patch / "host" / ADAPTER
        logs = context["root"] / "logs" / patch.name
        pc._private_dir(logs)
        if not rollback:
            with socket.socket() as reserve:
                reserve.bind(("127.0.0.1", 0)); side_port = reserve.getsockname()[1]
            missing_socket = context["root"] / "logs" / f".hs-{uuid.uuid4().hex[:12]}"
            if missing_socket.exists() or missing_socket.is_symlink() or len(os.fsencode(missing_socket)) >= 104:
                raise RuntimeError("cannot establish a unique short absent side-smoke socket")
            side_env = {**environment, "LAB_HOST_MCP_PORT": str(side_port), "LAB_HOST_ADAPTER_SOCKET": str(missing_socket)}
            side_identity = ops.start([str(context["executable"]), str(target)], target.parent.parent, side_env, logs, "smoke")
            try:
                ops.smoke(side_env, context["fence"])
            finally:
                ops.stop(side_identity)
        assert_unchanged(context, ops)
        # Stop only after all source, env, artifact, runtime, and smoke checks pass.
        ops.stop(context["identity"])
        fresh = None
        published = None
        try:
            if private_bytes(context["path"], "production runtime state") != context["raw"]:
                raise RuntimeError("runtime changed after verified Host stop")
            fresh = ops.start([str(context["executable"]), str(target)], target.parent.parent, environment, logs, "rollback" if rollback else "apply")
            ops.smoke(environment, context["fence"], require_error=False)
            if not ops.identity_matches(fresh):
                raise RuntimeError("new Host exact identity changed before CAS")
            published = publish_runtime(context, context["raw"], fresh, None if rollback else expected_provenance)
            return {"ok": True, "action": "rollback" if rollback else "apply", "host_pid": fresh["pid"], "component_patch": str(patch), "source_fence": context["fence"]}
        except Exception as error:
            if fresh is not None:
                ops.stop(fresh)
            # Restart the exact prior argv/environment, then record its REAL new PID.
            restored = ops.start(context["identity"]["argv"], Path(context["identity"]["argv"][1]).parent.parent, environment, logs, "restore")
            ops.smoke(environment, context["fence"], require_error=False)
            if not ops.identity_matches(restored):
                raise RuntimeError("Host recovery identity verification failed") from error
            try:
                publish_runtime(context, published or context["raw"], restored, provenance)
            except Exception as cas_error:
                raise RuntimeError(f"Host restored as verified PID {restored['pid']}; concurrent runtime state preserved and manual reconciliation required") from cas_error
            raise RuntimeError("Host maintenance failed; prior Host restored with fresh verified identity") from error


def check(runtime_root: Path, *, candidate=None, patch=None, ops=None):
    ops = ops or Operations()
    context = load_context(runtime_root, ops)
    if patch is not None:
        _, record, _, _ = verify_patch(context, patch)
        active = context["state"].get(PROVENANCE)
        expected_provenance = {"manifest": str(patch / "manifest.json"), "manifest_sha256": pc._sha256_file(patch / "manifest.json"), "source_fence": context["fence"], "adapter_sha256": record["artifact_hashes"][ADAPTER]}
        if active and (active != expected_provenance or context["identity"]["argv"] != [str(context["executable"]), str(patch / "host" / ADAPTER)]):
            raise RuntimeError("active component patch provenance mismatch")
    elif PROVENANCE in context["state"]:
        active_path = Path(context["state"][PROVENANCE]["manifest"]).parent
        return check(runtime_root, patch=active_path, ops=ops)
    elif candidate is not None:
        pc._validate_owned_file_source(candidate, "reviewed Host adapter")
        if candidate.read_bytes() != approved_patch((context["source_host"] / ADAPTER).read_bytes()):
            raise RuntimeError("candidate changes exceed the reviewed Host patch allowlist")
    else:
        raise RuntimeError("check requires --candidate or --patch-dir")
    assert_unchanged(context, ops)
    return {"ok": True, "action": "check", "host_pid": context["identity"]["pid"], "source_fence": context["fence"], "proves_restarted_host": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "install", "apply", "rollback"))
    parser.add_argument("--runtime-root", type=Path, default=pc.PRODUCTION_RUNTIME_ROOT)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--patch-dir", type=Path)
    args = parser.parse_args(argv)
    if args.action == "check":
        result = check(args.runtime_root, candidate=args.candidate, patch=args.patch_dir)
    elif args.action == "install":
        if args.candidate is None:
            parser.error("install requires --candidate")
        result = {"ok": True, "action": "install", "patch_dir": str(stage_patch(args.runtime_root, args.candidate)), "host_restarted": False}
    else:
        if args.patch_dir is None:
            parser.error("apply/rollback requires --patch-dir")
        result = execute_patch(args.runtime_root, args.patch_dir, rollback=args.action == "rollback")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        raise SystemExit(1)
