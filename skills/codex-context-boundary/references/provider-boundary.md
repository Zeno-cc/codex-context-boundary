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
