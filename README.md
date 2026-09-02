# CEO Escalation Handling

A multi-agent [LangGraph](https://langchain-ai.github.io/langgraph/) app that triages, summarizes,
and routes escalated issues to the right internal team — with context and a recommended action —
and never sends anything until a human approves it. Escalations can arrive by Gmail (polled) or be
submitted directly through a JWT-protected FastAPI service, with an `operator`/`approver` role
split enforcing that the person who submits an escalation isn't the one who approves sending it.

## Highlights

- **3-agent LangGraph pipeline** (Triage → Summarize → Route), each making its own Claude call
  with structured-output parsing (Pydantic) rather than one call reused three times.
- **Human-in-the-loop approval gate** via LangGraph `interrupt()` / `Command(resume=...)`,
  persisted with a SQLite checkpointer so pending approvals survive process restarts — nothing
  is ever sent automatically.
- **Provider-abstracted LLM client**: Claude on Amazon Bedrock (`langchain-aws`) is primary, with
  the direct Anthropic API client kept live as an automatic fallback on either construction or
  invocation failure — not just an unused alternate code path (see `agents/llm.py`).
- **DynamoDB-backed routing + audit trail**: an append-only, ID-keyed event log (every triage/
  summarize/route/approve/dispatch step recorded permanently) with an O(1) current-state snapshot
  and a sparse GSI for the pending-approvals queue — see `dynamodb_schema.md`.
- **JWT-protected FastAPI service** with real separation of duties: `operator` role submits
  escalations, `approver` role resolves the approval gate — two different people, enforced by role,
  not just access control for its own sake.
- **CLI-driven ops** as an alternative to the API: a polling daemon (`main.py run`) and an
  interactive approval queue (`main.py review`).
- **Test suite** (`pytest`) validates the full graph and the full API — pause-at-approval,
  approve-sends, reject-doesn't-send, the not-a-real-escalation short circuit, JWT role
  enforcement, and the LLM provider fallback — against mocked LLM/Gmail/DynamoDB (via `moto`)
  calls, so it runs offline with zero API/AWS cost.

## Architecture

```
Gmail inbox (label:escalation, unread)  ──┐
                                           │  poll_once()
POST /escalations (operator role) ────────┼──────────────┐
                                           ▼              │
                                  pipeline.submit_escalation
                                           │
                                           ▼
 ┌─────────────┐   not escalation   ┌──────────┐
 │   triage    │───────────────────▶│ archive  │──▶ END
 └─────────────┘                    └──────────┘
        │ genuine escalation
        ▼
 ┌─────────────┐
 │ summarize   │
 └─────────────┘
        ▼
 ┌─────────────┐   team taxonomy + owner_email from DynamoDB
 │   route     │◀──────────────────────────────────────────
 └─────────────┘
        ▼
 ┌─────────────────┐   interrupt() — pauses graph, persisted via SqliteSaver
 │ human_approval   │◀── POST /approve (approver role) / `main.py review`
 └─────────────────┘
        │approved                 │rejected
        ▼                         ▼
 ┌─────────────┐            ┌──────────┐
 │  dispatch   │            │ archive  │
 │ (send email)│            └──────────┘
 └─────────────┘
        ▼
       END
```

Three agents make three distinct Claude calls with three distinct prompts, via a provider-
abstracted client (Bedrock primary, direct Anthropic fallback — `agents/llm.py`):

- **Triage** (`agents/triage.py`) — is this a real escalation, how severe, what are the facts?
- **Summarizer** (`agents/summarizer.py`) — a concise brief for the receiving team.
- **Router** (`agents/router.py`) — which team owns it (from the live team taxonomy in
  DynamoDB) and what should they do about it?

`pipeline.py` holds the shared "submit an escalation" / "resolve an approval" logic used by both
the Gmail poller (CLI or the API's background task) and the API's `/escalations`/`/approve`
routes, so there's exactly one code path that invokes the graph — see its module docstring for
why a lock guards those calls.

## Why operator/approver roles

`POST /escalations` (submit) requires the `operator` role; `POST /approve` (resolve the
human-approval gate) requires `approver`. This isn't decorative — it's a real separation of
duties: whoever raises an escalation shouldn't be the one who signs off on sending it out. Auth
is JWT (`python-jose`), issued by a dev-only `POST /token` endpoint backed by two hardcoded
demo accounts (`operator1`/`approver1` — see `auth.py`'s module docstring). Swap that module for
a real user store/IdP before this touches anything that matters.

## Prerequisites

- Python 3.11+
- AWS access (standard credential chain — `aws configure`, an IAM role, or SSO) for Bedrock and
  DynamoDB, or Docker for local-only iteration against DynamoDB Local (Bedrock still needs real
  AWS access; there's no local Bedrock emulator)
- An Anthropic API key (kept as the automatic fallback if Bedrock is unavailable)
- A Google Cloud project with the Gmail API enabled

## Setup

1. Create a virtualenv and install dependencies:

   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and fill in `ANTHROPIC_API_KEY`, `BEDROCK_MODEL_ID`, and
   `JWT_SECRET_KEY` at minimum.

3. **AWS / Bedrock setup**:
   - Configure AWS credentials via the standard chain (`aws configure`, an SSO profile, or an
     IAM role) — no keys are hardcoded or required in `.env`.
   - In the AWS Bedrock console, request **model access** for your chosen Claude model. This is
     a per-account, per-region grant and the most common first-run gotcha — API calls fail until
     it's approved.
   - Copy the exact Bedrock model ID (or inference-profile ARN) into `BEDROCK_MODEL_ID`; it
     varies by account/region and isn't guessed here.

4. **DynamoDB setup** — either DynamoDB Local for zero-AWS-cost local iteration, or real AWS:

   ```bash
   docker compose up -d                                   # DynamoDB Local on :8000
   DYNAMODB_ENDPOINT_URL=http://localhost:8000 python -m scripts.dynamodb_setup
   ```

   Or, against real AWS DynamoDB, leave `DYNAMODB_ENDPOINT_URL` unset and just run
   `python -m scripts.dynamodb_setup`. Either way this creates the `escalations`/`teams` tables
   (idempotent) and seeds the standard 8-team set (Engineering, Product, Sales, Customer Support,
   Legal, Finance, HR, Security/Trust) with placeholder `*@example.com` owner emails. See
   `dynamodb_schema.md` for the full table/key/GSI design.

5. **Gmail OAuth setup** (Google Cloud Console):
   - Create/select a project, enable the **Gmail API**.
   - Configure the OAuth consent screen (External or Internal; add yourself as a test user).
   - Under Credentials, create an **OAuth client ID** of type **Desktop app**.
   - Download the JSON and save it as `data/credentials.json`.
   - The first time the poller runs (via `main.py run` or the API's background task), a browser
     window opens for consent and `data/token.json` is created/cached automatically after that.

6. **Gmail label**: create a Gmail label (e.g. `escalation`) and a filter/rule that applies it
   to incoming mail you want this app to consider. Only messages matching `GMAIL_QUERY_FILTER`
   (default `is:unread label:escalation`) are pulled in — this is your control over what reaches
   Claude via the Gmail path (escalations submitted through `POST /escalations` bypass this
   entirely).

## Editing team routing

```bash
aws dynamodb update-item \
  --table-name teams \
  --key '{"name": {"S": "Engineering"}}' \
  --update-expression "SET owner_email = :e" \
  --expression-attribute-values '{":e": {"S": "actual-team-lead@yourcompany.com"}}'
```

(swap in `--endpoint-url http://localhost:8000` for DynamoDB Local). You can also add/remove
teams — the router agent reads the taxonomy live from DynamoDB on every run, so there's no code
change needed. Re-running `python -m scripts.dynamodb_setup` is always safe: it never overwrites
an existing team's `owner_email`.

## Running

### As the API (recommended once you're using auth/roles)

```bash
uvicorn api:app --port 8080
```

(port `8080`, not `8000` — DynamoDB Local's default port is `8000`.) With `ENABLE_POLLER=true`
(the default), the API also runs the Gmail poller as a background task, so this one process is
the only thing touching `CHECKPOINT_DB_PATH` — see **Concurrency** below for why that matters.

Example flow:

```bash
TOKEN=$(curl -s -X POST localhost:8080/token -d "username=operator1&password=$DEV_OPERATOR_PASSWORD" | jq -r .access_token)
curl -X POST localhost:8080/escalations -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"sender":"a@example.com","subject":"Test","raw_body":"Angry customer, outage."}'

APPROVER_TOKEN=$(curl -s -X POST localhost:8080/token -d "username=approver1&password=$DEV_APPROVER_PASSWORD" | jq -r .access_token)
curl -X POST localhost:8080/approve -H "Authorization: Bearer $APPROVER_TOKEN" -H "Content-Type: application/json" \
  -d '{"escalation_id":"manual-...","decision":"approved"}'

curl localhost:8080/audit/manual-... -H "Authorization: Bearer $TOKEN"
```

### As the CLI (alternative, no auth)

```bash
# Terminal 1 — polls Gmail continuously, runs escalations up to the approval gate
python main.py run

# Terminal 2 — whenever you want to process what's pending
python main.py review
```

`review` lists every escalation currently paused at the approval gate and lets you
`[a]pprove`, `[r]eject`, `[e]dit` (change the team and/or the recommended action before
approving), or `[s]kip` each one.

### Concurrency

`compiled_graph.invoke()` reads/writes a single shared `SqliteSaver` checkpoint connection.
**Don't run `main.py run`/`review` at the same time as `uvicorn api:app` against the same
`CHECKPOINT_DB_PATH`** — pick one. When the API is running, use it (`/escalations`, `/approve`)
rather than the CLI. `pipeline.py` serializes the two `compiled_graph.invoke(...)` call sites
with a lock so concurrent requests within one process don't race each other, but two separate
processes both writing to the same sqlite file is still unsafe.

## Testing

```bash
pip install -r requirements.txt   # includes pytest, moto, httpx
pytest
```

Runs fully offline at zero API/AWS cost:

- `tests/test_graph_flow.py` — feeds a synthetic escalation straight into the compiled graph
  (Claude and Gmail calls mocked) and verifies the pause-at-approval / approve-sends /
  reject-doesn't-send / not-a-real-escalation-short-circuits behaviors.
- `tests/test_api.py` — the same round trips, but driven through FastAPI's `TestClient`: JWT
  issuance, role enforcement (operator can't approve), and the full submit→approve/reject flow.
- `tests/test_llm_fallback.py` — confirms the Bedrock/Anthropic fallback wrapper actually falls
  back on construction or invocation failure, and never touches the secondary provider when the
  primary succeeds.
- `tests/test_db.py` — DynamoDB CRUD, duplicate-id rejection, history recording (and the
  no-top-level-`status`-on-history-items invariant the sparse GSI depends on), and the
  pending-approvals GSI query — all against `moto`'s in-process DynamoDB mock.

Manual smoke tests (not required for the automated suite):

- **DynamoDB Local**: `docker compose up -d` → `python -m scripts.dynamodb_setup` → run the API
  or CLI against it via `DYNAMODB_ENDPOINT_URL=http://localhost:8000`.
- **Real Bedrock**: with AWS creds + a verified `BEDROCK_MODEL_ID` set, run `main.py run` against
  a real labeled test email and confirm triage/summarize/route succeed via Bedrock (check logs
  for the absence of a fallback warning); then temporarily set an invalid `BEDROCK_MODEL_ID` to
  confirm the fallback path actually engages and logs a warning before reverting.
- **Real Gmail**: apply the `escalation` label to a test email, run `main.py run` (or the API)
  with a short poll interval, and confirm it shows up in `main.py review` / `GET /audit/{id}`.
