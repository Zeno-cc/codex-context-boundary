# Provider-boundary contract

Codex Responses history can contain opaque provider output items. Their encrypted_content is not a portable summary:

- Native OpenAI/ChatGPT items normally use a Fernet-like gAAAA... envelope.
- Routed providers may emit a different opaque envelope.
- Proxy-minted compaction summaries use the local ocx1: envelope and can be decoded before forwarding.

At a route boundary, preserve the visible conversation semantics, but do not replay opaque reasoning or compaction ciphertext from the previous physical route. This intentionally resets hidden reasoning continuity while retaining user-visible context. The adapter may still rewrite tool schemas, IDs, or other wire details, and formal compaction may summarize or replace history.

The enforcement points are:

1. Native /responses/compact forwarding.
2. Normal Responses passthrough serialization.
3. Route identity binding for provider, destination, adapter, model, and credential.

Routed `agent_message` items need the same treatment: keep visible text parts, but drop nested `encrypted_content` parts before sending them to a third-party provider.

The bundled repair script is deliberately conservative. It uses explicit source anchors, preflights all four files before committing, compiles the affected files, and refuses to mutate an unknown opencodex layout.

## V2 subagent assignments

V2 worker creation has a stricter failure mode than an ordinary historical `agent_message`. The new worker assignment may contain a routing header plus a native ChatGPT `encrypted_content` part, with no readable task text. An external provider cannot decrypt that Fernet payload. Dropping it would leave the worker with an empty assignment, so the proxy must either keep the worker on a native ChatGPT model or recover the assignment before routing.

OpenCodex 2.25.0 provides the opt-in `agentTaskRecovery` path. For an authenticated local Codex request, it sends the exact encrypted worker envelope to the native ChatGPT Codex endpoint with a constrained extraction tool, validates the returned plaintext, replaces the encrypted part with a user message, and only then forwards the task to the routed provider. Recovery is not used for remote/shared proxy admission.

This is separate from multi-agent catalog policy. `ocx v2 mode default` respects model pins: Sol/Terra may use V2, Luna V1, and other models follow the Codex feature flag. Changing the catalog mode can avoid some cross-surface combinations, but it does not make native ciphertext portable.

## Complete repair chain

The health repair intentionally orders operations as follows:

1. Preflight and compile the four source boundary patches without restarting.
2. Back up and remove a root provider automatically only when it points to the current loopback opencodex port. Taking over another local gateway requires the explicit `--adopt-opencodex-route` flag.
3. Run `ocx restore back` so opencodex owns the supported local routing injection.
4. Enable `agentTaskRecovery` when absent.
5. Run `ocx service repair`, then repair the selected Codex runtime and run `ocx sync`.
6. Require `opencodex-local`, `rebootSafe=true`, a viable running service, boundary markers, task recovery, and a ready proxy.

`ocx sync` deliberately omits `--restart-codex`; an in-memory catalog warning is resolved by restarting Codex after active work finishes.
