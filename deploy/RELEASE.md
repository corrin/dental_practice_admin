# Releasing to production

How the application gets onto the practice server, first time and after. Each step says how to
check it worked; don't move on from a step whose check fails. Then sign off
[ACCEPTANCE.md](ACCEPTANCE.md).

This is the one file of production install steps for every workstream; see AGENTS.md.

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

1. Install uv, Node.js (LTS) and Caddy (`caddy.exe` in `C:\Program Files\Caddy`). Download a
   WinSW 2.x executable.
2. Set, once for the machine, where uv gets Python and how it installs packages. By default
   it uses any Python already installed, possibly a per-user one, or puts its own in the
   installing user's `%AppData%`, and it hardlinks packages from that user's cache, carrying its
   permissions. The service account can read none of those.

   ```powershell
   [Environment]::SetEnvironmentVariable('UV_PYTHON_PREFERENCE', 'only-managed', 'Machine')
   [Environment]::SetEnvironmentVariable('UV_PYTHON_INSTALL_DIR', 'C:\ProgramData\uv\python', 'Machine')
   [Environment]::SetEnvironmentVariable('UV_LINK_MODE', 'copy', 'Machine')
   ```
3. **Reboot.** Services see the machine `PATH` the Node installer changed, and the variables
   above, only after one.

**Check:** in a new PowerShell, `node --version`, `uv --version` and
`& 'C:\Program Files\Caddy\caddy.exe' version` all answer.

### 5. The release directory

1. On the development machine, export exactly the released commit, which carries no `.env`,
   `.venv` or other local files: `git archive --format=zip -o release.zip <commit>`. Extract it
   on the server to `C:\Program Files\DentalPracticeAdmin`.
2. In that directory: `uv sync --locked`, then `npm ci`.
3. As the service account (`runas /user:massey-admin powershell`), in that directory:
   `node node_modules/@playwright/mcp/cli.js install-browser chrome-for-testing`. The browser
   installs into that account's profile, which is where the automation looks for it.
4. Create `.env` in the release directory. Both the service and the launcher read it, from
   their working directory, and nothing else configures them. `Settings` in
   `src/dental_practice_admin/config.py` is the authority on what is required; the check below
   names anything missing.

   ```dotenv
   # Without these, both would run against staging, with a different database.
   PRINCIPLE_ENVIRONMENT=production
   ADMIN_DATA_ROOT=C:\ProgramData\DentalPracticeAdmin

   ADMIN_SIGN_IN=google
   ADMIN_GOOGLE_CLIENT_ID=...
   ADMIN_GOOGLE_CLIENT_SECRET=...
   ADMIN_SESSION_SECRET=...          # long random string, new for production
   ADMIN_STAFF_EMAILS=...            # and/or ADMIN_STAFF_DOMAIN=massey-smiles.co.nz
   ADMIN_CHATKIT_DOMAIN_KEY=...      # registered for admin.massey-smiles.co.nz (step 6)
   OPENAI_API_KEY=...

   PRINCIPLE_API_KEY_PROD=...
   PRINCIPLE_PRACTICE_ID_PROD=...
   PRINCIPLE_UI_EMAIL_PROD=...
   PRINCIPLE_UI_PASSWORD_PROD=...
   PRINCIPLE_FIREBASE_KEY_PROD=...
   PRINCIPLE_FIREBASE_PROJECT_PROD=...
   PRINCIPLE_FIRESTORE_ROOT_PROD=organisations/.../brands/...
   PRINCIPLE_WORKSPACE_PROD=...      # the exact workspace option, e.g. "Massey Smiles Dental"
   PRINCIPLE_WORKSPACE_SLUG_PROD=massey-smiles

   AKAHU_APP_TOKEN=...
   AKAHU_USER_TOKEN=...
   ADMIN_GOOGLE_MAPS_API_KEY=...     # Geocoding API enabled
   ADMIN_TASK_REPOSITORY=massey-reception-coder/admin_scripts
   ADMIN_GITHUB_TOKEN=...            # contents and pull requests on that repository
   ```

5. Restrict `.env` to the service account and administrators. A file inherits its folder's
   permissions, and Program Files lets every local user read:

   ```powershell
   icacls 'C:\Program Files\DentalPracticeAdmin\.env' /inheritance:r /grant:r 'SYSTEM:F' 'Administrators:F' 'massey-admin:R'
   ```

**Check:** in the release directory,
`.venv\Scripts\python.exe -c "from dental_practice_admin.config import Settings; s = Settings(); s.require_web_configured(); print(s.environment, s.data_dir)"`
prints `production C:\ProgramData\DentalPracticeAdmin\production`.

### 6. Google sign-in and ChatKit

1. Google Cloud, OAuth client: add the authorised redirect URI
   `https://admin.massey-smiles.co.nz/auth/callback`.
2. OpenAI platform: register `admin.massey-smiles.co.nz` for ChatKit, and put its key in
   `ADMIN_CHATKIT_DOMAIN_KEY`.

### 7. The application service

1. Copy [dental-practice-admin.xml](dental-practice-admin.xml) into the release directory, and
   the WinSW executable beside it, renamed `dental-practice-admin.exe`.
2. `dental-practice-admin.exe install`. In `services.msc`, set **Log On** to the service
   account, then start the service.

**Check:** `Invoke-RestMethod http://127.0.0.1:8080/health` reports `ok` and `production`.

### 8. Caddy, DNS and the router

This moves both public names from reception's own Caddy to the server's. Do it in the quiet
hour. The SMS bridge is dormant, so `office` answers 503 until [Reactivating SMS](#reactivating-sms)
is done. Caddy can obtain certificates only once the router sends ports 80 and 443 to it, so it
starts after the switch, not before: a failed attempt backs off, and staff would wait on its
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
3. Server firewall: allow inbound TCP 80 and 443.
4. DNS: an A record `admin.massey-smiles.co.nz` for the practice's public address, the same
   address `office.massey-smiles.co.nz` already uses.
5. Together, with nothing in between: stop and disable Caddy on reception
   (`sc.exe stop caddy`, then `sc.exe config caddy start= disabled`; in PowerShell `sc` means
   `Set-Content`), switch the router's forward for TCP 80 and 443 to the server, and start the
   server's `caddy` service. Watch `C:\ProgramData\Caddy\logs` until it has obtained both
   certificates. Leave reception's Caddyfile and certificates in place for the undo.

**Check:** from outside the practice network (a phone off Wi-Fi),
`https://admin.massey-smiles.co.nz` shows the Google sign-in and
`https://office.massey-smiles.co.nz` answers "SMS gateway inactive", both with valid
certificates. On reception, `sc.exe qc caddy` shows `DISABLED` and `sc.exe query caddy` shows
`STOPPED`. **To undo:** point the router back at reception, set
reception's Caddy service back to Automatic (a disabled service can't be started), and start
it. Reception's Caddy forwards `office` to the bridge, which with debug mode on serves patient
identifiers, so first confirm on reception that `netstat -ano | findstr :5170` prints nothing.
If it prints a listener, stop the bridge before starting Caddy.

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
   release moved Playwright to a new browser.
8. If step 2 listed other `deploy\` files, apply them:
   - `Caddyfile`: copy it to `C:\ProgramData\Caddy\Caddyfile`, validate as in step 8.1, and
     restart the `caddy` service.
   - `caddy-service.xml`: copy it to `C:\Program Files\Caddy`, then uninstall and install as in
     step 8.2, including the account.
   - `task-runner.xml`: re-register it as in step 9.2, adding `/F` to replace the existing task.
9. Start the service and enable the launcher
   (`Enable-ScheduledTask -TaskPath '\Massey Smiles Admin\' -TaskName 'Task runner'`). Wait five
   minutes, then run `scripts\verify.ps1`.
10. **To roll back:** do steps 3 to 5 with `DentalPracticeAdmin-previous` renamed back into
    place (keeping the failed release aside). If steps 6 or 8 applied changed `deploy\` files,
    apply the previous release's versions the same way. Start, enable, verify.

Installed practice tasks and their schedules live in the data directory and survive a release.

## Reactivating SMS

The SMS bridge is dormant, waiting on Principle's API, and `office` answers 503 meanwhile.
Before it carries traffic again:

1. Deploy SMS_Bridge's API-key fix, which requires the key on every `/smsgateway` route
   (corrin/SMS_Bridge#3).
2. Turn debug mode off in `C:\ProgramData\SMS_Bridge\install-settings.json`. While it is on,
   `/smsgateway/test/test-patient-lookup` returns patient identifiers to anyone, and
   `/smsgateway/test/check-send-sms` sends a real SMS. Never probe that one to test.
3. Decide where the bridge runs. JustRemotePhone lists "Windows Vista or newer" and says nothing of Windows Server, so
   before choosing the server, pair the phone with Call Centre there and leave it connected for
   a day.
   - **Reception.** Give it a DHCP reservation (192.168.192.125), since a renumbered machine
     silently stops SMS. Allow inbound TCP 5170 from the server's address only, and disable any
     program-level allow rule for the bridge, which would otherwise open 5170 to the whole LAN.
     The `office` upstream is `192.168.192.125:5170`.
   - **The server.** Call Centre is a desktop program the SDK drives, so the server needs a
     signed-in session at boot: automatic sign-in to a dedicated account, Call Centre started at
     logon, and the phone paired from there. Bind the bridge to `http://127.0.0.1:5170` (a
     change in SMS_Bridge's `Program.cs`). The `office` upstream is `127.0.0.1:5170`.
4. In [Caddyfile](Caddyfile), replace the `office` site's `respond` line with
   `reverse_proxy <upstream>`, carrying the same `header_up` as the `admin` site, and apply it as
   in Later releases step 8.
5. Add an alarm for the bridge's phone status. Texts that stop going out fail silently wherever
   the bridge runs.

**Check:** `https://office.massey-smiles.co.nz/smsgateway/gateway-status` with the API key
answers with SMS_Bridge's build, and a message sent from Principle to a staff mobile arrives.

## Back up

`C:\ProgramData\DentalPracticeAdmin` (database, installed tasks, audits), `C:\ProgramData\Caddy`
(certificates), and the release's `.env`. Take a consistent copy of the SQLite database rather
than copying the file while the service writes it.
