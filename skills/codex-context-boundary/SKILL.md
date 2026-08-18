---
name: codex-context-boundary
description: Maintain and repair Codex/opencodex provider-boundary handling for encrypted reasoning, compaction state, and Codex subagent messages sent to routed providers. Use when Codex reports invalid_encrypted_content, encrypted content could not be decrypted or parsed, a third-party model reconnects or returns 502 after a subagent reply, context compaction/upstream request failures after a model switch, or when OpenAI, Grok, CII, or another provider is switched inside one task; also use after upgrading or reinstalling opencodex.
---

# Codex Context Boundary

Keep provider-specific hidden reasoning and compaction state from crossing provider, model, adapter, destination, or credential boundaries.

## Explicit command

Use `$codex-context-boundary` in Codex when you need diagnosis or explanation around the repair. If `prompts/context-boundary-repair.md` has also been installed under `$CODEX_HOME/prompts`, the one-shot entry point is `/prompts:context-boundary-repair`.

## Core contract

- Treat reasoning.encrypted_content and foreign encrypted compaction items as opaque, provider-scoped replay state, never as portable conversation text. The local `ocx1:` compaction envelope is a decodable exception.
- On a physical route boundary, remove old opaque reasoning and foreign compaction items when the proxy still has the previous route binding; route bindings are bounded by a one-hour TTL and a 1024-entry cap.
- Convert Codex Desktop `agent_message` items to standard user messages before forwarding to a third-party routed provider, dropping nested `encrypted_content` parts; preserve the native OpenAI path unchanged.
- Do not proactively delete user messages, visible assistant messages, developer instructions, or tool results at the boundary; adapters may rewrite tool schemas, IDs, or custom-tool wire shape, and formal compaction may summarize or replace history.
- Do not edit Codex session JSONL files or delete continuation state as a first-line fix.
- Expect hidden reasoning continuity to restart after a provider/model switch. Visible continuity depends on the client sending full history or the proxy successfully expanding continuation state; that state has TTL and capacity limits.

## Workflow

1. Locate the local opencodex installation. Do not guess a package path when an explicit --source-root is available.
2. Run the bundled checker before mutating anything:

   ~~~sh
   python3 "${CODEX_HOME:-$HOME/.codex}/skills/codex-context-boundary/scripts/ensure_provider_boundary.py" --check
   ~~~

3. If markers are missing, or an opencodex upgrade replaced the patch, run the idempotent repair:

   ~~~sh
   python3 "${CODEX_HOME:-$HOME/.codex}/skills/codex-context-boundary/scripts/ensure_provider_boundary.py" --repair
   ~~~

   The patch-writing phase edits only four explicit opencodex source files. It preflights all anchors in memory before committing any file, compiles the affected Bun entry points, then restarts the matching ocx installation and requires a healthy JSON response. It refuses ambiguous anchors and stops before restarting if the installed version is structurally incompatible.

4. If a source-root override is required, use:

   ~~~sh
   python3 "${CODEX_HOME:-$HOME/.codex}/skills/codex-context-boundary/scripts/ensure_provider_boundary.py" --repair --source-root /absolute/path/to/opencodex
   ~~~

   The source-root override must be the same installation that owns the ocx process; the repair script prefers `<source-root>/bin/ocx.mjs` and will not silently restart a different global installation.

5. After repair, inspect the target task status and recent proxy log entries. A new invalid_encrypted_content, or a 502 that begins immediately after a subagent reply, indicates an uncovered request path and requires source-level investigation; do not repeatedly restart.
6. Classify remaining 502, 503, capacity, timeout, or connection errors separately. They are upstream availability failures, not proof that the encryption boundary fix failed.
7. Report whether the repair was already installed or applied. The script reports errors as `ok: false` with an error message; `blocked` is a workflow classification, not a script status. Include the source root, build result, proxy health result, and any remaining independent upstream errors.

## Safety boundaries

- Never print API keys, OAuth tokens, cookies, encrypted payloads, or full session transcripts.
- Never rewrite a session's history just to hide an error.
- Never apply a patch when an anchor is missing or duplicated; report the installed version and ask for a compatible patch.
- Do not claim that a Skill intercepts every desktop model switch. The proxy adapter is the enforcement point; this Skill is the self-healing/checking workflow invoked before or after a reported switch, compaction failure, or opencodex upgrade.
- After an opencodex package upgrade, run --check again. If it reports missing markers, run --repair before continuing long tasks.
