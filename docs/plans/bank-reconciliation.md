# Bank reconciliation: plan

## Context

Patients (and parents, partners, insurers/ACC/WINZ) pay by bank transfer. Card-terminal
settlements land in the same account. Today someone has to read the bank statement, work out
who each deposit is from, and key the payment into Principle. The goal is an admin page that
shows every deposit not yet dealt with, proposes the patient and invoice(s) it pays, and records
the payment in Principle with one click once staff confirm.

Decisions already made:
- **Bank data:** Akahu API (NZ open banking).
- **Write-back:** confirmed matches record the payment in Principle through the REST API
  (`createTransaction`).
- **Payers:** all of the above — patients, family members, insurers/ACC/WINZ bulk payments, and
  card settlements (to be set aside, not matched).

## How other products do it (the pattern we copy)

Xero's bank reconciliation is the reference; QuickBooks, MYOB and Stripe/Chargebee's "unapplied
payments" screens are variations of it:
- **One row per statement line**: bank line on the left, best suggested match on the right, a
  single **OK** button. Most lines are confirmed without typing.
- **Confidence tiers**: exact matches (amount + reference) are grouped first so they can be
  ticked through fast; weaker suggestions next; unknowns last.
- **Find & match**: a search panel to pick any patient/invoice, and to **split** one deposit
  across several invoices or patients (family and insurer payments), with a running "remaining"
  that must reach $0.00.
- **Rules / memory**: once a payer account has paid for patient X, the next deposit from that
  account suggests X first. Card-settlement lines are recognised by rule and set aside.
- **Nothing lost**: each statement line has a state (unreconciled / reconciled / ignored); the
  screen shows all unreconciled lines, not "since last visit".

## Design

### State, not a watermark
"Since the page was last used" is implemented as **per-line state**, not a last-visit timestamp.
A watermark silently drops lines when someone opens the page and doesn't finish. Each Akahu
transaction is stored by its Akahu `_id` with a status; the page shows every `open` line,
however old. Fetches overlap (from the oldest open line, minimum the last 14 days) and upsert by
id, so re-fetching is harmless.

SQLite table in `storage.py` (`bank_lines`): `akahu_id` PK, `date`, `amount` (cents),
`payer_name`, `payer_account`, `particulars`, `code`, `reference`, `status`
(`open | recording | recorded | check | ignored | wrong`), `decided_by`, `decided_at`, `note`.
Child table `bank_allocations`: `akahu_id`, `patient_id`, `invoice_id`, `amount`,
`principle_transaction_id`. Only credits (amount > 0) are stored.

Memory of who pays for whom is **derived from `bank_allocations`** (payer_account → patients
previously allocated), not a separate rules table — one source of truth.

### Fetching from Akahu
- Akahu "personal app" for the practice's own account: `AKAHU_APP_TOKEN`, `AKAHU_USER_TOKEN`,
  `AKAHU_ACCOUNT_ID` in `Settings` (validated at startup, ADR 0002).
- `GET /v1/accounts/{id}/transactions?start=…` (paginated by cursor). NZ transfers carry
  `meta.particulars/code/reference` and `meta.other_account` — the payer's account number is the
  strongest key for memory.
- The page header shows **"Bank data as of <time>"** from Akahu's account `refreshed` field, in
  red when older than 2 days, so an expired bank connection is noticed the same day rather than
  silently showing "nothing to reconcile". Akahu errors fail loudly on the page.

### Candidates and scoring
On page load, load outstanding invoices (verified in Phase 0 — likely
`listInvoicesByDateRange` over the last ~18 months filtered to unpaid, then outstanding =
`total` − paid allocations) and the patients they belong to. For each open line, score
candidates and show the top three with **visible reasons**. The window only limits
suggestions: `Find…` lists a chosen patient's unpaid invoices with `listInvoices` regardless of
age, so an old debt is still matchable by hand.

| Signal | Example reason shown |
|---|---|
| Invoice reference appears in particulars/code/reference | "reference contains INV-1234" |
| Amount equals one invoice's outstanding amount | "amount = INV-1234 outstanding" |
| Amount equals a patient's / family's total outstanding | "amount = Smith family balance" |
| Payer account previously paid for this patient | "this account paid for Aidan Glaser on 3 Aug" |
| Patient surname/first name in payer name or reference | "reference contains GLASER" |
| Card-settlement payer (configured list, e.g. Windcave/EFTPOS) | → suggested **Ignore: card settlement** |

Exact tier = reference or memory match **and** exact amount. Plain Python, no AI (ADR 0005:
scheduled/automatic paths don't use AI; this is deterministic and explainable).

### The page (`/reconcile`, Jinja template, minimal JS)
- Three sections: **Ready to confirm** (exact), **Suggested**, **Needs a person**.
- Each row: date, amount, payer, particulars/code/reference | suggestion + reasons | `OK`,
  `Find…`, `Ignore…`.
- `Find…` opens a search by patient name (reuse the existing patient search via
  `PrincipleClient.rows`) showing that patient's (and family's, if available) outstanding
  invoices with amount boxes prefilled oldest-first; "remaining" must be $0.00 to enable OK.
- `Ignore…` requires a reason (card settlement, not a patient payment, refund, other + note).

### Writing to Principle (high-consequence; ADR 0005 write rules)
Per allocation, `createTransaction` on `/v1/patients/{pid}/invoices/{iid}/transactions` with
type/provider for "bank transfer" (values verified in Phase 0), `amount`, `createdAt` = bank
date, and **`reference` = Akahu id** so the payment is traceable and idempotency can be checked.
1. Set line `recording` and write allocations in SQLite **before** calling the API.
2. On success, store the returned transaction id; line → `recorded` when all allocations are.
3. On an uncertain failure (timeout, 5xx), set `check` — **never retry automatically**. The row
   shows "Check in Principle"; a `Check` button looks up `listTransactions` on that invoice for
   our reference and resolves to `recorded` or back to `open`.
4. Every write appended to the audit log (`audit.py`) with staff email.

Overpayments / payments with no outstanding invoice: the API has no create for account credits
(only `listAccountCredits`/`getAccountCredit`). Those lines are allocated as far as invoices
allow and the remainder is marked **"record credit manually in Principle"** — to be listed in
`docs/principle/api-gaps.md`.

Undo is not offered in v1; a wrong match is corrected in Principle by hand (to be revisited if
`updateTransaction` is verified to void cleanly). A recorded line has a **Wrong match** action
that sets its status to `wrong` with a note, so its allocations stop feeding payer memory and
the wrong patient is not suggested again. A `wrong` line stays at the top of the page under **Fix in
Principle** until someone marks it fixed, which returns it to `open` for re-matching.

## Phases

### Phase 0 — verify before building (staging + Akahu sandbox)
- On Principle **staging** (per `skills/principle-staging-browser/SKILL.md`): create a payment
  with `createTransaction`; record the valid `type`, `provider`, `status` values, whether it
  updates invoice `status`/`paidAt` and the patient's `accountSummary`, partial payment
  behaviour, and how it appears in Principle's own UI. Find the cheapest way to list all
  outstanding invoices and their outstanding amount. Check whether family/guarantor links are
  readable. Record findings in `docs/principle/` and gaps in `api-gaps.md`.
- Akahu: create the personal app, connect the practice account, dump a month of transactions
  to confirm fields (`meta.other_account`, particulars/code/reference) for this bank and what
  card settlements look like.
- Ask reception how they reconcile today and collect 2–3 weeks of real deposits as a test set.

**Milestone 0:** a written list of verified field values and a replay of last month's deposits
through the scoring (offline script) showing what fraction lands in each tier.

### Phase 1 — build
1. `Settings` + small Akahu client (httpx, fake transport in tests).
2. `bank_lines`/`bank_allocations` in `storage.py`.
3. Scoring as a pure function (bank line + outstanding invoices + past allocations → ranked
   candidates with reasons).
4. Page, routes, write path with the state machine above.
5. Fake Principle: add verified invoice/transaction routes to `tests/fake/` (currently
   `tests/test_fake_covers_catalogue.py` expects invoices to be unhandled — update it). Fake
   Akahu transport.
6. Tests (ADR 0004, behavioural): scoring tiers; overlap re-fetch doesn't duplicate; split must
   sum; uncertain write → `check`, never retried; ignore requires reason; e2e: confirm an exact
   match and see the transaction on the fake.
7. Integration test on staging: one real `createTransaction` round trip.

**Milestone 1:** a week of real deposits reconciled on the page in parallel with the current
manual process, with no disagreements, before the manual process stops.

## Open issue: code budget
`tests/test_budget.py` caps app code at 2,000 lines and it is already at 2,761, so `hermetic`
fails today and this feature (~350–450 lines) cannot merge until that is resolved. Whether to
raise the cap or trim elsewhere first is still to be decided; this plan assumes neither.

## Risks
- **Wrong patient credited** — mitigated by visible reasons, explicit OK per line (no bulk confirm), traceable `reference`, audit log.
- **Akahu bank connection expires** — mitigated by the "bank data as of" banner.
- **Duplicate payment from a retried write** — no auto-retry; `check` state with lookup by
  reference.
- **Unverified API semantics** — nothing in Phase 1 starts before Phase 0 records them.
