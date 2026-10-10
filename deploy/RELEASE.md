# Releasing to production

How the application gets onto the practice server, first time and after. Each step says how to
check it worked; don't move on from a step whose check fails. Then sign off
[ACCEPTANCE.md](ACCEPTANCE.md).

This is the one file of production install steps for every workstream; see AGENTS.md.

Record every change these steps make to a practice computer (software installed, accounts,
services, scheduled tasks, firewall rules, settings) in the practice's computer setup guide, in
Google Docs, as you make it. The guide is the record of what each machine has. Its link stays out
of this public repository.

The server runs two Windows services and one scheduled task:

| What | Where | Runs as |
| --- | --- | --- |
| `dental-practice-admin` service: uvicorn on `127.0.0.1:8080`, [dental-practice-admin.xml](dental-practice-admin.xml) | `C:\Program Files\DentalPracticeAdmin` (the release) | the service account |
| `caddy` service: HTTPS for both public names, [caddy-service.xml](caddy-service.xml), [Caddyfile](Caddyfile) | `C:\Program Files\Caddy` | its own virtual account, `NT SERVICE\caddy` |
| `\Massey Smiles Admin\Task runner`: the five-minute launcher, [task-runner.xml](task-runner.xml) | Task Scheduler | the service account |

Runtime data lives in `C:\ProgramData\DentalPracticeAdmin` and `C:\ProgramData\Caddy`, never in
the release, because a release directory is replaced wholesale. Both hold secrets (patient data,
Principle's logged-in browser session, TLS keys), so neither may inherit ProgramData's
permissions, which let every local user read them.

The commands below use `massey-admin` for the service account; substitute your choice.

## First release

### 1. Decide and gather

- **Service account.** One local account for the service and the launcher, with a long
  password. `scripts\verify.ps1` refuses `LocalSystem`.
- **Access** to the DNS for `massey-smiles.co.nz`, the router, Google Cloud (the OAuth client),
  the OpenAI platform (ChatKit domains), GitHub (`massey-reception-coder/admin_scripts`), and
  Akahu (the bank feed).
- **A quiet hour** for step 8, which moves both public names from reception to the server.
- **Reception's LAN address.** Give reception a DHCP reservation for `192.168.192.125`. The
  server's firewall admits reception's SMS check by that address, and a renumbered reception
  would see a false SMS warning every hour.
- **The server's LAN address.** Give the server a DHCP reservation for
  `192.168.192.30`. The router's forward for ports 80 and 443, the bridge's listen address and
  reception's SMS check all name it.

### 2. Prepare the release on the development machine

At the commit to release, run `scripts\release_gate.ps1`. It must end "Release checks passed".
Run it in the development checkout: its staging tier compares the fake with recordings of
staging, which `scripts/record_principle_wire.py` keeps in `tests\recordings` outside version
control, and a fresh clone fails that tier without them.

### 3. Accounts and folders on the server

1. Create the service account. In Local Security Policy, grant it **Log on as a service** (for
   the service) and **Log on as a batch job** (for the launcher).
2. Create the data folders with only the access they need:

   ```powershell
   New-Item -ItemType Directory C:\ProgramData\DentalPracticeAdmin, C:\ProgramData\Caddy\logs, C:\ProgramData\uv
   icacls C:\ProgramData\DentalPracticeAdmin /inheritance:r /grant:r 'SYSTEM:(OI)(CI)F' 'Administrators:(OI)(CI)F' 'massey-admin:(OI)(CI)M'
   icacls C:\ProgramData\uv /inheritance:r /grant:r 'SYSTEM:(OI)(CI)F' 'Administrators:(OI)(CI)F' 'massey-admin:(OI)(CI)RX'
   ```

### 4. Software on the server

1. Install uv, Node.js (LTS), Git for Windows and Caddy (`caddy.exe` in
   `C:\Program Files\Caddy`). Download a WinSW 2.x executable. Chat and Reports & scripts save
   task drafts through `git`, and fail without it on the service account's `PATH`.
2. Set, once for the machine, where uv gets Python and how it installs packages. By default
   it uses any Python already installed, possibly a per-user one, or puts its own in the
   installing user's `%AppData%`, and it hardlinks packages from that user's cache, carrying its
   permissions. The service account can read none of those.

   ```powershell
   [Environment]::SetEnvironmentVariable('UV_PYTHON_PREFERENCE', 'only-managed', 'Machine')
   [Environment]::SetEnvironmentVariable('UV_PYTHON_INSTALL_DIR', 'C:\ProgramData\uv\python', 'Machine')
   [Environment]::SetEnvironmentVariable('UV_LINK_MODE', 'copy', 'Machine')
   ```
3. **Reboot.** Services see the machine `PATH` the Node and Git installers changed, and the variables
   above, only after one.

**Check:** in a new PowerShell, `node --version`, `uv --version`, `git --version` and
`& 'C:\Program Files\Caddy\caddy.exe' version` all answer.

### 5. The release directory

1. On the development machine, export exactly the released commit, which carries no `.env`,
   `.venv` or other local files: `git archive --format=zip -o release.zip <commit>`. Extract it
   on the server to `C:\Program Files\DentalPracticeAdmin`.
2. In that directory: `uv sync --locked`, then `npm ci`.
3. As the service account (`runas /user:massey-admin powershell`), in that directory:
   `node node_modules/@playwright/mcp/cli.js install-browser chrome-for-testing`. The browser
   installs into that account's profile, which is where the automation looks for it.

### 6. Settings

1. Google Cloud, OAuth client: add the authorised redirect URI
   `https://admin.massey-smiles.co.nz/auth/callback`.
2. OpenAI platform: register `admin.massey-smiles.co.nz` for ChatKit. Its key goes in
   `ADMIN_CHATKIT_DOMAIN_KEY` below.
3. Create `.env` in the release directory. Both the service and the launcher read it, from
   their working directory, and nothing else configures them. `Settings` in
   `src/dental_practice_admin/config.py` is the authority on what is required; the check below
   names anything missing.

   Keep each comment on its own line. A comment after an empty value becomes the value:
   `ADMIN_PUBLIC_BASE_URL=   # empty` sets it to `# empty`, and Google sign-in then fails.

   ```dotenv
   # Without these, both would run against staging, with a different database.
   PRINCIPLE_ENVIRONMENT=production
   ADMIN_DATA_ROOT=C:\ProgramData\DentalPracticeAdmin
   PRINCIPLE_API_BASE_URL_PROD=https://api.principle.dental
   ADMIN_PLAYWRIGHT_MCP_PATH=node_modules/@playwright/mcp/cli.js
   # Empty: each request's own origin, which Caddy forwards.
   ADMIN_PUBLIC_BASE_URL=""

   ADMIN_SIGN_IN=google
   ADMIN_GOOGLE_CLIENT_ID=...
   ADMIN_GOOGLE_CLIENT_SECRET=...
   # A long random string, new for production.
   ADMIN_SESSION_SECRET=...
   # One or both of these; "" for the one not used.
   ADMIN_STAFF_EMAILS=...
   ADMIN_STAFF_DOMAIN=massey-smiles.co.nz
   ADMIN_CHATKIT_DOMAIN_KEY=...
   OPENAI_API_KEY=...
   OPENAI_BASE_URL=https://api.openai.com/v1
   # A model /v1/models lists for that key.
   ADMIN_AGENT_MODEL=...

   PRINCIPLE_API_KEY_PROD=...
   PRINCIPLE_PRACTICE_ID_PROD=...
   PRINCIPLE_UI_EMAIL_PROD=...
   PRINCIPLE_UI_PASSWORD_PROD=...
   PRINCIPLE_FIREBASE_KEY_PROD=...
   PRINCIPLE_FIREBASE_PROJECT_PROD=...
   PRINCIPLE_FIRESTORE_ROOT_PROD=organisations/.../brands/...
   # The exact workspace option, e.g. "Massey Smiles Dental".
   PRINCIPLE_WORKSPACE_PROD=...
   PRINCIPLE_WORKSPACE_SLUG_PROD=massey-smiles

   AKAHU_APP_TOKEN=...
   AKAHU_BASE_URL=https://api.akahu.io/v1
   AKAHU_USER_TOKEN=...
   # With the Geocoding API enabled.
   ADMIN_GOOGLE_MAPS_API_KEY=...
   ADMIN_TASK_REPOSITORY=massey-reception-coder/admin_scripts
   # Contents and pull requests on that repository.
   ADMIN_GITHUB_TOKEN=...
   ```

4. Restrict `.env` to the service account and administrators. A file inherits its folder's
   permissions, and Program Files lets every local user read:

   ```powershell
   icacls 'C:\Program Files\DentalPracticeAdmin\.env' /inheritance:r /grant:r 'SYSTEM:F' 'Administrators:F' 'massey-admin:R'
   ```

**Check:** `scripts\check_settings.ps1 -ReleaseRoot 'C:\Program Files\DentalPracticeAdmin'`
prints `Ready: production C:\ProgramData\DentalPracticeAdmin\production`; otherwise it names
each setting `.env` lacks. Nothing has a value in the code, so every line of the template above
is needed. No value may still read `...`.

### 7. The application service

1. Copy [dental-practice-admin.xml](dental-practice-admin.xml) into the release directory, and
   the WinSW executable beside it, renamed `dental-practice-admin.exe`.
2. `dental-practice-admin.exe install`. In `services.msc`, set **Log On** to the service
   account, then start the service.

**Check:** `Invoke-RestMethod http://127.0.0.1:8080/health` reports `ok` and `production`.

### 8. Caddy, DNS and the router

This moves both public names from reception's own Caddy to the server's, and puts SMS
(SMS_Bridge, Call Centre and the paired phone) on the server. Reception's bridge is installed but
carries no traffic, so nothing moves from it; it is retired in step 12. Do it in the quiet hour.
Caddy can obtain certificates only once the router sends ports 80 and 443 to it, so it starts
after the switch, not before: a failed attempt backs off, and inbound SMS would wait on its
retry.

1. Copy [Caddyfile](Caddyfile) to `C:\ProgramData\Caddy\Caddyfile`. Check it with
   `& 'C:\Program Files\Caddy\caddy.exe' validate --config C:\ProgramData\Caddy\Caddyfile --adapter caddyfile`.
2. Copy [caddy-service.xml](caddy-service.xml) to `C:\Program Files\Caddy`, with the WinSW
   executable beside it renamed `caddy-service.exe`, and run `caddy-service.exe install`. Run it
   as the service's own virtual account, which exists once the service does and which only
   Caddy uses, and give that account alone its data. Don't start it yet.

   ```powershell
   sc.exe config caddy obj= "NT SERVICE\caddy"
   icacls C:\ProgramData\Caddy /inheritance:r /grant:r 'SYSTEM:(OI)(CI)F' 'Administrators:(OI)(CI)F' 'NT SERVICE\caddy:(OI)(CI)M'
   ```
3. Server firewall: allow inbound TCP 80 and 443, and inbound TCP 5170 from reception
   (`192.168.192.125`) only, for reception's SMS check. Disable any program-level allow rule for
   the bridge, which would otherwise open 5170 to the whole LAN.
4. SMS on the server, as [SMS_Bridge's PRODUCTION.md](https://github.com/corrin/SMS_Bridge/blob/master/PRODUCTION.md)
   describes. Nothing is built at the practice:
   1. On the development machine, which has the Open Dental and JustPhone SDK libraries the build
      needs, publish SMS_Bridge's `master` self-contained, with PRODUCTION.md's
      `dotnet publish` command. Copy the whole output to the server's
      `C:\Program Files\SMS_Bridge`.
   2. Write the server's `C:\ProgramData\SMS_Bridge\install-settings.json` new, from
      PRODUCTION.md's example: a new random `BRIDGE_API_KEY`, `SmsSettings.EnableDebugMode`
      `false`, Principle's production practice ID, API key and `WEBHOOK_SECRET`, and
      `Hosting.ListenUrl` `http://localhost:5170;http://192.168.192.30:5170`. With debug mode
      on, every patient text goes to the test phone and `/smsgateway/test/test-patient-lookup`
      returns patient identifiers.
   3. Set up Call Centre under an account that signs in automatically, pair the phone with it,
      and start the bridge at that account's logon, as PRODUCTION.md's "Automatic startup"
      describes.

   **Check:** on the server,
   `curl.exe -H "X-API-Key: <the new key>" http://localhost:5170/smsgateway/debug-status`
   reports `isDebugMode` false, and `http://localhost:5170/smsgateway/phone-status` reports
   `"up": true`.
5. DNS: an A record `admin.massey-smiles.co.nz` for the practice's public address, the same
   address `office.massey-smiles.co.nz` already uses.
6. Together, with nothing in between: stop and disable Caddy on reception
   (`sc.exe stop caddy`, then `sc.exe config caddy start= disabled`; in PowerShell `sc` means
   `Set-Content`); stop the bridge on reception and disable its startup task; switch the
   router's forward for TCP 80 and 443 to the server; and start the server's `caddy` service.
   Watch `C:\ProgramData\Caddy\logs` until it has obtained both certificates.
7. Point Principle's webhook at `https://office.massey-smiles.co.nz/smsgateway/webhooks/principle`.
8. Register the SMS check on reception, as PRODUCTION.md's "SMS warning on reception"
   describes, with `-Url http://192.168.192.30:5170/smsgateway/phone-status`. It warns
   reception within 5 minutes of texts stopping.

**Check, without sending an SMS:** from outside the practice network (a phone off Wi-Fi),
`https://admin.massey-smiles.co.nz` shows the Google sign-in, and
`https://office.massey-smiles.co.nz/smsgateway/debug-status` answers 401 without the key and 200
with `isDebugMode` false given `X-API-Key: <the new key>`, both with valid certificates. A 502
means Caddy can't reach the bridge: read the server's Caddy log. Never probe
`/smsgateway/test/check-send-sms`; it sends a real SMS, and a real test message is the owner's
call. From a LAN machine other than reception, port 5170 on the server times out. Reception's SMS
check passes PRODUCTION.md's acceptance check for it. On reception, `sc.exe qc caddy` shows
`DISABLED`, `sc.exe query caddy` shows `STOPPED`, and
`Get-NetTCPConnection -LocalPort 5170 -State Listen` finds nothing.

**To undo:** point the router back at reception, then set reception's Caddy service back to
Automatic (a disabled service can't be started) and start it. Reception's bridge carries no
traffic, so turn Principle's webhook off until step 8 is done again.

### 9. The launcher

1. If the old `\Massey Smiles Admin\Daily diary` task exists, disable and remove it.
2. Register the launcher under the service account:
   `schtasks /Create /XML deploy\task-runner.xml /TN "\Massey Smiles Admin\Task runner" /RU massey-admin /RP *`

**Check:** after five minutes, run `scripts\verify.ps1` from the release directory. Every line
must pass.

### 10. Practice tasks and schedules

1. Sign in at `https://admin.massey-smiles.co.nz` and open **Reports & scripts**. Install each
   reviewed task from its merged `admin_scripts` pull request, for example the contact-details
   clean-up from PR 7 and the day sheet from PR 8. Run the day sheet for today and print it on a
   surgery printer.
2. Run each task once by hand and read its result. Then set **Run automatically** for the ones
   that run on a schedule.
3. The contact-details clean-up has its own rollout in
   [the plan](../docs/plans/clean-contact-details.md): a dry run the owner approves, then
   `apply` with `limit` 5, a check that nobody was messaged, the rest in batches (`limit` 500), and
   then a daily schedule with `apply`.

### 11. Sign off

Work through [ACCEPTANCE.md](ACCEPTANCE.md): the reboot, an unattended scheduled run, staff access
from outside, and the restore drill. Record the release in its table.

### 12. Retire SMS on reception

Once the release is signed off and the undo in step 8 is no longer wanted, remove the bridge and
Caddy from reception. They hold the TLS keys for `office`, and reception's
`install-settings.json` may hold Principle credentials, none of which reception needs. After this,
step 8 can't be undone.

1. Find the old bridge's startup task with
   `Get-ScheduledTask | Where-Object { $_.Actions.Execute -match 'SMS_Bridge' }`, delete it with
   `Unregister-ScheduledTask`, then delete the installation directory its action names.
2. Stop Call Centre starting at logon, in Settings > Apps > Startup or as a logon task in Task
   Scheduler, unless reception uses it for calls.
3. `sc.exe delete caddy`, then delete `C:\Program Files\Caddy` and Caddy's data directory, which
   holds the certificates.
4. Delete everything in `C:\ProgramData\SMS_Bridge` except `check-sms.ps1`, which reception's
   SMS check runs.
5. Remove any firewall rule on reception that admits port 5170, 80 or 443.

**Check:** on reception, `sc.exe query caddy` reports that the service does not exist,
`Get-NetTCPConnection -LocalPort 5170,80,443 -State Listen` finds nothing, and
`C:\ProgramData\SMS_Bridge` holds only `check-sms.ps1`. Reception's SMS check still passes
PRODUCTION.md's acceptance check for it.

## Later releases

1. Run `scripts\release_gate.ps1` at the new commit, on the development machine.
2. Read the release's pull requests for new required settings, and list what changed under
   `deploy\`: `git diff --stat <released commit>..<new commit> -- deploy`.
3. Stop the launcher and wait for any run to finish, so no task is cut off mid-write or started
   from a half-installed release:
   `Disable-ScheduledTask -TaskPath '\Massey Smiles Admin\' -TaskName 'Task runner'`, then
   repeat `Get-ScheduledTask -TaskPath '\Massey Smiles Admin\' -TaskName 'Task runner'` until
   its State is not `Running`.
4. Make sure nobody has a run going from Reports & scripts or chat either: stopping the service
   ends those mid-write. The Results page shows any run still in progress; in the release
   directory this prints how many there are, which must be 0:
   `.venv\Scripts\python.exe -c "from dental_practice_admin.config import Settings; from dental_practice_admin.storage import Storage; print(Storage(Settings().database_path).db.execute('SELECT count(*) FROM task_runs WHERE outcome = ?', ('running',)).fetchone()[0])"`
   A run can stay marked in progress after a crash. If Task Manager shows no `python.exe`
   running as the service account other than the service itself, nothing is really running.
5. Stop the `dental-practice-admin` service. Delete `DentalPracticeAdmin-previous` if it exists
   (one kept release is enough, and each holds a copy of `.env`), rename the release directory to
   `DentalPracticeAdmin-previous`, and extract the new release in its place as in step 5.1.
6. Copy `.env` and `dental-practice-admin.exe` across with their permissions, so `.env` is never
   readable by everyone, then add any new settings to `.env`:
   `robocopy 'C:\Program Files\DentalPracticeAdmin-previous' 'C:\Program Files\DentalPracticeAdmin' .env dental-practice-admin.exe /COPY:DATS`.
   Copy `deploy\dental-practice-admin.xml` from the new release. If it changed (step 2), WinSW
   applies its start and failure settings only at install: `dental-practice-admin.exe uninstall`,
   `dental-practice-admin.exe install`, and set **Log On** again as in step 7.2.
7. Run `uv sync --locked` and `npm ci`, then repeat step 5.3 as the service account, in case the
   release moved Playwright to a new browser. Then run step 6's check of the first release,
   `scripts\check_settings.ps1 -ReleaseRoot 'C:\Program Files\DentalPracticeAdmin'`, so a
   missing setting shows before the service starts rather than as a service that stops.
8. If step 2 listed other `deploy\` files, apply them:
   - `Caddyfile`: copy it to `C:\ProgramData\Caddy\Caddyfile`, validate as in step 8.1, and
     restart the `caddy` service.
   - `caddy-service.xml`: copy it to `C:\Program Files\Caddy`, then uninstall and install as in
     step 8.2, including the account.
   - `task-runner.xml`: re-register it as in step 9.2, adding `/F` to replace the existing task.
9. Start the service and enable the launcher
   (`Enable-ScheduledTask -TaskPath '\Massey Smiles Admin\' -TaskName 'Task runner'`). Wait five
   minutes, then run `scripts\verify.ps1`.
10. **To roll back:** stop the launcher and the service as in steps 3 to 5. Rename the release
    directory to `DentalPracticeAdmin-failed`, and `DentalPracticeAdmin-previous` to
    `DentalPracticeAdmin`. If steps 6 or 8 applied changed `deploy\` files, apply the previous
    release's versions the same way. Then start the service, enable the launcher and verify as
    in step 9.

Installed practice tasks and their schedules live in the data directory and survive a release.

## Back up

`C:\ProgramData\DentalPracticeAdmin` (database, installed tasks, audits), `C:\ProgramData\Caddy`
(certificates), and the release's `.env`. Take a consistent copy of the SQLite database rather
than copying the file while the service writes it.
