import { createHash, randomUUID } from 'node:crypto';
import { appendFile, mkdir } from 'node:fs/promises';
import { dirname } from 'node:path';
import { createServer } from 'node:http';

const protocolVersion = '2026-07-28';
const tool = {
  name: 'identity_probe',
  description: 'Return a sanitized observation of request identity signals. This does not authenticate a ChatGPT user or conversation.',
  inputSchema: { type: 'object', properties: {}, additionalProperties: false },
  annotations: { readOnlyHint: true, openWorldHint: false, destructiveHint: false },
  securitySchemes: [{ type: 'noauth' }],
};

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
      lower.includes('conversation') ||
      lower.includes('session') ||
      lower.includes('thread') ||
      lower.includes('request-id') ||
      lower.includes('trace')
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

      const { id = null, method, params } = rpc ?? {};
      if (!params || typeof params !== 'object' || !params._meta || typeof params._meta !== 'object') {
        sendJson(res, error(id, -32602, 'params._meta is required'));
        return;
      }
      if (req.headers['mcp-method'] !== method) {
        sendJson(res, error(id, -32602, 'mcp-method header mismatch'));
        return;
      }

      if (method === 'server/discover') {
        sendJson(res, result(id, {
          protocolVersion,
          serverInfo: { name: 'identity-probe', version: '0.1.0' },
          securitySchemes: [{ type: 'noauth' }],
        }));
        return;
      }

      if (method === 'tools/list') {
        sendJson(res, result(id, { tools: [tool] }));
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

      if (params.name !== 'identity_probe' || req.headers['mcp-name'] !== params.name) {
        sendJson(res, error(id, -32602, 'tool name mismatch'));
        return;
      }

      const observation = {
        observationId: randomUUID(),
        probeSessionId,
        observedAt: new Date().toISOString(),
        oauthVerified: false,
        headers: sanitizeHeaders(req.headers, expectedConversationId),
        meta: sanitizeMeta(params._meta, expectedConversationId),
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
