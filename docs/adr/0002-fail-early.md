# 0002 — Fail early

Configuration and data are checked against their model once, where they enter, so every other
line of code can trust them without checking.

## Rules

- `Settings` is the model for configuration. Everything the running application needs is required
  there, and startup refuses to continue without it (`Settings.require_web_configured`). A missing
  setting is a configuration bug even if only one feature uses it, so it stops the whole
  application rather than leaving each user of the setting to check for it.
- Code past that point does not re-check. `x or default`, `.get(key, default)`,
  `getattr(obj, name, default)` and `if x is None` each claim the model allows the bad case. If it
  does not, delete the branch. If it does, tighten the model so it does not.
- Malformed data is fixed where it is written, never defended against where it is read.

## Observed

None yet.
