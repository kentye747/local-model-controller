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
