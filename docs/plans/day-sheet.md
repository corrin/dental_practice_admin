# Day sheet: plan

## Context

Staff want each surgery to have a printed one-page A4 day sheet, for one practitioner or for
all of them. Before the move to Principle, Open Dental's "Daily Appointments" print did this.
Principle's alternatives don't work on paper:
- **Timeline print.** It comes out pale grey and clipped, with no procedures.
- **Practitioner dashboard.** It prints grey too, without lengths or procedures. Shirley fixes
  it up by hand.

The goal is to reproduce what Open Dental printed, then do better on the page.

The report lives in the practice repository `massey-reception-coder/admin_scripts`, cloned at
`C:\Users\User\source\repos\admin_scripts`, as `tasks/day_sheet/`. Generic capability belongs
in `dental_practice_admin`: printable output, and shared practice code.

The work is two phases:
- **Phase 1:** fully replicate the data. Phase 1 ends at the milestone below.
- **Phase 2:** lay it out on A4 for printing.

## The reference: what Open Dental printed

The sample is `Downloads\OD.png`: Mariam, Tuesday 15/09/2026. For each appointment, the block
showed:

| Element | Example | Open Dental source |
|---|---|---|
| Start, end, length | Block from 8:10 to 8:50 | `appointment.AptDateTime`, `Pattern` (5-min slots) |
| Patient | "Glaser, Aidan" (Last, First) | `patient` |
| New-patient flag | "NP-Huang, Yuxun" | `appointment.IsNewPatient` |
| Procedures, abbreviated, with tooth and surface | "PBWs, Hyg-Std, Ex", "#47-V-C1(P)" | `appointment.ProcDescript` / `procedurelog` + `procedurecode.AbbrDesc` |
| Appointment note | "didn't want sooner as off work this week" | `appointment.Note` |
| Appointment reason (online booking) | "Appointment Reason: Comprehensive new patient examination" | part of `appointment.Note` |
| Confirmation | Coloured dot: yellow, grey, blue or white | `appointment.Confirmed` → `definition` |
| Appointment type | Block colour: green, purple | `appointment.AppointmentTypeNum` / colour |
| Blocks | "Lunch Tuesday", "Meeting: catch up with Mariam" | `schedule` blockouts, with their notes |

What each block shows is set by the practice's appointment view (`apptview` / `apptviewitem`).
The print layout comes from `opendental/OpenDental/Forms/FormApptPrintSetup.cs` and
`UI/Appt/ControlApptPanel.cs`, in the local fork at `C:\Users\User\source\repos\opendental`.

Open Dental's database can still be read through `C:\Users\User\source\repos\od_data`, which
has read-only access over the practice VPN (`recon_lib.py`).

## Phase 1: fully replicate the data

### 1. Pin down exactly what Open Dental showed

From the Open Dental database for 15/09/2026, list every appointment and block for every
provider and operatory. Include each element in the table above, following the practice's
`apptviewitem` configuration. The result is a reference record for that day, one line per
element.

**Where it lives:** an `od_data` notebook cell, kept in that private repository, which already
holds patient data on purpose.

### 2. Find each element in Principle, API first

The appointments for 15/09/2026 were migrated, so the same day exists in both systems.
**Method:** for every element in the reference record, find the same text or value in Principle.

1. **Dump everything the API returns for that day**, raw, with no schema filtering:
   - `listAppointmentsByDateRange` and `getAppointment`
   - `getPatient`
   - `listPatientTreatmentPlans` and `getPatientTreatmentStep`
   - `listTreatmentPlansByDateRange`
   - `listTreatmentOptions`
   - `listPractitioners` and `searchAvailability`
   - `listEmails`, `listSMSMessages` and `listPatientFiles` for the day's patients

   Keep any keys the published spec doesn't mention.
2. **Search the dump** for each reference value, such as note text, the "Appointment Reason"
   string, tooth numbers and procedure names. Principle's naming won't match Open Dental's or
   the timeline's, so search for values, not field names.
3. **Probe the undocumented reads Principle's own models imply**, using GETs only:
   - `…/appointments/{id}/interactions` and `…/patients/{id}/interactions`, since
     `AppointmentInteraction` and `PatientInteraction` have `title`, `content` and `type`
   - `listAllowedAppointmentStatuses`
   - appointment-tag reads with the brand or practice IDs
4. **Check these candidates specifically** (already-seen fields first, then guesses):

   | Element | Candidate |
   |---|---|
   | Note / reason | Interactions; `TreatmentPlan.name` (120 characters seen); webhook payload `data` |
   | Tooth and surface | Undocumented `treatments[]` keys; `serviceCodes` on other treatments; treatment-step detail |
   | Appointment type | `treatmentOptionId` → `listTreatmentOptions` (16 options, such as "ACC Consultation"); `treatmentCategory` |
   | Confirmation ("C" badge) | `listAllowedAppointmentStatuses`; SMS replies (`listSMSMessages`); patient `tags` |
   | Lunch / meeting blocks | Gaps from `searchAvailability` against working hours (no label) |
   | Abbreviated codes ("Hyg-Std") | `serviceCodes[].code`; treatment-option names |

5. **Repeat on a current day,** Monday 16/11/2026, booked natively in Principle. Compare with the
   timeline and the appointment card on the right.

**Tools:**
- Extend the probe in `run/`. It is read-only, prints value locations rather than patient
  values, and its output stays in `run/`.
- Use `PrincipleClient.rows` in `src/dental_practice_admin/principle.py` for paging.

### 3. Firestore for whatever the API lacks

Only for elements that step 2 can't find:
1. **Find the production Firestore root** (`organisations/{org}/brands/{brand}`) by signing in
   to the web app and reading the document paths it loads. This is how the staging root was
   found: `StagingBrowser` in `scripts/check_staging.py`. The Playwright helpers
   `_debug_login` and `_discover` in `od_data/principle_ui.py` already do this for production.
   Record the root as `PRINCIPLE_FIRESTORE_ROOT_PROD` in `.env`.
2. **Read the appointment, event and block documents for both days.** Use the read client
   (`Firestore.read` in `src/dental_practice_admin/firestore.py`), and search them for the
   remaining reference values the same way as step 2.
3. **Document each field that gets used** in `docs/principle/firestore.md`: the path, the field,
   what it means, and the date and build it was verified against.

### 4. Record the result

Update `docs/principle/api-gaps.md` for each element, saying which of three cases applies:
- it comes from the API, giving the field
- it comes from Firestore, giving the field, with a request for Principle to add it to the API
- it isn't available, with the request

The file should be ready to send to Principle.

### 5. Build the data collection

`tasks/day_sheet/` in `admin_scripts`, containing `task.json`, `source.txt` and `test_task.py`:
- **Inputs:** `{"date"?: YYYY-MM-DD, "practitioner"?: name}`. The date defaults to today in
  Auckland; the practitioner defaults to all.
- **Output `detail`:** per practitioner, sorted by time. Each item carries every element in the
  reference table, using Principle's values: kind (appointment or block), start, end, length,
  patient, new-patient flag, procedures with tooth and surface, note, reason, confirmation, and
  type. Cancelled appointments are left out but counted.
- **Coverage:** `partial`, naming the element, whenever anything can't be resolved for an item.
  A sheet must never look complete when it isn't.
- **Time handling** is shared with `daily_diary`: `PRACTICE_TZ`, `local_day_window`,
  `local_time`, `_event_window`. Both tasks import one shared module in `admin_scripts`.
  - Today the application installs only a task's own three files
    (`task_files.install_existing`, `saved_scripts.load`), so it needs to install the practice
    repository's shared module alongside them.
  - That is a small change in `dental_practice_admin`.
- **Synthetic tests:**
  - a daylight-saving day
  - an empty day
  - one practitioner vs all
  - blocks interleaved with appointments
  - an unresolved element makes coverage partial

### Milestone: data fully replicated

The milestone is met when an automated comparison shows no unexplained differences on both days:
- **15/09/2026:** run `day_sheet` and compare every element with the Open Dental reference
  record from step 1.
- **16/11/2026:** run it again and compare with Principle's timeline and appointment cards.

Every difference must be fixed, or explained in `api-gaps.md` (for example, migration defects
already in `od_data/principle-migration-bug-report.md`, such as issue 14, "Upcoming appointments
have no treatment"). Phase 2 doesn't start until this holds.

## Phase 2: lay it out on A4

**Generic, in `dental_practice_admin`:**
- A run may return a self-contained `printable` HTML document.
- The run page links to `/runs/{id}/print`, which serves it under the existing access control,
  with a CSP that allows inline styles and no scripts.
- Selecting a practitioner, or all, uses the existing run form in `/tasks/manage`.

**Layout, in `tasks/day_sheet/`:** the task renders its own `detail` to HTML.
- **Page.** Portrait A4, 10 mm margins, giving 190×277 mm usable.
- **Header.** About 12 mm: practitioner, day and date, printed time, and a warning if coverage is
  partial.
- **Grid.** A proportional time grid like Open Dental's, using the full width. That's a 12 mm
  time column and about 175 mm of blocks. Hours 8:00–17:30 over about 260 mm gives about
  0.45 mm a minute, so a 15-minute appointment is about 6.8 mm.
- **Text.** Pure black, 10 pt body, 8 pt minimum, 0.5 pt solid borders.
  - Line 1: start–end, name (with NP), and confirmation as a word.
  - Then procedures with tooth and surface, then note and reason.
  - Type is printed as text, because colour is lost in black-and-white printing.
  - Blocks are labelled and hatched.
- **Overflow.** Shrink the text to 8 pt. If it still doesn't fit, the block takes free time
  below it, or the sheet marks it. It never clips silently.
- **Print all.** One practitioner per page, printed as one job.

**Phase 2 is done when:**
- the 15/09 and 16/11 sheets print on the surgery printer,
- they're checked side by side with `OD.png`,
- they're readable at arm's length, and
- nothing is clipped.

## Risks

- **Open Dental access.** It depends on the practice VPN. If 15/09 can't be read, the reference
  record falls back to `OD.png` itself.
- **Possible migration defects for 15/09.** Missing treatment is already known, so differences
  on that day are triaged against the migration bug report before being treated as gaps.
- **Principle releases.** Firestore paths can change. The day sheet runs `check_web_build` as
  the diary does.
