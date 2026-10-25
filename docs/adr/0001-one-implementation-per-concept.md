# 0001 — One implementation per concept

Each fact (an address, a limit, a mapping) is written once, and everything that needs it imports
it.

## Rules

- Search before adding a constant or a helper. Extend a near-match rather than writing a sibling,
  because two copies drift and the stale one fails without saying so.
- A value that PowerShell and Python both need lives in Python, and the PowerShell calls the
  Python instead of restating the value.
