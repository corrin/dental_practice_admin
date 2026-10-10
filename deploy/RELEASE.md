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
| `dental-practice-admin` service: uvicorn on `127.0.0.1:8080`, [dental-practice-admin.xml](dental-practice-admin.xml) | `C:\Program Files\DentalPracticeAdmin`, a git checkout of this repository | `practice-app` |
| `caddy` service: HTTPS for both public names, [caddy-service.xml](caddy-service.xml), [Caddyfile](Caddyfile) | `C:\Program Files\Caddy`; `caddy.exe` from Chocolatey | its own virtual account, `NT SERVICE\caddy` |
| `\Massey Smiles Admin\Task runner`: the five-minute launcher, [task-runner.xml](task-runner.xml) | Task Scheduler | `practice-app` |

[scripts/install_server.ps1](../scripts/install_server.ps1) installs and upgrades all three, and
the tools they need, through Chocolatey. It is safe to run again at any time, including after it
fails part way; it stops at the first error.

Runtime data lives in `C:\ProgramData\DentalPracticeAdmin` and `C:\ProgramData\Caddy`, never in
the checkout. Both hold secrets (patient data, Principle's logged-in browser session, TLS keys),
so neither may inherit ProgramData's permissions, which let every local user read them.

## First release

### Now: remove reception's bridge

Reception's Caddy sends the public `office` name to `localhost:5170`, where reception's old
bridge can listen. That build has no API key and may have debug mode on, which makes
`/smsgateway/test/test-patient-lookup` return patient identifiers to anyone. It carries no
traffic, so remove it now, on reception, one command at a time:

1. `Get-CimInstance Win32_StartupCommand | Where-Object Command -match 'SMS_Bridge'` names
   what starts it at sign-in: a shortcut in the signed-in user's Startup folder,
   `C:\Users\<user>\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\SMS_Bridge.exe.lnk`.
   Delete that shortcut with `Remove-Item` and its full path. Moving it elsewhere in the Start
   Menu would leave it one click from running.
2. `Get-Service *sms*, *bridge*` and
   `Get-ScheduledTask | Where-Object { $_.Actions.Execute -match 'SMS_Bridge' }` find any other
   way it starts. Windows' own `SmsRouter` is unrelated.
3. `Get-Process SMS_Bridge` finds it if running; stop it with `Stop-Process -Name SMS_Bridge`.
4. Delete every copy of the old bridge and its data. Never search the whole of `C:\`, which
   takes hours; copies sit in `C:\Program Files\SMS_Bridge`, `C:\ProgramData\SMS_Bridge` (whose
   `install-settings.json` may hold Principle credentials) and users' Downloads, as folders and
   zips named for SMS_Bridge, ODSMS or Open Dental SMS, and as generic `publish` folders.
   `Get-ChildItem 'C:\Program Files\SMS_Bridge*', 'C:\ProgramData\SMS_Bridge*', 'C:\Users\*\Downloads\*SMS*', 'C:\Users\*\Downloads\publish*'`
   lists them; check a `publish` folder holds only bridge files before deleting it. Step 7.5
   recreates `C:\ProgramData\SMS_Bridge` for reception's SMS check.

**Check:** on reception, `netstat -ano | findstr :5170` prints nothing, and the commands in
steps 1 and 4 list nothing. From outside the practice network,
`https://office.massey-smiles.co.nz/smsgateway/debug-status` answers 502. A phone off Wi-Fi works;
from a machine on the practice VPN, use [check-host.net](https://check-host.net/check-http)'s
HTTP check.

### 1. Decide and gather

- **Service account.** One local account, `practice-app`, for the service and the launcher, with
  a long password in 1Password. Not an administrator; `scripts\verify.ps1` refuses
  `LocalSystem`.
- **Access** to the DNS for `massey-smiles.co.nz`, the router, Google Cloud (the OAuth client),
  the OpenAI platform (ChatKit domains), GitHub (`massey-reception-coder/admin_scripts`), and
  Akahu (the bank feed).
- **A quiet hour** for step 7, which moves both public names from reception to the server.

### 2. Prepare the release on the development machine

At the commit to release, run `scripts\release_gate.ps1`. It must end "Release checks passed".
Run it in the development checkout: its staging tier compares the fake with recordings of
staging, which `scripts/record_principle_wire.py` keeps in `tests\recordings` outside version
control, and a fresh clone fails that tier without them.

### 3. The service account

On the server, in Computer Management > Local Users and Groups > Users, create `practice-app`
with its password from 1Password and **Password never expires** ticked. The install script grants
it **Log on as a service** and **Log on as a batch job**.

### 4. The code on the server

Once, in an elevated PowerShell on the server: install Chocolatey, then Git, then clone. The
install script keeps Git, like every tool it installs, at its latest version afterwards.

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force; Invoke-RestMethod https://community.chocolatey.org/install.ps1 | Invoke-Expression
choco install git -y
```

In a new elevated PowerShell, so it finds `git`:

```powershell
git clone https://github.com/corrin/dental_practice_admin.git 'C:\Program Files\DentalPracticeAdmin'
```

### 5. Settings

1. Google Cloud, OAuth client: add the authorised redirect URI
   `https://admin.massey-smiles.co.nz/auth/callback`.
2. OpenAI platform: register `admin.massey-smiles.co.nz` for ChatKit. Its key goes in
   `ADMIN_CHATKIT_DOMAIN_KEY` below.
3. Create `C:\Program Files\DentalPracticeAdmin\.env`. Both the service and the launcher read
   it, from their working directory, and nothing else configures them. `Settings` in
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

**Check:** the install script locks `.env` to `practice-app` and administrators, and runs
`scripts\check_settings.ps1`, which prints
`Ready: production C:\ProgramData\DentalPracticeAdmin\production`; otherwise it names each
setting `.env` lacks. Nothing has a value in the code, so every line of the template above
is needed. No value may still read `...`.

### 6. Install

In an elevated PowerShell on the server:

```powershell
cd 'C:\Program Files\DentalPracticeAdmin'; powershell -ExecutionPolicy Bypass -File scripts\install_server.ps1
```

It asks for `practice-app`'s password, then installs the tools (Chocolatey: Git, uv, Node.js,
Caddy; WinSW), sets uv's machine settings, grants the account its logon rights, creates the data
folders with their permissions, installs the app's packages without development tools, clears
the download caches (everything on the server is backed up by the byte), checks `.env`, installs
and starts the application service as `practice-app`, installs Caddy without starting it, opens the
firewall (80 and 443 from anywhere; 5170 from reception, `192.168.192.125`, only; every network
profile, since the server sometimes comes up on Public), and registers the launcher. It ends
"Done."; anything else names what failed. Fix that and run it again.

Then:

1. **Restart the server once**, so services see the PATH and uv settings the installers changed.
   It also serves Romexis and the shares; restart when those can be down for a few minutes.
2. As `practice-app` (`runas /user:practice-app powershell`, at the server's desktop), in
   `C:\Program Files\DentalPracticeAdmin`:
   `node node_modules/@playwright/mcp/cli.js install-browser chrome-for-testing`. The browser
   installs into that account's profile, which is where the automation looks for it.
3. Disable any program-level firewall allow rule for SMS_Bridge, which would open 5170 to the
   whole LAN.

**Check:** `Invoke-RestMethod http://127.0.0.1:8080/health` reports `ok` and `production`. After
five minutes, `scripts\verify.ps1` in the checkout passes every line.

### 7. Caddy, DNS and the router

This moves both public names from reception's own Caddy to the server's, and puts SMS
(SMS_Bridge, Call Centre and the paired phone) on the server. Reception's bridge is already gone,
so nothing moves from it. Rollback is a complete reinstall, so reception keeps nothing for an
undo: its Caddy is deleted, not disabled. Do it in the quiet hour.
Caddy can obtain certificates only once the router sends ports 80 and 443 to it, so it starts
after the switch, not before: a failed attempt backs off, and inbound SMS would wait on its
retry.

1. SMS on the server, as [SMS_Bridge's PRODUCTION.md](https://github.com/corrin/SMS_Bridge/blob/master/PRODUCTION.md)
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
2. DNS: an A record `admin.massey-smiles.co.nz` for the practice's public address, the same
   address `office.massey-smiles.co.nz` already uses.
3. Together, with nothing in between: stop Caddy on reception (`sc.exe stop caddy`; in
   PowerShell `sc` means `Set-Content`); switch the router's forward for TCP 80 and 443 to the
   server; and start the server's `caddy` service. Watch `C:\ProgramData\Caddy\logs` until it has
   obtained both certificates.
4. Point Principle's webhook at `https://office.massey-smiles.co.nz/smsgateway/webhooks/principle`.
5. Register the SMS check on reception, as PRODUCTION.md's "SMS warning on reception"
   describes, with `-Url http://192.168.192.30:5170/smsgateway/phone-status`. It warns
   reception within 5 minutes of texts stopping.
6. Remove Caddy from reception. It holds the TLS keys for `office`, which reception no longer
   needs. First note the paths `sc.exe qc caddy` gives in `BINARY_PATH_NAME`, then
   `sc.exe delete caddy`, and delete Caddy's program directory and its data directory, which
   holds the certificates. Remove any firewall rule on reception that admits port 80, 443 or
   5170. Stop Call Centre starting at logon on reception, unless reception uses it for calls.

**Check, without sending an SMS:** from outside the practice network (a phone off Wi-Fi),
`https://admin.massey-smiles.co.nz` shows the Google sign-in, and
`https://office.massey-smiles.co.nz/smsgateway/debug-status` answers 401 without the key and 200
with `isDebugMode` false given `X-API-Key: <the new key>`, both with valid certificates. A 502
means Caddy can't reach the bridge: read the server's Caddy log. Never probe
`/smsgateway/test/check-send-sms`; it sends a real SMS, and a real test message is the owner's
call. From a LAN machine other than reception, port 5170 on the server times out. Reception's SMS
check passes PRODUCTION.md's acceptance check for it. On reception, `sc.exe query caddy` reports
that the service does not exist, `netstat -ano | findstr ":80 :443 :5170"` shows nothing
`LISTENING`, and `C:\ProgramData\SMS_Bridge` holds only `check-sms.ps1`.

### 8. Practice tasks and schedules

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

### 9. Sign off

Work through [ACCEPTANCE.md](ACCEPTANCE.md): the reboot, an unattended scheduled run, staff access
from outside, and the restore drill. Record the release in its table.

## Later releases

1. Run `scripts\release_gate.ps1` at the new commit, on the development machine, and merge it to
   `main`.
2. Read the release's pull requests for new required settings, and add them to `.env`.
3. On the server, in an elevated PowerShell:

   ```powershell
   cd 'C:\Program Files\DentalPracticeAdmin'; git pull; powershell -ExecutionPolicy Bypass -File scripts\install_server.ps1
   ```

   The script pauses the launcher, refuses while a run from Reports & scripts or chat is in
   progress (the Results page shows them; run it again once they finish), stops the service,
   updates the tools and packages, and starts everything again. A run can stay marked in
   progress after a crash: if Task Manager shows no `python.exe` running as `practice-app` other
   than the service itself, nothing is really running.
4. If `deploy\dental-practice-admin.xml` or `deploy\caddy-service.xml` changed its start or
   failure settings, which WinSW applies only at install: stop the service, run its
   `uninstall` (`dental-practice-admin.exe` in the checkout, `caddy-service.exe` in
   `C:\Program Files\Caddy`), and run the script again.
5. If the release moved Playwright to a new browser, repeat step 6.2 of the first release.
6. Wait five minutes, then run `scripts\verify.ps1`.

**To roll back:** `git checkout <previous commit>` in the checkout, then run the script again.
`git checkout main` returns to following `main`.

Installed practice tasks and their schedules live in the data directory and survive a release.

## Back up

`C:\ProgramData\DentalPracticeAdmin` (database, installed tasks, audits), `C:\ProgramData\Caddy`
(certificates), and the release's `.env`. Take a consistent copy of the SQLite database rather
than copying the file while the service writes it.
