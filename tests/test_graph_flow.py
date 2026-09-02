"""End-to-end graph verification without a live inbox or real Claude calls."""

from __future__ import annotations

from langgraph.types import Command

import db


def _initial_state(message_id: str) -> dict:
    escalation_id = db.create_escalation(
        gmail_message_id=message_id,
        gmail_thread_id="thread-1",
        sender="customer@bigcorp.com",
        subject="Repeated outages",
        raw_body="We've had three outages this week and are considering switching vendors.",
    )
    return {
        "escalation_id": escalation_id,
        "gmail_message_id": message_id,
        "gmail_thread_id": "thread-1",
        "sender": "customer@bigcorp.com",
        "subject": "Repeated outages",
        "raw_body": "We've had three outages this week and are considering switching vendors.",
    }


def test_pauses_at_human_approval(patch_agents, mock_gmail):
    patch_agents()
    from graph import compiled_graph, thread_id_for

    message_id = "msg-pause-1"
    state = _initial_state(message_id)
    config = {"configurable": {"thread_id": thread_id_for(state["escalation_id"])}}

    compiled_graph.invoke(state, config=config)

    snapshot = compiled_graph.get_state(config)
    assert snapshot.next == ("human_approval",)

    escalation = db.get_escalation(state["escalation_id"])
    assert escalation["status"] == "pending_approval"
    assert escalation["routed_team"] == "Engineering"
    assert escalation["owner_email"] == "engineering@example.com"

    # Nothing is sent just by reaching the gate.
    mock_gmail["send_email"].assert_not_called()


def test_resume_approved_sends_email(patch_agents, mock_gmail):
    patch_agents()
    from graph import compiled_graph, thread_id_for

    message_id = "msg-approve-1"
    state = _initial_state(message_id)
    config = {"configurable": {"thread_id": thread_id_for(state["escalation_id"])}}
    compiled_graph.invoke(state, config=config)

    compiled_graph.invoke(
        Command(
            resume={
                "decision": "approved",
                "final_team": "Engineering",
                "final_action": "Fix it now.",
                "notes": "looks right",
            }
        ),
        config=config,
    )

    mock_gmail["send_email"].assert_called_once()
    _, kwargs = mock_gmail["send_email"].call_args
    assert kwargs["to"] == "engineering@example.com"

    escalation = db.get_escalation(state["escalation_id"])
    assert escalation["status"] == "sent"


def test_resume_rejected_does_not_send(patch_agents, mock_gmail):
    patch_agents()
    from graph import compiled_graph, thread_id_for

    message_id = "msg-reject-1"
    state = _initial_state(message_id)
    config = {"configurable": {"thread_id": thread_id_for(state["escalation_id"])}}
    compiled_graph.invoke(state, config=config)

    compiled_graph.invoke(
        Command(resume={"decision": "rejected", "notes": "not actually urgent"}),
        config=config,
    )

    mock_gmail["send_email"].assert_not_called()

    escalation = db.get_escalation(state["escalation_id"])
    assert escalation["status"] == "rejected"


def test_not_genuine_escalation_short_circuits(monkeypatch, stub_llm, mock_gmail):
    from agents.triage import TriageResult

    monkeypatch.setattr(
        "agents.triage.get_llm",
        lambda: stub_llm(
            TriageResult(
                is_genuine_escalation=False,
                severity="low",
                urgency_notes="Just a newsletter.",
                key_facts=[],
            )
        ),
    )

    def _fail(*_args, **_kwargs):
        raise AssertionError("summarizer/router should not run for a non-escalation")

    monkeypatch.setattr("agents.summarizer.get_llm", _fail)
    monkeypatch.setattr("agents.router.get_llm", _fail)

    from graph import compiled_graph, thread_id_for

    message_id = "msg-noise-1"
    state = _initial_state(message_id)
    config = {"configurable": {"thread_id": thread_id_for(state["escalation_id"])}}

    compiled_graph.invoke(state, config=config)

    snapshot = compiled_graph.get_state(config)
    assert snapshot.next == ()

    escalation = db.get_escalation(state["escalation_id"])
    assert escalation["status"] == "not_escalation"

    mock_gmail["send_email"].assert_not_called()
    mock_gmail["mark_processed"].assert_called_once()
