import { createHash, randomUUID } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { dirname, resolve } from 'node:path';
import { spawn } from 'node:child_process';

const protocolVersion = '2026-07-28';
const workspace = resolve(process.env.POC_WORKSPACE || 'experiments/web-local-poc/web-safe-workspace');
const sourceFile = resolve(workspace, 'read-source.txt');
const targetFile = resolve(workspace, 'write-target.txt');
const logPath = process.env.OBSERVATION_LOG || resolve('experiments/web-local-poc/logs/safe-poc-observations.jsonl');
const expectedConversationId = process.env.EXPECTED_CONVERSATION_ID || '';
const jobs = new Map();

const tools = [
  { name: 'poc_read', description: 'Read the fixed PoC source file inside the isolated workspace.', inputSchema: { type: 'object', properties: {}, additionalProperties: false }, annotations: { readOnlyHint: true, openWorldHint: false, destructiveHint: false } },
  { name: 'poc_write', description: 'Write a fixed marker to the fixed PoC target file inside the isolated workspace.', inputSchema: { type: 'object', properties: {}, additionalProperties: false }, annotations: { readOnlyHint: false, openWorldHint: false, destructiveHint: false } },
  { name: 'poc_edit', description: 'Edit the fixed PoC target file by replacing the write marker with an edited marker.', inputSchema: { type: 'object', properties: {}, additionalProperties: false }, annotations: { readOnlyHint: false, openWorldHint: false, destructiveHint: false } },
  { name: 'poc_command', description: 'Run a fixed harmless local command in the isolated workspace.', inputSchema: { type: 'object', properties: {}, additionalProperties: false }, annotations: { readOnlyHint: true, openWorldHint: false, destructiveHint: false } },
  { name: 'poc_start_job', description: 'Start a fixed short background Node job and return a job id.', inputSchema: { type: 'object', properties: {}, additionalProperties: false }, annotations: { readOnlyHint: false, openWorldHint: false, destructiveHint: false } },
  { name: 'poc_poll_job', description: 'Poll a job previously started by poc_start_job.', inputSchema: { type: 'object', properties: { jobId: { type: 'string' } }, required: ['jobId'], additionalProperties: false }, annotations: { readOnlyHint: true, openWorldHint: false, destructiveHint: false } },
].map(t => ({ ...t, securitySchemes: [{ type: 'noauth' }] }));

function hashPrefix(value) { return createHash('sha256').update(String(value)).digest('hex').slice(0, 16); }
function fingerprint(value) { const raw = String(value); return { present: true, valueLength: raw.length, valueSha256Prefix: hashPrefix(raw), matchesExpectedConversationId: Boolean(expectedConversationId) && raw === expectedConversationId }; }
function sanitizeHeaders(headers) {
  const safe = {};
  const identity = {};
  const safeNames = new Set(['content-type', 'mcp-protocol-version', 'mcp-method', 'mcp-name', 'user-agent']);
  for (const [name, value] of Object.entries(headers)) {
    if (value == null) continue;
    const lower = name.toLowerCase();
    if (safeNames.has(lower)) safe[lower] = String(value);
    else if (lower.startsWith('x-openai-') || lower.includes('conversation') || lower.includes('session') || lower.includes('thread') || lower.includes('request-id') || lower.includes('trace')) identity[lower] = fingerprint(value);
  }
  return { safe, identity };
}
function sanitizeMeta(meta) {
  const out = {};
  for (const [key, value] of Object.entries(meta ?? {})) {
    if (value == null) continue;
    if (['string', 'number', 'boolean'].includes(typeof value)) out[key] = fingerprint(value);
    else out[key] = { present: true, type: Array.isArray(value) ? 'array' : typeof value };
  }
  return out;
}
async function appendObservation(req, rpc) {
  const observation = { observationId: randomUUID(), observedAt: new Date().toISOString(), method: req.method, path: req.url, headers: sanitizeHeaders(req.headers), meta: sanitizeMeta(rpc?.params?._meta), rpcMethod: rpc?.method, toolName: rpc?.params?.name ?? null };
  await mkdir(dirname(logPath), { recursive: true });
  await writeFile(logPath, JSON.stringify(observation) + '\n', { encoding: 'utf8', flag: 'a', mode: 0o600 });
}
function result(id, value) { return { jsonrpc: '2.0', id: id ?? null, result: value }; }
function error(id, code, message) { return { jsonrpc: '2.0', id: id ?? null, error: { code, message } }; }
function send(res, value, status = 200) { const body = JSON.stringify(value); res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store', 'content-length': Buffer.byteLength(body) }); res.end(body); }
async function readBody(req) { const chunks = []; for await (const c of req) chunks.push(c); return JSON.parse(Buffer.concat(chunks).toString('utf8')); }
function toolResult(structuredContent) { return { content: [{ type: 'text', text: JSON.stringify(structuredContent) }], structuredContent, isError: false }; }

async function callTool(name, args = {}) {
  await mkdir(workspace, { recursive: true });
  if (name === 'poc_read') return toolResult({ path: 'read-source.txt', text: await readFile(sourceFile, 'utf8') });
  if (name === 'poc_write') { await writeFile(targetFile, 'written-by-chatgpt-web-poc\n', 'utf8'); return toolResult({ path: 'write-target.txt', text: await readFile(targetFile, 'utf8') }); }
  if (name === 'poc_edit') { const before = await readFile(targetFile, 'utf8'); const after = before.replace('written-by-chatgpt-web-poc', 'edited-by-chatgpt-web-poc'); await writeFile(targetFile, after, 'utf8'); return toolResult({ path: 'write-target.txt', before, after }); }
  if (name === 'poc_command') {
    return await new Promise((resolvePromise, reject) => {
      const child = spawn('/bin/pwd', [], { cwd: workspace, stdio: ['ignore', 'pipe', 'pipe'] });
      let stdout = '', stderr = '';
      child.stdout.on('data', d => stdout += d);
      child.stderr.on('data', d => stderr += d);
      child.once('error', reject);
      child.once('close', code => resolvePromise(toolResult({ command: '/bin/pwd', code, stdout, stderr })));
    });
  }
  if (name === 'poc_start_job') {
    const jobId = randomUUID();
    const child = spawn(process.execPath, ['-e', "setTimeout(()=>{process.stdout.write('job-done\\n')},2200)"], { cwd: workspace, stdio: ['ignore', 'pipe', 'pipe'] });
    const job = { jobId, pid: child.pid, state: 'running', stdout: '', stderr: '', code: null };
    jobs.set(jobId, job);
    child.stdout.on('data', d => job.stdout += d);
    child.stderr.on('data', d => job.stderr += d);
    child.on('close', code => { job.state = 'finished'; job.code = code; });
    return toolResult({ jobId, state: job.state, pid: job.pid });
  }
  if (name === 'poc_poll_job') {
    const job = jobs.get(args.jobId);
    if (!job) throw new Error('unknown jobId');
    return toolResult({ jobId: job.jobId, state: job.state, pid: job.pid, code: job.code, stdout: job.stdout, stderr: job.stderr });
  }
  throw new Error('unknown tool');
}

export function createSafePocServer() {
  return createServer(async (req, res) => {
    try {
      const url = new URL(req.url ?? '/', 'http://127.0.0.1');
      if (url.pathname !== '/mcp') { res.writeHead(404).end(); return; }
      if (req.method === 'DELETE') { res.writeHead(204).end(); return; }
      if (req.method !== 'POST') { res.writeHead(405, { allow: 'POST, DELETE' }).end(); return; }
      let rpc;
      try { rpc = await readBody(req); } catch { send(res, error(null, -32700, 'invalid JSON')); return; }
      await appendObservation(req, rpc);
      const { id = null, method, params } = rpc ?? {};
      if (!params || typeof params !== 'object' || !params._meta || typeof params._meta !== 'object') { send(res, error(id, -32602, 'params._meta is required')); return; }
      if (req.headers['mcp-method'] !== method) { send(res, error(id, -32602, 'mcp-method header mismatch')); return; }
      if (method === 'server/discover') { send(res, result(id, { protocolVersion, serverInfo: { name: 'local-bridge-safe-poc', version: '0.1.0' }, securitySchemes: [{ type: 'noauth' }] })); return; }
      if (method === 'tools/list') { send(res, result(id, { tools })); return; }
      if (method === 'ping') { send(res, result(id, {})); return; }
      if (method !== 'tools/call') { send(res, error(id, -32601, 'method not found')); return; }
      if (req.headers['mcp-name'] !== params.name) { send(res, error(id, -32602, 'tool name mismatch')); return; }
      const declared = tools.find(t => t.name === params.name);
      if (!declared) { send(res, error(id, -32601, 'unknown tool')); return; }
      send(res, result(id, await callTool(params.name, params.arguments ?? {})));
    } catch (cause) { send(res, error(null, -32603, cause instanceof Error ? cause.message : 'internal error'), 500); }
  });
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const port = Number(process.env.PORT || 33303);
  await mkdir(workspace, { recursive: true });
  try { await readFile(sourceFile, 'utf8'); } catch { await writeFile(sourceFile, 'hello-from-local-bridge-safe-poc\n', 'utf8'); }
  createSafePocServer().listen(port, '127.0.0.1', () => console.log(`safe-poc listening ${port}`));
}
