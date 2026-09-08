# Chrome Web Host driver candidate

This directory is the product-side browser machine-boundary candidate for LAB-HOST-01.
It is separate from Local Tool Bridge, Computer Runtime, Web ChatGPT Executor, and
Adaptive Agent Runtime governance.

## Trust boundary

The extension uses chrome.debugger to bind the exact Chrome page target to
Page.getFrameTree. Strong identity input is limited to the browser target id,
top-frame id, loader/document id, https://chatgpt.com security origin, stable
/c/<conversation-id> route, and a fresh machine timestamp.

A target URL is used only to locate a candidate conversation. It is not accepted
as the attestation by itself: the extension attaches the debugger, checks the CDP
target identity, reads the top frame, and requires the frame route to agree.

DOM text, page title, screenshots, prompt claims, x-openai-session, and
x-openai-subject are correlation-only and are absent from the machine observation
contract.

The HMAC attestation key stays in the native Host process. It is never sent to
the extension or page.

## Local process boundary

The Chrome Native Messaging process also exposes a 0600 Unix socket for the
local Runtime/provider client.

Defaults:

- key: ~/.local-agent-bridge/web-host/attestation.key
- socket: ~/.local-agent-bridge/web-host/host.sock

The state directory is forced to 0700. The key must be a current-user-owned
non-symlink regular file with no group/other permissions and at least 32 bytes.
The socket is created with mode 0600. This is a same-user local boundary and does
not claim hostile same-UID isolation.

Install a Chrome Native Messaging manifest based on native-host-manifest.example.json,
using the absolute path to native-host.mjs and the exact installed extension id.
Load extension/ as an unpacked MV3 extension.

## Current capability

Implemented:

- exact conversation target discovery with ambiguity rejection;
- exact target re-observation;
- Chrome debugger target + top-frame + loader + origin machine binding;
- local Host-signed Runtime challenge attestation;
- exact target focus followed by fresh machine re-observation;
- bounded Native Messaging and Runtime socket protocols.

Implemented and Live-validated on the dedicated authenticated Personal Web profile:

- provider-specific submit_continuation into the exact ChatGPT Web target.

The submit path rechecks the exact CDP target/conversation immediately before
page action, refuses to overwrite a non-empty user composer, injects only into
the already-verified tab, and treats any post-click uncertainty as
dispatch_attempted=true / submit_confirmed=false / retryable=false. DOM state is
used only for delivery action and confirmation, never as Host identity evidence.

The native Host health surface starts conservatively with `submit_live_validated=false` after process start and flips to true only after that Host process observes a real `submit_confirmed=true` result. Result-unknown or pre-dispatch failure never upgrades health. A Host restart resets this runtime validation state to false, which is intentionally fail-closed.

The dedicated authenticated Personal Web profile has now produced a real confirmed submit plus model response, but this still does not by itself close LAB-HOST-01 or unblock LAB-GOV-02: Runtime canonical ownership/generation must be reconciled and the final SAME-Controller continuation must run through the canonical Runtime lifecycle.

AI-Bridge is not a product runtime dependency of this candidate.

## Tests

Run:

    node --test attestation.test.mjs chrome-host/*.test.mjs

Real browser validation additionally requires the unpacked extension and its
Native Messaging manifest to be installed in Chrome.
