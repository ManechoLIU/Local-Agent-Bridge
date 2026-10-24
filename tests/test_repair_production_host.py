import copy
import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import repair_production_host as repair


class ProcessEnvironmentParsingTests(unittest.TestCase):
    def test_darwin_environment_stops_before_variable_shaped_apple_vector(self):
        entries = [
            b"HOME=/Users/test",
            b"PATH=/usr/bin:/bin",
            b"",
            b"ptr_munge=0x1234",
            b"main_stack=0x5678",
            b"executable_file=0x9abc",
        ]
        self.assertEqual(
            repair._parse_process_environment_entries(entries),
            {"HOME": "/Users/test", "PATH": "/usr/bin:/bin"},
        )

    def test_linux_trailing_empty_entry_terminates_environment(self):
        self.assertEqual(
            repair._parse_process_environment_entries([b"HOME=/home/test", b""]),
            {"HOME": "/home/test"},
        )


class FakeProcesses:
    def __init__(self, identity, environment):
        self.live = {identity['pid']: copy.deepcopy(identity)}
        self.env = environment
        self.events = []
        self.pid = 500
        self.fail_smoke_label = None
        self.last_label = None
        self.on_side_smoke = None

    def identity_matches(self, identity):
        return self.live.get(identity['pid']) == identity

    def environment(self, identity):
        if not self.identity_matches(identity):
            raise RuntimeError('fake identity drift')
        return dict(self.env)

    def start(self, command, cwd, environment, logs, label):
        self.pid += 1
        identity = {**copy.deepcopy(next(iter(self.live.values()), self.baseline)), 'pid': self.pid, 'pgid': self.pid, 'argv': list(command), 'lstart': f'fake-{self.pid}', 'start_timestamp': float(self.pid)}
        self.live[self.pid] = identity
        self.events.append(('start', label, self.pid, list(command), dict(environment)))
        self.last_label = label
        return copy.deepcopy(identity)

    def stop(self, identity):
        if not self.identity_matches(identity):
            raise RuntimeError('fake identity drift refused')
        self.events.append(('stop', identity['pid']))
        del self.live[identity['pid']]

    def smoke(self, environment, fence, *, require_error=True):
        self.events.append(('smoke', self.last_label, require_error))
        if self.last_label == 'smoke' and self.on_side_smoke:
            self.on_side_smoke()
        if self.last_label == self.fail_smoke_label:
            self.fail_smoke_label = None
            raise RuntimeError('fake smoke failed')


class HostMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hp-', dir='/tmp' if sys.platform == 'darwin' else None)
        self.addCleanup(self.cleanup)
        self.root = Path(self.temp.name).resolve() / 'production'
        self.root.mkdir(mode=0o700)
        self.source = self.root / 'releases' / ('a' * 40) / 'generation-34'
        host = self.source / 'host'
        (host / 'chrome-host').mkdir(parents=True, mode=0o700)
        original = ("import { chmodSync, lstatSync, readFileSync } from 'node:fs';\n" + repair.OLD_FAILURE + '\n' + repair.OLD_CHMOD + '\n').encode()
        (host / repair.ADAPTER).write_bytes(original)
        (host / 'integration.mjs').write_bytes(b'untouched source module')
        node = self.source / 'bin' / 'node-runtime'
        node.parent.mkdir(mode=0o700)
        node.write_bytes(b'fake-node')
        self.manifest = {'connector_id': repair.pc.CONNECTOR_ID, 'contract_version': repair.pc.CONTRACT_VERSION, 'fail_closed': True, 'workspace': str(repair.pc.canonical_workspace()), 'release_revision': 'a' * 40, 'endpoint_generation': 34, 'tunnel': {'profile_name': repair.pc.TUNNEL_PROFILE_NAME}}
        self.manifest['artifacts_sha256'] = {'host/' + str(p.relative_to(host)): repair.pc._sha256_file(p) for p in host.rglob('*') if p.is_file()}
        self.manifest['artifacts_sha256']['bin/node-runtime'] = repair.pc._sha256_file(node)
        self.write(self.source / 'release.json', self.manifest)
        repair.seal(host)
        current = self.root / 'releases' / ('b' * 40) / 'generation-35'
        current.mkdir(parents=True)
        self.write(current / 'release.json', {**self.manifest, 'release_revision': 'b' * 40, 'endpoint_generation': 35})
        self.identity = {'pid': 100, 'uid': os.geteuid(), 'pgid': 100, 'lstart': 'fake-original', 'start_timestamp': 1.0, 'exe_realpath': str(node), 'exe_inode': 1, 'exe_sha256': repair.pc._sha256_file(node), 'argv': [str(node), str(host / repair.ADAPTER)]}
        self.state = {'release_revision': 'b' * 40, 'endpoint_generation': 35, 'workspace': self.manifest['workspace'], 'tunnel_profile_name': repair.pc.TUNNEL_PROFILE_NAME, 'host_pid': 100, 'host_process_identity': self.identity, 'reused_live_backends': {'source_release_revision': 'a' * 40, 'source_endpoint_generation': 34, 'release_manifest': str(self.source / 'release.json')}, 'bridge_pid': 200, 'bridge_capability_id': 'private-never-change', 'native_binding': {'unchanged': True}, 'other_field': ['must', 'remain']}
        self.state_path = self.root / 'runtime.json'
        self.write(self.state_path, self.state)
        self.environment = {'LAB_HOST_MCP_PORT': '33321', 'LAB_PRODUCTION_RELEASE_REVISION': 'a' * 40, 'PRIVATE_TEST_SECRET': 'must-not-output'}
        self.ops = FakeProcesses(self.identity, self.environment)
        self.ops.baseline = self.identity
        self.candidate = self.root.parent / 'candidate.mjs'
        self.candidate.write_bytes(repair.approved_patch(original))
        self.mocks = [patch.object(repair.pc, '_validate_manifest_contract'), patch.object(repair.pc, 'validate_installed_release'), patch.object(repair.pc, 'production_component_env', return_value={'host': self.environment})]
        for mock in self.mocks:
            mock.start(); self.addCleanup(mock.stop)

    def cleanup(self):
        for p in sorted(Path(self.temp.name).rglob('*'), key=lambda p: len(p.parts)):
            if p.is_dir():
                p.chmod(0o700)
            elif p.is_file():
                p.chmod(0o600)
        self.temp.cleanup()

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    def stage(self):
        return repair.stage_patch(self.root, self.candidate, ops=self.ops)

    @unittest.skipUnless(sys.platform == 'darwin' and hasattr(os, 'chflags'), 'macOS file flags required')
    def test_content_copy_does_not_inherit_real_macos_immutable_flags(self):
        source = self.root.parent / 'flagged-source'
        source.mkdir(mode=0o700)
        member = source / 'current-entry-registry.js'
        member.write_bytes(b'production source content')
        member.chmod(0o400)
        original_flags = member.stat().st_flags
        os.chflags(member, original_flags | stat.UF_IMMUTABLE)
        destination = self.root.parent / 'flag-free-patch'
        try:
            repair.copy_owned_host_contents(source, destination)
            copied = destination / member.name
            self.assertEqual(copied.read_bytes(), member.read_bytes())
            self.assertEqual(copied.stat().st_flags & stat.UF_IMMUTABLE, 0)
            self.assertEqual(stat.S_IMODE(copied.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o700)
            self.assertTrue(member.stat().st_flags & stat.UF_IMMUTABLE)
            copied.write_bytes(b'patch may be written before sealing')
            self.assertEqual(member.read_bytes(), b'production source content')
        finally:
            os.chflags(member, original_flags)

    def test_check_has_no_process_or_state_effects(self):
        before = self.state_path.read_bytes()
        result = repair.check(self.root, candidate=self.candidate, ops=self.ops)
        self.assertTrue(result['ok'])
        self.assertEqual(before, self.state_path.read_bytes())
        self.assertEqual(self.ops.events, [])

    def test_bad_patch_rejected_before_stage_or_stop(self):
        self.candidate.write_bytes(self.candidate.read_bytes() + b'//unreviewed\n')
        with self.assertRaisesRegex(RuntimeError, 'allowlist'):
            self.stage()
        self.assertEqual(self.ops.events, [])
        self.assertFalse((self.root / 'component-patches').exists())

    def test_source_hash_tamper_is_rejected(self):
        source = self.source / 'host' / 'integration.mjs'
        source.chmod(0o600); source.write_bytes(b'tamper'); source.chmod(0o400)
        with self.assertRaisesRegex(RuntimeError, 'immutable release manifest'):
            self.stage()
        self.assertEqual(self.ops.events, [])

    def test_side_smoke_failure_never_stops_production_host(self):
        staged = self.stage(); self.ops.fail_smoke_label = 'smoke'
        with self.assertRaisesRegex(RuntimeError, 'smoke failed'):
            repair.execute_patch(self.root, staged, ops=self.ops)
        self.assertIn(100, self.ops.live)
        self.assertNotIn(('stop', 100), self.ops.events)
        self.assertEqual(json.loads(self.state_path.read_bytes()), self.state)

    def test_state_drift_after_smoke_refuses_production_stop(self):
        staged = self.stage()
        def drift():
            self.write(self.state_path, {**self.state, 'concurrent': True})
        self.ops.on_side_smoke = drift
        with self.assertRaisesRegex(RuntimeError, 'runtime bytes changed'):
            repair.execute_patch(self.root, staged, ops=self.ops)
        self.assertNotIn(('stop', 100), self.ops.events)
        self.assertTrue(json.loads(self.state_path.read_bytes())['concurrent'])

    def test_pid_identity_drift_refuses_production_stop(self):
        staged = self.stage()
        self.ops.on_side_smoke = lambda: self.ops.live[100].update(lstart='replacement')
        with self.assertRaisesRegex(RuntimeError, 'identity changed'):
            repair.execute_patch(self.root, staged, ops=self.ops)
        self.assertNotIn(('stop', 100), self.ops.events)

    def test_side_smoke_has_absent_socket_and_production_keeps_original_environment(self):
        self.environment['LAB_HOST_ADAPTER_SOCKET'] = '/original-native-host.sock'
        staged = self.stage()
        repair.execute_patch(self.root, staged, ops=self.ops)
        starts = [entry for entry in self.ops.events if entry[0] == 'start']
        side = next(entry for entry in starts if entry[1] == 'smoke')[4]
        production = next(entry for entry in starts if entry[1] == 'apply')[4]
        self.assertNotEqual(side['LAB_HOST_ADAPTER_SOCKET'], self.environment['LAB_HOST_ADAPTER_SOCKET'])
        self.assertFalse(Path(side['LAB_HOST_ADAPTER_SOCKET']).exists())
        self.assertEqual(Path(side['LAB_HOST_ADAPTER_SOCKET']).parent, self.root / 'logs')
        self.assertLess(len(os.fsencode(side['LAB_HOST_ADAPTER_SOCKET'])), 104)
        self.assertEqual(production, self.environment)

    def test_apply_and_rollback_preserve_every_non_host_field(self):
        staged = self.stage()
        result = repair.execute_patch(self.root, staged, ops=self.ops)
        self.assertTrue(result['ok'])
        applied = json.loads(self.state_path.read_bytes())
        self.assertNotEqual(applied['host_pid'], 100)
        for key in self.state:
            if key not in ('host_pid', 'host_process_identity'):
                self.assertEqual(applied[key], self.state[key])
        self.assertIn(repair.PROVENANCE, applied)
        repair.check(self.root, patch=staged, ops=self.ops)
        rollback = repair.execute_patch(self.root, staged, rollback=True, ops=self.ops)
        restored = json.loads(self.state_path.read_bytes())
        self.assertNotIn(repair.PROVENANCE, restored)
        self.assertEqual(restored['host_process_identity']['argv'], self.identity['argv'])
        self.assertNotEqual(restored['host_pid'], 100)
        self.assertEqual(restored['host_pid'], rollback['host_pid'])
        for key in self.state:
            if key not in ('host_pid', 'host_process_identity'):
                self.assertEqual(restored[key], self.state[key])

    def test_production_smoke_failure_restores_old_command_and_real_new_pid(self):
        staged = self.stage(); self.ops.fail_smoke_label = 'apply'
        with self.assertRaisesRegex(RuntimeError, 'prior Host restored'):
            repair.execute_patch(self.root, staged, ops=self.ops)
        restored = json.loads(self.state_path.read_bytes())
        self.assertNotEqual(restored['host_pid'], 100)
        self.assertEqual(restored['host_process_identity']['argv'], self.identity['argv'])
        self.assertTrue(self.ops.identity_matches(restored['host_process_identity']))
        self.assertNotIn(repair.PROVENANCE, restored)

    def test_rollback_failure_restores_active_patch_with_real_identity(self):
        staged = self.stage()
        repair.execute_patch(self.root, staged, ops=self.ops)
        prior = json.loads(self.state_path.read_bytes())
        self.ops.fail_smoke_label = 'rollback'
        with self.assertRaisesRegex(RuntimeError, 'prior Host restored'):
            repair.execute_patch(self.root, staged, rollback=True, ops=self.ops)
        restored = json.loads(self.state_path.read_bytes())
        self.assertNotEqual(restored['host_pid'], prior['host_pid'])
        self.assertEqual(restored[repair.PROVENANCE], prior[repair.PROVENANCE])
        self.assertEqual(restored['host_process_identity']['argv'], prior['host_process_identity']['argv'])
        self.assertTrue(self.ops.identity_matches(restored['host_process_identity']))

    def test_active_provenance_tamper_is_not_hidden_by_read_only_check(self):
        staged = self.stage()
        repair.execute_patch(self.root, staged, ops=self.ops)
        state = json.loads(self.state_path.read_bytes())
        state[repair.PROVENANCE]['adapter_sha256'] = '0' * 64
        self.write(self.state_path, state)
        with self.assertRaisesRegex(RuntimeError, 'provenance mismatch'):
            repair.check(self.root, ops=self.ops)

    def test_staged_patch_tamper_fails_before_any_process_operation(self):
        staged = self.stage(); target = staged / 'host' / repair.ADAPTER
        target.chmod(0o600); target.write_bytes(target.read_bytes() + b'tamper'); target.chmod(0o400)
        with self.assertRaisesRegex(RuntimeError, 'hashes/allowlist'):
            repair.execute_patch(self.root, staged, ops=self.ops)
        self.assertEqual(self.ops.events, [])

    def test_environment_snapshot_and_patch_modes_are_private_immutable(self):
        staged = self.stage()
        for p in (staged, *staged.rglob('*')):
            self.assertEqual(stat.S_IMODE(p.stat().st_mode) & 0o277, 0)
        record = json.loads((staged / 'manifest.json').read_bytes())
        self.assertNotIn('must-not-output', json.dumps(record))
        self.assertEqual(set(record['artifact_hashes']), set(record['source_hashes']))
        self.assertEqual([name for name in record['artifact_hashes'] if record['artifact_hashes'][name] != record['source_hashes'][name]], [repair.ADAPTER])


class RealHostSmokeTests(unittest.TestCase):
    def setUp(self):
        self.node = shutil.which('node')
        if not self.node:
            self.skipTest('local Node runtime required; no installation performed')
        self.temp = tempfile.TemporaryDirectory(prefix='hs-', dir='/tmp' if sys.platform == 'darwin' else None)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.release = self.root / 'release'
        self.release.mkdir(mode=0o700)
        source = Path(__file__).resolve().parents[1] / 'experiments' / 'web-host-attestation'
        repair.copy_owned_host_contents(source, self.release / 'host')
        (self.release / 'bin').mkdir(mode=0o700)
        (self.release / 'bin' / 'node-runtime').symlink_to(self.node)
        self.fence = {'connector_id': repair.pc.CONNECTOR_ID, 'contract_version': repair.pc.CONTRACT_VERSION, 'release_revision': 'a' * 40, 'endpoint_generation': 34, 'workspace': str(repair.pc.canonical_workspace()), 'tunnel_profile_name': repair.pc.TUNNEL_PROFILE_NAME}
        manifest = {**self.fence, 'fail_closed': True, 'tunnel': {'profile_name': repair.pc.TUNNEL_PROFILE_NAME}, 'host': {'contract_version': repair.pc.CONTRACT_VERSION, 'release_revision': 'a' * 40, 'endpoint_generation': 34}}
        self.private(self.release / 'release.json', json.dumps(manifest).encode())
        self.private(self.root / 'backend.key', b'b' * 32)
        self.private(self.root / 'attestation.key', b'a' * 32)
        with socket.socket() as reserve:
            reserve.bind(('127.0.0.1', 0)); port = reserve.getsockname()[1]
        self.env = {**os.environ, 'LAB_HOST_MCP_PORT': str(port), 'LAB_HOST_MCP_BIND': '127.0.0.1', 'LAB_HOST_MCP_BACKEND_KEY_FILE': str(self.root / 'backend.key'), 'LAB_HOST_ATTESTATION_KEY_FILE': str(self.root / 'attestation.key'), 'LAB_HOST_ADAPTER_SOCKET': str(self.root / 'absent.sock'), 'LAB_HOST_VERIFIER_STATE_FILE': str(self.root / 'verifier.json'), 'LAB_HOST_REENTRY_STATE_FILE': str(self.root / 'reentry.json'), 'LAB_PRODUCTION_RELEASE_MANIFEST_FILE': str(self.release / 'release.json')}
        for field, name in [('connector_id', 'CONNECTOR_ID'), ('contract_version', 'CONTRACT_VERSION'), ('release_revision', 'RELEASE_REVISION'), ('endpoint_generation', 'ENDPOINT_GENERATION'), ('workspace', 'WORKSPACE'), ('tunnel_profile_name', 'TUNNEL_PROFILE_NAME')]:
            self.env['LAB_PRODUCTION_' + name] = str(self.fence[field])

    def private(self, path, payload):
        path.write_bytes(payload); path.chmod(0o600)

    def start(self, fixture=False, mode='success'):
        if fixture:
            script = self.root / 'http-fixture.mjs'
            script.write_text("""
import { createServer } from 'node:http';
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
const root = process.env.TEST_RELEASE;
const { createHostMcpHandler } = await import(pathToFileURL(root + '/host/chrome-host/host-mcp-adapter.mjs'));
const { signCurrentEntryEvidence } = await import(pathToFileURL(root + '/host/attestation.mjs'));
const fence = JSON.parse(process.env.TEST_FENCE);
const handler = createHostMcpHandler({backendKey: readFileSync(process.env.LAB_HOST_MCP_BACKEND_KEY_FILE, 'utf8'), productionFence: fence, execute: async input => {
  if (process.env.TEST_MODE === 'fenced_error') throw new Error('HOST_CURRENT_ENTRY_UNAVAILABLE: no active test invocation');
  const current = {...signCurrentEntryEvidence({secret: readFileSync(process.env.LAB_HOST_ATTESTATION_KEY_FILE), targetGeneration: input.target_generation, ownershipGeneration: input.ownership_generation, machineContext: {entry_scope: 'runtime_invocation', machine_source: 'host_invocation_context_v1', conversation_id: '01234567-89ab-cdef-0123-456789abcdef', browser_target_id: 'target-test', top_frame_id: 'frame-test', loader_id: 'loader-test', secure_origin: 'https://chatgpt.com', generation_anchor_sha256: 'a'.repeat(64), observed_at_ms: Date.now()}})};
  if (process.env.TEST_MODE === 'forged') current.host_mac_sha256 = '0'.repeat(64);
  return {ok: true, operation: 'discover_current_entry', current_entry: current};
}});
createServer(async (req, res) => {
  const chunks = []; for await (const chunk of req) chunks.push(chunk);
  const response = await handler({headers: req.headers, body: Buffer.concat(chunks)});
  let body = response.body;
  if (process.env.TEST_MODE === 'error_with_entry') { const parsed = JSON.parse(body); if (parsed.result?.structuredContent?.error_code) parsed.result.structuredContent.current_entry = {forged:true}; body = JSON.stringify(parsed); }
  res.writeHead(response.status, {'content-type': 'application/json'}); res.end(body);
}).listen(Number(process.env.LAB_HOST_MCP_PORT), '127.0.0.1');
""")
            extra = {'TEST_RELEASE': str(self.release), 'TEST_FENCE': json.dumps(self.fence), 'TEST_MODE': 'fenced_error' if mode == 'error_with_entry' else mode}
            if mode == 'error_with_entry':
                # The fixture adds an entry after producing a real error envelope.
                script.write_text(script.read_text().replace("process.env.TEST_MODE === 'fenced_error'", "['fenced_error','error_with_entry'].includes(process.env.TEST_MODE)"))
                extra['TEST_MODE'] = mode
            command = [self.node, str(script)]
        else:
            extra = {}
            command = [self.node, str(self.release / 'host' / repair.ADAPTER)]
        process = subprocess.Popen(command, cwd=self.root, env={**self.env, **extra}, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def stop():
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=5)
        self.addCleanup(stop)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self.fail('temporary Host subprocess exited before readiness')
            if repair.pc._tcp_ready('127.0.0.1', int(self.env['LAB_HOST_MCP_PORT'])):
                return
            time.sleep(0.02)
        self.fail('temporary Host subprocess readiness timed out')

    def test_real_host_absent_socket_returns_fenced_enoent_without_production_access(self):
        self.start()
        self.assertEqual(repair.Operations().smoke(self.env, self.fence), 'fenced_error')
        self.assertFalse(Path(self.env['LAB_HOST_ADAPTER_SOCKET']).exists())
        self.assertFalse((self.root / 'verifier.json').exists())
        self.assertFalse((self.root / 'reentry.json').exists())

    def test_real_http_success_requires_original_verifier_mac_and_accepts_one_one(self):
        self.start(fixture=True)
        self.assertEqual(repair.Operations().smoke(self.env, self.fence, require_error=False), 'verified_current_entry')

    def test_real_http_forged_success_is_rejected_by_original_verifier(self):
        self.start(fixture=True, mode='forged')
        with self.assertRaisesRegex(RuntimeError, 'proof authentication failed'):
            repair.Operations().smoke(self.env, self.fence, require_error=False)

    def test_real_http_fenced_error_is_accepted_without_current_entry(self):
        self.start(fixture=True, mode='fenced_error')
        self.assertEqual(repair.Operations().smoke(self.env, self.fence, require_error=False), 'fenced_error')

    def test_real_http_error_with_current_entry_remains_rejected(self):
        self.start(fixture=True, mode='error_with_entry')
        with self.assertRaisesRegex(RuntimeError, 'fenced MCP isError'):
            repair.Operations().smoke(self.env, self.fence, require_error=False)


if __name__ == '__main__':
    unittest.main()
