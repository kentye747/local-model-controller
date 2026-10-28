# Privacy and data handling

- No telemetry endpoint, cloud account, background updater or prompt upload is included.
- Status polling reads memory, local model endpoints, process identity and native agent metadata. It does not submit inference.
- The Test button submits exactly one short local `Reply only OK` request when explicitly clicked. The bridge currently uses the equivalent Chinese prompt; this action is not part of status refresh.
- Task names are shown in memory and are not stored in the numeric usage-history database.
- Engine logs can include prompts and responses. The app reads bounded log segments to extract statistics, but stores only numbers, model IDs, dates, finish reasons and provenance. A live LM Studio stream is parsed and its text output is discarded.
- Configuration backups may contain local API keys. They stay in the user's controller data directory with restrictive permissions. **Never attach the whole data directory, settings file, backups, raw engine logs or chat databases to a public issue.**
- Remote SSH monitoring is off by default. When explicitly configured, it reads bounded metadata on the named remote Mac, including task titles. It does not replicate the native session database.
- Exporting CSV is an explicit user action and includes model identifiers and usage information. Review it before sharing.
- Closing the app ends its own observer but does not unload model engines. No system daemon or schedule is installed.

For a bug report, start with app version, macOS version, CPU architecture, engine versions and a minimal reproduction. Redact usernames, hosts, keys, task names and paths from any optional diagnostic excerpt.
