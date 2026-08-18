Run the Codex/opencodex provider-boundary repair now.

This is the explicit command entry point for the `codex-context-boundary` skill. Execute the deterministic repair script immediately; do not merely explain the steps:

```sh
python3 "${CODEX_HOME:-$HOME/.codex}/skills/codex-context-boundary/scripts/ensure_provider_boundary.py" --repair
```

Rules:

- Do not edit Codex session JSONL, delete conversation history, or manually rewrite encrypted payloads.
- Do not print API keys, OAuth tokens, cookies, encrypted payloads, or full transcripts.
- If the command exits non-zero, stop and report its JSON/error output and the next safe action; do not improvise a patch.
- On success, report the `state`, `source_root`, build result, and proxy health result from the command output.
- Treat independent 502/503, timeout, capacity, or connection errors as upstream availability errors unless the command itself reports a repair failure.
