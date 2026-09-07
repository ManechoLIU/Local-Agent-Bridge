import assert from 'node:assert/strict';
import { mkdtemp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

const moduleUrl = new URL('./server.mjs', import.meta.url);

async function loadServer() {
  try {
    return await import(moduleUrl);
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

function modernRequest(method, params = {}, headers = {}) {
  return {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'mcp-protocol-version': '2026-07-28',
      'mcp-method': method,
      ...headers,
    },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }),
  };
}

async function rpc(url, method, params = {}, headers = {}) {
  const response = await fetch(url, modernRequest(method, params, headers));
  return { response, body: await response.json() };
}

test('server/discover and tools/list expose the phase-1 noauth contract', async (t) => {
  const probe = await startProbe();
  t.after(probe.close);

  const discover = await rpc(probe.url, 'server/discover', { _meta: {} });
  assert.equal(discover.response.status, 200);
  assert.equal(discover.body.result.protocolVersion, '2026-07-28');
  assert.equal(discover.body.result.serverInfo.name, 'identity-probe');
  assert.deepEqual(discover.body.result.securitySchemes, [{ type: 'noauth' }]);

  const listed = await rpc(probe.url, 'tools/list', { _meta: {} });
  assert.equal(listed.response.status, 200);
  assert.deepEqual(listed.body.result.tools, [{
    name: 'identity_probe',
    description: 'Return a sanitized observation of request identity signals. This does not authenticate a ChatGPT user or conversation.',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
    annotations: {
      readOnlyHint: true,
      openWorldHint: false,
      destructiveHint: false,
    },
    securitySchemes: [{ type: 'noauth' }],
  }]);
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
    'mcp-name': 'identity_probe',
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
  assert.equal(observation.meta['conversationId'].matchesExpectedConversationId, true);
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

test('modern requests fail closed without params._meta', async (t) => {
  const probe = await startProbe();
  t.after(probe.close);

  const missingMeta = await rpc(probe.url, 'ping', {});
  assert.equal(missingMeta.response.status, 200);
  assert.equal(missingMeta.body.error.code, -32602);
  assert.match(missingMeta.body.error.message, /params\._meta/);
});

test('HTTP method handling and MCP correlation headers are enforced', async (t) => {
  const probe = await startProbe();
  t.after(probe.close);

  const getResponse = await fetch(probe.url);
  assert.equal(getResponse.status, 405);

  const deleteResponse = await fetch(probe.url, { method: 'DELETE' });
  assert.equal(deleteResponse.status, 204);

  const mismatchedMethod = await fetch(probe.url, modernRequest('ping', { _meta: {} }, {
    'mcp-method': 'tools/list',
  }));
  assert.equal((await mismatchedMethod.json()).error.code, -32602);

  const missingName = await rpc(probe.url, 'tools/call', {
    _meta: {},
    name: 'identity_probe',
    arguments: {},
  });
  assert.equal(missingName.body.error.code, -32602);
});
