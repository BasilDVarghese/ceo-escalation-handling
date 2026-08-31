from __future__ import annotations

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
    assert any(e.id == escalation_id for e in pending)

    escalation = db.get_escalation(escalation_id)
    assert escalation.summary == "a summary"
    assert escalation.status == "pending_approval"
