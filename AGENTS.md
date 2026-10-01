# AGENTS.md

Read [`docs/adr/README.md`](docs/adr/README.md) before non-trivial work. An ADR wins over habit.

## Minimise ongoing maintenance

This is an internal application for one six-person dental practice. Scale is not a concern.

Judge engineering decisions by their ongoing maintenance cost, not their implementation effort.

The operational test is: **how likely is this to make a dentist ring up saying it is broken?**
Silent failures that may go unnoticed for a week matter most.

Prefer one clear mechanism and one source of truth. Avoid arrangements that can drift, or that
require two things to be kept in step.

## Make the smallest complete change

Do what was asked. Avoid adjacent improvements, speculative features, future-proofing, and
"while I'm here" work. If something unasked-for seems necessary, say so rather than either adding
it silently or leaving it out silently.

Prefer the simplest conventional implementation with the fewest permanent moving parts.

When a decision has already been made, implement it. If it appears wrong, raise the concern rather
than substituting another design.

Patient privacy and unintended patient-facing actions are high-consequence failure modes. Preserve
existing access controls, and be especially careful with changes that send messages or modify the
patient-management system.

## Keep work in a pull request

Work that exists only on one machine gets lost. Get it into a GitHub pull request as early as
possible, and keep it there.

- Work on a branch, never on `main`. A worktree is fine.
- Commit and push the plan, or the first change, as soon as it exists. Open a draft pull request
  then.
- Commit and push after every step that works. Never end a turn with unpushed commits.
- Commit everything in the working tree. Don't stash, cherry-pick, rebase, or switch branches
  over uncommitted work, and don't pick files selectively.
- Mark the pull request ready when the work is done. Done means merged to `main`.
