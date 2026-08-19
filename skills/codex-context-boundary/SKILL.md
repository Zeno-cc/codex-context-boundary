---
name: codex-context-boundary
description: Diagnose and repair the complete Codex/opencodex context-boundary health chain, including invalid_encrypted_content, unreadable_encrypted_agent_task, encrypted V2 subagent delivery, provider/model switches, compaction failures, conflicting model_provider routing, reboot protection, background service, Codex runtime, and catalog sync. Use after opencodex upgrades or reinstalls, when switching OpenAI/Grok/CII models inside one task, when routed subagents cannot decrypt their assignment, or when Codex reports that routed models are unsupported with a ChatGPT account.
---

# Codex Context Boundary

Keep provider-specific hidden reasoning and compaction state from crossing provider, model, adapter, destination, or credential boundaries.

## Entry points

Use `$codex-context-boundary` for automatic diagnosis when the description matches. If `prompts/context-boundary-repair.md` is installed under `$CODEX_HOME/prompts`, use `/prompts:context-boundary-repair` for an explicit one-shot repair.

## Core contract

- Treat reasoning.encrypted_content and foreign encrypted compaction items as opaque, provider-scoped replay state, never as portable conversation text. The local `ocx1:` compaction envelope is a decodable exception.
- On a physical route boundary, remove old opaque reasoning and foreign compaction items when the proxy still has the previous route binding; route bindings are bounded by a one-hour TTL and a 1024-entry cap.
- Convert Codex Desktop `agent_message` items to standard user messages before forwarding to a third-party routed provider, dropping nested `encrypted_content` parts; preserve the native OpenAI path unchanged.
- For a V2 worker assignment whose entire payload is native ChatGPT ciphertext, use opencodex `agentTaskRecovery` to recover the plaintext through the authenticated native endpoint before routing it. Merely dropping the ciphertext produces an empty task and is not a repair.
- Do not proactively delete user messages, visible assistant messages, developer instructions, or tool results at the boundary; adapters may rewrite tool schemas, IDs, or custom-tool wire shape, and formal compaction may summarize or replace history.
- Do not edit Codex session JSONL files or delete continuation state as a first-line fix.
- Expect hidden reasoning continuity to restart after a provider/model switch. Visible continuity depends on the client sending full history or the proxy successfully expanding continuation state; that state has TTL and capacity limits.

## Workflow

1. Run the complete read-only checker before mutating anything:

   ~~~sh
   python3 "${CODEX_HOME:-$HOME/.codex}/skills/codex-context-boundary/scripts/repair_opencodex_health.py" --check
   ~~~

2. If any managed health item fails, run the idempotent repair:

   ~~~sh
   python3 "${CODEX_HOME:-$HOME/.codex}/skills/codex-context-boundary/scripts/repair_opencodex_health.py" --repair --adopt-opencodex-route
   ~~~

   This checks or repairs, in order: source boundary markers, conflicting local route ownership, opencodex routing injection, encrypted V2 task recovery, launchd/service protection, Codex runtime, model catalog sync, and final proxy readiness. `--adopt-opencodex-route` is the explicit authorization to back up and remove a root custom-local provider selector. It does not restart Codex Desktop, because that can interrupt active turns.

3. If a source-root override is required, use it with the complete repair:

   ~~~sh
   python3 "${CODEX_HOME:-$HOME/.codex}/skills/codex-context-boundary/scripts/repair_opencodex_health.py" --repair --adopt-opencodex-route --source-root /absolute/path/to/opencodex
   ~~~

   The source repair still validates exact anchors and compiles affected Bun entry points. The health repair invokes the `ocx` executable on PATH for supported lifecycle operations.

4. If a running Codex process still has a stale in-memory catalog, tell the user to restart Codex after active turns finish. Never silently use `ocx sync --restart-codex`.
5. After repair, retry a fresh subagent spawn. Existing failed child tasks are not retroactively decrypted; spawn a replacement child.
6. Classify remaining 502, 503, capacity, timeout, or connection errors separately. They are upstream availability failures, not proof that the encryption boundary fix failed.
7. Report `failures`, `actions`, routing/reboot state, runtime version, `agent_task_recovery`, and proxy readiness from the JSON output.

## Safety boundaries

- Never print API keys, OAuth tokens, cookies, encrypted payloads, or full session transcripts.
- Never rewrite a session's history just to hide an error.
- Never apply a patch when an anchor is missing or duplicated; report the installed version and ask for a compatible patch.
- Without `--adopt-opencodex-route`, only remove a conflicting root `model_provider` when its table points to the current loopback opencodex port. The explicit flag allows taking over another `custom-local` gateway but never a custom remote provider. The script backs up `config.toml` before that edit.
- `agentTaskRecovery` is an explicit opencodex opt-in. It uses the authenticated native ChatGPT Codex endpoint to recover only the V2 worker assignment, then routes the recovered plaintext to the selected provider. Do not enable it on a remote/shared proxy or where native ChatGPT authentication is unavailable.
- Do not claim that a Skill intercepts every desktop model switch. The proxy adapter is the enforcement point; this Skill is the self-healing/checking workflow invoked before or after a reported switch, compaction failure, or opencodex upgrade.
- After an opencodex package upgrade, run the complete `--check` again. If it reports failures, run `--repair` before continuing long tasks.

Read [references/provider-boundary.md](references/provider-boundary.md) when explaining the protocol boundary, V1/V2 behavior, or recovery trade-offs.
