import test from 'node:test';
import assert from 'node:assert/strict';
import { executeRuntimeVerifierCommand } from '../experiments/web-host-attestation/chrome-host/integration.mjs';

test('canonical Host supports discover_current_entry without caller conversation identity', async () => {
  const calls = [];
  const result = await executeRuntimeVerifierCommand({
    operation: 'discover_current_entry',
    target_generation: 8,
    ownership_generation: 8
  }, {
    integration: {
      async discoverCurrentEntry(input) {
        calls.push(input);
        return {
          provenance: 'runtime_host_current_entry_v1',
          entry_scope: 'runtime_invocation',
          machine_source: 'host_invocation_context_v1',
          conversation_id: '6aa0fff1-288c-83ea-bae8-d605463cb75b',
          browser_target_id: 'browser-current',
          top_frame_id: 'frame-current',
          loader_id: 'loader-current',
          secure_origin: 'https://chatgpt.com',
          target_generation: 8,
          ownership_generation: 8,
          host_receipt_id: 'hce_test',
          observed_at_unix_ms: 1800000000200
        };
      }
    }
  });
  assert.deepEqual(calls, [{ target_generation: 8, ownership_generation: 8 }]);
  assert.equal(result.ok, true);
  assert.equal(result.operation, 'discover_current_entry');
  assert.equal(result.current_entry.secure_origin, 'https://chatgpt.com');

  await assert.rejects(
    executeRuntimeVerifierCommand({
      operation: 'discover_current_entry',
      conversation_id: 'caller-controlled',
      target_generation: 8,
      ownership_generation: 8
    }, { integration: { async discoverCurrentEntry() { return {}; } } }),
    /caller.*conversation|conversation.*not.*accepted/i
  );
});
