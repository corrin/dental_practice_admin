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
   `appointmentRequestRef` on the appointment document. `Appointment` has no such field.
   *Request:* the booking source, or the appointment request ID, on `Appointment`.
