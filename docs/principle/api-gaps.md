# What the day sheet needs that the API does not provide

The day sheet reproduces the timeline for one practitioner-day, with what each appointment is
for. Verified against production with reads only, 2026-10-09 and 2026-10-10, build
`main.8e7a8bfa2c5bf44c.js`. For Monday 2026-11-16, all 19 appointments from
`listAppointmentsByDateRange` matched the timeline on practitioner, start and end.

## Provided by the API

| Day sheet field | Source |
|---|---|
| Start, end, length | `Appointment.event.from` / `.to` (UTC, `+00:00`) |
| Practitioner | `Appointment.practitionerId` → `listPractitioners` |
| Patient name | `Appointment.patientId` → `getPatient` → `name` |
| Kind of appointment | `Appointment.treatmentCategory.name` (e.g. Hygiene, Recall, New Patient Exam) |
| Procedures | `Appointment.treatments[].description` (e.g. "Composite Filling - Direct Adhesive Restoration (1 surface)") |
| Status | `Appointment.status` |
| Confirmed or not | `Appointment.status`: `confirmed`, or unconfirmed when `scheduled` or `unscheduled` |

`treatments[]` on the appointment carries the whole linked treatment step, and matches
`getPatientTreatmentStep` for the same step. No extra call is needed.

The timeline's red "C" badge means *not confirmed*. The web build defines it as
`AppointmentNotConfirmed` and shows it when `status` is `scheduled` or `unscheduled`. The
"confirmed" badge is disabled, so a confirmed appointment shows none. On 16/11 all 19
appointments are `scheduled`, and all 19 cards show "C".

Principle has one unconfirmed state where Open Dental had several ("1 week sent", "Not Called",
"Left Msg"). That is a difference between the systems, not a gap in the API. Open Dental's
confirmations were not migrated as status: on 15/09 Principle shows each appointment marked
`confirmed` minutes before it started, as the patient arrived.

## Missing from the API, read from Firestore

Each is visible in Principle's timeline or appointment card. See [firestore.md](firestore.md).
Requests to Principle:

1. **Card notes.** The note lines on a timeline card are the patient's pinned notes. Firestore
   copies them into `scheduleSummaries` (`events[].metadata.pinnedNotes`). The API cannot read
   notes at all: `…/interactions` exists for patients and appointments but answers GET with
   405. *Request:* `GET …/patients/{id}/interactions` with a `pinned` flag, or `pinnedNotes` on
   `Appointment`.
2. **Tooth and surfaces.** The card shows each treatment's tooth and surfaces ("18 o",
   "17 mod"). Firestore holds them on the treatment step
   (`treatments[].chartedSurfaces[].chartedRef.tooth`). `TreatmentInPlan` has neither, and
   `serviceCodes` is empty for most treatments. *Request:* `tooth` and `surfaces` on
   `TreatmentInPlan`.
3. **Lunch, meetings and other blocks.** These are roster schedules on each staff member
   (`staff/{id}/rosterSchedules`, `item.event.type` `break` or `preBlock`). No endpoint returns
   them, and every row from `listAppointmentsByDateRange` has a `patientId`. *Request:* a
   practitioner roster or blocked-time endpoint.
4. **Appointment tags.** `GET /v1/tags/appointment` lists the practice's tags, but `Appointment`
   has no `tags` field. Firestore has `tags[]` on the appointment. *Request:* `tags` on
   `Appointment`.
5. **Online booking.** The timeline shows a globe on appointments booked online, the
   equivalent of Open Dental's blue "Created from Web Sched" dot. Firestore has
   `appointmentRequestRef` on the appointment document. `Appointment` has no such field, so
   the day sheet reads each appointment's document: one Firestore read per appointment. On
   2026-10-12 the two flagged appointments were the two with a globe. The 2026-11-16
   appointment Open Dental marks "Created from Web Sched" has no `appointmentRequestRef` in
   Principle, so at least that migrated booking lost it.
   *Request:* the booking source, or the appointment request ID, on `Appointment`.
6. **Category colour.** A timeline card is filled with its treatment category's colour.
   `Appointment.treatmentCategory` is a `NamedReference` (ID and name) only, and no endpoint
   lists categories. Firestore has `treatmentCategories/{id}.colour`. *Request:* `colour` on
   the category reference, or a categories endpoint.
7. **Short treatment names.** Open Dental printed abbreviations ("Hyg-Std", "PBWs"). Principle
   has none: `treatmentConfigurations` carries `name` and search `keyword`s only. The day
   sheet keeps its own table mapping each name to Open Dental's `AbbrDesc`. *Request:* a short
   name on each treatment.

# What bank reconciliation found in the payments API

Phase 0 of [the bank reconciliation plan](../plans/bank-reconciliation.md). Production reads
2026-10-10; one staging write the same day. The scripts are in the Phase 0 pull request's
history under `scripts/spikes/`.

## How payments are recorded

- Every payment keyed at the desk has `provider` `manual`. The method staff chose is
  `extendedData.transactionType.name`, the practice's own list: Credit Card, EFTPOS, Direct
  Deposit, Southern Cross, ACC Payment, Cash, WINZ (card or bank deposit), Refer a Friend
  credit. Principle's ACC integration uses `provider` `acc`, with `pending` and `failed` rows
  as well as `complete`.
- One payment split across invoices is one row per invoice, with the same `id` and that
  invoice's share as `amount`. A row's identity is (`id`, `invoiceId`).
- Insurer payments carry no batch or remittance number: `reference` is Principle's own id and
  `description` is usually empty.
- How reception records payments, from bank deposits 1 September to 9 October 2026: Credit Card
  payments are what Smartpay settles the next day, and EFTPOS what Paymark settles the same day,
  gross. Southern Cross payments are recorded the day before Southern Cross pays them. ACC
  payments are often recorded after ACC's deposit arrives. Almost every bank transfer is
  recorded as a Direct Deposit payment (or WINZ), usually the same day. Verified by
  `card_days.py` and `transfers.py` in that history.
- `Patient` has no balance. What a patient owes is the sum over their `issued` invoices of
  `total` less the allocations in `transactionAllocations`.

## Client refusals

`PrincipleClient` validates every response against the published specification, which is
stricter than the data in two places. Each refusal blocks an otherwise good read:

1. **Every invoice with allocations.** `AllocationTarget` puts a `discriminator` on inline
   `oneOf` branches with no mapping, so openapi-core looks for component schemas named
   `practitioner` and `unallocated`. The `oneOf` alone accepts the data.
2. **Patients with a spaced phone number.** `ContactNumber.number` must match
   `^\+?\d{6,15}$`; numbers like `021 123 4567` fail. The client reads `getPatient` before every
   patient-scoped call, so none of that patient's invoices can be read. 1 in 15 production
   patients who owe money, 5 in 60 staging patients.
   - It also stopped the day sheet for every practitioner: 1 of the 15 patients booked on
     2026-11-16. The day sheet takes that patient's name from the timeline card instead and
     marks the sheet partial. Remove that fallback, the `getPatient` handler in admin_scripts'
     `tasks/day_sheet/source.txt`, once Principle fixes this.
   - The practice's own data is being cleaned: see
     [the contact details plan](../plans/clean-contact-details.md).
   - *Request to Principle:* reject contact numbers on entry that the specification doesn't
     allow, in the website and the API, or correct the specification to match what is stored.
3. **`listTransactionsByDateRange` over a split payment.** `PrincipleClient.rows` treats the
   repeated `id` as a restarted walk. Seen on staging's migrated payments (115 of 1,899 rows);
   none in 890 production rows from 2026-09-01 to 2026-10-09.

## Missing for recording payments

1. **A payment method.** `createTransaction` accepts only `provider` `manual` and has no
   `extendedData`, so a payment made through the API has no method. It cannot be recorded as
   EFTPOS or Direct Deposit, and Principle's takings by method will not include it.
   *Request:* `transactionTypeId` on `createTransaction`.
2. **The payment date.** `createdAt` in the request is ignored; the payment is dated when the
   call is made (staging, sent 2026-03-27, stored 2026-10-10). *Request:* honour `createdAt` on
   `createTransaction`.
3. **Account credit.** No endpoint creates one, so an overpayment cannot be recorded.
4. **Voiding.** `updateTransaction` changes only `status`. Whether `failed` reverses a payment
   is not yet tested.
5. **Filtering invoices by status practice-wide.** `listInvoicesByDateRange` has no `status`,
   so finding unpaid invoices means reading all of them: 7,716 since 2025-01-01, 99 s.

Straight after `createTransaction` on staging, the invoice still read `issued` with nothing
allocated. Whether Principle allocates the payment later was not re-checked.
