# Principle's website

An Angular app using Angular Material: staging at `staging.principle.dental`, production at
`app.principle.dental`. Values used below (selectors, the workspace option, the slug) live in
`scripts/address_via_browser.py`.

## Finding elements

**What:** elements carry no test IDs (`data-testid`, `data-automation-id`). Generated IDs
such as `mat-input-42` change between visits.
**Use instead:**
- form inputs: their `formcontrolname` attribute
- components: their `pr-*` and `pt-*` tag names, for example `pr-patient-address`
- buttons and links: visible text
**Verified:** staging, 2026-10-01, build `main.ecfbec0077a05029.js`.

## Signing in

**What:** after the email and password form, a workspace picker appears. Type into its search
box first; options appear only after typing. The staging login has several workspaces whose
names start "Massey Smiles", so match the exact option, not a substring.
**Verified:** `scripts/address_via_browser.py`. Staging, 2026-10-01, build
`main.ecfbec0077a05029.js`.

## Opening a patient

**What:** a patient's page is `/{workspace-slug}/patients/{patientId}`, and the slug belongs
to the workspace. A URL with another workspace's slug leaves the app on its loading screen
indefinitely, with no error.
**Patient search is unusable from automation:** in headless Chromium the patients list
search (backed by Typesense) never returns results. Open patients by ID, which the API
search can supply.
**Verified:** `scripts/address_via_browser.py`. Staging, 2026-10-01, build
`main.ecfbec0077a05029.js`.

## Editing a patient

**What:** the profile's Edit button opens an "Update Patient" dialog holding the whole
patient. Saving with "Update Patient" resubmits every field in it, not only the changed one.

**Address entry:**
- The address field verifies what is typed. Free text shows "Address not verified" until a
  suggestion is picked or "Manually Enter Address" is used.
- Manual entry has street, unit, city, region, postcode and country fields. They open blank,
  because Principle stores only the joined string (see [firestore.md](firestore.md)).
  Saving replaces the whole address.
- The country field is an autocomplete. Pick the option: its open list covers the save button.
- Escape closes the whole dialog without saving.

**Effect:** a save stamps `updatedAt` and `updatedBy`, and the API shows the change at once.
**Verified:** `scripts/address_via_browser.py`. Staging, 2026-10-01, build
`main.ecfbec0077a05029.js`.
