# Principle: what we know about its website and Firestore

Principle publishes an API specification. Its website and the Firestore database behind it
are undocumented, so this folder records what tasks in this repository have learned about
them. It grows one task at a time: document what a task used and verified, including what
the data means, and nothing speculative.

- [firestore.md](firestore.md): document paths, fields and what they mean, and how writes behave.
- [website.md](website.md): navigating the web app, and what its forms do.

The REST API is covered elsewhere: `tests/spec/` and the pagination contract table in the
top-level README.

Execution audits wrap the shared API, Firestore read and browser-tool boundaries, including
direct chat exploration. Authentication exchanges are outside the Firestore audit boundary;
the browser login script reads credentials from its process environment. The audit tests in
`tests/test_task_lifecycle.py` verify local call evidence and credential exclusion with synthetic
inputs. This establishes recording behaviour, not new website or Firestore business semantics.

## Entries

Each entry says what the thing is, what it means or is used for, how it was verified (the
script or test that exercises it), and the date and Principle build it was verified against.

Entries explain meaning and gotchas. Values (paths, IDs, selectors) live once, in the code
that uses them, and entries link to that code rather than restating them.

Record field names, selectors and meanings only. Never record values from patient records.

## Which way to change Principle data

Write through the REST API. It applies Principle's own handling and stamps `updatedAt` and
`updatedBy`. Read Firestore when the API cannot answer a question, for example which
patients changed since a given time. Use the website only for behaviour that exists
nowhere else. Never write to Firestore directly: see [firestore.md](firestore.md).

## When Principle releases a new version

The web app's build is identified by the hashed name of its main script, `main.<hash>.js`,
which changes with every release. The daily task records a warning when the production
build differs from the one this repository last accepted, and staff pages show it.

When the warning appears:

1. Run the scripts and tests for the tasks in use against staging.
2. Fix anything that broke, and update the entries it affects.
3. Accept the new build in a pull request. The warning clears on the next daily run.
