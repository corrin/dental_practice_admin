# What the day sheet needs that the API does not provide

The day sheet reproduces the timeline view for one practitioner-day, with what each
appointment is for. Verified against production with GET requests only, 2026-10-09, comparing
`listAppointmentsByDateRange` for Monday 2026-11-16 with the timeline for the same day. All
19 appointments matched on practitioner, start and end.

## Provided by the API

| Day sheet field | Source |
|---|---|
| Start, end, length | `Appointment.event.from` / `.to` (UTC, `+00:00`) |
| Practitioner | `Appointment.practitionerId` → `listPractitioners` |
| Patient name | `Appointment.patientId` → `getPatient` → `name` |
| Kind of appointment | `Appointment.treatmentCategory.name` (e.g. Hygiene, Recall, New Patient Exam) |
| Procedures | `Appointment.treatments[].description` (e.g. "Composite Filling - Direct Adhesive Restoration (1 surface)") |

`treatments[]` on the appointment carries the whole linked treatment step, and matches
`getPatientTreatmentStep` for the same step. No extra call is needed.

## Missing from the API

Each is visible in Principle's timeline or appointment card. Requests to Principle:

1. **Appointment notes.** The text on the timeline card (for example payment or scheduling
   notes) is not on `Appointment`. `AppointmentInteraction` can only be created; there is no
   list or get. *Request:* a `notes` field on `Appointment`, or `GET …/appointments/{id}/interactions`.
2. **Tooth and surfaces.** The card shows each treatment's tooth and surfaces ("18 o",
   "17 mod"). `TreatmentInPlan` has neither, and `serviceCodes` is empty for every treatment
   seen. *Request:* `tooth` and `surfaces` on `TreatmentInPlan`.
3. **Non-patient bookings.** Lunch and meeting blocks on the timeline are absent from
   `listAppointmentsByDateRange`; every row has a `patientId`. *Request:* a practitioner
   schedule or blocked-time endpoint.
4. **The "C" badge.** Every appointment on the timeline shows "C", but all 19 have `status`
   `scheduled`, so the badge is not the status. Its meaning, and where it is held, is unknown.
5. **Appointment tags.** `GET /v1/tags/appointment` with the practice's `practiceId` returns
   403 "Practice not found", although the same key and ID work for every other call.
   `Appointment` has no `tags` field to read them from in any case.
6. **Card colour.** On 2026-11-16 every card's colour was consistent with its treatment category, which the sheet
   prints as text. This needs no new field.
