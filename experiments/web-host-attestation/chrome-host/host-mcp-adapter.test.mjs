import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { chmodSync, mkdtempSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  createHostMcpHandler,
  productionReleaseFenceFromEnvironment,
  validateProductionReleaseFence
} from './host-mcp-adapter.mjs';

const KEY = 'host-backend-0123456789abcdef0123456789abcdef';
const REVISION = 'a'.repeat(40);
const FENCE = Object.freeze({
  connector_id: 'local-agent-bridge-production',
  contract_version: 'lab-production-connector-v1',
  release_revision: REVISION,
  endpoint_generation: 7,
  workspace: '/Users/echoman/Documents/ChatGPT/Local-Agent-Bridge',
  tunnel_profile_name: 'local-agent-bridge-production'
});

function request(body, key = KEY) {
  return {
    headers: {
      'x-lab-backend-key': key,
      'mcp-method': body.method,
      ...(body.method === 'tools/call' ? {'mcp-name': body.params?.name || ''} : {})
    },
    body: Buffer.from(JSON.stringify(body))
  };
}

function handlerOptions(extra = {}) {
  return {
    backendKey: KEY,
    productionFence: FENCE,
    execute: async () => { throw new Error('unused'); },
    ...extra
  };
}

test('Host MCP production release fence accepts only matching contract revision generation workspace and profile', () => {
  const manifest = {
    connector_id: FENCE.connector_id,
    contract_version: FENCE.contract_version,
    release_revision: FENCE.release_revision,
    endpoint_generation: FENCE.endpoint_generation,
    fail_closed: true,
    workspace: FENCE.workspace,
    tunnel: {profile_name: FENCE.tunnel_profile_name},
    host: {
      contract_version: FENCE.contract_version,
      release_revision: FENCE.release_revision,
      endpoint_generation: FENCE.endpoint_generation
    }
  };
  assert.deepEqual(validateProductionReleaseFence(manifest, FENCE), FENCE);
  for (const [key, value] of [
    ['connector_id', 'legacy-connector'],
    ['contract_version', 'old-v0'],
    ['release_revision', 'b'.repeat(40)],
    ['endpoint_generation', 6],
    ['workspace', '/private/tmp/lab-catdesk-candidate-e2e/workspace'],
    ['tunnel_profile_name', 'local-agent-bridge-poc']
  ]) {
    const observed = {...FENCE, [key]: value};
    assert.throws(() => validateProductionReleaseFence(manifest, observed), /production|release|generation|contract|connector/i);
  }
});

test('Host MCP handler refuses to exist without a validated production fence', () => {
  assert.throws(
    () => createHostMcpHandler({backendKey: KEY, execute: async () => ({ok:true})}),
    /production.*fence|required/i
  );
});

test('Host MCP loads the production fence from a private manifest and exact runtime observation', t => {
  const root = mkdtempSync(join(tmpdir(), 'lab-host-production-fence-'));
  t.after(() => rmSync(root, {recursive: true, force: true}));
  const manifestPath = join(root, 'release.json');
  writeFileSync(manifestPath, JSON.stringify({
    ...FENCE,
    fail_closed: true,
    workspace: FENCE.workspace,
    tunnel: {profile_name: FENCE.tunnel_profile_name},
    host: {
      contract_version: FENCE.contract_version,
      release_revision: FENCE.release_revision,
      endpoint_generation: FENCE.endpoint_generation
    }
  }));
  chmodSync(manifestPath, 0o600);
  const env = {
    LAB_PRODUCTION_RELEASE_MANIFEST_FILE: manifestPath,
    LAB_PRODUCTION_CONNECTOR_ID: FENCE.connector_id,
    LAB_PRODUCTION_CONTRACT_VERSION: FENCE.contract_version,
    LAB_PRODUCTION_RELEASE_REVISION: FENCE.release_revision,
    LAB_PRODUCTION_ENDPOINT_GENERATION: String(FENCE.endpoint_generation),
    LAB_PRODUCTION_WORKSPACE: FENCE.workspace,
    LAB_PRODUCTION_TUNNEL_PROFILE_NAME: FENCE.tunnel_profile_name
  };
  assert.deepEqual(productionReleaseFenceFromEnvironment(env), FENCE);
  assert.throws(
    () => productionReleaseFenceFromEnvironment({...env, LAB_PRODUCTION_RELEASE_REVISION: 'b'.repeat(40)}),
    /release.*mismatch/i
  );
  assert.throws(
    () => productionReleaseFenceFromEnvironment({...env, LAB_PRODUCTION_RELEASE_REVISION: REVISION.toUpperCase()}),
    /full Git SHA|release/i
  );
  assert.throws(
    () => productionReleaseFenceFromEnvironment({...env, LAB_PRODUCTION_WORKSPACE: '/private/tmp/lab-catdesk-candidate-e2e/workspace'}),
    /workspace/i
  );
  assert.throws(
    () => productionReleaseFenceFromEnvironment({...env, LAB_PRODUCTION_TUNNEL_PROFILE_NAME: 'local-agent-bridge-poc'}),
    /profile/i
  );
});

test('Host MCP reads owner-only immutable production manifest without chmod mutation', t => {
  const root = mkdtempSync(join(tmpdir(), 'lab-host-immutable-release-'));
  t.after(() => rmSync(root, {recursive:true, force:true}));
  const manifestPath = join(root, 'release.json');
  writeFileSync(manifestPath, JSON.stringify({
    ...FENCE,
    fail_closed:true,
    tunnel:{profile_name:FENCE.tunnel_profile_name},
    host:{contract_version:FENCE.contract_version, release_revision:FENCE.release_revision,
      endpoint_generation:FENCE.endpoint_generation}
  }));
  chmodSync(manifestPath, 0o400);
  const env = {
    LAB_PRODUCTION_RELEASE_MANIFEST_FILE:manifestPath,
    LAB_PRODUCTION_CONNECTOR_ID:FENCE.connector_id,
    LAB_PRODUCTION_CONTRACT_VERSION:FENCE.contract_version,
    LAB_PRODUCTION_RELEASE_REVISION:FENCE.release_revision,
    LAB_PRODUCTION_ENDPOINT_GENERATION:String(FENCE.endpoint_generation),
    LAB_PRODUCTION_WORKSPACE:FENCE.workspace,
    LAB_PRODUCTION_TUNNEL_PROFILE_NAME:FENCE.tunnel_profile_name
  };
  assert.deepEqual(productionReleaseFenceFromEnvironment(env), FENCE);
  assert.equal(statSync(manifestPath).mode & 0o777, 0o400,
    'private immutable production manifest must never be chmod-ed on read');
  chmodSync(manifestPath, 0o644);
  assert.throws(() => productionReleaseFenceFromEnvironment(env), /permissions must be 0600 or stricter/);
});

test('Host MCP process rejects an invalid production manifest before reading backend keys or state', t => {
  const root = mkdtempSync(join(tmpdir(), 'lab-host-production-startup-order-'));
  t.after(() => rmSync(root, {recursive: true, force: true}));
  const manifestPath = join(root, 'release.json');
  writeFileSync(manifestPath, JSON.stringify({
    ...FENCE,
    connector_id: 'legacy-connector',
    fail_closed: true,
    workspace: FENCE.workspace,
    tunnel: {profile_name: FENCE.tunnel_profile_name},
    host: {
      contract_version: FENCE.contract_version,
      release_revision: FENCE.release_revision,
      endpoint_generation: FENCE.endpoint_generation
    }
  }));
  chmodSync(manifestPath, 0o600);
  const result = spawnSync(process.execPath, [fileURLToPath(new URL('./host-mcp-adapter.mjs', import.meta.url))], {
    encoding: 'utf8',
    env: {
      ...process.env,
      LAB_PRODUCTION_RELEASE_MANIFEST_FILE: manifestPath,
      LAB_PRODUCTION_CONNECTOR_ID: FENCE.connector_id,
      LAB_PRODUCTION_CONTRACT_VERSION: FENCE.contract_version,
      LAB_PRODUCTION_RELEASE_REVISION: FENCE.release_revision,
      LAB_PRODUCTION_ENDPOINT_GENERATION: String(FENCE.endpoint_generation),
      LAB_PRODUCTION_WORKSPACE: FENCE.workspace,
      LAB_PRODUCTION_TUNNEL_PROFILE_NAME: FENCE.tunnel_profile_name,
      LAB_HOST_MCP_BACKEND_KEY_FILE: join(root, 'missing-backend.key')
    }
  });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /production release manifest connector_id mismatch/i);
  assert.doesNotMatch(result.stderr, /missing-backend|ENOENT/i);
});

test('Host MCP tools/list exposes only discover_current_entry without conversation_id input and advertises production fence', async () => {
  const handler = createHostMcpHandler(handlerOptions());
  const response = await handler(request({jsonrpc:'2.0', id:'list', method:'tools/list', params:{}}));
  assert.equal(response.status, 200);
  const payload = JSON.parse(response.body);
  const tools = payload.result.tools;
  assert.equal(tools.length, 1);
  assert.equal(tools[0].name, 'discover_current_entry');
  assert.deepEqual(tools[0].inputSchema.required, ['target_generation', 'ownership_generation']);
  assert.equal('conversation_id' in tools[0].inputSchema.properties, false);
  assert.equal(tools[0].inputSchema.additionalProperties, false);
  assert.deepEqual(tools[0]._meta?.['local-agent-bridge/production-fence'], FENCE);
});

test('Host MCP discover_current_entry forwards only generation fences and returns verified machine evidence', async () => {
  const calls = [];
  const handler = createHostMcpHandler(handlerOptions({
    execute: async input => {
      calls.push(input);
      return {
        ok: true,
        operation: 'discover_current_entry',
        current_entry: {
          provenance: 'runtime_host_current_entry_v1',
          entry_scope: 'runtime_invocation',
          machine_source: 'host_invocation_context_v1',
          conversation_id: 'machine-proved-conversation',
          secure_origin: 'https://chatgpt.com',
          target_generation: 8,
          ownership_generation: 8,
          host_receipt_id: 'hce_test'
        }
      };
    }
  }));
  const response = await handler(request({
    jsonrpc:'2.0', id:'call', method:'tools/call',
    params:{name:'discover_current_entry', arguments:{target_generation:8, ownership_generation:8}}
  }));
  assert.equal(response.status, 200);
  assert.deepEqual(calls, [{operation:'discover_current_entry', target_generation:8, ownership_generation:8}]);
  const payload = JSON.parse(response.body);
  assert.equal(payload.result.structuredContent.current_entry.conversation_id, 'machine-proved-conversation');
  assert.equal(payload.result.structuredContent.current_entry.secure_origin, 'https://chatgpt.com');
  assert.deepEqual(payload.result._meta?.['local-agent-bridge/production-fence'], FENCE);
});

test('Host MCP rejects caller conversation identity before Host execution', async () => {
  let called = false;
  const handler = createHostMcpHandler(handlerOptions({execute: async () => { called = true; }}));
  const response = await handler(request({
    jsonrpc:'2.0', id:'bad', method:'tools/call',
    params:{name:'discover_current_entry', arguments:{target_generation:8, ownership_generation:8, conversation_id:'caller'}}
  }));
  assert.equal(response.status, 400);
  assert.equal(called, false);
  assert.match(JSON.parse(response.body).error.message, /conversation_id|additional/i);
});

test('Host MCP preserves trusted current-entry failure as fenced tool error rather than opaque 503', async () => {
  const handler = createHostMcpHandler(handlerOptions({
    execute: async () => { throw new Error('HOST_CURRENT_ENTRY_UNAVAILABLE: trusted Host invocation context unavailable'); }
  }));
  const response = await handler(request({
    jsonrpc:'2.0', id:'host-unavailable', method:'tools/call',
    params:{name:'discover_current_entry', arguments:{target_generation:8, ownership_generation:8}}
  }));
  assert.equal(response.status, 200);
  const payload = JSON.parse(response.body);
  assert.equal(payload.result.isError, true);
  assert.equal(payload.result.structuredContent.error_code, 'HOST_CURRENT_ENTRY_UNAVAILABLE');
  assert.match(payload.result.structuredContent.message, /trusted Host invocation context unavailable/);
  assert.equal('current_entry' in payload.result.structuredContent, false);
  assert.deepEqual(payload.result._meta?.['local-agent-bridge/production-fence'], FENCE);
});

test('Host MCP rejects untrusted verifier results without producing identity evidence', async () => {
  const handler = createHostMcpHandler(handlerOptions({
    execute: async () => ({ok:true, operation:'discover_current_entry', current_entry:{conversation_id:'forged'}})
  }));
  const response = await handler(request({
    jsonrpc:'2.0', id:'untrusted', method:'tools/call',
    params:{name:'discover_current_entry', arguments:{target_generation:8, ownership_generation:8}}
  }));
  assert.equal(response.status, 200);
  const payload = JSON.parse(response.body);
  assert.equal(payload.result.isError, true);
  assert.equal(payload.result.structuredContent.error_code, 'HOST_ATTESTATION_FAILED');
  assert.equal('current_entry' in payload.result.structuredContent, false);
});

test('Host MCP rejects wrong backend key', async () => {
  const handler = createHostMcpHandler(handlerOptions({execute: async () => ({ok:true})}));
  const response = await handler(request({jsonrpc:'2.0', id:'list', method:'tools/list', params:{}}, 'wrong-key'));
  assert.equal(response.status, 401);
});
