import { createHash, randomUUID } from 'node:crypto';
import { appendFile, mkdir } from 'node:fs/promises';
import { createServer } from 'node:http';
import { dirname } from 'node:path';

const listenPort = Number(process.env.PROXY_PORT || 33301);
const upstreamBase = process.env.UPSTREAM_BASE || 'http://127.0.0.1:33300';
const logPath = process.env.OBSERVATION_LOG || new URL('./logs/identity-observations.jsonl', import.meta.url).pathname;

function sha(value) {
  return createHash('sha256').update(String(value)).digest('hex').slice(0, 16);
}

function fingerprint(value) {
  const raw = String(value);
  return { present: true, valueLength: raw.length, valueSha256Prefix: sha(raw) };
}

function sanitizeHeaders(headers) {
  const safe = {};
  const observed = {};
  const safeNames = new Set(['content-type', 'mcp-protocol-version', 'mcp-method', 'mcp-name', 'user-agent']);
  for (const [name, value] of Object.entries(headers)) {
    if (value == null) continue;
    const lower = name.toLowerCase();
    if (safeNames.has(lower)) {
      safe[lower] = String(value);
      continue;
    }
    if (lower === 'authorization') {
      const raw = String(value);
      const i = raw.indexOf(' ');
      observed.authorization = {
        scheme: i < 0 ? raw : raw.slice(0, i),
        credential: fingerprint(i < 0 ? '' : raw.slice(i + 1)),
      };
      continue;
    }
    if (lower === 'cookie' || lower === 'proxy-authorization' || lower === 'x-api-key') {
      observed[lower] = fingerprint(value);
      continue;
    }
    if (
      lower.startsWith('x-openai-') || lower.startsWith('openai-') ||
      lower.includes('conversation') || lower.includes('session') ||
      lower.includes('thread') || lower.includes('request') || lower.includes('trace') ||
      lower.includes('client')
    ) observed[lower] = fingerprint(value);
  }
  return { safe, observed };
}

function sanitizeMeta(bodyText) {
  try {
    const body = JSON.parse(bodyText);
    const meta = body?.params?._meta;
    if (!meta || typeof meta !== 'object' || Array.isArray(meta)) return {};
    const out = {};
    for (const [key, value] of Object.entries(meta)) {
      if (value == null) continue;
      if (['string', 'number', 'boolean'].includes(typeof value)) out[key] = fingerprint(value);
      else out[key] = { present: true, type: Array.isArray(value) ? 'array' : typeof value };
    }
    return out;
  } catch {
    return {};
  }
}

async function logObservation(entry) {
  await mkdir(dirname(logPath), { recursive: true });
  await appendFile(logPath, JSON.stringify(entry) + '\n', { encoding: 'utf8', mode: 0o600 });
}

createServer(async (req, res) => {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const body = Buffer.concat(chunks);
  const text = body.toString('utf8');
  const observation = {
    observationId: randomUUID(),
    observedAt: new Date().toISOString(),
    method: req.method,
    path: req.url,
    headers: sanitizeHeaders(req.headers),
    meta: sanitizeMeta(text),
    bodyLength: body.length,
  };
  await logObservation(observation);

  try {
    const upstream = await fetch(upstreamBase + req.url, {
      method: req.method,
      headers: req.headers,
      body: ['GET', 'HEAD'].includes(req.method || '') ? undefined : body,
      redirect: 'manual',
    });
    const responseBody = Buffer.from(await upstream.arrayBuffer());
    const headers = {};
    upstream.headers.forEach((value, key) => {
      if (!['content-length', 'transfer-encoding', 'connection'].includes(key.toLowerCase())) headers[key] = value;
    });
    res.writeHead(upstream.status, headers);
    res.end(responseBody);
  } catch (error) {
    res.writeHead(502, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ error: 'upstream_unavailable', detail: String(error?.message || error) }));
  }
}).listen(listenPort, '127.0.0.1', () => {
  console.log(`identity proxy listening on http://127.0.0.1:${listenPort}`);
});
