---
name: principle-staging-browser
description: Run and diagnose authorised Principle Dental staging browser experiments, including English-directed patient-profile edits and restoration.
---

# Principle staging browser work

Read the repository ADR index before changing the implementation. Work within the user's
named records and operations; this skill does not authorise patient edits by itself.

## Configuration and browser setup

- Staging UI origin: `https://staging.principle.dental`. The workspace is
  **Massey Smiles Dental**. Do not infer the UI environment from API credentials.
- Browser login uses `PRINCIPLE_UI_EMAIL` and `PRINCIPLE_UI_PASSWORD` in this repo's
  ignored `.env`. Do not depend on a sibling repository's configuration.
- Reuse `scripts.check_staging.staging_browser` and its login helper. The helper returns
  when Firebase issues a token; the page can still contain the password field. Wait
  for that field to disappear before taking screenshots or sending page text to an LLM.
- Workspace selection is a dropdown. Its option text is not visible until it is opened.
- Browser launch reporting a missing executable can mean sandbox access is denied to
  the installed Playwright browser directory. Check the actual error before installing
  another browser or treating browser control as unavailable.

## Diagnosis and patient selection

- Treat timeouts as evidence to investigate the current page, URL, overlays and selectors.
  Increasing a timeout does not correct a bad route or a closed dropdown.
- Workspace selection can produce the duplicated path
  `/principle-platform/principle-platform/schedule/timeline` or
  `/massey-smiles/massey-smiles/schedule/timeline`. The rendered Patients/person sidebar
  link uses `/massey-smiles/patients`. Follow the visible Patients link rather than
  constructing a route from the timeline URL. The global Search & Quick Actions dialog
  returned no matches in these browser experiments; use the Patients page to investigate.
- Search results can contain multiple patients with the same name. Bind the chosen
  patient to its record ID and visible identity; use DOB when supplied. Account for
  active/inactive filters. Never select a different record just to make a test pass.
- Coordinate record ownership with concurrent sessions. The fourth-method script has
  explicit exclusions for the other session's two Crash Test Dummy records.

## English-directed test

Run `uv run python -m scripts.check_browser_address` from the repository root.
Read [the runner notes](../../scripts/browser_address_test.md) before running or changing it.
The model follows [the English procedure](../../scripts/browser_address_procedure.md);
the Python runner supplies browser tools and checks outcomes. ChatKit is the chat UI,
not the browser driver. This diagnostic does not add writes to staff chat.

Capture the original field value from the UI before editing. Verify persistence by
reloading and reading the field again; neither a filled input nor a model's completion
message proves a save. Restore and independently verify the original. Report edit and
restoration separately. After an uncertain write, read the saved state before retrying.

Screenshots and rendered UI sent to the model can contain staging patient data.
Keep credentials out of observations and retain raw diagnostics only where authorised.
Never commit `.env`, session state, or unredacted recordings.
