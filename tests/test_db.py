from __future__ import annotations

import pytest

import db

STANDARD_TEAM_NAMES = {
    "Engineering",
    "Product",
    "Sales",
    "Customer Support",
    "Legal",
    "Finance",
    "HR",
    "Security/Trust",
}


def test_get_team_taxonomy_has_standard_teams():
    teams = db.get_team_taxonomy()
    assert {t["name"] for t in teams} == STANDARD_TEAM_NAMES
    assert all(t["description"] for t in teams)


def test_get_owner_email_known_team():
    assert db.get_owner_email("Engineering") == "engineering@example.com"


def test_get_owner_email_unknown_team():
    assert db.get_owner_email("Not A Real Team") is None


def test_escalation_crud_roundtrip():
    escalation_id = db.create_escalation(
        gmail_message_id="crud-test-msg-1",
        sender="a@example.com",
        subject="Test",
        raw_body="body",
    )

    db.update_escalation(escalation_id, status="pending_approval", summary="a summary")

    pending = db.get_pending_approvals()
    assert any(e["escalation_id"] == escalation_id for e in pending)

    escalation = db.get_escalation(escalation_id)
    assert escalation["summary"] == "a summary"
    assert escalation["status"] == "pending_approval"


def test_create_escalation_duplicate_id_raises():
    db.create_escalation(gmail_message_id="dup-test-msg-1", sender="a@example.com")
    with pytest.raises(ValueError):
        db.create_escalation(gmail_message_id="dup-test-msg-1", sender="a@example.com")


def test_update_escalation_missing_id_raises():
    with pytest.raises(ValueError):
        db.update_escalation("does-not-exist", status="approved")


def test_get_escalation_history_records_create_and_update():
    escalation_id = db.create_escalation(
        gmail_message_id="history-test-msg-1", sender="a@example.com", subject="Test"
    )
    db.update_escalation(escalation_id, status="pending_approval", routed_team="Engineering")

    history = db.get_escalation_history(escalation_id)
    assert [event["event_type"] for event in history] == ["created", "updated"]

    # Invariant the sparse GSI depends on: history items never carry a top-level "status".
    for event in history:
        assert "status" not in event
    assert history[1]["changes"]["status"] == "pending_approval"


def test_get_pending_approvals_excludes_non_pending():
    id_a = db.create_escalation(gmail_message_id="pending-filter-a", sender="a@example.com")
    id_b = db.create_escalation(gmail_message_id="pending-filter-b", sender="b@example.com")
    db.update_escalation(id_a, status="pending_approval")
    db.update_escalation(id_b, status="sent")

    pending_ids = {e["escalation_id"] for e in db.get_pending_approvals()}
    assert id_a in pending_ids
    assert id_b not in pending_ids
