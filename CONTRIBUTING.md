# Contributing

Thanks for helping make local inference easier to see and control.

Keep changes small and explain the external engine/API version you tested. Run the unit tests, localization coverage, and macOS native checks relevant to the change. Tests must use isolated temporary files and mocked engines; do not run load/release operations against a contributor's real active models.

Translations belong in `locales/`; see `docs/localization.md`. For adapters, document the supported lifecycle/ownership contract and how unknown, stale, concurrent and disconnected states are handled. Do not replace uncertainty with a plausible-looking task title or speed.

Do not add cloud analytics, automatic engine launches on status, or secret-bearing diagnostic uploads as incidental changes. Never include runtime databases, local settings, screenshots of private tasks, model weights or credentials in a pull request. By contributing, you agree to license your original contribution under this project's MIT license.
