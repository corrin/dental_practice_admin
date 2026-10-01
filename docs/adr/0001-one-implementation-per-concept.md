# 0001 — One implementation per concept

Each fact (an address, a limit, a mapping) is written once, and everything that needs it imports
it.

## Rules

- Search before adding a constant or a helper. Extend a near-match rather than writing a sibling,
  because two copies drift and the stale one fails without saying so.
- A value that PowerShell and Python both need lives in Python, and the PowerShell calls the
  Python instead of restating the value.

## Observed

- `PRINCIPLE_URLS` in `scripts/run.py` restates the environment-to-address map in `config.py`, and
  the two disagree about the fake's address (`9884a99`).
- The 26-hour freshness limit is written in both `scripts/verify.ps1` and `scripts/check_runs.py`
  (`9884a99`).
