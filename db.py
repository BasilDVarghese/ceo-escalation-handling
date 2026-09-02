"""DynamoDB access layer: table setup + query helpers used by the graph, pipeline, and CLI.

Two tables (see dynamodb_schema.md for the full design rationale):

- `escalations` — PK `escalation_id` (str), SK `sort_key` (str). One `"LATEST"` item per
  escalation holds the full current-state snapshot (a top-level `status` attribute, upserted
  on every `update_escalation` call). Every `create_escalation`/`update_escalation` call also
  writes an immutable history event item, sort-keyed by an ISO timestamp + random suffix, with
  all changed fields nested under `changes` — never a top-level `status` — so a GSI on `status`
  (`gsi_status`) stays sparse and only ever indexes current snapshots.
- `teams` — PK `name` (str), attributes `description`/`owner_email`. Small (8 rows); a plain
  Scan is fine, no GSI needed.

Table creation lives in scripts/dynamodb_setup.py, not here — this module only reads/writes.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from config import CONFIG

LATEST = "LATEST"


def get_dynamodb_resource():
    """Build the boto3 DynamoDB resource — shared with scripts/dynamodb_setup.py so table
    creation and normal reads/writes always target the same account/region/endpoint."""
    kwargs: dict[str, Any] = {}
    if CONFIG.aws_region:
        kwargs["region_name"] = CONFIG.aws_region
    if CONFIG.dynamodb_endpoint_url:
        kwargs["endpoint_url"] = CONFIG.dynamodb_endpoint_url
    return boto3.resource("dynamodb", **kwargs)


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


_dynamodb = get_dynamodb_resource()
_escalations_table = _dynamodb.Table(CONFIG.dynamodb_escalations_table)
_teams_table = _dynamodb.Table(CONFIG.dynamodb_teams_table)


def init_db() -> None:
    """Verify both tables exist. Does NOT create them — see scripts/dynamodb_setup.py."""
    for table in (_escalations_table, _teams_table):
        try:
            table.load()
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ResourceNotFoundException":
                raise RuntimeError(
                    f"DynamoDB table {table.name!r} does not exist. "
                    "Run `python -m scripts.dynamodb_setup` first."
                ) from exc
            raise


def get_team_taxonomy() -> list[dict[str, str]]:
    """Team name + description for the router agent's prompt (owner_email intentionally omitted)."""
    items = _teams_table.scan().get("Items", [])
    return [{"name": i["name"], "description": i["description"]} for i in items]


def get_owner_email(team_name: str) -> str | None:
    item = _teams_table.get_item(Key={"name": team_name}).get("Item")
    return item.get("owner_email") if item else None


def _new_escalation_id(fields: dict[str, Any]) -> str:
    return fields.get("gmail_message_id") or f"manual-{uuid.uuid4()}"


def _history_sort_key(now: str) -> str:
    return f"{now}#{uuid.uuid4().hex[:8]}"


def create_escalation(**fields: Any) -> str:
    escalation_id = fields.pop("escalation_id", None) or _new_escalation_id(fields)
    now = _now_iso()

    latest_item = {
        "escalation_id": escalation_id,
        "sort_key": LATEST,
        "status": fields.get("status", "pending_triage"),
        "created_at": now,
        "updated_at": now,
        **fields,
    }
    try:
        _escalations_table.put_item(
            Item=latest_item,
            ConditionExpression="attribute_not_exists(escalation_id)",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ValueError(f"Escalation {escalation_id!r} already exists")
        raise

    _escalations_table.put_item(
        Item={
            "escalation_id": escalation_id,
            "sort_key": _history_sort_key(now),
            "event_type": "created",
            "changes": dict(fields),
            "updated_at": now,
        }
    )
    return escalation_id


def update_escalation(escalation_id: str, **fields: Any) -> None:
    now = _now_iso()
    fields = {**fields, "updated_at": now}

    # Alias every attribute name defensively (not just reserved words like "status") so new
    # field names added later never collide with a DynamoDB reserved word.
    names: dict[str, str] = {}
    values: dict[str, Any] = {}
    sets: list[str] = []
    for i, (key, value) in enumerate(fields.items()):
        name_placeholder, value_placeholder = f"#f{i}", f":v{i}"
        names[name_placeholder] = key
        values[value_placeholder] = value
        sets.append(f"{name_placeholder} = {value_placeholder}")

    try:
        _escalations_table.update_item(
            Key={"escalation_id": escalation_id, "sort_key": LATEST},
            UpdateExpression="SET " + ", ".join(sets),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ConditionExpression="attribute_exists(escalation_id)",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ValueError(f"No escalation with id={escalation_id}")
        raise

    _escalations_table.put_item(
        Item={
            "escalation_id": escalation_id,
            "sort_key": _history_sort_key(now),
            "event_type": "updated",
            "changes": dict(fields),
            "updated_at": now,
        }
    )


def get_pending_approvals() -> list[dict]:
    resp = _escalations_table.query(
        IndexName="gsi_status",
        KeyConditionExpression=Key("status").eq("pending_approval"),
        ScanIndexForward=True,  # oldest-first: FIFO review queue
    )
    return resp.get("Items", [])


def get_escalation(escalation_id: str) -> dict | None:
    return _escalations_table.get_item(
        Key={"escalation_id": escalation_id, "sort_key": LATEST}
    ).get("Item")


def get_escalation_history(escalation_id: str) -> list[dict]:
    resp = _escalations_table.query(
        KeyConditionExpression=Key("escalation_id").eq(escalation_id) & Key("sort_key").lt(LATEST),
        ScanIndexForward=True,
    )
    return resp.get("Items", [])
