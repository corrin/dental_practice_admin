# Architecture Decision Records

Rules for changing this codebase, written for whoever is about to change it, usually an agent
session.

## Conventions

- Files are `NNNN-short-topic.md`, numbered in sequence. A number is never reused. A retired ADR is
  deleted, and every citation of its number is reworded in the same change.
- The first line is the rule in one sentence. **Rules** follow, then **Observed**.
- **Observed** lists only mistakes actually seen, each with the commit that shows it. It may be
  empty; nothing is invented to fill it.
- An ADR lands in its own commit, apart from any code it authorises.
- An ADR is in force once the owner merges the pull request that adds it.

## Index

| N | Title |
| --- | --- |
| [0001](0001-one-implementation-per-concept.md) | One implementation per concept |
| [0002](0002-fail-early.md) | Fail early |
| [0003](0003-unhappy-case-first.md) | Unhappy case first |
| [0004](0004-tests-state-behaviour.md) | Tests state behaviour |
