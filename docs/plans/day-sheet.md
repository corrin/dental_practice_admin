# Day sheet — plan

## Context
Staff want a one-page A4 day sheet printed in each surgery, for one practitioner or all of them.
Principle's own options fail on paper:
- The timeline prints in pale grey and is clipped, and it has no procedures. The procedures
  only appear in the appointment card on the right.
- The practitioner dashboard prints grey too, without lengths or procedures. Shirley currently
  fixes it up by hand.

Open Dental's "Daily Appointments" print is the model.

The day sheet is a practice report, so it lives in `massey-reception-coder/admin_scripts` as
`tasks/day_sheet/`. Only generic capability, which is printable output, goes in this repository.

Work runs in two stages, each a separate task and PR:
1. **Collect the data.**
2. **Draw it.**

## What the images tell us
| | Open Dental print | Principle timeline (screen) | Principle timeline (print) |
|---|---|---|---|
| Time axis | 10-min rows, hour labels both sides | 10-min ticks | Same, faint |
| Block text | Name, procedures (PBWs, Hyg-Std, Ex), notes, "Appointment Reason" | Name, notes, reason | Name, notes in pale grey, clipped |
| Procedures | Yes | Card only ("Composite Filling, 18 O") | No |
| Length | Block height | Block height | Borders lost, so unreadable |
| Status | Coloured dot | "C" badge | Grey "C" |
| Type | Fill colour | Fill colour | Lost |
| Lunch/Meeting | Blocks with text | Grey block | Text only |
| Page use | About half the width, one practitioner | — | About 40%; two columns squeezed |

**Keep:**
- A proportional time grid, so gaps and lengths show at a glance.
- Name, procedures and notes in each block.
- Breaks shown.

**Fix:**
- Black on white, with solid borders.
- Explicit start–end times.
- Type and status as text rather than colour.
- Full page width.
- Nothing silently clipped.

## Stage 1: collect the data (this task)

### Sources
| Field | API (`listAppointmentsByDateRange` etc.) | Needs Firestore |
|---|---|---|
| Start, end, length | `event.from/to` | |
| Practitioner | `listPractitioners` | |
| Patient name | `getPatient` (two calls per patient) | Or `patients/{id}.name` |
| Status | `status` (words, not "C") | |
| Procedures | `treatments[].description`, `serviceCodes`, `treatmentCategory` | Tooth/surface ("18 O")? |
| Notes / reason | Not in API | Yes |
| Appointment type/colour | Not in API | Yes |
| Lunch / Meeting blocks | Not in API | Yes |

`treatments[]` has never been seen populated: both recorded staging rows had none.

### Steps
0. **Clone the practice repository.** Clone `massey-reception-coder/admin_scripts` to
   `C:\Users\User\source\repos\admin_scripts` and branch `day-sheet` there. Push early and open
   a draft PR, following the same rules as here.
1. **Get each field from the API where it can, and from Firestore where it can't.** Do this
   against staging, read-only, on a populated day. On a populated staging day:
   - Confirm `treatments[]` is filled when the appointment card shows treatments.
   - Use `scripts/check_staging.py` → `staging_browser()` to find the Firestore collections for
     appointments/events and breaks. Read the fields for notes, reason, tooth/surface, type and
     blocks.
   - Record what is verified in `docs/principle/firestore.md` (and the README index), as
     AGENTS.md requires. This is a small docs PR in this repository.
   - Write `docs/principle/api-gaps.md`, listing each day-sheet field the API lacks. Give the
     Firestore field it was read from instead, and what endpoint or field Principle would need to
     add. It must be ready to send to Principle.
2. **Shared helper in admin_scripts.** Move `PRACTICE_TZ`, `local_day_window`, `local_time` and
   `_event_window` out of `tasks/daily_diary/source.txt` into one shared module. Both
   `daily_diary` and `day_sheet` import it; the diary's tests must stay green.
   - First check how the app installs a task. Today it copies one task's folder to
     `installed/<task>/<sha>/`, so a shared module may not be installed or importable.
   - If it isn't, installing shared practice code is a generic app change, and it belongs in this
     repository. I'll stop and raise it before building it.
3. **`tasks/day_sheet/` in admin_scripts** (`task.json`, `source.txt`, `test_task.py`):
   - **Inputs:** `{"date"?: YYYY-MM-DD (default today, Auckland), "practitioner"?: name (default all)}`.
     The run form in `/tasks/manage` already renders inputs from the JSON schema.
   - **Reuse:** the time helpers come from the shared module in step 2.
   - **Output `detail`:** one entry per practitioner, sorted:
     `{practitioner, items: [{start, end, minutes, kind: appointment|block, patient, status, procedures: [{description, codes, tooth}], category, notes}]}`.
     Cancelled appointments are excluded but counted.
   - **Coverage:** `partial` with a note whenever:
     - a practitioner or patient name can't be resolved, or
     - a Firestore field is unavailable.

     A sheet missing notes must say so, not look complete.
   - **Synthetic tests:**
     - DST day
     - empty day
     - single practitioner vs all
     - an unresolved patient makes coverage partial
     - blocks interleaved with appointments
4. **Install and run** on staging through `/tasks/manage`. Compare one practitioner's day by
   hand against the Principle timeline and appointment cards.

**Privacy:** run results will hold patient names and notes in local SQLite, visible to signed-in
staff, who already see them in Principle. Only synthetic data goes into admin_scripts.

## Stage 2: draw it (next task, outline only)
**Generic, in this repository.** A run may return a self-contained `printable` HTML string. The
run page shows a **Print** link to `/runs/{id}/print`, which serves that HTML:
- behind the existing `AccessControl`
- with CSP `sandbox; default-src 'none'; style-src 'unsafe-inline'`, so no script runs.

This is a few lines of code plus tests. **Layout belongs to the report** (admin_scripts), which
renders its own `detail` to HTML.

**A4 design to settle in stage 2, with a printed test page:**
- **Page.** Portrait, `@page { size: A4; margin: 10mm }`, giving 190×277 mm usable.
- **Header.** About 12 mm: practitioner, day and date, printed time, and the coverage warning if any.
- **Grid.** About 260 mm for the working day. Working hours 8:00–17:30 = 570 min gives about
  0.45 mm/min, so a 15-min appointment is about 6.8 mm (two lines at 9 pt).
- **Columns.** A 12 mm time column, then the full remaining width for blocks: about 175 mm, or
  roughly 90 characters a line at 10 pt.
- **Type.** 10 pt body, 8 pt floor, pure black.
  - Line 1: start–end, name, status as a word.
  - Then procedures, then notes.
  - 0.5 pt solid borders; breaks hatched or labelled.
- **Overflow.** If a block's text won't fit, shrink it to 8 pt. If it still won't fit, the
  block extends into free time below, or the sheet flags it. It never clips silently.
- **Print all.** One practitioner per page (`break-after: page`), printed as one job.

## Concerns
- **Line budget.** Application code is 2761/2000 on `main` and `hermetic` is failing. The
  stage 2 app change can't merge until that is resolved. Stage 1 lives in admin_scripts, which
  has its own CI, and the docs-only PR doesn't add code.
- **Production Firestore isn't configured.** Production credentials don't exist and the
  production build hasn't been checked. Notes, types and blocks in production depend on that.

## Verification
- **Stage 1.**
  - Run `python -m unittest discover -s tasks/day_sheet -p test_task.py` in admin_scripts;
    its CI must be green.
  - Install the task and run it on staging for a populated day.
  - Compare the detail field by field with Principle's timeline and appointment cards for one
    practitioner.
- **Stage 2.** Print to PDF and paper on A4, for a light day and the busiest staging day.
  Check readability at arm's length and that nothing is clipped.
