#!/usr/bin/env node
import { timingSafeEqual } from 'node:crypto';
import { createServer } from 'node:http';
import { lstatSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createChromeRuntimeHostIntegration, executeRuntimeVerifierCommand } from './integration.mjs';

const TOOL_NAME = 'discover_current_entry';
const MAX_BODY_BYTES = 64 * 1024;
const PRODUCTION_CONNECTOR_ID = 'local-agent-bridge-production';
const PRODUCTION_CONTRACT_VERSION = 'lab-production-connector-v1';
const PRODUCTION_WORKSPACE = '/Users/echoman/Documents/ChatGPT/Local-Agent-Bridge';
const PRODUCTION_TUNNEL_PROFILE = 'local-agent-bridge-production';

function normalizeProductionFence(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('production release fence is required');
  const fence = {
    connector_id: String(value.connector_id || '').trim(),
    contract_version: String(value.contract_version || '').trim(),
    release_revision: String(value.release_revision || '').trim(),
    endpoint_generation: Number(value.endpoint_generation),
    workspace: String(value.workspace || '').trim(),
    tunnel_profile_name: String(value.tunnel_profile_name || '').trim()
  };
  if (fence.connector_id !== PRODUCTION_CONNECTOR_ID) throw new Error('production connector identity mismatch');
  if (fence.contract_version !== PRODUCTION_CONTRACT_VERSION) throw new Error('production contract version mismatch');
  if (!/^[0-9a-f]{40}$/.test(fence.release_revision)) throw new Error('production release revision must be a full Git SHA');
  if (!Number.isSafeInteger(fence.endpoint_generation) || fence.endpoint_generation < 1) throw new Error('production endpoint generation must be a positive integer');
  if (fence.workspace !== PRODUCTION_WORKSPACE) throw new Error('production workspace mismatch');
  if (fence.tunnel_profile_name !== PRODUCTION_TUNNEL_PROFILE) throw new Error('production tunnel profile mismatch');
  return Object.freeze(fence);
}

export function validateProductionReleaseFence(manifest, observed) {
  const fence = normalizeProductionFence(observed);
  if (!manifest || typeof manifest !== 'object' || Array.isArray(manifest) || manifest.fail_closed !== true) {
    throw new Error('production release manifest must be fail-closed');
  }
  for (const field of ['connector_id', 'contract_version', 'release_revision', 'endpoint_generation']) {
    if (manifest[field] !== fence[field]) throw new Error(`production release manifest ${field} mismatch`);
  }
  if (manifest.workspace !== fence.workspace) throw new Error('production release manifest workspace mismatch');
  if (manifest.tunnel?.profile_name !== fence.tunnel_profile_name) throw new Error('production release manifest tunnel profile mismatch');
  const host = manifest.host;
  if (!host || typeof host !== 'object' || Array.isArray(host)) throw new Error('production Host manifest section is required');
  for (const field of ['contract_version', 'release_revision', 'endpoint_generation']) {
    if (host[field] !== fence[field]) throw new Error(`production Host ${field} mismatch`);
  }
  return fence;
}

export function productionReleaseFenceFromEnvironment(env = process.env) {
  const manifestPath = String(env.LAB_PRODUCTION_RELEASE_MANIFEST_FILE || '').trim();
  if (!manifestPath) throw new Error('LAB_PRODUCTION_RELEASE_MANIFEST_FILE is required');
  let manifest;
  try {
    manifest = JSON.parse(readPrivateText(manifestPath, 2).toString('utf8'));
  } catch (error) {
    throw new Error(`production release manifest could not be loaded: ${String(error?.message || error)}`);
  }
  return validateProductionReleaseFence(manifest, {
    connector_id: env.LAB_PRODUCTION_CONNECTOR_ID,
    contract_version: env.LAB_PRODUCTION_CONTRACT_VERSION,
    release_revision: env.LAB_PRODUCTION_RELEASE_REVISION,
    endpoint_generation: Number(env.LAB_PRODUCTION_ENDPOINT_GENERATION),
    workspace: env.LAB_PRODUCTION_WORKSPACE,
    tunnel_profile_name: env.LAB_PRODUCTION_TUNNEL_PROFILE_NAME
  });
}

function toolDefinition(productionFence) {
  return Object.freeze({
    name: TOOL_NAME,
    description: 'Return machine-verified identity for the current ChatGPT Web invocation. Caller-supplied conversation identity is not accepted.',
    inputSchema: {
      type: 'object',
      properties: {
        target_generation: {type: 'integer', minimum: 1},
        ownership_generation: {type: 'integer', minimum: 1}
      },
      required: ['target_generation', 'ownership_generation'],
      additionalProperties: false
    },
    annotations: {
      readOnlyHint: true,
      openWorldHint: false,
      destructiveHint: false,
      idempotentHint: false
    },
    _meta: {'local-agent-bridge/production-fence': productionFence}
  });
}

function json(status, value) {
  return {status, body: JSON.stringify(value)};
}

function safeEqualText(left, right) {
  const a = Buffer.from(String(left || ''), 'utf8');
  const b = Buffer.from(String(right || ''), 'utf8');
  return a.length === b.length && a.length > 0 && timingSafeEqual(a, b);
}

function positiveInteger(value, name) {
  if (!Number.isSafeInteger(value) || value < 1) throw new Error(`${name} must be a positive integer`);
  return value;
}

function parseBody(buffer) {
  if (!Buffer.isBuffer(buffer) || buffer.length === 0 || buffer.length > MAX_BODY_BYTES) {
    throw new Error('MCP request body is missing or exceeds limit');
  }
  return JSON.parse(buffer.toString('utf8'));
}

function validateDiscoverArguments(args) {
  if (!args || typeof args !== 'object' || Array.isArray(args)) throw new Error('discover_current_entry arguments must be an object');
  const keys = Object.keys(args).sort();
  if (keys.includes('conversation_id')) throw new Error('discover_current_entry does not accept conversation_id');
  const allowed = new Set(['target_generation', 'ownership_generation']);
  const extra = keys.filter(key => !allowed.has(key));
  if (extra.length) throw new Error(`discover_current_entry additional arguments are not allowed: ${extra.join(',')}`);
  return {
    target_generation: positiveInteger(args.target_generation, 'target_generation'),
    ownership_generation: positiveInteger(args.ownership_generation, 'ownership_generation')
  };
}

export function createHostMcpHandler(options) {
  const backendKey = String(options?.backendKey || '');
  const execute = options?.execute;
  const productionFence = normalizeProductionFence(options?.productionFence);
  const tool = toolDefinition(productionFence);
  if (Buffer.byteLength(backendKey) < 32) throw new Error('Host MCP backend key must contain at least 32 bytes');
  if (typeof execute !== 'function') throw new Error('Host MCP execute function is required');

  return async function handle(request) {
    const headers = request?.headers || {};
    const suppliedKey = headers['x-lab-backend-key'] || headers['X-Lab-Backend-Key'];
    if (!safeEqualText(suppliedKey, backendKey)) {
      return json(401, {error: {code: -32001, message: 'Host MCP backend authentication failed'}});
    }

    let payload;
    try {
      payload = parseBody(request?.body);
    } catch (error) {
      return json(400, {error: {code: -32700, message: String(error?.message || error)}});
    }
    const id = payload?.id ?? null;
    const method = String(payload?.method || '');
    if (headers['mcp-method'] && headers['mcp-method'] !== method) {
      return json(400, {jsonrpc: '2.0', id, error: {code: -32600, message: 'Mcp-Method does not match body method'}});
    }

    if (method === 'tools/list') {
      return json(200, {jsonrpc: '2.0', id, result: {tools: [tool]}});
    }
    if (method !== 'tools/call' || payload?.params?.name !== TOOL_NAME) {
      return json(404, {jsonrpc: '2.0', id, error: {code: -32601, message: 'Host MCP method/tool not found'}});
    }
    if (headers['mcp-name'] && headers['mcp-name'] !== TOOL_NAME) {
      return json(400, {jsonrpc: '2.0', id, error: {code: -32600, message: 'Mcp-Name does not match body tool'}});
    }

    let args;
    try {
      args = validateDiscoverArguments(payload?.params?.arguments || {});
    } catch (error) {
      return json(400, {jsonrpc: '2.0', id, error: {code: -32602, message: String(error?.message || error)}});
    }
    try {
      const verified = await execute({operation: TOOL_NAME, ...args});
      if (!verified?.ok || verified?.operation !== TOOL_NAME || !verified?.current_entry) {
        throw new Error('Host verifier did not return verified current-entry evidence');
      }
      const current = verified.current_entry;
      if (current.provenance !== 'runtime_host_current_entry_v1' ||
          current.entry_scope !== 'runtime_invocation' ||
          current.machine_source !== 'host_invocation_context_v1' ||
          current.secure_origin !== 'https://chatgpt.com') {
        throw new Error('Host verifier returned untrusted current-entry provenance');
      }
      const structuredContent = {current_entry: current};
      return json(200, {
        jsonrpc: '2.0',
        id,
        result: {
          content: [{type: 'text', text: JSON.stringify(structuredContent)}],
          structuredContent,
          isError: false,
          _meta: {'local-agent-bridge/production-fence': productionFence}
        }
      });
    } catch (error) {
      // A Host identity failure is a tool-level error, not a transport outage.
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
      });
    }
  };
}

function readPrivateText(path, minimumBytes) {
  const stat = lstatSync(path);
  if (!stat.isFile() || stat.isSymbolicLink()) throw new Error(`private file must be regular and non-symlink: ${path}`);
  if (typeof process.getuid === 'function' && stat.uid !== process.getuid()) throw new Error(`private file owner mismatch: ${path}`);
  if ((stat.mode & 0o077) !== 0) throw new Error(`private file permissions must be 0600 or stricter: ${path}`);
  // Immutable production releases can be owner-readable but chmod-denied.
  // The lstat already enforces a safe private mode; do not mutate the file
  // just to read it. On some hardened installations chmod throws EPERM.
  const value = readFileSync(path);
  if (value.length < minimumBytes) throw new Error(`private file is too short: ${path}`);
  return value;
}

async function readRequestBody(req) {
  let body = Buffer.alloc(0);
  for await (const chunk of req) {
    body = Buffer.concat([body, chunk]);
    if (body.length > MAX_BODY_BYTES) throw new Error('MCP request exceeds limit');
  }
  return body;
}

async function runServer() {
  const root = join(homedir(), '.local-agent-bridge', 'web-host');
  const productionFence = productionReleaseFenceFromEnvironment();
  const backendKeyFile = process.env.LAB_HOST_MCP_BACKEND_KEY_FILE;
  if (!backendKeyFile) throw new Error('LAB_HOST_MCP_BACKEND_KEY_FILE is required');
  const backendKey = readPrivateText(backendKeyFile, 32).toString('utf8').trim();
  const attestationKey = readPrivateText(process.env.LAB_HOST_ATTESTATION_KEY_FILE || join(root, 'attestation.key'), 32);
  const integration = createChromeRuntimeHostIntegration({
    secret: attestationKey,
    socketPath: process.env.LAB_HOST_ADAPTER_SOCKET || join(root, 'host.sock'),
    verifierStatePath: process.env.LAB_HOST_VERIFIER_STATE_FILE || join(root, 'runtime-verifier-state.json'),
    reentryStatePath: process.env.LAB_HOST_REENTRY_STATE_FILE || join(root, 'runtime-reentry-state.json')
  });
  const handle = createHostMcpHandler({
    backendKey,
    productionFence,
    execute: input => executeRuntimeVerifierCommand(input, {integration})
  });
  const host = process.env.LAB_HOST_MCP_BIND || '127.0.0.1';
  if (host !== '127.0.0.1') throw new Error('Host MCP adapter must bind numeric loopback 127.0.0.1');
  const port = Number(process.env.LAB_HOST_MCP_PORT || '33321');
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('LAB_HOST_MCP_PORT must be a valid port');

  const server = createServer(async (req, res) => {
    if (req.method !== 'POST' || req.url !== '/mcp') {
      res.writeHead(404, {'content-type': 'application/json'});
      res.end(JSON.stringify({error: 'not found'}));
      return;
    }
    try {
      const body = await readRequestBody(req);
      const headers = Object.fromEntries(Object.entries(req.headers).map(([k, v]) => [k, Array.isArray(v) ? v[0] : v || '']));
      const result = await handle({headers, body});
      res.writeHead(result.status, {'content-type': 'application/json'});
      res.end(result.body);
    } catch (error) {
      res.writeHead(400, {'content-type': 'application/json'});
      res.end(JSON.stringify({error: {message: String(error?.message || error)}}));
    }
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, host, resolve);
  });
  process.stderr.write(`lab-host-mcp ready http://${host}:${port}/mcp\n`);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  runServer().catch(error => {
    process.stderr.write(String(error?.message || error) + '\n');
    process.exit(1);
  });
}
