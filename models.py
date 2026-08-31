"""SQLAlchemy ORM models: team routing taxonomy + escalation audit trail."""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, Enum, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


ESCALATION_STATUSES = (
    "pending_triage",
    "not_escalation",
    "pending_approval",
    "approved",
    "rejected",
    "sent",
    "failed",
    "archived",
)

escalation_status_enum = Enum(*ESCALATION_STATUSES, name="escalation_status")


class Team(Base):
    """Routing taxonomy: which team owns which kind of escalation."""

    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("name", name="uq_teams_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    owner_email: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Escalation(Base):
    """One row per escalation email — full audit trail of the pipeline's run."""

    __tablename__ = "escalations"
    __table_args__ = (UniqueConstraint("gmail_message_id", name="uq_escalations_gmail_message_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)

    gmail_message_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    gmail_thread_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    sender: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(Text)
    raw_body: Mapped[str | None] = mapped_column(Text)

    # triage
    is_genuine_escalation: Mapped[bool | None] = mapped_column(nullable=True)
    severity: Mapped[str | None] = mapped_column(Text, nullable=True)
    urgency_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_facts: Mapped[str | None] = mapped_column(Text, nullable=True)  # newline-joined bullets

    # summarizer
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # router
    routed_team: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_email: Mapped[str | None] = mapped_column(Text, nullable=True)

    # status / lifecycle
    status: Mapped[str] = mapped_column(escalation_status_enum, nullable=False, default="pending_triage")
    approver_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # denormalized copy of f"escalation-{gmail_message_id}", cached for convenience
    thread_checkpoint_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
