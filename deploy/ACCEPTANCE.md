# Deployment acceptance

Automated checks cover what a machine can check. These are the ones a person signs off,
because they need a reboot, a second account, or a deliberate act of destruction.

Run `scripts\verify.ps1` first; it must pass before any of this is worth doing.

## Cutover: the server becomes the front door

Before cutover the router sends 80/443 to reception, whose Caddy (service `caddy`,
`C:\Program Files\Caddy\Caddyfile`, running as LocalSystem) fronts `office.massey-smiles.co.nz`.
Afterwards the server's Caddy runs [`Caddyfile`](Caddyfile) and fronts both names. Each step
names the check that proves it.

- [ ] **DNS.** Add an A record `admin.massey-smiles.co.nz`, with the same address as `office`.
      Check: `Resolve-DnsName admin.massey-smiles.co.nz` returns that address.
- [ ] **The application on the server.** Install the release, the WinSW service and the task
      runner as the README describes. Check: `scripts\verify.ps1` passes.
- [ ] **Caddy on the server.** Install stock Caddy as a service running this repository's
      `Caddyfile`, and allow inbound 80 and 443 in Windows Firewall. Check:
      `caddy validate --config <path> --adapter caddyfile`.
- [ ] **Router.** Forward 80 and 443 to the server instead of reception. Check: from outside the
      practice, `https://admin.massey-smiles.co.nz` presents a valid certificate and Google
      sign-in completes.
- [ ] **Retire reception's Caddy.** On reception: `sc.exe stop caddy`, then
      `sc.exe config caddy start= disabled`. Leave its Caddyfile and certificates in place for
      rollback. In PowerShell, type `sc.exe`, not `sc`, which means `Set-Content`.
- [ ] **The day sheet.** Install `day_sheet` through `/tasks/manage`, run it for today and print
      it on a surgery printer.

**Rollback.** Forward 80 and 443 back to reception, then on reception
`sc.exe config caddy start= auto` and `sc.exe start caddy`.

## Before reactivating SMS

The SMS bridge is dormant, waiting on Principle's API. Reactivating it is a separate change:

- [ ] Deploy SMS_Bridge's API-key fix, which requires the key on every `/smsgateway` route
      (corrin/SMS_Bridge#3).
- [ ] Turn debug mode off in `C:\ProgramData\SMS_Bridge\install-settings.json`. While it is on,
      `/smsgateway/test/test-patient-lookup` returns patient identifiers to anyone, and
      `/smsgateway/test/check-send-sms` sends a real SMS. Never probe that one to test.
- [ ] Decide where the bridge runs, and finish that location's arrangement:
  - **Reception.** Give it a DHCP reservation for 192.168.192.125. Allow 5170 from the server's
    address only, and disable any program-level allow rule for the bridge, which would otherwise
    open 5170 to the whole LAN.
  - **The server.** Call Centre is a desktop program the SDK drives, so the server needs a
    signed-in session at boot: automatic sign-in to a dedicated account, Call Centre started at
    logon, and the phone paired from there. Bind the bridge to `http://127.0.0.1:5170` (a
    change in SMS_Bridge's `Program.cs`) and change the `office` upstream here to
    `127.0.0.1:5170`.
- [ ] Add an alarm for the bridge's phone status. Texts that stop going out fail silently
      wherever the bridge runs.

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
