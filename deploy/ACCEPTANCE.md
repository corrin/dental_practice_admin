# Deployment acceptance

Automated checks cover what a machine can check. These are the ones a person signs off,
because they need a reboot, a second account, or a deliberate act of destruction.

Run `scripts\verify.ps1` first; it must pass before any of this is worth doing.

## Before staff use it

- [ ] **Reboot.** Restart the host. Without logging in, confirm the service came back
      (`scripts\verify.ps1`) and that the staff page loads from another machine on the
      practice network.
- [ ] **A scheduled run with nobody logged in.** After the reboot, leave the host at the
      logon screen over a scheduled trigger. Confirm the run is recorded and its summary is
      useful. This is the check that catches a task configured "run only when logged on".
- [ ] **The service account, not yours.** Confirm `verify.ps1` reports a designated account.
      A service running as your own login stops working the day the password changes.
- [ ] **Wrong-Principle check.** Confirm the health endpoint reports `production` and the
      staff page shows no fake banner.
- [ ] **VPN path.** From a machine connected the way staff connect, load the page and open a
      run. Confirm the scheduled task also reaches Principle under the service identity,
      which may route differently from an interactive session.

## Restore and rollback, proven once

- [ ] **Restore the database.** Take a backup, delete a run from a copy, restore, confirm the
      run is back. A backup nobody has restored is a hypothesis.
- [ ] **Reinstate the previous release.** Stop the service, swap the release directory back,
      start, run `verify.ps1`. Confirm runtime data under `C:\ProgramData\DentalPracticeAdmin`
      survived the swap untouched.

## Repeat quarterly

- [ ] Restore drill, as above.
- [ ] `uv run pytest -m integration` against staging, to catch Principle changing under us.
- [ ] `uv run python scripts/record_principle_wire.py`, then review the diff. A change in the
      recordings is Principle moving, and the fake must follow.

## Signed

| Date | Who | Release | Notes |
| --- | --- | --- | --- |
|  |  |  |  |
