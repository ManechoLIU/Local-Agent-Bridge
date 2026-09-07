import assert from 'node:assert/strict';
import { mkdtemp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

const moduleUrl = new URL('./server.mjs', import.meta.url);

async function loadServer() {
  try {
    return await import(moduleUrl.href + `?v=${Date.now()}-${Math.random()}`);
  } catch (error) {
    assert.fail(`identity probe server must load: ${error.message}`);
  }
}

async function startProbe(options = {}) {
  const { createProbeServer } = await loadServer();
  const directory = await mkdtemp(join(tmpdir(), 'identity-probe-'));
  const logPath = join(directory, 'probe.jsonl');
  const server = createProbeServer({ logPath, ...options });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const { port } = server.address();

  return {
    logPath,
    url: `http://127.0.0.1:${port}/mcp`,
    close: () => new Promise((resolve, reject) => {
      server.close((error) => error ? reject(error) : resolve());
    }),
  };
}

async function rpc(url, method, params = {}, headers = {}, id = 1) {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...headers },
    body: JSON.stringify({ jsonrpc: '2.0', id, method, params }),
  });
  return { response, body: await response.json() };
}

test('standard MCP initialize and tools/list expose a noauth identity tool', async (t) => {
  const probe = await startProbe();
  t.after(probe.close);

  const initialized = await rpc(probe.url, 'initialize', {
    protocolVersion: '2025-03-26',
    capabilities: {},
    clientInfo: { name: 'test-client', version: '1' },
  });
  assert.equal(initialized.response.status, 200);
  assert.equal(initialized.body.result.protocolVersion, '2025-03-26');
  assert.equal(initialized.body.result.serverInfo.name, 'identity-probe');
  assert.deepEqual(initialized.body.result.capabilities, { tools: { listChanged: false } });

  const listed = await rpc(probe.url, 'tools/list', {});
  assert.equal(listed.response.status, 200);
  assert.equal(listed.body.result.resultType, 'complete');
  assert.equal(listed.body.result.ttlMs, 0);
  assert.equal(listed.body.result.cacheScope, 'public');
  assert.equal(listed.body.result.tools[0].name, 'identity_probe');
  assert.deepEqual(listed.body.result.tools.map((entry) => entry.name), [
    'identity_probe', 'poc_read', 'poc_write', 'poc_edit', 'poc_command', 'poc_start_job', 'poc_poll_job',
  ]);
});

test('identity_probe sanitizes secrets and reports expected conversation matches', async (t) => {
  const bearer = 'top-secret-bearer-token';
  const expectedConversationId = 'conversation_expected_123';
  const probe = await startProbe({ expectedConversationId });
  t.after(probe.close);

  const called = await rpc(probe.url, 'tools/call', {
    _meta: {
      conversationId: expectedConversationId,
      trace_id: 'trace-secret-456',
      harmless: 'not-identity-metadata',
    },
    name: 'identity_probe',
    arguments: {},
  }, {
    authorization: `Bearer ${bearer}`,
    cookie: 'session=raw-cookie-secret',
    'x-api-key': 'raw-api-key-secret',
    'x-openai-request-id': 'request-secret-789',
    'user-agent': 'identity-probe-test',
  });

  assert.equal(called.response.status, 200);
  const observation = called.body.result.structuredContent;
  assert.equal(observation.oauthVerified, false);
  assert.match(observation.probeSessionId, /^[0-9a-f-]{36}$/);
  assert.match(observation.observationId, /^[0-9a-f-]{36}$/);
  assert.equal(observation.headers.safe['user-agent'], 'identity-probe-test');
  assert.deepEqual(Object.keys(observation.headers.authorization).sort(), [
    'credentialLength', 'credentialSha256Prefix', 'scheme',
  ]);
  assert.equal(observation.headers.authorization.scheme, 'Bearer');
  assert.equal(observation.headers.authorization.credentialLength, bearer.length);
  assert.equal(observation.meta.conversationId.matchesExpectedConversationId, true);
  assert.equal(observation.headers.identity['x-openai-request-id'].matchesExpectedConversationId, false);
  assert.equal('harmless' in observation.meta, false);

  const rawLog = await readFile(probe.logPath, 'utf8');
  assert.equal(rawLog.includes(bearer), false);
  assert.equal(rawLog.includes(expectedConversationId), false);
  assert.equal(rawLog.includes('raw-cookie-secret'), false);
  assert.equal(rawLog.includes('raw-api-key-secret'), false);
  assert.equal(rawLog.includes('request-secret-789'), false);
  const entries = rawLog.trim().split('\n').map(JSON.parse);
  assert.equal(entries.at(-1).meta.conversationId.matchesExpectedConversationId, true);
});

test('standard MCP requests do not require custom correlation headers or _meta', async (t) => {
  const probe = await startProbe();
  t.after(probe.close);

  const listed = await rpc(probe.url, 'tools/list', {});
  assert.equal(listed.response.status, 200);
  assert.equal(listed.body.result.tools[0].name, 'identity_probe');

  const called = await rpc(probe.url, 'tools/call', { name: 'identity_probe', arguments: {} });
  assert.equal(called.response.status, 200);
  assert.deepEqual(called.body.result.structuredContent.meta, {});
});

test('notifications/initialized and optional legacy correlation checks behave safely', async (t) => {
  const probe = await startProbe();
  t.after(probe.close);

  const notification = await fetch(probe.url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized', params: {} }),
  });
  assert.equal(notification.status, 202);

  const mismatchedMethod = await rpc(probe.url, 'ping', {}, { 'mcp-method': 'tools/list' });
  assert.equal(mismatchedMethod.body.error.code, -32602);

  const mismatchedName = await rpc(probe.url, 'tools/call', {
    name: 'identity_probe', arguments: {},
  }, { 'mcp-name': 'wrong-name' });
  assert.equal(mismatchedName.body.error.code, -32602);
});

test('HTTP method handling and legacy server/discover remain available for local harnesses', async (t) => {
  const probe = await startProbe();
  t.after(probe.close);

  const getResponse = await fetch(probe.url);
  assert.equal(getResponse.status, 405);

  const deleteResponse = await fetch(probe.url, { method: 'DELETE' });
  assert.equal(deleteResponse.status, 204);

  const discover = await rpc(probe.url, 'server/discover', { _meta: {} }, {
    'mcp-protocol-version': '2026-07-28',
    'mcp-method': 'server/discover',
  });
  assert.equal(discover.response.status, 200);
  assert.equal(discover.body.result.resultType, 'complete');
  assert.equal(discover.body.result.supportedVersions.includes('2026-07-28'), true);
  assert.deepEqual(discover.body.result.capabilities, { tools: {} });
  assert.equal(discover.body.result.ttlMs, 0);
  assert.equal(discover.body.result.cacheScope, 'private');
  assert.equal(discover.body.result._meta['io.modelcontextprotocol/serverInfo'].name, 'identity-probe');
});
