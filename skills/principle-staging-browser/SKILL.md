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
- Browser login uses `PRINCIPLE_UI_EMAIL_STAGING` and `PRINCIPLE_UI_PASSWORD_STAGING` in this repo's
  ignored `.env`. Do not depend on a sibling repository's configuration.
- Reuse `dental_practice_admin.browser.session` and `browser.code`. The session owns
  automatic login, workspace selection and the cross-process workflow lock. Require the
  password field to disappear before taking screenshots or sending page text to an LLM.
- Workspace selection is a dropdown. Its option text is not visible until it is opened.
- Browser launch reporting a missing executable can mean sandbox access is denied to
  the installed Playwright browser directory. Check the actual error before installing
  another browser or treating browser control as unavailable.

## Diagnosis and patient selection

- Treat timeouts as evidence to investigate the current page, URL, overlays and selectors.
  Increasing a timeout does not correct a bad route or a closed dropdown.
- Workspace selection can produce the duplicated path
  `/principle-platform/principle-platform/schedule/timeline`. Inspect the landing route
  and rendered navigation before interpreting empty patient-search results.
- Search results can contain multiple patients with the same name. Bind the chosen
  patient to its record ID and visible identity; use DOB when supplied. Account for
  active/inactive filters. Never select a different record just to make a test pass.
- Coordinate record ownership with concurrent sessions. Never edit a record another
  agent is testing.

## English-directed test

Read [the website knowledge](../../docs/principle/website.md) before testing.
Exercise the shared script runner or browser fallback through chat, binding the exact
authorised patient and operation. Keep patient-derived inputs in ignored runtime storage.
Promote only source, input schemas and synthetic behaviour tests through a reviewed release.

Capture the original field value from the UI before editing. Verify persistence by
reloading and reading the field again; neither a filled input nor a model's completion
message proves a save. Restore and independently verify the original. Report edit and
restoration separately. After an uncertain write, read the saved state before retrying.

Screenshots and rendered UI sent to the model can contain staging patient data.
Keep credentials out of observations and retain raw diagnostics only where authorised.
Never commit `.env`, session state, or unredacted recordings.
