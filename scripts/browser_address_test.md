# English-directed staging browser experiment

Run from the repository root:

```powershell
uv run python -m scripts.check_browser_address
```

The local ignored `.env` supplies `PRINCIPLE_UI_EMAIL`, `PRINCIPLE_UI_PASSWORD`,
`OPENAI_API_KEY`, and the application's existing model/endpoint settings.
Chromium must be installed for Playwright. `--env-file` selects another configuration;
`--ui-env-file` explicitly overrides only the browser credential source.

The target is Annette Dummy in Massey Smiles Dental staging. Both Crash Test Dummy IDs
used by the other test session are excluded. Login is scripted; the OpenAI model then
follows `browser_address_procedure.md` and chooses browser actions from screenshots
and page structure. It receives no API, Firestore, credential, or code-execution tools.
ChatKit is not involved in this diagnostic; staff chat remains read-only.

The runner captures the first address line, supplies a unique temporary value, checks
the persisted value after a reload, and attempts the same round trip to restore it.
It reports edit and restoration verification independently and exits nonzero unless
both succeed. Each phase is bounded to 40 model calls and five minutes, with individual
model requests limited to 60 seconds. Inspect an uncertain save before retrying.

Do not run another editor on this patient concurrently. Original values remain in
process memory: do not terminate the process after editing begins. If restoration
fails, inspect the patient and restore the original before any further test.

`--diagnostic` prints selectors, current URLs and rendered UI text on a model stop.
Use it only for local diagnosis: its output may contain staging patient information.
Screenshots are sent to the configured inference endpoint but are not saved locally.
