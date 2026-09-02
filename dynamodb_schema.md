# DynamoDB schema

Two tables. Both created (idempotently) by `python -m scripts.dynamodb_setup`; see `db.py` for
the read/write access patterns that use them.

## Why two item shapes in one table (`escalations`)

An escalation record is naturally an **append-only, ID-keyed audit log**: every triage/summarize/
route/approve/dispatch step should be recorded permanently, but the app also constantly needs
"what does this escalation look like *right now*" without replaying its whole history. Rather
than picking one of those and faking the other, `escalations` stores both, distinguished by sort
key:

- **`sort_key = "LATEST"`** — one item per escalation, upserted on every write. The full current
  state snapshot. This is what `get_escalation(id)` reads: an O(1) `GetItem`.
- **`sort_key = "<ISO timestamp>#<8 hex chars>"`** — one item per `create_escalation`/
  `update_escalation` call, immutable, never overwritten. This is the true audit trail —
  `get_escalation_history(id)` reads these via a `Query`.

The random hex suffix guards against two writes landing in the same microsecond and colliding on
sort key. It has a convenient side effect too: `"LATEST"` (starts with `L`, ASCII 76) sorts
lexicographically *after* any ISO-8601 timestamp (starts with a digit, ASCII ≤ 57), so
`sort_key < "LATEST"` cleanly selects history-only items in chronological order, and the LATEST
item always lands last in a full forward scan of one escalation's items.

## `escalations` item shapes

**LATEST snapshot** (example):
```json
{
  "escalation_id": "18c9f2a1b2c3d4e5",
  "sort_key": "LATEST",
  "status": "pending_approval",
  "gmail_message_id": "18c9f2a1b2c3d4e5",
  "gmail_thread_id": "18c9f2a1b2c3d4e0",
  "sender": "customer@bigcorp.com",
  "subject": "Repeated outages",
  "raw_body": "We've had three outages this week...",
  "is_genuine_escalation": true,
  "severity": "high",
  "urgency_notes": "Customer threatening to churn.",
  "key_facts": "Customer X reported repeated outages.",
  "summary": "Customer X is hitting repeated outages and is at risk of churn.",
  "routed_team": "Engineering",
  "recommended_action": "Investigate the outage root cause today.",
  "owner_email": "engineering@example.com",
  "created_at": "2026-08-31T12:00:00.000000+00:00",
  "updated_at": "2026-08-31T12:05:00.000000+00:00"
}
```

**History event** (immutable — one per write):
```json
{
  "escalation_id": "18c9f2a1b2c3d4e5",
  "sort_key": "2026-08-31T12:05:00.000000+00:00#a1b2c3d4",
  "event_type": "updated",
  "changes": { "status": "pending_approval", "routed_team": "Engineering", "...": "..." },
  "updated_at": "2026-08-31T12:05:00.000000+00:00"
}
```

**Important invariant:** history items never carry a top-level attribute literally named
`status` — the same information lives nested inside `changes`. Only the LATEST item ever sets a
top-level `status`. This is what keeps the GSI below sparse (see next section) — get this wrong
and every historical status change would also show up as a "currently pending" row.

## `gsi_status` (Global Secondary Index on `escalations`)

- Partition key: `status` (S)
- Sort key: `updated_at` (S)
- Projection: `ALL`

Because only LATEST items carry a top-level `status` attribute, this GSI is automatically
**sparse** — it only ever indexes current snapshots, never history events. `get_pending_approvals()`
is a `Query` on this index (`status = "pending_approval"`), not a full table scan.

## `teams` item shape

```json
{ "name": "Engineering", "description": "Handles product bugs, outages, ...", "owner_email": "engineering@example.com" }
```

Partition key `name` only, no sort key, no GSI — 8 rows, a plain `Scan` (`get_team_taxonomy()`)
is fine at this scale.

## Access pattern → DynamoDB call

| Operation | Call |
|---|---|
| `get_team_taxonomy()` | `Scan` on `teams` |
| `get_owner_email(name)` | `GetItem` on `teams` |
| `create_escalation(**fields)` | Conditional `PutItem` (LATEST, `attribute_not_exists`) + `PutItem` (history event) |
| `update_escalation(id, **fields)` | Conditional `UpdateItem` (LATEST, `attribute_exists`) + `PutItem` (history event) |
| `get_escalation(id)` | `GetItem` on `escalations`, `sort_key="LATEST"` |
| `get_escalation_history(id)` | `Query` on `escalations`, `escalation_id=id AND sort_key < "LATEST"` |
| `get_pending_approvals()` | `Query` on `gsi_status`, `status="pending_approval"` |

## Billing mode

`PAY_PER_REQUEST` (on-demand) for both tables — this is low-volume, spiky, internal-tool
traffic (a handful of escalations at a time), so provisioned capacity planning would be pure
overhead with no benefit.

## Local dev vs. real AWS

Set `DYNAMODB_ENDPOINT_URL=http://localhost:8000` (see `docker-compose.yml`) to point at
DynamoDB Local; leave it unset to use real AWS DynamoDB via the standard credential chain.
