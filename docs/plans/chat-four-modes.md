# Link the chat agent to Principle with the least code: MCP

## Context

The app is an internal chat interface to Principle Dental. A handful of staff use it, behind a
strict Google login. Its only job is to link the chatbot (ChatKit and the Agents SDK) to
Principle through four modes, all working from day 1:
1. API
2. Firestore
3. Scripted browser
4. AI-driven browser

This codebase doesn't control how the chatbot works: no turn limits, rate limits, result caps
or safeguard layers. The measure of success is fewest lines of code.

MCP is the standard link between agents and systems. The Agents SDK (0.22, installed) attaches
MCP servers natively: `Agent(mcp_servers=[...])`. So most of the link is off-the-shelf servers
plus configuration.

## The link

**`src/dental_practice_admin/mcp_server.py`** (ours, about 30 lines), a FastMCP server run
over stdio:
- **API:** `FastMCP.from_openapi(spec, client)` turns every operation in Principle's published
  spec into a tool. The client is an `httpx.AsyncClient` with the `X-API-Key` header and the
  configured base URL. There's no generator and no hand-written tools.
- **Firestore:** one pass-through tool, `firestore(method, path, body)`. It calls the
  Firestore REST API under the workspace's database with a Firebase ID token, from Firebase's
  REST sign-in using the UI credentials. The model writes the Firestore requests itself
  (`runQuery`, `runAggregationQuery`, get, patch), so one tool covers queries, reports and
  updates.

**Browser, both modes:** Microsoft's `@playwright/mcp` via `npx`, with a persistent profile
(`--user-data-dir` under the data directory).
- **Login:** a developer logs in to Principle once in that profile. The session persists, so
  there's no login code.
- **Scripted mode:** the agent runs steps recorded in the docs, using the server's own
  code-running and action tools.
- **AI-driven mode:** the same tools, exploring.

**`chat.py`:**
- `build_agent(..., mcp_servers=[principle, playwright])`, replacing `api_tools`.
- `INSTRUCTIONS` keeps only what links the two systems:
  - the four modes and their order of preference
  - the practice ID
  - the translation check the owner asked for: before a change, state what will change and
    check it against the request; after it, read the record back
  - the content of `principle_docs/` (one folder per mode), read at startup
- Remove `MAX_TURNS`, so the SDK default applies, and drop "read-only".

**`app.py`:** start the two servers in the FastAPI lifespan; every chat request shares them.

**Config:** `Settings` gains the UI email and password and the Firebase web API key, project
and database path, per environment.

## Delete (link code that's no longer needed)
- `principle_tools.py`: the 24k result cap and the coverage wrapper
- `scripts/address_via_*.py`
- the address-specific browser experiment on PRs #8/#11
  (`check_browser_address.py`, procedure, runbook, test) and the address parts of `SKILL.md`.
  Merge `origin/test/english-browser-address` first so its useful knowledge moves into
  `principle_docs/`, then delete it and close #8/#11.

**Owner's call, not done without a yes:** with the API served from the spec, the generator
pipeline only feeds the daily diary and the interface-warning banner:
- `refresh_spec.py`, `generated_principle.json`, `fingerprint.json`, `compatibility.json`
- `test_generated_principle.py`, and most of `principle.py`

Replacing the diary's few API calls with plain httpx would let all of that go: well over
1,000 lines.

## Verification
- **Hermetic:** one test lists the `principle` server's tools from a small fixture spec and
  calls an API tool and the Firestore tool against stub transports.
- **Day-1 acceptance on staging** (`scripts/run.py --preset staging`):
  - change a Crash Test Dummy's address once per mode, then restore it
  - "which patients changed today?"
  - "create a report on the average lab fee"
  - "move <test appointment> to 3pm"
  - a button-only action on the website
- **Line counts** from `scripts/code_size.py`, before and after, are reported in the PR.

## Delivery
- One PR, #12 (`chat-full-api`), merged after acceptance passes.
- Commit and push each step. When done: ready, then `gh pr merge --auto --merge`, then remove
  the worktree and branch.
