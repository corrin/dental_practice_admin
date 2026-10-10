# Releasing to production

How the application gets onto the practice server, first time and after. Each step says how to
check it worked; don't move on from a step whose check fails. Then sign off
[ACCEPTANCE.md](ACCEPTANCE.md).

The server runs two Windows services and one scheduled task:

| What | Where | Runs as |
| --- | --- | --- |
| `dental-practice-admin` service: uvicorn on `127.0.0.1:8080`, [dental-practice-admin.xml](dental-practice-admin.xml) | `C:\Program Files\DentalPracticeAdmin` (the release) | the service account |
| `caddy` service: HTTPS for both public names, [caddy-service.xml](caddy-service.xml), [Caddyfile](Caddyfile) | `C:\Program Files\Caddy` | Local Service |
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
- **Reception's LAN address.** Give the reception machine a DHCP reservation. Caddy forwards
  SMS_Bridge traffic to it, and a renumbered machine silently stops SMS from arriving.
- **Access** to the DNS for `massey-smiles.co.nz`, the router, Google Cloud (the OAuth client),
  the OpenAI platform (ChatKit domains), GitHub (`massey-reception-coder/admin_scripts`), and
  Akahu (the bank feed).
- **A quiet hour** for step 8, which moves inbound SMS from reception to the server.

### 2. Prepare the release on the development machine

At the commit to release, run `scripts\release_gate.ps1`. It must end "Release checks passed".

### 3. Accounts and folders on the server

1. Create the service account. In Local Security Policy, grant it **Log on as a service** (for
   the service) and **Log on as a batch job** (for the launcher).
2. Create the data folders with only the access they need:

   ```powershell
   New-Item -ItemType Directory C:\ProgramData\DentalPracticeAdmin, C:\ProgramData\Caddy\logs, C:\ProgramData\uv
   icacls C:\ProgramData\DentalPracticeAdmin /inheritance:r /grant:r 'SYSTEM:(OI)(CI)F' 'Administrators:(OI)(CI)F' 'massey-admin:(OI)(CI)M'
   icacls C:\ProgramData\Caddy /inheritance:r /grant:r 'SYSTEM:(OI)(CI)F' 'Administrators:(OI)(CI)F' 'LOCAL SERVICE:(OI)(CI)M'
   icacls C:\ProgramData\uv /inheritance:r /grant:r 'SYSTEM:(OI)(CI)F' 'Administrators:(OI)(CI)F' 'massey-admin:(OI)(CI)RX'
   ```

### 4. Software on the server

1. Install uv, Node.js (LTS) and Caddy (`caddy.exe` in `C:\Program Files\Caddy`). Download a
   WinSW 2.x executable.
2. Set, once for the machine, where uv puts Python and how it installs packages. By default
   Python lands in the installing user's `%AppData%`, and packages are hardlinked from that
   user's cache, carrying its permissions; the service account can read neither.

   ```powershell
   [Environment]::SetEnvironmentVariable('UV_PYTHON_INSTALL_DIR', 'C:\ProgramData\uv\python', 'Machine')
   [Environment]::SetEnvironmentVariable('UV_LINK_MODE', 'copy', 'Machine')
   ```
3. **Reboot.** Services see the machine `PATH` the Node installer changed, and the variables
   above, only after one.

**Check:** in a new PowerShell, `node --version`, `uv --version` and
`& 'C:\Program Files\Caddy\caddy.exe' version` all answer.

### 5. The release directory

1. Copy the repository at the released commit to `C:\Program Files\DentalPracticeAdmin`.
   Grant the service account read: `icacls 'C:\Program Files\DentalPracticeAdmin' /grant 'massey-admin:(OI)(CI)RX'`.
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

This moves inbound SMS from reception's own Caddy to the server's. Do it in the quiet hour.
Caddy can obtain certificates only once the router sends ports 80 and 443 to it, so it starts
after the switch, not before: a failed attempt backs off, and SMS would wait on its retry.

1. Copy [Caddyfile](Caddyfile) to `C:\ProgramData\Caddy\Caddyfile` and replace
   `RECEPTION_HOST` with reception's reserved LAN address. Check it with
   `& 'C:\Program Files\Caddy\caddy.exe' validate --config C:\ProgramData\Caddy\Caddyfile --adapter caddyfile`.
2. Copy [caddy-service.xml](caddy-service.xml) to `C:\Program Files\Caddy`, with the WinSW
   executable beside it renamed `caddy-service.exe`. Run `caddy-service.exe install`, then in
   `services.msc` set its **Log On** to **Local Service**. Don't start it yet.
3. Server firewall: allow inbound TCP 80 and 443. Reception's firewall: allow inbound TCP 5170
   from the server's address only (SMS_Bridge's PRODUCTION.md: keep 5170 off the internet).
4. DNS: an A record `admin.massey-smiles.co.nz` for the practice's public address, the same
   address `office.massey-smiles.co.nz` already uses.
5. Together, with nothing in between: stop and disable Caddy on reception, switch the router's
   forward for TCP 80 and 443 to the server, and start the server's `caddy` service. Watch
   `C:\ProgramData\Caddy\logs` until it has obtained both certificates.

**Check:** from outside the practice network (a phone off Wi-Fi),
`https://office.massey-smiles.co.nz/smsgateway/gateway-status` answers with SMS_Bridge's build,
and `https://admin.massey-smiles.co.nz` shows the Google sign-in. Principle reaches SMS_Bridge
through this path (`/smsgateway/webhooks/principle`), so send a message from Principle to a
staff mobile and confirm it arrives. **To undo:** point the router back at reception and start
reception's Caddy.

### 9. The launcher

1. If the old `\Massey Smiles Admin\Daily diary` task exists, disable and remove it.
2. Register the launcher under the service account:
   `schtasks /Create /XML deploy\task-runner.xml /TN "\Massey Smiles Admin\Task runner" /RU massey-admin /RP *`

**Check:** after five minutes, run `scripts\verify.ps1` from the release directory. Every line
must pass.

### 10. Practice tasks and schedules

1. Sign in at `https://admin.massey-smiles.co.nz` and open **Reports & scripts**. Install each
   reviewed task from its merged `admin_scripts` pull request, for example the contact-details
   clean-up from PR 7.
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
2. Read the release's pull requests for new required settings.
3. Stop the launcher and wait for any run to finish, so no task is cut off mid-write or started
   from a half-installed release:
   `Disable-ScheduledTask -TaskPath '\Massey Smiles Admin\' -TaskName 'Task runner'`, then
   repeat `Get-ScheduledTask -TaskPath '\Massey Smiles Admin\' -TaskName 'Task runner'` until
   its State is not `Running`.
4. Stop the `dental-practice-admin` service. Rename the release directory to keep it, for
   example `DentalPracticeAdmin-previous`, and copy the new release in its place. Grant the
   service account read on it as in step 5.1.
5. Copy `.env` and `dental-practice-admin.exe` across from the kept release, and
   `deploy\dental-practice-admin.xml` from the new one, so a changed service definition takes
   effect. Add any new settings to `.env`, then restrict it again as in step 5.5: a copy takes
   its new folder's permissions.
6. Run `uv sync --locked` and `npm ci`, then repeat step 5.3 as the service account, in case the
   release moved Playwright to a new browser.
7. Start the service and enable the launcher
   (`Enable-ScheduledTask -TaskPath '\Massey Smiles Admin\' -TaskName 'Task runner'`). Wait five
   minutes, then run `scripts\verify.ps1`.
8. **To roll back:** do steps 3 and 4 with the directories swapped back, start, enable, verify.

Caddy is untouched by a release. Installed practice tasks and their schedules live in the data
directory and survive it.

Caddy's administration endpoint is off (see the Caddyfile), so a changed Caddyfile takes effect
by restarting the `caddy` service, which drops connections for a few seconds.

## Back up

`C:\ProgramData\DentalPracticeAdmin` (database, installed tasks, audits), `C:\ProgramData\Caddy`
(certificates), and the release's `.env`. Take a consistent copy of the SQLite database rather
than copying the file while the service writes it.
