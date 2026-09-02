"""The LangGraph pipeline: triage -> summarize -> route -> human approval -> dispatch/archive."""

from __future__ import annotations

import datetime
import logging

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

import db
import gmail_client
from agents.router import router_node
from agents.summarizer import summarizer_node
from agents.triage import triage_node
from config import CONFIG
from state import EscalationState

logger = logging.getLogger(__name__)


def thread_id_for(escalation_id: str) -> str:
    return f"escalation-{escalation_id}"


def _now() -> str:
    """ISO-8601 timestamp string — DynamoDB (via boto3) can't serialize raw datetime objects."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def human_approval_node(state: EscalationState) -> dict:
    """Pause here until the CEO reviews and resumes with a decision (see main.py `review`)."""
    decision = interrupt(
        {
            "escalation_id": state.get("escalation_id"),
            "sender": state.get("sender"),
            "subject": state.get("subject"),
            "severity": state.get("severity"),
            "summary": state.get("summary"),
            "routed_team": state.get("routed_team"),
            "recommended_action": state.get("recommended_action"),
            "owner_email": state.get("owner_email"),
        }
    )

    approval_status = "approved" if decision.get("decision") == "approved" else "rejected"
    final_team = decision.get("final_team") or state.get("routed_team", "")
    final_action = decision.get("final_action") or state.get("recommended_action", "")
    notes = decision.get("notes", "")

    db.update_escalation(
        state["escalation_id"],
        status=approval_status,
        approver_notes=notes,
        approved_at=_now(),
    )

    return {
        "approval_status": approval_status,
        "approver_notes": notes,
        "final_team": final_team,
        "final_action": final_action,
    }


def dispatch_node(state: EscalationState) -> dict:
    final_team = state.get("final_team") or state.get("routed_team", "")
    owner_email = db.get_owner_email(final_team) or state.get("owner_email", "")

    subject = f"[Escalation] {state.get('subject', '(no subject)')}"
    body = (
        f"Routed team: {final_team}\n"
        f"Severity: {state.get('severity', '')}\n\n"
        f"Summary:\n{state.get('summary', '')}\n\n"
        f"Recommended action:\n{state.get('final_action') or state.get('recommended_action', '')}\n\n"
        f"---\n"
        f"Original sender: {state.get('sender', '')}\n"
        f"Original subject: {state.get('subject', '')}\n"
    )

    try:
        service = gmail_client.get_gmail_service()
        gmail_client.send_email(service, to=owner_email, subject=subject, body=body)
        db.update_escalation(state["escalation_id"], status="sent", sent_at=_now())
        _mark_processed_if_from_gmail(state)
        return {"dispatch_status": "sent"}
    except Exception as exc:  # noqa: BLE001 - a failed send must not crash the poller
        logger.exception("Failed to dispatch escalation %s", state.get("escalation_id"))
        db.update_escalation(state["escalation_id"], status="failed", error=str(exc))
        return {"dispatch_status": "failed", "error": str(exc)}


def archive_not_escalation_node(state: EscalationState) -> dict:
    db.update_escalation(state["escalation_id"], status="not_escalation")
    _mark_processed_if_from_gmail(state)
    return {"dispatch_status": "archived"}


def archive_rejected_node(state: EscalationState) -> dict:
    db.update_escalation(state["escalation_id"], status="rejected")
    _mark_processed_if_from_gmail(state)
    return {"dispatch_status": "archived"}


def _mark_processed_if_from_gmail(state: EscalationState) -> None:
    """Escalations submitted manually via the API have no Gmail message to label."""
    gmail_message_id = state.get("gmail_message_id")
    if not gmail_message_id:
        return
    service = gmail_client.get_gmail_service()
    gmail_client.mark_processed(service, gmail_message_id)


def _route_after_triage(state: EscalationState) -> str:
    return "summarize" if state.get("is_genuine_escalation") else "archive_not_escalation"


def _route_after_approval(state: EscalationState) -> str:
    return "dispatch" if state.get("approval_status") == "approved" else "archive_rejected"


def build_graph() -> StateGraph:
    graph = StateGraph(EscalationState)

    graph.add_node("triage", triage_node)
    graph.add_node("summarize", summarizer_node)
    graph.add_node("route", router_node)
    graph.add_node("human_approval", human_approval_node)
    graph.add_node("dispatch", dispatch_node)
    graph.add_node("archive_not_escalation", archive_not_escalation_node)
    graph.add_node("archive_rejected", archive_rejected_node)

    graph.set_entry_point("triage")

    graph.add_conditional_edges(
        "triage",
        _route_after_triage,
        {"summarize": "summarize", "archive_not_escalation": "archive_not_escalation"},
    )
    graph.add_edge("summarize", "route")
    graph.add_edge("route", "human_approval")

    graph.add_conditional_edges(
        "human_approval",
        _route_after_approval,
        {"dispatch": "dispatch", "archive_rejected": "archive_rejected"},
    )

    graph.add_edge("dispatch", END)
    graph.add_edge("archive_not_escalation", END)
    graph.add_edge("archive_rejected", END)

    return graph


def _make_checkpointer():
    import os

    os.makedirs(os.path.dirname(CONFIG.checkpoint_db_path) or ".", exist_ok=True)
    return SqliteSaver.from_conn_string(CONFIG.checkpoint_db_path)


# Module-level singletons: one SqliteSaver connection, one compiled graph per process.
# `run` and `review` are documented as separate OS processes so they don't contend
# for the same sqlite connection.
_checkpointer_cm = _make_checkpointer()
checkpointer = _checkpointer_cm.__enter__()
compiled_graph = build_graph().compile(checkpointer=checkpointer)
