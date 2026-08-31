# CEO Escalation Handling

A multi-agent [LangGraph](https://langchain-ai.github.io/langgraph/) app that triages, summarizes,
and routes escalated issues arriving by email to the right internal team — with context and a
recommended action — and never sends anything until the CEO approves it.

## Architecture

```
Gmail inbox (label:escalation, unread)
        │  polling loop (main.py run)
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
 ┌─────────────┐   team taxonomy + owner_email from SQL DB
 │   route     │◀──────────────────────────────────────────
 └─────────────┘
        ▼
 ┌─────────────────┐   interrupt() — pauses graph, persisted via SqliteSaver
 │ human_approval   │
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

Three agents make three distinct Claude calls with three distinct prompts:

- **Triage** (`agents/triage.py`) — is this a real escalation, how severe, what are the facts?
- **Summarizer** (`agents/summarizer.py`) — a concise brief for the receiving team.
- **Router** (`agents/router.py`) — which team owns it (from the live team taxonomy in the
  database) and what should they do about it?

The graph then pauses at a human-approval gate (`langgraph.types.interrupt`) — nothing is ever
sent without a person confirming it via the `review` command.

## Prerequisites

- Python 3.11+
- A Postgres database (or SQLite for local dev/iteration)
- An Anthropic API key
- A Google Cloud project with the Gmail API enabled

## Setup

1. Create a virtualenv and install dependencies:

   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and fill in `ANTHROPIC_API_KEY` and `DATABASE_URL`.
   For quick local iteration, point `DATABASE_URL` at SQLite instead of Postgres:

   ```
   DATABASE_URL=sqlite:///data/dev.db
   ```

3. **Gmail OAuth setup** (Google Cloud Console):
   - Create/select a project, enable the **Gmail API**.
   - Configure the OAuth consent screen (External or Internal; add yourself as a test user).
   - Under Credentials, create an **OAuth client ID** of type **Desktop app**.
   - Download the JSON and save it as `data/credentials.json`.
   - The first time you run `python main.py run`, a browser window opens for consent and
     `data/token.json` is created/cached automatically after that.

4. **Gmail label**: create a Gmail label (e.g. `escalation`) and a filter/rule that applies it
   to incoming mail you want this app to consider (forwarding rule, sender-domain filter, etc.).
   Only messages matching `GMAIL_QUERY_FILTER` (default `is:unread label:escalation`) are pulled
   in — this is your control over what reaches Claude.

5. **Database setup**: create the database (if using Postgres: `createdb ceo_escalations`), then
   seed the team taxonomy:

   ```bash
   python -m scripts.seed
   ```

   This creates the `teams`/`escalations` tables if needed and seeds the standard 8-team set
   (Engineering, Product, Sales, Customer Support, Legal, Finance, HR, Security/Trust) with
   placeholder `*@example.com` owner emails. `schema.sql` has the same schema/seed data as raw
   Postgres DDL if you'd rather run it via `psql`.

## Editing team routing

Point each team at a real inbox before relying on this for real routing:

```sql
UPDATE teams SET owner_email = 'actual-team-lead@yourcompany.com' WHERE name = 'Engineering';
```

You can also add/remove teams — the router agent reads the taxonomy live from the database on
every run, so there's no code change needed.

## Running

Run these as two separate processes/terminals — `run` must never block on interactive input:

```bash
# Terminal 1 — polls Gmail continuously, runs escalations up to the approval gate
python main.py run

# Terminal 2 — whenever you want to process what's pending
python main.py review
```

`review` lists every escalation currently paused at the approval gate and lets you
`[a]pprove`, `[r]eject`, `[e]dit` (change the team and/or the recommended action before
approving), or `[s]kip` each one. Approving resumes the graph and sends the email immediately;
rejecting archives it — nothing is ever sent on a rejection.

## Testing

```bash
pip install -r requirements.txt  # includes pytest
pytest
```

- `tests/test_graph_flow.py` feeds a synthetic escalation straight into the compiled graph
  (Claude and Gmail calls mocked) and verifies: the graph pauses at the human-approval
  interrupt; approving resumes it and sends exactly one email; rejecting sends none; and a
  "not a real escalation" triage result short-circuits straight to archive without ever
  calling the summarizer or router.
- `tests/test_db.py` checks basic CRUD against a throwaway SQLite database.

To manually smoke-test the real Gmail OAuth + send path once you have real credentials, apply
the `escalation` label to a test email, run `python main.py run` with a short poll interval, and
confirm it shows up in `python main.py review`.
