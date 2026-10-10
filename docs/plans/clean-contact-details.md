# Clean patient contact details: requirements

Keep patients' contact details in Principle clean, now and from now on. Contact details are
phone numbers, postal addresses, email addresses, and any other contact field Principle holds.

This document says what is needed. The session that builds it does the investigation and the
design.

## Why

On 2026-10-10, one patient's second phone number broke the format in Principle's own API
specification, `^\+?\d{6,15}$`. The app rejects data that breaks the specification, so that one record
stopped the day sheet for every practitioner.

Bad contact details come from the Open Dental migration, and they can come back whenever
anyone types a new one into Principle. The Open Dental cleaner fixed this kind of problem in
Open Dental. The owner will say where it is. Its rules are a starting point.

## Requirements

### Clean now

1. **Every patient is checked.** Every active and inactive patient in the practice, and
   every contact field on each one.
2. **Each problem is fixed or flagged, never guessed at.**
   - A detail that can be cleaned without doubt is fixed. Two examples:
     - "021 123 4567" becomes the practice's standard form.
     - An email address with stray spaces has them removed.
   - A detail whose meaning is unclear is listed for staff to fix by hand. Examples:
     - too few digits
     - two numbers in one field
     - "ring mum" written in a phone field
     - an address that can't be matched to a real one
   - A cleaned detail keeps its type, for example mobile or home, and its position among the
     patient's details.
3. **The standard forms are the owner's decision.** Before building, show the owner worked
   examples, including an edge case, and get approval. Two of the open questions:
   - national "021…" or international "+6421…"
   - how an address is laid out

### Keep it clean

4. **New bad data is found within a day.** A regular check finds any contact detail that
   has gone bad since the last check, however it got in: the website, the API, or anything
   else.
5. **Staff hear about what needs fixing.** Unclear details reach staff as a short list
   they'll actually see. A report that no one opens doesn't count.
6. **When nothing needs fixing, the check says so.** Silence must never be what a working
   check and a broken check have in common. A check that fails to run is noticed.

### Never break the practice

7. **One bad record never stops a report or task.** It makes that result partial, with the
   record identified, and everything else still works. The app goes on detecting
   specification breaks rather than quietly accepting them.
8. **Changing a detail never contacts the patient.** No SMS, email or reminder is sent
   because a detail was cleaned. Prove this on staging before production is touched.
9. **Writes go through the REST API only, never Firestore.** Each write changes only the
   intended field. Each is read back, and a read-back that doesn't match is reported, not
   counted as done.
10. **Patient records change only with approval.** The owner approves the first clean-up
    as a before-and-after list. For the regular check, the owner decides whether
    unambiguous fixes go ahead automatically or wait for approval.
11. **No patient data in the repository.** It's public, so tests use synthetic data.
12. **No patient data leaves the practice without the owner's approval.** Checking addresses
    against an outside service, for example, is the owner's decision.

## Done

- The owner has approved the standard forms.
- Production holds no contact detail that breaks the specification or the standard forms.
  The only exceptions are the unclear ones, and those are with staff.
- The regular check is running, and has reported at least once.
- The day sheet (`admin_scripts` PR #5) runs for 2026-11-16 against production without a
  failure.
- A synthetic test shows that a patient whose contact detail breaks the specification makes
  the day sheet partial, with that record named, and does not stop it.
- The `getPatient / response_schema` interface warning that this record raised is cleared.
- `docs/principle/` records what was learned, with no patient data.
- Merged to `main`, following `AGENTS.md`.

## Decisions

Taken by the owner on 2026-10-10, from a read of every production patient.

- **Phones** are stored as `+64` and the number without its leading 0. Seven digits with no
  area code are Auckland (`+649…`), as the Open Dental cleaner did. Placeholders such as
  `N/A` and `-` are removed. A note or extension typed after a number moves into the label.
  Labels are lower case. A blank label is inferred from the number: `+642…` is `mobile`,
  a landline is `home`, and `+64800…` or `+64508…` is `work`.
- **Addresses** use the layout Principle's verified-address picker saves, which is NZ Post
  order (town or city, then postcode). They may be matched with Google's Geocoding API,
  accepting only exact New Zealand matches. Only the address string is sent: no name, identifier or other detail.
  Overseas addresses are left alone.
- **Staff** hear through this application's pages, and only about active patients whose
  details can't be fixed from evidence. Reception's list stays as short as possible.
- **The daily check** applies the approved rules without waiting. The first clean-up waits for
  the owner's approval of its before-and-after list.

## Implementation

**This repository.**
- A response that breaks the specification only inside named patient records raises an error
  naming them, instead of rejecting the whole response. The scope check and the pre-read
  before a write both tolerate it, so the cleaner can fix exactly the records that break.
  The break is still recorded as an interface warning. A write replaces only the target field
  of the record as read, and only if that field still holds the value the change was computed
  from.
- A task result can carry a list for staff. The run page shows it as a table, and every page
  shows a banner while the latest run of a scheduled task has such a list, failed, or is
  overdue. The failed and overdue cases are requirement 6; they apply to every scheduled task
  because nothing makes this one different.
- A required Google Maps key setting. A missing key stops the application at startup. A
  rejected key or exhausted quota leaves addresses unchanged and makes the run partial, which
  the banner shows.

**`admin_scripts`.**
- The day sheet names the patient whose record could not be read.
- A `clean_contact_details` task reads every patient from Firestore, applies the rules, and
  either lists the changes (dry run) or writes each through `updatePatient` and reads it back.
  A read-back that doesn't match, or a write that fails, is listed and makes the run partial,
  which the banner shows. It is not counted as done.

**Rollout.** Prove on staging that a write changes only its field and contacts nobody. Run a
production dry run for the owner's approval. Apply to five patients and check nothing was
sent, then to all. Schedule daily.
