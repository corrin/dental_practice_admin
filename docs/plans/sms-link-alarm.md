# SMS link alarm: plan

## Context

Patient texts go out through SMS_Bridge on reception. It drives JustRemotePhone's Call Centre,
which drives a paired phone. If the phone drops off, the bridge loses Call Centre, or the bridge
itself stops, texts silently stop going out, and Principle tells nobody. While the bridge ran on
reception with nothing else in front of it, the receptionist could see Call Centre and notice.
Once the server fronts SMS, that is the only safeguard left, so it is replaced with three
warnings (the owner, 2026-10-11):

1. **A pop-up on reception's screen**, from the bridge itself, as long as it is simple and
   needs no extra program.
2. **A banner on every staff page** of this application.
3. **An email to the owner**, which works out of hours.

## How a drop is detected

The JustRemotePhone SDK reports `Phone.State` as `Unknown` when "either the Application is not
connected to a CallCenter process or CallCenter is not connected to a remote phone"
(`publish/RemotePhoneService.xml` in SMS_Bridge). The link is **up** when
`Application.State == Connected` and `Phone.State != Unknown`; otherwise it is **down**.
SMS_Bridge already tracks `Application.State` (`JustRemotePhoneSmsProvider._isConnected`). It
does not yet watch `PhoneStateChanged`.

A bridge that has stopped can't report anything, so this application also treats "no answer"
as down.

## Changes

### SMS_Bridge (its own PR, in that repository)

- **`GET /smsgateway/phone-status`**, behind the API key. It returns
  `{"up": bool, "since": "<ISO time of the last change>", "detail": "<application and phone state>"}`,
  from `ApplicationStateChanged` and `PhoneStateChanged`.
- **The reception pop-up.** When the link has been down for 2 minutes, which rides out a
  reconnect, the bridge shows one Windows message box. It uses `MessageBoxW` from `user32` (a
  P/Invoke, no new program or package), system-modal so it sits on top, on a background thread
  so nothing waits on it. The text: "Patient texts are not being sent. The phone is not connected
  to Call Centre. Check the phone is on and connected, then open Call Centre." Dismissing the box
  acknowledges the outage, so there's one pop-up per outage. An outage ends only once the link
  has stayed up for 30 minutes, so a link that flaps all morning is one outage, not a pop-up
  every few minutes.
  - **When the bridge can't show it.** The bridge knows whether it's interactive
    (`Environment.UserInteractive` in `Program.cs`). `phone-status` reports `"popup": false`
    when it isn't, and the staff banner then adds "Reception can't show SMS warnings", so a
    missing pop-up is never silent.
  - **This only works in a signed-in session.** Call Centre needs one anyway, so the bridge
    already runs there (SMS_Bridge `PRODUCTION.md`: Task Scheduler at logon). Verify that on
    reception before relying on it. That check goes in RELEASE.md's SMS go-live section.

### dental_practice_admin (this repository)

- **Settings**, explicit as everywhere else:
  - `SMS_BRIDGE_URL`: `http://192.168.192.125:5170`, the bridge on the LAN. The public
    `office` name would need the router to hairpin, which is unverified. After cutover the
    bridge listens on that address and admits only the server.
  - `SMS_BRIDGE_API_KEY`
  - `ADMIN_ALERT_EMAIL`
  - `ADMIN_SMTP_HOST`, `ADMIN_SMTP_PORT`, `ADMIN_SMTP_USER`, `ADMIN_SMTP_PASSWORD`. A Google
    Workspace app password on `smtp.gmail.com:587` is the expected choice, decided at
    deployment.
- **The check.** The five-minute launcher (`schedules.main`) calls `phone-status` with a short
  timeout, every poll, and records the result in a new `sms_link` row (time, up, detail, since).
  It runs whether or not any task is due. A timeout, an error status or a refused connection
  records **down**, with that as the detail.
- **The banner.** `schedules.attention()` adds "Patient texts are not being sent since <time>:
  <detail>" when the latest check is down. It shows "SMS link not checked since <time>" when no
  check is recent, because a stopped launcher would otherwise hide a dead link. "Since" is the
  bridge's own `since` when it answers, and the first failed check when it doesn't.
  - The banner shows on the first down check, unlike the email: a staff member is looking, and
    a blip is cheap to read past.
- **The email.**
  - One email when the link has been down for 3 consecutive checks (about 15 minutes), and one
    when the outage ends. The outage uses the same 30-minute rule as the pop-up, so flapping
    sends no more mail.
  - The row records what was sent, so there is never a repeat.
  - Sent with `smtplib` from the standard library.
  - A failed send is retried on the next poll. The banner says the email failed.
- **Tests.**
  - A fake bridge with the same contract: up, down, timing out, refusing.
  - A local SMTP sink, so tests prove what is sent and when without network access.
  - Cases:
    - down for 2 checks: banner, no email
    - down for 3 checks: one email
    - down for 4 checks: still one email
    - up again: one recovery email, and the banner clears
    - bridge unreachable: counts as down
    - the launcher stopped: "not checked" banner
- **RELEASE.md.** The new settings go in step 5's `.env`. The SMS go-live check also checks the
  banner is clear and that a test email arrives.

## Worked examples

1. At 10:02 someone switches the phone off. By 10:04 reception has a pop-up. By 10:05 every staff
   page shows "Patient texts are not being sent since 10:02". At about 10:15 the owner gets an
   email. When the phone is back on at 10:30, the banner clears at the next check and one
   "texts are going out again" email arrives.
2. Wi-Fi blips for 40 seconds and Call Centre reconnects. There's no pop-up (under 2 minutes) and
   no email (under 3 checks). The banner may show for one check if a poll lands inside the blip.
3. The phone drops at 10:02 and the owner can't deal with it until that night. Reception gets
   one pop-up at 10:04 and dismisses it, and nothing more interrupts them. The banner sits
   quietly on staff pages all day, and the owner has one email from about 10:15. If the link
   flaps back and forth during the day, that is still the same outage until it has stayed up
   for 30 minutes.
4. **Edge case:** reception is restarted for updates and nobody signs in. The bridge isn't
   running, so there's no pop-up. The next check can't reach the bridge, so the banner shows
   that, and 15 minutes later the owner gets an email.

## Answered (the owner, 2026-10-11)

- **The phone is connected around the clock.** Any drop at any hour is a fault, so there are no
  quiet hours.
- **The email comes from a massey-smiles.co.nz Workspace account with an app password**, on
  `smtp.gmail.com:587`. The account needs 2-step verification, and the Workspace admin must
  allow app passwords.
- **Reception is told once, not all day.** One outage gives one pop-up and one email, however
  long it lasts and however often it flaps.

## Notes

- A restarted bridge starts its record over, so its `since` is the restart time.

## Not in this change

- **An email when the launcher itself stops.** The launcher is what sends the email, so a
  stopped launcher shows only as the "not checked" banner. An outside check for that is a
  separate change.

- Alerting by SMS. It goes through the same phone that is down.
- Watching delivery failures of individual messages. The bridge already logs `SendFailure`.
  This alarm is about the link, which covers the silent case.
