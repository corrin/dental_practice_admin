# Principle_admin

## Write as little code as possible

Do what was asked. Nothing adjacent, nothing defensive, nothing "while I'm here".

Ask before adding anything not requested — including security controls, extra tools, config
options, and tests beyond the ones the change needs. A good idea I had is still unrequested work.

When a decision has already been made, implement it. If it looks wrong, say so and let it be
reconsidered; do not substitute a different decision. This rule exists because a country filter and
a rate limiter were added that nobody asked for, replacing an access decision that had been made.

Bug fixes needed to make a requested thing function are not additions. Extra robustness around it
is.
