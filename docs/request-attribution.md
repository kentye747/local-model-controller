# Request origins and tasks

Request history can show the caller, task title/ID, native session, source host and completion ID. Association uses the engine's native task-to-completion mapping and caller-supplied native task identity. The controller never joins by nearby timestamps, a busy model or the latest session. Missing and conflicting mappings remain explicitly unrecorded. Old logs without a common identifier cannot be reconstructed reliably.

## llama.cpp b11840

`integrations/patch_llama_b11840.py` instruments upstream commit `36e36e63a54fc8f85b95e9c499a022b597e478dd`. It verifies the original server source hash before editing. Build with upstream CMake; the result emits one `LMC_REQUEST` JSON metadata marker alongside native task assignment. The marker relates the returned completion ID, internal task number and allowlisted caller/session/task/host headers. It never logs request bodies, prompts, responses or credentials. Existing timing lines keep their original meaning. This is a local instrumentation patch, not an upstream llama.cpp release; keep original binaries for rollback. Updating the managed engine can replace the instrumentation, in which case new request attribution becomes unavailable until an adapted build is verified.

## Hermes

Install `integrations/hermes_trace_headers.py` as `__init__.py` in a native user plugin directory, with a `plugin.yaml` manifest, and enable it with Hermes's normal plugin management. It registers `llm_execution` middleware and adds native task/session/request IDs only for loopback destinations. It calls downstream once and leaves message bodies and responses unchanged. Enable/reload each intended profile; merely placing a file or listing it as enabled is not acceptance. No cloud destination receives these tracing headers.

## DeepSeek Harness

Install `integrations/dsh_trace_headers.mjs` using a native Cordis profile patch **insert** entry. The module uses the native `llm/stream` waterfall and per-call async context to add task/session/request identity only to explicitly allowed loopback origins. It does not rewrite messages. Separate concurrent sessions have separate context. Cloud requests and unrelated fetches are unchanged. The optional diagnosticsPath is for bounded acceptance work only; omit it in normal configuration.

## Remote task titles

Place `history_titles.py` beside the configured remote metadata probe under the name `history_titles_probe.py`. The controller uses its existing configured SSH alias and Python to request at most 80 exact session IDs; the helper reads only native titles, never messages. The response must match the configured expected hostname. Remote failure leaves the original task ID visible and does not reuse an old title. No daemon, tunnel or schedule is installed by this reader.

Titles are resolved for display and are not written into the numeric history database. Persistent attribution contains opaque task/session/completion IDs, caller and host. Do not publish the runtime database, journals, configuration or logs. CSV retains its numeric usage format.

## Validation and limits

Local verification covers reordered concurrent completions, exact engine/model matching, conflict withholding, duplicate replay, legacy-ID upgrade without double counting, isolated async contexts and the loopback privacy boundary. Native acceptance additionally checks actual Hermes, DSH and remote Hermes requests against engine markers, imported controller rows and native task content. The isolated small-model engine tests validate instrumentation rather than model quality or performance. Unknown callers and uninstrumented engines remain unknown. TensorFold/LM Studio journal support is retained; neither engine's live integration is newly claimed by the llama.cpp acceptance.
