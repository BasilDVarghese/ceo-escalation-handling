"""Shared graph-invocation logic used by both main.py's CLI and api.py's routes.

A module-level lock serializes access to the shared SqliteSaver checkpoint connection —
see graph.py's module docstring / README's Concurrency note for why this matters once more
than one caller (CLI + API, or concurrent API requests) can invoke the graph in one process.
"""

from __future__ import annotations

import datetime
import logging
import threading

from langgraph.types import Command

import db
import gmail_client
from graph import compiled_graph, thread_id_for

logger = logging.getLogger(__name__)

_graph_lock = threading.Lock()


def submit_escalation(email: dict) -> str:
    """Create the DB record and run the graph up to the first interrupt/terminal state.

    `email` needs `raw_body` at minimum. `gmail_message_id`/`gmail_thread_id`/`sender`/
    `subject`/`received_at` are optional — absent for API-submitted escalations, which get a
    generated `manual-<uuid4>` escalation_id instead of reusing a Gmail message id.
    Returns the escalation_id.
    """
    escalation_id = db.create_escalation(
        gmail_message_id=email.get("gmail_message_id"),
        gmail_thread_id=email.get("gmail_thread_id"),
        sender=email.get("sender"),
        subject=email.get("subject"),
        raw_body=email.get("raw_body"),
        received_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )

    initial_state = {
        "escalation_id": escalation_id,
        "gmail_message_id": email.get("gmail_message_id", ""),
        "gmail_thread_id": email.get("gmail_thread_id", ""),
        "sender": email.get("sender", ""),
        "subject": email.get("subject", ""),
        "raw_body": email.get("raw_body", ""),
        "received_at": email.get("received_at", ""),
    }
    config = {"configurable": {"thread_id": thread_id_for(escalation_id)}}

    # Returns as soon as it hits the human_approval interrupt (or completes early,
    # e.g. if triage decided this wasn't a real escalation).
    with _graph_lock:
        compiled_graph.invoke(initial_state, config=config)

    logger.info("Processed escalation id=%s subject=%r", escalation_id, email.get("subject"))
    return escalation_id


def resolve_approval(escalation_id: str, decision: dict) -> dict:
    """Resume a paused human_approval interrupt with a decision.

    decision: {"decision": "approved"|"rejected", "final_team"?, "final_action"?, "notes"?}
    Returns db.get_escalation(escalation_id) after the resume completes.
    """
    config = {"configurable": {"thread_id": thread_id_for(escalation_id)}}
    with _graph_lock:
        compiled_graph.invoke(Command(resume=decision), config=config)
    return db.get_escalation(escalation_id)


def poll_once(service) -> list[str]:
    """One Gmail fetch-and-submit cycle. Returns the escalation_ids created this cycle.

    Logs and swallows per-email failures so one bad email doesn't kill the cycle, matching
    the original cmd_run loop's behavior.
    """
    try:
        emails = gmail_client.fetch_new_escalation_emails(service)
    except Exception:
        logger.exception("Failed to fetch emails this cycle; will retry next cycle")
        return []

    escalation_ids: list[str] = []
    for email in emails:
        try:
            escalation_ids.append(submit_escalation(email))
        except Exception:
            logger.exception("Failed to process email %s", email.get("gmail_message_id"))

    return escalation_ids
