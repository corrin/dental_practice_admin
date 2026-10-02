# Principle's Firestore

Principle's web app reads and writes Google Firestore directly. The REST API reads the same
documents, so Firestore is the source of truth behind both.

## Access

**What:** a staff login's Firebase ID token authorises Firestore REST calls with that staff
member's permissions. It expires after an hour.
**Used for:** reads the API cannot answer.
The Firebase key requires the selected Principle site's `Referer` header for password sign-in
and token renewal. A missing referrer returns `API_KEY_HTTP_REFERRER_BLOCKED`.
**Verified:** `StagingBrowser.token` in `scripts/check_staging.py`, used by
[the staging Firestore address experiment](https://github.com/corrin/dental_practice_admin/blob/8b251fc/scripts/address_via_firestore.py). Staging, 2026-10-01, build `main.ecfbec0077a05029.js`.

The token may run structured queries (`:runQuery`) over a whole collection, not only fetch
documents by name.

## Where a workspace's patients live

**What:** patients are documents in a `patients` collection under an organisation and a
brand: `organisations/{org}/brands/{brand}/patients/{patientId}`. The document ID is the
patient ID the API and web URLs use.
**Used for:** finding the collection to query.
**Verified:** the staging workspace document path. Staging, 2026-10-01,
build `main.ecfbec0077a05029.js`.

The staging login reaches more than one organisation and brand holding patients. Only one
pair belongs to the Massey Smiles Dental workspace; the others return different patients or
none. A document path built from the wrong pair returns 404, not an error about permissions.

## Patient documents

**Verified:** read on staging, 2026-10-01, build `main.ecfbec0077a05029.js`.

| Field | Meaning |
| --- | --- |
| `name`, `dateOfBirth`, `gender`, `email`, `contactNumbers` | The same values the API returns. |
| `address` | One free-text string. The website's manual entry joins street, unit, city, postcode and country into it with commas; the structured parts are not stored. |
| `status` | Active or inactive, as shown on the profile. |
| `deleted` | Soft-delete flag. |
| `createdAt`, `createdBy` | When and by whom the patient was created. |
| `updatedAt`, `updatedBy` | Last change. Stamped by API and website saves only (see below). |
| `accountSummary` | Copies of invoice, payment and credit totals, maintained by Principle. |
| `recall`, `secondaryRecall` | Recall period and next-due settings. |
| `preferredDentist`, `preferredFeeSchedule` | References, each with a display name. |

## Finding patients changed since a time

**What:** a `runQuery` on `patients` filtered by `updatedAt >=` a timestamp returns every
patient changed since then.
**Used for:** "which patients changed today". The API cannot answer this: its patient search
has no date filter.
**Verified:** [the staging changed-since query](https://github.com/corrin/dental_practice_admin/blob/8b251fc/scripts/address_via_firestore.py). Staging, 2026-10-01,
build `main.ecfbec0077a05029.js`. It returned exactly the patients changed that day through
the API and the website.

It does not see direct Firestore writes, which do not stamp `updatedAt`.

## Direct writes bypass Principle

**What:** the staff token may PATCH patient documents. The write succeeds and the API and
website show the new value immediately. But `updatedAt` and `updatedBy` are not stamped,
and no address verification runs.
**Why it matters:** the change is invisible to the changed-since query and to the record's
audit fields. Documents also hold copies Principle maintains (`accountSummary`), which a
direct write can leave inconsistent.
**Rule:** never write to Firestore directly. Write through the API.
**Verified:** [the staging direct-write comparison](https://github.com/corrin/dental_practice_admin/blob/8b251fc/scripts/address_via_firestore.py).
Staging, 2026-10-01, build `main.ecfbec0077a05029.js`.

The shared read-only client passed a staging changed-since query and matching count aggregation
on 2026-10-03, including token renewal. Authentication and rejected-refresh behaviour have
synthetic coverage in [`test_automation.py`](../../tests/test_automation.py).
