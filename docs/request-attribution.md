# Request attribution (experimental)

The controller can display an origin, task ID and session ID when a local caller journal supplies the **same native completion ID** as an imported engine event. It matches the engine, native model ID and completion ID, never time proximity, a busy model, or an active session. Conflicting associations stay withheld. Older events without shared IDs remain unrecorded. This metadata does not create another usage event or change totals.

The optional `integrations/hermes_request_attribution.py` module implements Hermes's `llm_execution` middleware contract. A plugin context must supply `journal_path` pointing at `request-attribution.jsonl` in the controller data directory. The middleware calls downstream once, returns its response unchanged, and records only the returned completion ID/model, Hermes task/session IDs, caller and hostname. Journal failure never retries inference. This adapter is experimental and has not yet passed an actual Hermes UI request acceptance test; it is not installed automatically.

The journal must be an owner-only regular file owned by the controller user, with a maximum size of 8 MiB. Each JSON line has schema=1, engine (`TensorFold` or `LM Studio`), request_id, model_id, origin, task_id, session_id and host. IDs are restricted to letters, digits, underscores, dots, colons and hyphens. No prompts, replies, task titles, URLs, headers or credentials belong in this journal. It is local runtime data and must never be published with the source.

Current coverage:

- TensorFold and LM Studio: exact-ID import contract and local fixture tests. Live caller integration pending.
- Hermes: experimental middleware producer; named provider must be `tensorfold` or `lmstudio` (optionally `custom:` prefixed). Alias providers are not guessed.
- llama.cpp: unsupported until its timing events expose a common completion ID; a slot/task integer is not a completion ID.
- DSH, other callers and cross-host forwarding: not connected by this change. No claim of complete caller coverage.

Task identity is shown as its original ID; no task title is retained. CSV continues to contain the existing numeric usage export. No configuration, plugin activation, network route or schedule is changed by building the controller.
