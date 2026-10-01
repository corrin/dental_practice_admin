# Principle_admin

## Judge every decision by maintenance, not by effort to build

This is a tool one small practice keeps running for years. The cost that matters is what it takes
to keep it working, not what it took to write.

The operational test: **how likely is this to make a dentist ring up saying it is broken?** If the
answer is "it cannot", it does not matter how inelegant it is. If the answer is "silently, and they
will not notice for a week", it matters more than anything else on the list.

So: a one-off migration that leaves one clear arrangement beats a shortcut that leaves two places
to remember. Configuration belongs in the repository that deploys it. Two things that do the same
job will drift, and the drift is the cost. A simulation of someone else's API is a liability unless
something checks it against the real thing.

When arguing for an approach, compare the ongoing cost, not the afternoon.

## Write as little code as possible

Do what was asked. Nothing adjacent, nothing defensive, nothing "while I'm here".

Ask before adding anything not requested — including security controls, extra tools, config
options, and tests beyond the ones the change needs. A good idea I had is still unrequested work.

When a decision has already been made, implement it. If it looks wrong, say so and let it be
reconsidered; do not substitute a different decision. This rule exists because a country filter and
a rate limiter were added that nobody asked for, replacing an access decision that had been made.

Bug fixes needed to make a requested thing function are not additions. Extra robustness around it
is.
