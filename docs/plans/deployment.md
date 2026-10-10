# Deployment: plan

## Context

The application has never been installed on the practice host. `deploy/ACCEPTANCE.md` has no
signed row, ARCHITECTURE.md lists "the production Windows host and service identity" as a
decision still required, and the install steps exist only as prose in the README: install with
`uv sync --locked`, register WinSW and the task runner, keep runtime data outside the release,
"stop the service for updates, retain the previous working release". Nothing does those steps,
so nothing stops a person doing them in the wrong order.

Pull request #29 makes every setting required. A release installed on a host whose `.env` lacks
one now refuses to start, which is right, but it must be found before the running release is
stopped, not after.

The goal is one command, run on the host, that installs a reviewed revision, refuses to switch
to it unless it will start, switches, proves it is healthy, and puts the previous release back
by itself if it is not.

## What we copy

- **Capistrano's release layout.** Each release in its own directory under `releases\`, a
  `current` link pointing at the live one, configuration in `shared\` linked into every release,
  and the previous release kept so going back is a link change, not a reinstall.
- **Octopus Deploy's deploy, health check and roll back.** A deployment is a fixed sequence of
  steps on the target; after the switch a health check decides success; a failed deployment is
  answered by redeploying the previous release, here by pointing `current` back.

Nothing here is invented beyond mapping those onto WinSW and Task Scheduler.

## Layout on the host

```
C:\Program Files\DentalPracticeAdmin\
    dental-practice-admin.exe     WinSW, and its XML, outside any release
    dental-practice-admin.xml
    releases\<commit>\            one checkout per release, with its own .venv and node_modules
    current\                      directory junction to the live release
C:\ProgramData\DentalPracticeAdmin\
    shared\.env                   the host's settings; each release gets a link to it
    production\                   runtime data, as today (ADMIN_DATA_ROOT)
```

- The service and the `Task runner` task run `current\.venv\Scripts\python.exe` with `current`
  as their working directory, so switching the junction switches both.
- `deploy/dental-practice-admin.xml`, `deploy/task-runner.xml` and `scripts/verify.ps1` change
  their paths to `current`. WinSW and its XML move out of the release, because the release
  directory is replaced on every deploy and the service definition must not be. That makes two
  copies of each definition, the installed one and the release's `deploy\` copy, chosen
  because WinSW and Task Scheduler hold their own. Every deploy compares them (step 5) and
  refuses on a difference, so they cannot drift apart unnoticed.
- `.env` lives once, in `shared\`, as Capistrano's linked files: one copy, so releases cannot
  disagree about settings.

## `deploy/install.ps1 -Revision <commit>`

Run elevated, on the host, by a person. Each step stops the run on failure; until step 6 the
running release is untouched.

1. Refuse unless `<commit>` is on `origin/main` (`git merge-base --is-ancestor`). `main`
   accepts only pull requests whose `hermetic` check passed, so this is also the CI check, and
   the host needs `git` but no GitHub credentials.
2. Clone that commit into `releases\<commit>`, unless that directory already holds it: going
   back to the previous release is the same command with its commit, and reuses its directory.
3. `uv sync --locked`, `npm ci`, and the locked Playwright browser, in the release.
4. Link `shared\.env` into the release.
5. Refuse, naming what differs, if:
   - `scripts\check_settings.ps1 -ReleaseRoot releases\<commit>` fails: a setting is missing;
   - the installed WinSW XML or the registered `Task runner` differs from the release's
     `deploy\` copies: the service definition changed, and the bootstrap checklist's re-install
     step applies first.

   The old release is still serving.
6. Disable the task runner and wait for any run in progress to finish, so no scheduled task is
   writing to Principle while its code is switched underneath it. A run still going after ten
   minutes is a failure like any other in steps 6–8 (step 9): the deploy exits with failure,
   printing the stuck task's name to the person running it, who is watching. Then stop the
   service, and record where `current` points.
7. Point `current` at the new release and start the service.
8. Health: `/health` must answer within 60 seconds, then `scripts\verify.ps1` must pass. Only
   then is the task runner enabled, so a release that is not healthy never runs a scheduled
   task that writes to Principle.
9. If anything fails from step 6 on, including the run being interrupted: put back what had
   changed by then, and exit with failure, naming the step to the person running it. Steps 6–8
   run inside one `try`/`finally` that does this, so every failure has one path back:
   - the task runner, if disabled, is enabled again;
   - `current`, if repointed, points back at the recorded release;
   - the service, if stopped, is started, and `verify.ps1` is run again.

   A stuck run fails before the service is stopped, so it only re-enables the task runner. If
   `verify.ps1` fails after the rollback too, the message says first that the practice now has
   no working release, then which step failed in each.
10. Delete releases other than `current` and the one it replaced.

The first install is the same command after a one-off bootstrap, written as its own checklist
in `deploy/ACCEPTANCE.md`: the service account, `shared\.env`, WinSW install, task registration
and Caddy. Those happen once and need a person's judgement, so they are not scripted.

## Verifying the script itself

- A rehearsal on this development machine against a temporary install root, with WinSW and the
  task runner registered under test names: a good release, a release with a setting missing
  (stops at step 5, old release still answering), and a release whose service fails to start
  (rolls back at step 9).
- The existing acceptance items, "Reinstate the previous release" among them, now run through
  `install.ps1` rather than by hand.

## Phases

1. **Layout and paths.** The path changes to the XML files and `verify.ps1`; the bootstrap
   checklist. Verify: the rehearsal installs and serves `/health` from `current`.
2. **`install.ps1`.** Steps 1–10. Verify: the three rehearsal cases behave as described.
3. **First install on the host.** Bootstrap, then `install.ps1` with #29's merged revision.
   Verify: `verify.ps1` passes, and `deploy/ACCEPTANCE.md` is worked through and signed.

## Needs the owner

- **Which machine is the host**, and whether it meets Playwright's Windows requirement, which
  ARCHITECTURE.md records from the Playwright installation page in its references as Windows 11,
  or Windows Server 2019 and later; to confirm against that page when the host is named.
- **How changes reach it**: whether someone runs `install.ps1` at the host, or this session can
  reach it (remote desktop, SSH or a remote PowerShell session).
- **The service account** the service and the task runner run as.
