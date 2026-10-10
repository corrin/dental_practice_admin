# Patient phone number cleaner: requirements

Requirements only. The session that builds this investigates the code, the data and the
design itself.

## Why

On 2026-10-10 one patient's record made `getPatient` fail. Their second contact number
doesn't match the pattern in Principle's own API specification, `^\+?\d{6,15}$`. The app's
client rejects any response that breaks the specification, so that one number stopped the
day sheet for every practitioner.

The number was in Principle's data before the app read it. Following ADR 0002, malformed
data is fixed where it is stored, not worked around by every reader. The fix is to clean the
numbers, the way the Open Dental cleaner did for Open Dental. Ask the owner where that
cleaner is, and follow its approach and rules where they apply.

## Requirements

1. **Find every affected patient.** List every patient in the practice with a contact number
   that breaks the specification.
   - The search can't depend on `getPatient` succeeding, because the affected records are
     the ones it fails on.
   - The list says, for each number: which number on the patient it is (its type and
     position), why it fails, and the proposed clean value.
2. **Propose clean values, and never guess.**
   - A number that can be cleaned without doubt, such as one with only spacing or
     punctuation to remove, gets a proposed value.
   - A number whose meaning is unclear gets no proposal. Examples: too few digits, two
     numbers in one field, or text in the field. It is listed for staff to fix by hand.
   - Each number keeps its type and its position among the patient's numbers.
   - The clean form is the one the Open Dental cleaner used, unless it fails the
     specification.
3. **Nothing is written without the owner's approval.**
   - A dry run is the default. It shows the before and after for each change.
   - The owner approves the list, then the writes run.
4. **Write only through the REST API.** Never write Firestore directly. See
   `docs/principle/README.md`.
5. **Prove it on staging first.** Production is written only after staging shows all of
   these:
   - the write changes only the intended number
   - the patient's other fields are unchanged
   - `getPatient` now passes the specification
   - **no patient-facing message is triggered.** Changing a phone number must not send an
     SMS, an email or a reminder. If it would, stop and tell the owner.
6. **Verify every production write by reading it back.** A write whose read-back doesn't
   pass the specification is reported, never counted as done.
7. **Keep patient data out of the repository.** It's public. No names, numbers or patient
   IDs go in commits, the pull request, docs or test fixtures. Tests use synthetic data
   only.
8. **It can be run again.** New bad numbers can come in through the website and the API
   later, so the scan can be repeated. When nothing needs cleaning, it says so.
9. **Clear the interface warning.** The failure recorded a `getPatient / response_schema`
   interface warning. It is cleared once production is clean and `getPatient` passes for
   every patient.

## Decisions for the session to make and explain

- Whether this belongs in the app or as a task in the practice repository
  (`admin_scripts`). See ADR 0005, "Promote working scripts".
- How the session finds affected patients without `getPatient`.
- Whether bad numbers should be caught on entry from now on. Raise this with the owner
  rather than building it.

## Done

- Production has no patient with a contact number that breaks the specification, except
  those listed for staff to fix by hand, which the owner has received.
- The day sheet task (`admin_scripts` PR #5) runs for 2026-11-16 against production without
  a `getPatient` failure.
- `docs/principle/` records what was learned, such as how phone numbers are stored and what
  a write changes, with no patient data.
- Merged to `main`, following `AGENTS.md`.

## Not in scope

- Email addresses, postal addresses and other patient fields.
- Changing how strictly the client validates responses. It is right to reject this data.
