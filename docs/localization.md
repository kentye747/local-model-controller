# Translating the controller

The source UI keys are Simplified Chinese. `locales/en.json` maps source strings to English. `locales/zh-Hans.json` is intentionally empty because source strings are already Chinese. `locales/languages.json` controls the language menu; adding a language does not require changing Swift code.

1. Copy `locales/en.json` to a new BCP-47 locale filename, for example `fr.json`.
2. Translate values only. Keep keys intact, keep `%@` placeholders in the same order, and preserve format specifiers such as `%.1f`. Placeholders may contain model names or numbers.
3. Add `{ "id": "fr", "name": "Français" }` to `languages.json`.
4. Run `python scripts/extract_strings.py` to see source keys missing from the English baseline, then run the tests and rebuild.
5. Inspect the four pages, error messages, accessibility labels, long text and narrow windows. Check reduced motion and both appearances. Do not translate model IDs, provider IDs, file paths or conversation titles.

The language selection changes the controller's own UI. Agent conversations, operating-system dialog controls and external applications keep their own language. Dynamic engine states are translated at explicit display boundaries. Unknown vendor error messages may remain in their original language. Generated CSV uses stable source headers in this initial preview; language-specific CSV headers are not implemented.
