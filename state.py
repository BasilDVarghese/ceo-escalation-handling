"""LangGraph state schema for the escalation-handling pipeline.

Every field is written by exactly one node (or the approval/dispatch nodes),
so this is a plain TypedDict with no reducers/merge logic required — a linear
pipeline with no concurrent writes to the same key.
"""

from __future__ import annotations

from typing import TypedDict


class EscalationState(TypedDict, total=False):
    # --- identity / persistence linkage ---
    escalation_id: int  # DB row id, set immediately after ingestion
    gmail_message_id: str
    gmail_thread_id: str

    # --- raw email ---
    sender: str
    subject: str
    raw_body: str
    received_at: str  # ISO timestamp

    # --- triage agent output ---
    is_genuine_escalation: bool
    severity: str  # "low" | "medium" | "high" | "critical"
    urgency_notes: str
    key_facts: list[str]

    # --- summarizer agent output ---
    summary: str

    # --- router agent output ---
    routed_team: str
    recommended_action: str
    owner_email: str

    # --- human approval ---
    approval_status: str  # "pending" | "approved" | "rejected"
    approver_notes: str
    final_team: str  # may differ from routed_team if the CEO edits it
    final_action: str  # may differ from recommended_action if the CEO edits it

    # --- dispatch ---
    dispatch_status: str  # "not_sent" | "sent" | "failed" | "archived"
    error: str | None
