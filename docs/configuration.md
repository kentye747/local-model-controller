# Configuration and adapter contracts

Connection settings are local to the user and are never checked in. See `settings.example.json`. Restart only the controller after changes. The four-second refresh interval starts after the preceding read completes; remote latency and background throttling can increase it.

## Paths and standalone use

`paths.lms`, `paths.node`, `paths.hermes_app`, `paths.hermes_home`, `paths.dsh_app`, `paths.dsh_source`, `paths.dsh_home` select existing installations. The example paths are examples; defaults try common local locations. Installing the controller does not install or launch an engine. With no Hermes installation, LM Studio and the empty state remain usable.

Advanced isolation variables: `LMC_HOME` overrides the metadata home; `LMC_DATA_DIR` overrides controller data storage; `LMC_DISABLE_HERMES=1` disables importing the optional native managed-runtime reader. `LMC_LANGUAGE=en` or `zh-Hans` forces a language for validation. GUI choices are stored in macOS application preferences. Do not use isolation variables to create a second writer for an agent's session database; this app only reads those databases.

## Hermes and an explicit llama.cpp router

With Hermes installed, its ownership-guarded `managed_root()` reader is imported from that installation. Its source and dependencies are not distributed with this project. If the standalone bridge lacks the installation's Python dependencies, the read-only ownership check runs under Hermes' own bundled Python. Without a verified reader, the adapter stays offline rather than trusting a fixed port.

For a separately installed llama.cpp router, explicitly set `llama.base_url` to its HTTP loopback root, and put the credential in the environment variable named by `llama.api_key_env`. The endpoint must implement `/models`, `/slots?model=…`, `/models/load`, `/models/unload` and `/metrics`. A single-model server without router lifecycle APIs is not a fully supported management backend. `llama.context` is a user-supplied routing context, not a detected model limit. Load budgets use reported weight size, previously observed numeric metadata, or the total size of an existing GGUF (including a complete split set) named by the Hermes preset. You can explicitly set `llama.models["your-model-id"].estimated_bytes` for another installation. Unknown sizes block controller-initiated loads; no fixed model-specific estimate is assumed.

Hermes model assignment edits only the model/provider configuration with a backup and readback. Existing unrelated configuration is retained. This does not change the model in an active conversation.

## DeepSeek Harness

The desktop app is preferred when opening Harness. The configured source tree and Node are used only for a source-install web launcher. Session/config schemas are external interfaces and may change. The app verifies exact titles and rejects duplicate titles before using Accessibility to navigate. This permission is optional and only needed for that feature.

## TensorFold command adapter

The optional command at `tensorfold.command` must support `status`, `start`, `stop`. `status` must print a JSON object. A representative **contract example**, not a benchmark result:

```json
{"pid":123,"health":{"status":"ok","model":"your-model-id","warming":false,"memory":{"active":0},"live":{"connections":0,"waiting":0}}}
```

`pid` must represent ownership checked by your adapter. A matching model ID alone is not enough for the controller to accept a service. The adapter must expose active request counts and its real identity. Use the exact model ID/base URL and a realistic, nonzero `estimated_bytes` in settings before enabling loads. Do not point this at a vendor CLI with a different contract.

## Remote metadata probe (optional)

Remote monitoring uses an existing SSH alias, existing reverse forwards and a metadata-only script. It does not create tunnels, change SSH settings, install services or start schedules. To use it:

1. Place `mini_probe.py` on the remote Mac at the path you choose (the filename is historical; no particular Mac name is assumed).
2. Set `remote.enabled=true`, `ssh_host`, `expected_hostname` (the actual `socket.gethostname()` result), Python executable and `probe_path`.
3. The remote Python must have standard-library support for the script; no extra dependency is required. Existing SSH forwarding commands must target the configured alias and local loopback destinations.
4. The controller validates fresh host identity, active SSH destination, exact model ID, valid native turn lease and process start time. Named Hermes profiles are included. A native `active-work` reply can exclude idle keep-alive connections.

The supplied probe returns direct Hermes metadata only. `remote.gateway_sha256` is an advanced validation field for a separately maintained probe that also reports compatible gateway metadata; leave it empty for the generic probe. No gateway implementation or private integration is included. Remote conversations are display-only; they are not opened on another Mac automatically.

## History

The history database contains numeric events and identifiers. It does not contain chat text or task names. `history_timezone` is empty by default, using the system local timezone; an IANA name such as `Europe/London` is optional. CSV dates follow that history timezone, while native date labels use the Mac's timezone. Undated older logs stay undated, and only the All range includes them. Missing statistics are not replaced with zero.
