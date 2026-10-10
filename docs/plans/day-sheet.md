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

### Result: Phase 1 is done

Met on 2026-10-10, with `day_sheet` merged in admin_scripts (PR #5).
- **The data.** Each item's fields are defined by `tasks/day_sheet/source.txt` and its tests.
  Where each one comes from is in [`api-gaps.md`](../principle/api-gaps.md) and
  [`firestore.md`](../principle/firestore.md).
- **16/11.** The sheet matches Principle's timeline and appointment panels:
  - 19 appointments, the lunch blocks, card notes and tags
  - every card unconfirmed
  - the tooth labels
- **Open Dental.** 14 of the 15 appointments in its snapshot match on practitioner, start,
  length and patient. Each difference is a change made in Principle after the migration, as
  the Firestore `eventHistory` shows:
  - The one mismatch, Andrea's 14:00, was rebooked on 08/10.
  - Four of her appointments exist only in Principle. The 15:00 and 16:00 were moved to 16/11
    from other days, and the 08:50 and 11:20 were booked on 20/09 and 05/10.
- **15/09 became a format reference only.** Open Dental's copy is the plan from before the
  migration, while Principle's is the day as it was run. Every element of `OD.png` has a field,
  except two that Principle doesn't hold:
  - block notes: no roster item in the practice has one
  - the "Web Sched" dot on migrated bookings. Online booking made in Principle is flagged.
- **Confirmation.** Principle has two states, confirmed and unconfirmed, where Open Dental had
  several.
- **Until patient contact details are cleaned,** a patient whose record fails the API
  specification takes the timeline card's name, and the sheet is marked partial. 16/11 has
  one. See [the contact details plan](clean-contact-details.md).
- **Phase 2 starts by installing it** in the application with `install_existing`, so that it
  runs from `/tasks/manage`. It isn't installed yet.

## Phase 2: lay it out on A4

**Generic, in `dental_practice_admin`:**
- A run may return a self-contained `printable` HTML document.
- The run page links to `/runs/{id}/print`, which serves it under the existing access control,
  with a CSP that allows inline styles and no scripts.
- Selecting a practitioner, or all, uses the existing run form in `/tasks/manage`.

**Layout, in `tasks/day_sheet/`:** `printable(detail, printed)` renders the task's own
`detail` to one self-contained HTML document, one A4 page per practitioner. It was designed
against 22 production days, 15/09 to 16/11 (see "Design" below).

### Design

Each fact is encoded once: by position, text, icon or colour, never two of them. The
starting point is what Open Dental printed. This replaces the earlier black-text plan (type as
text, times on line 1, 0.5 pt borders). On 2026-10-10 the owner asked for colour and icons, and
confirmed that the surgery printer prints in colour. On a black-and-white printer the
categories, and with them the new-patient flag, would not show.

| Fact | Open Dental | Day sheet |
|---|---|---|
| Start, end, length | Position and height on the grid | Same. No time text on the card |
| Category | Block colour | A 2.5 mm strip of Principle's own category colour ([firestore.md](../principle/firestore.md)) down the card's left edge; the card is white. A legend lists the day's categories |
| Patient | Name first | Name first, 12 pt bold |
| New patient | "NP-" prefix | The New Patient Exam colour alone |
| Procedures, teeth | Abbreviations: "PBWs, Hyg-Std, Ex", "#47-V-C1(P)" | Open Dental's abbreviations after the name, teeth after each: "Exo 28 18 46, C1 48o". A name with no abbreviation prints in full |
| Step | Not shown | Only if someone typed it: not "Planned - …", "Step N", "New Step", or a procedure's name |
| Notes, reason | Text after the procedures | Same, joined with " / " |
| Confirmation | Coloured dot | A hollow circle on unconfirmed only, since Principle has two states |
| Online booking | Blue dot | Globe, as Principle's timeline shows it |
| Lunch, meetings | Pastel block, label | Grey hatch, label. Includes blocks edited for one day, which Principle keeps as calendar events ([firestore.md](../principle/firestore.md)) |
| Pending online request | — | White card naming the requested treatment, with an hourglass. The owner asked for these (2026-10-10); Principle's timeline shows them. It has no patient until accepted |
| Free time | White | White |

- **Page.** A4 portrait, 10 mm margins, a 16 mm header, and a 9 mm time column labelled
  every 10 minutes. 08:00 to 17:00 fills the page height, 0.48 mm a minute; a day that runs
  longer widens the range.
- **Type.** Arial, black. The name is 12 pt bold and the rest 10 pt; Open Dental printed
  about 10 pt.
- **Width and whitespace.**
  - Cards use the full width. Overlapping appointments sit side by side.
  - Neighbours are separated by a 0.5 mm white gap rather than borders.
  - A 20-minute card shows two lines: name and procedures, then a note.
- **Overflow.** The text shrinks to 11/9 pt, then 10/9 pt, then is cut at a whole line ending
  in "…". Notes are never dropped silently. A card never grows past its end time: a proof
  that let text spill into free time below made a 14:20–15:00 appointment read as 15:10.
- **Empty day.** One page saying nobody is booked.
- **Partial coverage.** Red INCOMPLETE lines under the header name each gap by practitioner
  and start time. The header grows to fit them and the legend.

**Measured on production (2026-10-10 proofs):** every one of 22 days printed exactly one page
per practitioner, and no card was clipped. About one card a week is cut with "…". These
are 10–20-minute slots with long notes and no free time below. The check printed each day
with headless Chromium and compared every card's laid-out height with its slot.

**Phase 2 is done when:**
- the 15/09 and 16/11 sheets print on the surgery printer,
- they're checked side by side with `OD.png`,
- they're readable at arm's length, and
- nothing is clipped silently: a card cut short ends in "…".

## Risks

- **Open Dental access.** It depends on the practice VPN. If 15/09 can't be read, the reference
  record falls back to `OD.png` itself.
- **Possible migration defects for 15/09.** Missing treatment is already known, so differences
  on that day are triaged against the migration bug report before being treated as gaps.
- **Principle releases.** Firestore paths can change. The day sheet runs `check_web_build` as
  the diary does.
