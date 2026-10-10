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
[the staging Firestore address experiment](https://github.com/corrin/dental_practice_admin/blob/84daa1a/scripts/address_via_firestore.py). Staging, 2026-10-01, build `main.ecfbec0077a05029.js`.

The token may run structured queries (`:runQuery`) over a whole collection, not only fetch
documents by name.

## IDs the API and Firestore share

The practice document ID (`practices/{id}`) is the API's practice ID, a staff document ID
(`staff/{id}`) is the API's `practitionerId`, and an appointment document ID is the API's
appointment ID. Joins between the two need no mapping.
**Verified:** all 19 appointments on 2026-11-16. Production, 2026-10-10,
build `main.8e7a8bfa2c5bf44c.js`.

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
**Verified:** [the staging changed-since query](https://github.com/corrin/dental_practice_admin/blob/84daa1a/scripts/address_via_firestore.py). Staging, 2026-10-01,
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
**Verified:** [the staging direct-write comparison](https://github.com/corrin/dental_practice_admin/blob/84daa1a/scripts/address_via_firestore.py).
Staging, 2026-10-01, build `main.ecfbec0077a05029.js`.

## Production

**What:** production's Massey Smiles workspace uses the same organisation and brand IDs as
staging, because staging is a copy of production. The root is `PRINCIPLE_FIRESTORE_ROOT_PROD`.
**Verified:** document paths observed while the production timeline loaded, then a document
read under the root. Production, 2026-10-10, build `main.8e7a8bfa2c5bf44c.js`.

A staff token cannot list collection IDs (`:listCollectionIds` returns 403) or run collection
group queries from the database root. Collection names come from the web app's own queries.

## What the timeline reads

**What:** for one day, the timeline subscribes to `practices/{practiceId}/scheduleSummaries`
filtered by `day` and `staffer`, to each staff member's `staff/{staffId}/rosterSchedules`, and
to `calendarEvents` filtered by practice, `event.from`, participants and `event.type`.
**Used for:** reproducing the timeline's content, including the pinned notes on each card.
**Verified:** the timeline's Firestore listen targets, captured in a headless production session
and replayed as reads. Production, 2026-10-10, build `main.8e7a8bfa2c5bf44c.js`. Monday
2026-11-16 matched the timeline: 19 appointments and the lunch blocks.

### Schedule summaries

One document per practitioner per day, at `practices/{practiceId}/scheduleSummaries/{id}`.
Query by `day` (`YYYY-MM-DD` string). These are copies Principle maintains for the timeline.

| Field | Meaning |
| --- | --- |
| `day`, `staffer`, `practice` | The local day and the practitioner it covers. |
| `events[].event` | `from`, `to` (UTC), `type` (`appointment`), participants: the patient and the staffer. |
| `events[].ref` | The appointment, `patients/{patientId}/appointments/{appointmentId}`, with the API's appointment ID. |
| `events[].metadata.label` | The patient name the card shows. |
| `events[].metadata.pinnedNotes[]` | The note lines the card shows: the patient's pinned notes, as plain strings. |
| `events[].metadata.status` | Appointment status, as the API's `status`. |
| `events[].metadata.tags[]` | Appointment tags, each with `name` and `ref`. |
| `events[].metadata.categoryRef` | The treatment category that sets the card colour. |
| `events[].metadata.treatmentPlanName`, `treatmentStepName` | The linked plan and step. |
| `gaps[]` | Free `from`/`to` windows in the practitioner's day. |

### Roster schedules

Recurring blocks for one staff member, at `staff/{staffId}/rosterSchedules/{id}`.

| Field | Meaning |
| --- | --- |
| `item.event.type` | `rosteredOn` (working hours), `break` (lunch, team meeting) or `preBlock` (time held for a kind of treatment, with `allowedTreatmentCategories`). |
| `item.title[].text` | The label the timeline shows, such as "Lunch". |
| `item.notes` | Rich-text notes. |
| `item.isBlocking` | Whether appointments can be booked over it. |
| `pattern` | Weekly repetition: `daysOfWeek`, `startDate`, `endingType` (`never`, or `on` with `endingDate`). |
| `scheduleTime.from`, `.to` | Local clock times, `HH:MM`. |
| `modifiers[]` | Per-date exceptions; `type: delete` removes the listed `dates`. Each date is the block's start instant on the removed day, in UTC. |

`endingDate` is the last day an item applies: a practitioner who left on 2026-10-09 has
items ending on that date. Every pattern seen is `Custom`, `Weekly`, `seperationCount` 1
(spelt so), except one fortnightly item that is deleted. `item.notes` is empty rich text
(`{"content": {"type": "doc"}}`) on every roster item in the practice.
**Verified:** every staff member's roster schedules, and the owner confirmed the leaving
date. Production, 2026-10-10, build `main.8e7a8bfa2c5bf44c.js`.

### Calendar events

`calendarEvents` at the brand root holds non-roster events: `event.type` seen as
`appointmentRequest`, `gapCandidate` (gap-fill offers) and `break`. Appointments are not here.

**A roster block edited for one day lives here.** Editing one occurrence adds that day to the
roster item's `delete` modifier and creates a `calendarEvents` document with the roster's
`event.type` (`break`), its `title`, the edited `event.from`/`to`, the staff member in
`event.participantRefs`, and `scheduleRef` pointing back to the roster item.
**Verified:** Andrea's 2026-10-12 lunch, edited on 2026-09-28. Production, 2026-10-10, build
`main.8e7a8bfa2c5bf44c.js`.

An `appointmentRequest` (an online booking not yet accepted) has `isBlocking: true` and the
practitioner in `practitioner`.

## Appointment documents

**What:** each appointment is `patients/{patientId}/appointments/{appointmentId}`, under the
same IDs the API uses.
**Verified:** read for every appointment on 2026-11-16. Production, 2026-10-10,
build `main.8e7a8bfa2c5bf44c.js`.

| Field | Meaning |
| --- | --- |
| `event.from`, `event.to` | The booked window, UTC. |
| `practitioner`, `practice` | Name and reference. |
| `status`, `statusHistory` | Current status and its changes. |
| `tags[]` | Appointment tags (`name`, `ref`). The API's `Appointment` omits them. |
| `appointmentRequestRef` | Present only when the appointment was booked online, referencing the `calendarEvents` request. The timeline shows a globe for it. |
| `treatmentPlan` | Plan name and reference, with the step's `name`, `duration` and category. |
| `eventHistory` | Earlier windows after a reschedule. |
| `waitListItem` | Wait-list settings, with rich-text `notes`. |

`appointmentRequestRef` was on 7 of the 227 appointments from 2026-10-12 to 2026-11-01. On
2026-10-12 its two appointments were exactly the timeline's two globes.
**Verified:** production, 2026-10-10, build `main.8e7a8bfa2c5bf44c.js`.

The appointment document holds no card notes; those are the patient's pinned notes, copied
into the schedule summary.

## Treatment categories: card colour

**What:** `treatmentCategories/{id}`, at the brand root. The ID is the API's
`treatmentCategory.id`. `colour.value` is the hex the timeline fills the card with, and
`colour.name` is its Material palette name ("Pink a100"). Deleted categories stay, with
`deleted: true`.
**Used for:** the day sheet's card colours.
**Verified:** the five categories on 2026-11-16 read by the API's IDs. Production,
2026-10-10, build `main.8e7a8bfa2c5bf44c.js`.

Two live categories share a colour: Recall and Consultation / Examination are both
`#d7ccc8`. Review/Ros is `#eeeeee`, close to white.

## Treatment steps: tooth and surface

**What:** `patients/{patientId}/treatmentPlans/{planId}/treatmentSteps/{stepId}` holds each
treatment's charting. `treatments[].chartedSurfaces[].chartedRef.tooth` has `quadrant` (1–4),
`quadrantIndex` (1–8) and `surface` (`occlusal`, `mesial`, `distal`, `lingual`, `facial`), so
quadrant 1, index 8, occlusal is the card's "18 o". The label is the surfaces' first letters,
in charted order, and lingual is "l" on upper teeth too ("24 l"), never "p". `treatments[].config.name` is the treatment
name, as the API's `description`. The step's `treatments[].uuid` is the API's
`treatments[].id`, in the same order. The API's `TreatmentInPlan` has no tooth or surface.
**Verified:** read for every appointment's step on 2026-11-16, and the labels compared with
the appointment panel for two of them. Production, 2026-10-10, build `main.8e7a8bfa2c5bf44c.js`.

The shared read-only client passed a staging changed-since query and matching count aggregation
on 2026-10-03, including token renewal. Authentication and rejected-refresh behaviour have
synthetic coverage in [`test_automation.py`](../../tests/test_automation.py).
