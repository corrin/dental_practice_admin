# Writing a patient's contact details

## Updating through the API

**What:** `updatePatient` (`PATCH /v1/patients/{patientId}`) takes the whole `PatientCreate`
body. `practiceId`, `name`, `dateOfBirth`, `gender` and `email` are required even when only a
contact field changes. The fields sent replace the stored ones. Contact numbers, address and
tags that are not sent are kept. Only the field changed and `updatedAt` differ afterwards.
**Gotchas:**
- A patient with no date of birth cannot be updated through the API: the PATCH returns 400
  `must have required property 'dateOfBirth'`. Staff fix those in the website.
- `email` is `format: email`, so this application's client refuses an empty one before sending.
  A patient with no email cannot be updated through the API either.
- `contactNumbers` is replaced as a whole list. Send every number, in order, to change one.
**Messages:** no SMS or email appears in the patient's `listSMSMessages` or `listEmails`
after changing their numbers or address.
**Verified:** [`scripts/patient_write_probe.py`](../../scripts/patient_write_probe.py) on staging
dummy patients, 2026-10-10, comparing every field `getPatient` returns. A comparison of the
whole Firestore document, which holds fields the API does not return, is still to do: the
staging automation login was rejected (`EMAIL_NOT_FOUND`) that day.

## The address the website saves

**What:** the website's verified-address picker saves NZ Post order with the country:
`street, suburb, city postcode, New Zealand`.
**Verified:** production, read only, 2026-10-10. Addresses of that shape appear at about one a
month until September 2026, then 28 in September and 7 in early October, while new addresses
of the Open Dental migration's shape (`street, suburb, city, postcode`) fall away. Saving one
through the picker on staging is still to do, for the same login reason.
