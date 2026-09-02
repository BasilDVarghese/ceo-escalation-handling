"""Create the DynamoDB tables (+ GSI) if needed, and idempotently seed the team taxonomy.

Works against DynamoDB Local (set DYNAMODB_ENDPOINT_URL) or real AWS DynamoDB — same code
path either way. See dynamodb_schema.md for the full table/key/GSI design.

Usage:
    python -m scripts.dynamodb_setup
"""

from __future__ import annotations

from botocore.exceptions import ClientError

from config import CONFIG
from db import get_dynamodb_resource

TEAM_SEED_DATA: list[dict[str, str]] = [
    {
        "name": "Engineering",
        "description": "Handles product bugs, outages, infrastructure, and technical failures.",
        "owner_email": "engineering@example.com",
    },
    {
        "name": "Product",
        "description": "Handles feature requests, product strategy, and roadmap-related escalations.",
        "owner_email": "product@example.com",
    },
    {
        "name": "Sales",
        "description": "Handles customer deal issues, contract negotiations, and revenue-impacting escalations.",
        "owner_email": "sales@example.com",
    },
    {
        "name": "Customer Support",
        "description": "Handles individual customer complaints, service quality, and support ticket escalations.",
        "owner_email": "support@example.com",
    },
    {
        "name": "Legal",
        "description": "Handles compliance, contracts, disputes, regulatory, and litigation matters.",
        "owner_email": "legal@example.com",
    },
    {
        "name": "Finance",
        "description": "Handles billing disputes, payment issues, refunds, and financial reporting concerns.",
        "owner_email": "finance@example.com",
    },
    {
        "name": "HR",
        "description": "Handles employee relations, workplace conduct, and internal personnel escalations.",
        "owner_email": "hr@example.com",
    },
    {
        "name": "Security/Trust",
        "description": "Handles security incidents, data breaches, fraud, and trust & safety issues.",
        "owner_email": "security@example.com",
    },
]


def create_tables(resource=None) -> None:
    resource = resource or get_dynamodb_resource()

    try:
        table = resource.create_table(
            TableName=CONFIG.dynamodb_escalations_table,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "escalation_id", "AttributeType": "S"},
                {"AttributeName": "sort_key", "AttributeType": "S"},
                {"AttributeName": "status", "AttributeType": "S"},
                {"AttributeName": "updated_at", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "escalation_id", "KeyType": "HASH"},
                {"AttributeName": "sort_key", "KeyType": "RANGE"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "gsi_status",
                    "KeySchema": [
                        {"AttributeName": "status", "KeyType": "HASH"},
                        {"AttributeName": "updated_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        )
        table.wait_until_exists()
        print(f"Created table {CONFIG.dynamodb_escalations_table!r}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceInUseException":
            raise
        print(f"Table {CONFIG.dynamodb_escalations_table!r} already exists, skipping.")

    try:
        table = resource.create_table(
            TableName=CONFIG.dynamodb_teams_table,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[{"AttributeName": "name", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "name", "KeyType": "HASH"}],
        )
        table.wait_until_exists()
        print(f"Created table {CONFIG.dynamodb_teams_table!r}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceInUseException":
            raise
        print(f"Table {CONFIG.dynamodb_teams_table!r} already exists, skipping.")


def seed_teams(resource=None) -> None:
    resource = resource or get_dynamodb_resource()
    teams_table = resource.Table(CONFIG.dynamodb_teams_table)

    seeded = 0
    for team in TEAM_SEED_DATA:
        try:
            teams_table.put_item(
                Item=team,
                ConditionExpression="attribute_not_exists(#n)",
                ExpressionAttributeNames={"#n": "name"},
            )
            seeded += 1
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            # Already present — don't clobber a manually-edited owner_email.

    print(f"Seeded {seeded} new team(s) into {CONFIG.dynamodb_teams_table!r} (existing rows untouched).")


def main() -> None:
    create_tables()
    seed_teams()


if __name__ == "__main__":
    main()
