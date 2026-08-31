"""Database access layer: engine/session setup + query helpers used by the graph and CLI."""

from __future__ import annotations

import contextlib
import datetime
from typing import Any, Iterator

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from config import CONFIG
from models import Base, Escalation, Team

_connect_args: dict[str, Any] = {}
if CONFIG.database_url.startswith("sqlite"):
    # Allow the sqlite connection to be used across threads (dev convenience only).
    _connect_args = {"check_same_thread": False}

engine = create_engine(CONFIG.database_url, pool_pre_ping=True, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@contextlib.contextmanager
def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Create tables if they don't exist yet (dev/SQLite convenience path).

    In production against Postgres, prefer running schema.sql / db/seed.py directly.
    """
    Base.metadata.create_all(engine)


def get_team_taxonomy() -> list[dict[str, str]]:
    """Team name + description for the router agent's prompt (owner_email intentionally omitted)."""
    with get_session() as session:
        teams = session.execute(select(Team)).scalars().all()
        return [{"name": t.name, "description": t.description} for t in teams]


def get_owner_email(team_name: str) -> str | None:
    with get_session() as session:
        return session.execute(
            select(Team.owner_email).where(Team.name == team_name)
        ).scalar_one_or_none()


def create_escalation(**fields: Any) -> int:
    with get_session() as session:
        escalation = Escalation(**fields)
        session.add(escalation)
        session.flush()
        return escalation.id


def update_escalation(escalation_id: int, **fields: Any) -> None:
    with get_session() as session:
        escalation = session.get(Escalation, escalation_id)
        if escalation is None:
            raise ValueError(f"No escalation with id={escalation_id}")
        for key, value in fields.items():
            setattr(escalation, key, value)
        escalation.updated_at = datetime.datetime.now(datetime.timezone.utc)


def get_pending_approvals() -> list[Escalation]:
    with get_session() as session:
        return list(
            session.execute(
                select(Escalation).where(Escalation.status == "pending_approval")
            ).scalars()
        )


def get_escalation(escalation_id: int) -> Escalation | None:
    with get_session() as session:
        return session.get(Escalation, escalation_id)
