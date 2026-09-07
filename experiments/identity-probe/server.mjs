import { createHash, randomUUID } from 'node:crypto';
import { appendFile, mkdir } from 'node:fs/promises';
import { dirname } from 'node:path';
import { createServer } from 'node:http';

const legacyProtocolVersion = '2026-07-28';
const supportedMcpProtocolVersions = new Set(['2025-11-25', '2025-03-26']);
const defaultMcpProtocolVersion = '2025-11-25';

const tool = {
  name: 'identity_probe',
  description: 'Return a sanitized observation of request identity signals. This does not authenticate a ChatGPT user or conversation.',
  inputSchema: { type: 'object', properties: {}, additionalProperties: false },
  annotations: { readOnlyHint: true, openWorldHint: false, destructiveHint: false, idempotentHint: true },
};

const safePocTools = [
  { name: 'poc_read', description: 'Read the fixed PoC source file inside the isolated workspace.', inputSchema: { type: 'object', properties: {}, additionalProperties: false }, annotations: { readOnlyHint: true, openWorldHint: false, destructiveHint: false, idempotentHint: true } },
  { name: 'poc_write', description: 'Write a fixed marker to the fixed PoC target file inside the isolated workspace.', inputSchema: { type: 'object', properties: {}, additionalProperties: false }, annotations: { readOnlyHint: false, openWorldHint: false, destructiveHint: false, idempotentHint: true } },
  { name: 'poc_edit', description: 'Edit the fixed PoC target file by replacing the fixed write marker with the fixed edited marker.', inputSchema: { type: 'object', properties: {}, additionalProperties: false }, annotations: { readOnlyHint: false, openWorldHint: false, destructiveHint: false, idempotentHint: true } },
  { name: 'poc_command', description: 'Run the fixed harmless /bin/pwd command in the isolated PoC workspace.', inputSchema: { type: 'object', properties: {}, additionalProperties: false }, annotations: { readOnlyHint: true, openWorldHint: false, destructiveHint: false, idempotentHint: true } },
  { name: 'poc_start_job', description: 'Start the fixed short background PoC job and return its job id.', inputSchema: { type: 'object', properties: {}, additionalProperties: false }, annotations: { readOnlyHint: false, openWorldHint: false, destructiveHint: false, idempotentHint: false } },
  { name: 'poc_poll_job', description: 'Poll a PoC job previously started by poc_start_job.', inputSchema: { type: 'object', properties: { jobId: { type: 'string' } }, required: ['jobId'], additionalProperties: false }, annotations: { readOnlyHint: true, openWorldHint: false, destructiveHint: false, idempotentHint: true } },
];

const allTools = [tool, ...safePocTools];
const safePocToolNames = new Set(safePocTools.map((entry) => entry.name));
const safePocServerUrl = 'http://127.0.0.1:33303/mcp';

async function callSafePocTool(name, args = {}) {
  const response = await fetch(safePocServerUrl, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'mcp-protocol-version': legacyProtocolVersion,
      'mcp-method': 'tools/call',
      'mcp-name': name,
    },
    body: JSON.stringify({
      jsonrpc: '2.0',
      id: `safe-${randomUUID()}`,
      method: 'tools/call',
      params: {
        _meta: {
          'io.modelcontextprotocol/protocolVersion': legacyProtocolVersion,
          'io.modelcontextprotocol/clientCapabilities': {},
          'io.modelcontextprotocol/clientInfo': { name: 'identity-probe-safe-poc-adapter', version: '1' },
        },
        name,
        arguments: args,
      },
    }),
  });
  const payload = await response.json();
  if (!response.ok || payload?.error) throw new Error(payload?.error?.message || `safe PoC backend HTTP ${response.status}`);
  return payload.result;
}

function hashPrefix(value) {
  return createHash('sha256').update(String(value)).digest('hex').slice(0, 16);
}

function fingerprint(value, expectedConversationId) {
  const raw = String(value);
  return {
    present: true,
    valueLength: raw.length,
    valueSha256Prefix: hashPrefix(raw),
    matchesExpectedConversationId: Boolean(expectedConversationId) && raw === expectedConversationId,
  };
}

function sanitizeAuthorization(value) {
  if (!value) return { present: false };
  const raw = String(value);
  const split = raw.indexOf(' ');
  const scheme = split < 0 ? raw : raw.slice(0, split);
  const credential = split < 0 ? '' : raw.slice(split + 1);
  return {
    scheme,
    credentialLength: credential.length,
    credentialSha256Prefix: hashPrefix(credential),
  };
}

function sanitizeHeaders(headers, expectedConversationId) {
  const safe = {};
  const identity = {};
  const safeNames = new Set(['content-type', 'mcp-protocol-version', 'mcp-method', 'mcp-name', 'user-agent']);

  for (const [name, value] of Object.entries(headers)) {
    if (value == null) continue;
    const lower = name.toLowerCase();
    if (safeNames.has(lower)) {
      safe[lower] = String(value);
      continue;
    }
    if (
      lower.startsWith('x-openai-') ||
      lower.startsWith('openai-') ||
      lower.includes('conversation') ||
      lower.includes('session') ||
      lower.includes('thread') ||
      lower.includes('request-id') ||
      lower.includes('trace') ||
      lower.includes('client')
    ) {
      identity[lower] = fingerprint(value, expectedConversationId);
    }
  }

  return {
    safe,
    authorization: sanitizeAuthorization(headers.authorization),
    identity,
  };
}

function sanitizeMeta(meta, expectedConversationId) {
  const allowed = new Set([
    'conversationId', 'conversation_id',
    'sessionId', 'session_id',
    'threadId', 'thread_id',
    'requestId', 'request_id',
    'traceId', 'trace_id',
  ]);
  const out = {};
  for (const [key, value] of Object.entries(meta ?? {})) {
    if (!allowed.has(key) || value == null) continue;
    if (!['string', 'number', 'boolean'].includes(typeof value)) continue;
    out[key] = fingerprint(value, expectedConversationId);
  }
  return out;
}

function result(id, value) {
  return { jsonrpc: '2.0', id: id ?? null, result: value };
}

function error(id, code, message) {
  return { jsonrpc: '2.0', id: id ?? null, error: { code, message } };
}

function sendJson(res, value, status = 200) {
  const body = JSON.stringify(value);
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store',
    'content-length': Buffer.byteLength(body),
  });
  res.end(body);
}

async function readBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

function negotiatedProtocolVersion(requested) {
  return supportedMcpProtocolVersions.has(requested) ? requested : defaultMcpProtocolVersion;
}

export function createProbeServer({ logPath, expectedConversationId = null } = {}) {
  const probeSessionId = randomUUID();

  return createServer(async (req, res) => {
    try {
      const url = new URL(req.url ?? '/', 'http://127.0.0.1');
      if (url.pathname !== '/mcp') {
        res.writeHead(404).end();
        return;
      }
      if (req.method === 'DELETE') {
        res.writeHead(204).end();
        return;
      }
      if (req.method !== 'POST') {
        res.writeHead(405, { allow: 'POST, DELETE' }).end();
        return;
      }

      let rpc;
      try {
        rpc = await readBody(req);
      } catch {
        sendJson(res, error(null, -32700, 'invalid JSON'));
        return;
      }

      const { id = null, method, params = {} } = rpc ?? {};
      if (typeof method !== 'string') {
        sendJson(res, error(id, -32600, 'invalid request'));
        return;
      }

      if (logPath) {
        const transportObservation = {
          observationId: randomUUID(),
          probeSessionId,
          observedAt: new Date().toISOString(),
          phase: 'rpc',
          rpcMethod: method,
          toolName: method === 'tools/call' && typeof params?.name === 'string' ? params.name : null,
          headers: sanitizeHeaders(req.headers, expectedConversationId),
          meta: sanitizeMeta(params?._meta, expectedConversationId),
        };
        await mkdir(dirname(logPath), { recursive: true });
        await appendFile(logPath, JSON.stringify(transportObservation) + '\n', { encoding: 'utf8', mode: 0o600 });
      }

      const declaredMethod = req.headers['mcp-method'];
      if (declaredMethod && declaredMethod !== method) {
        sendJson(res, error(id, -32602, 'mcp-method header mismatch'));
        return;
      }

      if (method === 'initialize') {
        sendJson(res, result(id, {
          protocolVersion: negotiatedProtocolVersion(params?.protocolVersion),
          capabilities: { tools: { listChanged: false } },
          serverInfo: { name: 'identity-probe', version: '0.2.0' },
        }));
        return;
      }

      if (method === 'notifications/initialized') {
        res.writeHead(202, { 'cache-control': 'no-store' }).end();
        return;
      }

      if (method === 'server/discover') {
        sendJson(res, result(id, {
          resultType: 'complete',
          supportedVersions: ['2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25', legacyProtocolVersion],
          capabilities: { tools: {} },
          instructions: 'Read-only Local Agent Bridge identity probe for PoC transport and identity-signal validation.',
          ttlMs: 0,
          cacheScope: 'private',
          _meta: {
            'io.modelcontextprotocol/serverInfo': { name: 'identity-probe', version: '0.2.0' },
          },
        }));
        return;
      }

      if (method === 'tools/list') {
        sendJson(res, result(id, {
          resultType: 'complete',
          ttlMs: 0,
          cacheScope: 'public',
          tools: allTools,
        }));
        return;
      }

      if (method === 'ping') {
        sendJson(res, result(id, {}));
        return;
      }

      if (method !== 'tools/call') {
        sendJson(res, error(id, -32601, 'method not found'));
        return;
      }

      const declaredTool = allTools.find((entry) => entry.name === params?.name);
      if (!declaredTool) {
        sendJson(res, error(id, -32601, 'unknown tool'));
        return;
      }
      const declaredName = req.headers['mcp-name'];
      if (declaredName && declaredName !== params.name) {
        sendJson(res, error(id, -32602, 'mcp-name header mismatch'));
        return;
      }

      if (safePocToolNames.has(params.name)) {
        sendJson(res, result(id, await callSafePocTool(params.name, params.arguments ?? {})));
        return;
      }

      const observation = {
        observationId: randomUUID(),
        probeSessionId,
        observedAt: new Date().toISOString(),
        oauthVerified: false,
        headers: sanitizeHeaders(req.headers, expectedConversationId),
        meta: sanitizeMeta(params?._meta, expectedConversationId),
      };

      if (logPath) {
        await mkdir(dirname(logPath), { recursive: true });
        await appendFile(logPath, JSON.stringify(observation) + '\n', { encoding: 'utf8', mode: 0o600 });
      }

      sendJson(res, result(id, {
        content: [{
          type: 'text',
          text: 'Identity signals observed and sanitized. This does not authenticate a ChatGPT user or conversation.',
        }],
        structuredContent: observation,
        isError: false,
      }));
    } catch (cause) {
      sendJson(res, error(null, -32603, cause instanceof Error ? cause.message : 'internal error'), 500);
    }
  });
}
