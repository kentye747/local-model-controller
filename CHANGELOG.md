# 2.5.1

- Attribute completed requests using native completion/task identity instead of model or time proximity.
- Add loopback-only Hermes and DSH request identity adapters and a pinned llama.cpp b11840 metadata instrumentation patch.
- Resolve exact native task titles for display on local and configured remote hosts without retaining conversation content in usage history.
- Preserve unknown/conflicting attribution, legacy counters and existing configuration.

# Changelog

## 2.5.0-preview.1 — 2026-10-28

First public preview, extracted into a clean repository without private development history or runtime data.

- Add English, Simplified Chinese and system language selection; externalize language catalogs.
- Package a standalone local bridge so an unrelated engine is not a startup requirement.
- Make paths, optional TensorFold command contract, local llama.cpp router and remote SSH metadata configurable.
- Default remote monitoring and custom TensorFold integration to off.
- Add first-run empty state, connection-settings entry and unknown-size load protection.
- Preserve native controls, memory protection, numeric history, CSV export, task attribution and profile-aware Hermes leases.
- Distinguish native confirmed-idle gateways from open keep-alive connections.
- Add build documentation, MIT license, third-party notices and automated fixture checks.

Known limits: ad-hoc signed / not notarized; initial downloadable binary is Apple silicon; external app schemas may change; natural remote active-title validation and broad hardware/OS compatibility are not claimed by unit tests; CSV headers remain source-language based in this preview.
