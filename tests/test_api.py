"""FastAPI TestClient tests: auth, role enforcement, and the full submit->approve/reject
round trip through the API alone. Everything mocked (LLM stubs, Gmail, DynamoDB via moto) —
runs fully offline.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from config import CONFIG


def _login(client: TestClient, username: str, password: str) -> str:
    resp = client.post("/token", data={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_token_issues_jwt_with_correct_role():
    from api import app

    with TestClient(app) as client:
        operator_token = _login(client, "operator1", CONFIG.dev_operator_password)
        approver_token = _login(client, "approver1", CONFIG.dev_approver_password)

    from jose import jwt

    operator_claims = jwt.decode(operator_token, CONFIG.jwt_secret_key, algorithms=[CONFIG.jwt_algorithm])
    approver_claims = jwt.decode(approver_token, CONFIG.jwt_secret_key, algorithms=[CONFIG.jwt_algorithm])
    assert operator_claims["role"] == "operator"
    assert approver_claims["role"] == "approver"


def test_token_rejects_bad_password():
    from api import app

    with TestClient(app) as client:
        resp = client.post("/token", data={"username": "operator1", "password": "wrong"})
        assert resp.status_code == 401


def test_escalations_requires_auth():
    from api import app

    with TestClient(app) as client:
        resp = client.post(
            "/escalations", json={"sender": "a@example.com", "subject": "Test", "raw_body": "body"}
        )
        assert resp.status_code == 401


def test_approve_rejects_operator_role(patch_agents, mock_gmail):
    patch_agents()
    from api import app

    with TestClient(app) as client:
        operator_token = _login(client, "operator1", CONFIG.dev_operator_password)
        resp = client.post(
            "/approve",
            headers=_auth_headers(operator_token),
            json={"escalation_id": "whatever", "decision": "approved"},
        )
        assert resp.status_code == 403


def test_submit_approve_round_trip_sends_email(patch_agents, mock_gmail):
    patch_agents()
    from api import app

    with TestClient(app) as client:
        operator_token = _login(client, "operator1", CONFIG.dev_operator_password)
        approver_token = _login(client, "approver1", CONFIG.dev_approver_password)

        create_resp = client.post(
            "/escalations",
            headers=_auth_headers(operator_token),
            json={
                "sender": "customer@bigcorp.com",
                "subject": "Repeated outages",
                "raw_body": "We've had three outages this week and are considering switching vendors.",
            },
        )
        assert create_resp.status_code == 200, create_resp.text
        escalation_id = create_resp.json()["escalation_id"]

        audit_resp = client.get(f"/audit/{escalation_id}", headers=_auth_headers(operator_token))
        assert audit_resp.status_code == 200
        assert audit_resp.json()["current"]["status"] == "pending_approval"

        approve_resp = client.post(
            "/approve",
            headers=_auth_headers(approver_token),
            json={"escalation_id": escalation_id, "decision": "approved"},
        )
        assert approve_resp.status_code == 200, approve_resp.text
        assert approve_resp.json()["status"] == "sent"

        mock_gmail["send_email"].assert_called_once()

        audit_resp2 = client.get(f"/audit/{escalation_id}", headers=_auth_headers(operator_token))
        assert audit_resp2.json()["current"]["status"] == "sent"
        assert len(audit_resp2.json()["history"]) >= 1


def test_submit_reject_round_trip_does_not_send(patch_agents, mock_gmail):
    patch_agents()
    from api import app

    with TestClient(app) as client:
        operator_token = _login(client, "operator1", CONFIG.dev_operator_password)
        approver_token = _login(client, "approver1", CONFIG.dev_approver_password)

        create_resp = client.post(
            "/escalations",
            headers=_auth_headers(operator_token),
            json={
                "sender": "customer2@bigcorp.com",
                "subject": "Another issue",
                "raw_body": "A different escalation entirely.",
            },
        )
        escalation_id = create_resp.json()["escalation_id"]

        approve_resp = client.post(
            "/approve",
            headers=_auth_headers(approver_token),
            json={"escalation_id": escalation_id, "decision": "rejected", "notes": "not needed"},
        )
        assert approve_resp.status_code == 200
        assert approve_resp.json()["status"] == "rejected"

        mock_gmail["send_email"].assert_not_called()


def test_audit_unknown_id_returns_404():
    from api import app

    with TestClient(app) as client:
        operator_token = _login(client, "operator1", CONFIG.dev_operator_password)
        resp = client.get("/audit/does-not-exist", headers=_auth_headers(operator_token))
        assert resp.status_code == 404
