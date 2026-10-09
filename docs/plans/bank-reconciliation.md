# Bank reconciliation: plan

## Context

Money reaches the practice's bank account in two ways, and staff have to account for every
deposit:

- **Batch deposits.** A card acquirer (Smartpay, Paymark EFTPOS) or an insurer (Southern Cross,
  ACC) pays one lump sum. Each payment inside it was already entered in Principle at the desk.
  The deposit has to equal the sum of those payments; when it doesn't, a payment was missed or
  entered wrongly.
- **Individual deposits.** A patient, parent or partner pays by transfer or weekly automatic
  payment. Nothing is in Principle yet. Someone has to work out whose debt it pays and record the
  payment.

A sample of ten days (1–10 October 2026) had 41 deposits:
- 17 card settlements (one or two a day);
- 10 insurer batches;
- 11 automatic payments, nearly all the same payer and amount every week (payment plans);
- 7 one-off transfers, often from an account in a different name from the patient;
- 3 oddities: a one-cent account check, a reference naming only the practice, and a
  non-patient payment.

So patient matching is about two deposits a day, and over half of those repeat weekly.

The goal is one admin page that shows every deposit not yet accounted for, does the arithmetic
for batch deposits, proposes the patient and invoice for individual ones, and records confirmed
payments in Principle.

Decisions made:
- Bank data comes from **Akahu** (NZ open banking).
- Confirmed individual matches are recorded in Principle through the REST API
  (`createTransaction`).
- An LLM (a fast model) ranks and explains candidates for individual deposits. The practice's
  OpenAI plan does not let the provider retain data, so sending patient names and balances is
  accepted. ADR 0005's rule against AI covers scheduled tasks; this is an interactive page and a
  person confirms every match.
- Wrong matches are corrected on the page with **Remove & redo**.
- Card days frequently won't add up, typically because reception forgot to record a payment.
  The page's main job for batch deposits is explaining the difference, not just reporting it.
- Smartpay's own transaction list (each card payment with its time and amount) is used if
  Phase 0 finds a way to get it. With it, a mismatch becomes a direct comparison that names the
  missing payment; without it, the page can only infer the missing payment from Principle
  (below).

## The reference: Xero's reconcile screen

Bank reconciliation is a solved UI problem. We clone **Xero's reconcile screen and Find &
Match**, the screen the practice's accountant already knows, and add two things Xero lacks:
memory of which bank account pays for which patient, and a visible reason on every suggestion.

What we copy, with sources:
- **One row per deposit:** the deposit on the left, the single best match on the right, one
  **OK**, and "*n* other possible matches" beneath
  ([Xero: Reconcile your bank account](https://central.xero.com/s/article/Reconcile-your-bank-account)).
- **Find & Match:** search by name, invoice number or amount; tick several invoices across
  patients; **Split** the last one for a part payment; a running total that must reach $0.00
  before OK is enabled
  ([Xero: Find & Match](https://central.xero.com/s/article/Reconcile-a-bank-statement-line-using-Find-Match),
  [part payments](https://central.xero.com/s/article/Record-a-part-payment-during-reconciliation)).
- **Two ways to correct a match**
  ([Xero: Fix an incorrectly reconciled line](https://central.xero.com/s/article/Fix-an-incorrectly-reconciled-bank-statement-line)):
  - **Unreconcile** unlinks the deposit and keeps the payment in Principle.
  - **Remove & redo** reverses the payment this page created, and the deposit goes back to
    unmatched.

  Like Odoo and ERPNext, the page only ever reverses payments it created itself.
- **A label on every match saying how it was made:** Rule, Sum, Remembered, Suggested or Manual.
  Hovering shows the reasoning. Xero's automatic reconciliation (JAX) and MYOB both do this. It
  is what lets a wrong match be noticed in a review list, rather than a week later.
- **Plain confidence tiers, not percentages.** QuickBooks hides low-confidence suggestions; we
  show them in **Needs a person** with no pre-selected match. When two candidates score the
  same, nothing is suggested, which is ERPNext's rule for ties.
- **Exclude:** a reversible "not a patient payment" state with a required reason, like
  QuickBooks' Exclude. It covers one-cent checks, refunds and non-patient money.

What practice software does: none of the dental or medical systems we checked matches a bank feed
to patients. They push to Xero, use deposit slips (Open Dental), or use a clearing account per
card channel. A card settlement is matched to the day's card total, not to patients. That is the
batch-deposit design below.

## Design

### Deposits are tracked by state, not since-last-visit
Every Akahu transaction is stored by its Akahu `_id` with a status, and the page shows every
unresolved deposit however old. If the page instead showed only deposits since the last visit,
an unfinished visit would silently lose deposits. Each fetch re-reads from the oldest unresolved
deposit, and at least the last 14 days, and updates rows by `_id`, so re-fetching does no harm.
Only credits are stored.

SQLite tables in `storage.py`:

**`bank_deposits`**
- Columns: `akahu_id` (primary key), `date`, `amount_cents`, `payer_name`, `payer_account`,
  `particulars`, `code`, `reference`, `kind` (`batch`/`individual`), `status`, `method`,
  `decided_by`, `decided_at`, `note`.
- `status` is one of `open`, `recording`, `matched`, `check` or `excluded`.
- `method` is one of `rule`, `sum`, `remembered`, `suggested` or `manual`.

**`bank_matches`**
- Columns: `akahu_id`, `principle_transaction_id`, `patient_id`, `invoice_id`,
  `amount_cents`, `created_here`.
- A batch deposit has one row for each existing payment it covers.
- An individual deposit has one row for each payment the page created
  (`created_here` = true).

**`payer_links`**
- Columns: `payer_account`, `patient_id`, `created_by`, `created_at`.
- This is the memory: which bank account pays for which patients.
- A row is written when staff confirm a match. It is listed on a **Remembered payers** view
  where any link can be deleted.
- The memory is plain rows that staff can see and edit, as in Actual Budget, not a hidden model.
  One bank account can link to several patients (a parent paying for a family).

### Fetching from Akahu
- An Akahu "personal app" on the practice's own account. `AKAHU_APP_TOKEN`, `AKAHU_USER_TOKEN`
  and `AKAHU_ACCOUNT_ID` are validated in `Settings` at startup (ADR 0002).
- `GET /v1/accounts/{id}/transactions?start=…`, paged by cursor. NZ transfers carry
  `meta.particulars`, `meta.code`, `meta.reference` and `meta.other_account`.
- The page header shows **"Bank data as of <time>"** from the account's `refreshed` field. It is
  red when the data is over two days old, so an expired bank connection is noticed the same day.
  Akahu errors are shown on the page.

### Recognising batch deposits: rules
A short list of rules in `Settings` classifies a deposit as a batch and names the Principle
payment method it settles. Each rule is a payer name or particulars "starts with" pattern
(Xero's rule shape). For example, `SMARTPAY` → card payments, `SOUTHERN CROSS` → Southern Cross
payments. The exact Principle method names are verified in Phase 0. Everything else is
individual. A rule-classified deposit is labelled **Rule**.

### Batch deposits: a clearing queue per channel
This is the standard clearing-account pattern (Xero's "undeposited funds", and what Cliniko and
Core Practice recommend for card channels). Every payment recorded in Principle under a batch
method sits in that channel's queue until a deposit claims it. A deposit is matched against the
queue, not against a fixed day, so a settlement that arrives two or three days late still
matches. Deterministic arithmetic, no LLM.

- **Channels.** Smartpay, Paymark, Southern Cross and ACC each have their own queue. A Smartpay
  deposit is the Smartpay terminal's takings only: no cash and nothing from Paymark. Phase 0
  checks that Principle can tell Smartpay and Paymark payments apart; if it can't, the two share
  one card queue.
- **Card deposits claim whole days.** A card deposit is matched to one or more whole takings
  days still in the queue, from the last 10 days, that sum exactly to it. When the line carries
  its takings date (`Shift4 5842 09/10`), that day is tried first.
- **Insurer deposits claim payments.** The unmatched payments of that insurer, oldest first; a
  run of them summing exactly to the deposit is the match. If Principle stores the insurer's
  batch number on the payment (checked in Phase 0), the page matches on that instead.
- **The queue is always visible.** The page header shows, per channel, how much recorded in
  Principle is still waiting for a deposit and how old the oldest item is, for example
  "Smartpay: $2,151.00 waiting, oldest 4 days". An item older than 5 days is shown in red. It
  means a deposit hasn't arrived or a payment was recorded under the wrong method, and it is
  noticed rather than sitting unseen.
- **It adds up:** the row shows ✓ "14 payments, 8–9 Oct" (illustrative), labelled **Sum**. One OK
  confirms it and writes the `bank_matches` rows. Nothing is written to Principle.
- **It doesn't add up:** the row shows the difference, for example "$185.00 more in the bank than
  in Principle", and an **Explain the difference** panel (below). It stays open until Principle
  is corrected and the sum is recomputed, or until someone accepts the difference with a reason.
- **Two different combinations both add up:** nothing is pre-selected and staff pick.

### Explaining a difference
The cause reported by the practice is a payment that reception forgot to record, so that is the
one the page searches for. The panel shows:
- **Likely missing payments.** With Smartpay's list: terminal payments with no Principle payment
  of the same amount that day. Without it: patients seen that day whose invoice from that day is
  still unpaid, with the invoice amount. An invoice whose amount equals the difference is listed
  first.
- **Side by side:** the day's Principle payments of this method, and that day's unpaid
  invoices, so a person can spot anything else.

**Record payment** creates the card payment on one chosen invoice. It goes through the same
write path and rules as individual deposits. It is offered on a single invoice, for a patient
who had an appointment that day, and is never pre-selected or offered for a combination of
invoices. A combination goes through Find & Match like any split.

After any correction, the page re-reads Principle and recomputes the sum. A deposit is only
marked matched when the sum agrees, or when someone uses **Accept difference**, which needs a
reason and labels the match **Accepted difference** in Reconciled so it stays visible.

Other causes (a payment recorded under the wrong method, on the wrong day, or twice) are not
searched for in Phase 1. The Phase 0 replay records how often each happens; searches are added
for the ones that do. Correcting an existing payment stays manual in Principle.

### Individual deposits: candidates and suggestions
1. **Narrow the candidates in code.** The page loads patients with an outstanding balance and
   their unpaid invoices (the cheapest way is verified in Phase 0). It keeps about 10 for each
   deposit, using:
   - patients linked to the payer's account in `payer_links`;
   - an invoice number or patient name token in particulars, code or reference;
   - a surname matching the payer name;
   - an amount equal to an invoice or patient balance.
2. **A remembered payer short-circuits the LLM.** If `payer_links` gives exactly one patient,
   that patient is the suggestion, labelled **Remembered**. This is how the weekly automatic
   payments become one click.
3. **The LLM ranks the rest.** A fast model gets the deposit and the short list. It returns a
   ranked list, each item with a one-line reason and a tier (`likely`/`possible`). It never
   records anything. The model is a setting separate from `agent_model` and uses the same OpenAI
   client. The model sees patient names and balances, data that already goes to OpenAI through
   chat; Open questions asks for an explicit yes. A suggestion from the LLM is labelled
   **Suggested**. If the model call fails, the deposit's candidates are listed unranked in
   Needs a person under "Suggestions unavailable: <error>", so the failure is visible.

### What staff see for an individual deposit
Illustrative data only:

```
12 Oct  $185.00   J & M SMITH   12-3456-0789012-00
        Particulars: SMITH   Code: DENTAL   Ref: LILY
──────────────────────────────────────────────────────────────────────
Lily Smith (9)                owes $185.00              [Remembered]
  "Reference says LILY; this account has paid Lily's invoices before."
  Invoice 28 Sep · Dr A · Exam, 2 fillings · $185.00
  Family: Jane Smith ($0), Tom Smith (12, owes $90)
  This account has paid for: Lily ×2, Tom ×1
                                                            [OK]
2 other possible matches ▾
                         [Find & Match]  [Exclude…]
```

For each candidate:
- name and age, to tell people with the same name apart;
- each unpaid invoice with its date, practitioner, a short treatment summary and the amount;
- family members and what they owe, if Principle exposes family links (Phase 0);
- who this bank account has paid for before;
- the reason and the method label.

The deposit is shown exactly as the bank sent it, including the account number.

**Find & Match** searches patients by name or invoice number. It lists a patient's unpaid
invoices of any age, oldest first, with tick boxes and amounts pre-filled. Staff can add other
patients, for example siblings. The last invoice can be **Split** to make a part payment, which
is how a weekly $30 plan payment is applied. OK is enabled when the remaining amount is $0.00.

If the deposit is more than all of the patient's unpaid invoices, the extra cannot be recorded
through the API, because the API has no way to create an account credit. The deposit is matched
as far as the invoices go, and the row stays flagged **"Record $X credit manually in
Principle"** until someone ticks it done. This gap is also listed in
`docs/principle/api-gaps.md`.

### Page layout (`/reconcile`, Jinja, minimal JS)
- Header: "Bank data as of …", and **Fetch now**.
- Tabs, as in Xero:
  - **To reconcile:** grouped into Batch deposits, Ready (a remembered or likely match), and
    Needs a person.
  - **Reconciled:** recent matches with method labels and reasons, plus Unreconcile and
    Remove & redo.
  - **Excluded:** with Undo.
  - **Remembered payers.**
- Every match is confirmed one row at a time. There is no confirm-all.

### Writing to Principle (ADR 0005 write rules)
**Confirming an individual match.** For each ticked invoice the page calls
`createTransaction` on `/v1/patients/{pid}/invoices/{iid}/transactions`. The call carries:
- the "bank transfer" type and provider (the values are verified in Phase 0);
- the amount;
- `createdAt` set to the bank date;
- `reference` set to the Akahu id, so every payment can be traced back to its deposit.

The steps are:
1. Set the deposit to `recording` and write the `bank_matches` rows **before** calling the API.
2. On success, store each returned transaction id. When all of them are stored, the deposit
   becomes `matched` and the `payer_links` row is written.
3. On an uncertain failure (a timeout or a 5xx error), set the deposit to `check`, and **never
   retry automatically**. A **Check** button looks for our reference with `listTransactions` on
   each invoice. It sets the deposit to `matched` or back to `open`.

**Remove & redo** reverses only payments with `created_here` set. It uses `updateTransaction`
to void them; Phase 0 verifies that this is possible and what Principle shows afterwards. If
Principle cannot void through the API, Remove & redo stops and we come back to this design
before Phase 1. The deposit returns to `open`. If the match was labelled **Remembered**, the
confirmation asks "Forget that this account pays for <patient>?" so a wrong link is dealt with
at the moment it is found.

**Unreconcile** deletes only the page's own `bank_matches` rows, and never touches Principle.

Every write to Principle, and every exclusion, goes into the audit log (`audit.py`) with the
staff member's email.

## Phases

### Phase 0: verify before building
**Principle staging**, following `skills/principle-staging-browser/SKILL.md`:
- Create, then void, a bank-transfer payment. Record:
  - the valid `type`, `provider` and `status` values;
  - whether invoice `status` and `paidAt` and the patient's `accountSummary` update;
  - part-payment behaviour;
  - what Principle's own screens show.
- Whether Smartpay and Paymark card payments are recorded under different methods.
- Which payment date a takings day is grouped by: the date the payment was entered, or the
  appointment date. Check it against a month of real deposits.
- That ACC payments, like Southern Cross, are entered per patient before the deposit arrives.
- Whether `createTransaction` can record a card payment (Smartpay method) against an invoice
  for a past date, for **Record payment**.
- How card, Southern Cross and ACC payments are stored: their method or provider values,
  whether `createdAt` is the entry date, and whether the insurer batch number is kept.
- The cheapest way to list patients with an outstanding balance and their unpaid invoices.
- Whether family or guarantor links can be read.

Findings go in `docs/principle/`, and gaps in `api-gaps.md`.

**Akahu:** create the personal app and connect the account. Dump a month of transactions to
confirm, for this bank:
- which fields arrive;
- whether `other_account` is present on automatic payments;
- how Smartpay and Paymark lines show their takings date.

**Smartpay:** find out whether the merchant portal has an API or a downloadable transaction
report, and whether its settlement amount is gross or net of fees. A fee deducted from the
deposit would make every day disagree by the fee.

**Replay of differences:** for the last month, record how many card days didn't add up and
what caused each one (forgotten payment, wrong method, wrong day, duplicate, other).

**Replay:** run a month of real deposits through the batch arithmetic and the candidate
narrowing, using an offline script that is not committed.

**Milestone 0:**
- Every field value used above is verified.
- The replay shows how many card days add up exactly.
- The replay shows how many individual deposits land in Ready.
- Remove & redo is confirmed possible on staging.

### Phase 1: build
1. `Settings`: the Akahu credentials, the batch rules and the matching model. A small Akahu
   client using httpx, with a fake transport for tests.
2. The tables in `storage.py`.
3. The batch arithmetic, written as a pure function.
4. Candidate narrowing (pure function) and the LLM ranking call.
5. The page, the routes and the write path, following the states above.
6. Fake Principle: add verified routes for invoices and transactions to `tests/fake/`. Update
   `tests/test_fake_covers_catalogue.py`, which currently expects invoices to be unhandled.
7. Tests (ADR 0004):
   - a card day that adds up, and one that is short;
   - a settlement two days late, and one covering two days;
   - a queue item older than 5 days shown in red;
   - two equal combinations, where nothing is pre-selected;
   - a remembered payer, which skips the LLM;
   - a split that must reach $0.00;
   - an uncertain write, which goes to `check` and is never retried;
   - Remove & redo only voids payments the page created;
   - re-fetching does not duplicate deposits;
   - e2e: confirming a match creates the transaction on the fake Principle.
8. An integration test on staging: one real create and void round trip.

**Milestone 1:** a week of real deposits is reconciled on the page alongside the current manual
process, with no disagreements, before the manual process stops.

## Usages left out
- Reconciling outgoing payments.
- A way to edit the batch rules from the page. They live in `Settings` until they change often
  enough to need one.
- The Smartpay transaction list, if Phase 0 finds no API or report to read it from.
- Correcting a payment's method or date from the page.
- Bulk confirm.

## Code budget
`tests/test_budget.py` caps application code at 2,000 lines. It is already at 2,761, so
`hermetic` fails today. This feature (an estimated 450–550 lines) cannot merge until the cap is
raised or other code is trimmed. That decision is open.

## Risks
- **Wrong patient credited.** Mitigations:
  - visible reasons and method labels;
  - per-row OK;
  - a traceable reference on each payment;
  - Remove & redo;
  - the audit log.
- **Bank connection expires unnoticed.** Mitigated by the "Bank data as of" banner.
- **A duplicate payment from a retried write.** Mitigated by never retrying automatically, and
  by the `check` state with lookup by reference.
- **API behaviour isn't verified.** Phase 1 does not start until Phase 0 records it.
