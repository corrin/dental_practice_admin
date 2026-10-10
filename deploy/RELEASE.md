# Releasing to production

How the application gets onto the practice server, first time and after. Each step says how to
check it worked; don't move on from a step whose check fails. Then sign off
[ACCEPTANCE.md](ACCEPTANCE.md).

The server runs two Windows services and one scheduled task:

| What | Where | Wraps |
| --- | --- | --- |
| `dental-practice-admin` service | `C:\Program Files\DentalPracticeAdmin` (the release) | uvicorn on `127.0.0.1:8080`, [dental-practice-admin.xml](dental-practice-admin.xml) |
| `caddy` service | `C:\Program Files\Caddy` | HTTPS for both public names, [caddy-service.xml](caddy-service.xml), [Caddyfile](Caddyfile) |
| `\Massey Smiles Admin\Task runner` | Task Scheduler | the five-minute launcher, [task-runner.xml](task-runner.xml) |

Runtime data lives in `C:\ProgramData\DentalPracticeAdmin` and `C:\ProgramData\Caddy`, never in
the release, because a release directory is replaced wholesale.

## First release

### 1. Decide and gather

- **Service account.** One local account for the service and the launcher, for example
  `massey-admin`, with a long password. `scripts\verify.ps1` refuses `LocalSystem`.
- **Reception's LAN address.** Give the reception machine a DHCP reservation. Caddy forwards
  SMS_Bridge traffic to it, and a renumbered machine silently stops SMS from arriving.
- **Access** to the DNS for `massey-smiles.co.nz`, the router, Google Cloud (the OAuth client),
  the OpenAI platform (ChatKit domains), and GitHub (`massey-reception-coder/admin_scripts`).
- **A quiet hour** for step 8, which moves inbound SMS from reception to the server.

### 2. Prepare the release on the development machine

At the commit to release, run `scripts\release_gate.ps1`. It must end "Release checks passed".

### 3. Accounts and folders on the server

1. Create the service account. In Local Security Policy, grant it **Log on as a service** (for
   the service) and **Log on as a batch job** (for the launcher).
2. Create `C:\ProgramData\DentalPracticeAdmin` and `C:\ProgramData\Caddy\logs`. Give the
   service account **Modify** on `C:\ProgramData\DentalPracticeAdmin`.

### 4. Software on the server

1. Install uv, Node.js (LTS) and Caddy (`caddy.exe` in `C:\Program Files\Caddy`). Download a
   WinSW 2.x executable.
2. Python must live where the service account can read it. uv otherwise installs it in the
   installing user's `%AppData%`, which the service account can't reach. In the PowerShell
   session used for the next step, set `$env:UV_PYTHON_INSTALL_DIR = 'C:\ProgramData\uv\python'`
   and grant the service account **Read & execute** on `C:\ProgramData\uv`.

**Check:** `caddy version`, `node --version` and `uv --version` all answer.

### 5. The release directory

1. Copy the repository at the released commit to `C:\Program Files\DentalPracticeAdmin`.
   Give the service account **Read & execute** on it.
2. In that directory: `uv sync --locked`, then `npm ci`.
3. As the service account (`runas /user:massey-admin powershell`), in that directory:
   `node node_modules/@playwright/mcp/cli.js install-browser chrome-for-testing`. The browser
   installs into that account's profile, which is where the automation looks for it.
4. Create `.env` in the release directory. Both the service and the launcher read it, from
   their working directory:

   ```dotenv
   # Also in dental-practice-admin.xml, but the launcher reads only this file. Without
   # these it would run against staging, with a different database.
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

   ADMIN_GOOGLE_MAPS_API_KEY=...     # Geocoding API enabled
   ADMIN_TASK_REPOSITORY=massey-reception-coder/admin_scripts
   ADMIN_GITHUB_TOKEN=...            # contents and pull requests on that repository
   ```

   Give only the service account and administrators access to this file.

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

1. Copy [Caddyfile](Caddyfile) to `C:\ProgramData\Caddy\Caddyfile` and replace
   `RECEPTION_HOST` with reception's reserved LAN address. Check it with
   `caddy validate --config C:\ProgramData\Caddy\Caddyfile --adapter caddyfile`.
2. Copy [caddy-service.xml](caddy-service.xml) to `C:\Program Files\Caddy`, with the WinSW
   executable beside it renamed `caddy-service.exe`. Run `caddy-service.exe install` and start
   it.
3. Server firewall: allow inbound TCP 80 and 443. Reception's firewall: allow inbound TCP 5170
   from the server's address only (SMS_Bridge's PRODUCTION.md: keep 5170 off the internet).
4. DNS: an A record `admin.massey-smiles.co.nz` for the practice's public address, the same
   address `office.massey-smiles.co.nz` already uses.
5. Router: forward TCP 80 and 443 to the server instead of reception. Caddy now obtains both
   certificates; watch `C:\ProgramData\Caddy\logs` until it has.
6. Stop and disable Caddy on reception, so only one machine terminates TLS.

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
2. Read the release's pull requests for new required settings, and add them to the server's
   `.env` before starting. A missing setting stops the service at startup.
3. Stop the `dental-practice-admin` service. Rename the release directory to keep it, for
   example `DentalPracticeAdmin-previous`, and copy the new release in its place.
4. Copy `.env`, `dental-practice-admin.xml` and `dental-practice-admin.exe` across from the
   kept release. Run `uv sync --locked` and `npm ci`, with `UV_PYTHON_INSTALL_DIR` set as in
   step 4.
5. Start the service, wait five minutes, run `scripts\verify.ps1`.
6. **To roll back:** stop the service, swap the directories back, start, and verify.

The scheduled task and Caddy are untouched by a release. Installed practice tasks and their
schedules live in the data directory and survive it.

## Back up

`C:\ProgramData\DentalPracticeAdmin` (database, installed tasks, audits), `C:\ProgramData\Caddy`
(certificates), and the release's `.env`. Take a consistent copy of the SQLite database rather
than copying the file while the service writes it.
