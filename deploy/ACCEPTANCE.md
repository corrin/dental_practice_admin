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
- [ ] **Local execution.** Install a reviewed task, disable GitHub access, then run it manually
      and through its application schedule. Confirm both results and local audits are available.
- [ ] **Clean review.** Create a synthetic task PR in the private practice repository. Confirm
      it contains only source, input contract and synthetic tests, then merge and install it.
- [ ] **Launcher migration.** Remove the old per-diary Windows task. Register `task-runner.xml`
      as `Massey Smiles Admin\Task runner` and verify its five-minute repetition.
- [ ] **The service account, not yours.** Confirm `verify.ps1` reports a designated account.
      Check the data directory permissions for that account and complete an actual scheduled run;
      an operator being able to write there does not prove the service account can.
- [ ] **Wrong-Principle check.** Confirm the health endpoint reports `production` and the
      staff page shows no fake banner.
- [ ] **Staff access.** From another machine, open the public HTTPS address, sign in with an
      approved Google account, and open a run. Confirm an anonymous browser cannot read a run.
      Confirm the scheduled task reaches Principle under its designated service identity.

## Restore and rollback, proven once

- [ ] **Restore the database.** Take a backup, delete a run from a copy, restore, confirm the
      run is back. A backup nobody has restored is a hypothesis.
- [ ] **Restore task files.** Restore installed revisions, local draft repositories, immutable
      source snapshots and audit files alongside the database. Verify a restored schedule runs.
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
