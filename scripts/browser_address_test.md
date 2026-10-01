# English-directed staging browser experiment

Run from the repository root:

```powershell
uv run python -m scripts.check_browser_address
```

The local ignored `.env` supplies `PRINCIPLE_UI_EMAIL`, `PRINCIPLE_UI_PASSWORD`,
`OPENAI_API_KEY`, and the application's existing model/endpoint settings.
Chromium must be installed for Playwright. `--env-file` selects another configuration;
`--ui-env-file` explicitly overrides only the browser credential source.
Use `--discovery-only` to exercise login, patient selection and address capture without saving.

The staging round trip passed on 1 October 2026 with `gpt-6.1-sol`: discovery,
temporary address persistence, and restoration all verified. The successful run took
24 model calls and 133.8 seconds (184,158 input tokens; 847 output tokens). Both address
checks read the saved Profile after a fresh reload. Annette's original `15 Otitori`
was restored. No Crash Test Dummy record was used.

An earlier run verified the edit but repeated clicks during restoration. A separate
model-directed browser recovery restored and verified the original before the complete
successful rerun. The runner retains rendered-page text in the model conversation and
stops three identical consecutive actions. The live test validates the address round
trip; it does not independently compare every unrelated patient field.

The target is Annette Dummy in Massey Smiles Dental staging. Both Crash Test Dummy IDs
used by the other test session are excluded. Login is scripted; the OpenAI model then
follows `browser_address_procedure.md` and chooses browser actions from screenshots
and page structure. It receives no API, Firestore, credential, or code-execution tools.
ChatKit is not involved in this diagnostic; staff chat remains read-only.

The runner captures the saved Profile address, supplies a unique temporary value, checks
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
