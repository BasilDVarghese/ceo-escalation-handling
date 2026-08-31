"""Create tables (if needed) and idempotently seed the standard team taxonomy.

Works against SQLite (local dev) or Postgres (prod) via SQLAlchemy — same code
path either way, so this is the primary seeding mechanism. schema.sql mirrors
this as Postgres-flavored documentation / a manual-psql alternative.

Usage:
    python -m scripts.seed
"""

from __future__ import annotations

from sqlalchemy import select

from db import engine, get_session, init_db
from models import Team

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


def seed_teams() -> None:
    init_db()
    with get_session() as session:
        existing_names = set(session.execute(select(Team.name)).scalars().all())
        for row in TEAM_SEED_DATA:
            if row["name"] in existing_names:
                continue
            session.add(Team(**row))
    print(f"Seeded teams table against {engine.url!r} (skipped any already present).")


if __name__ == "__main__":
    seed_teams()
