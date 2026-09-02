"""Shared test fixtures.

Env vars are set, and moto's AWS mocking is started, at import time — before any project
module is imported — since config.py builds its CONFIG singleton and db.py builds its boto3
Table resources at import time.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from moto import mock_aws

_tmp_dir = Path(tempfile.mkdtemp(prefix="ceo-escalation-tests-"))
os.environ["ANTHROPIC_API_KEY"] = "test-key"
os.environ["CHECKPOINT_DB_PATH"] = str(_tmp_dir / "checkpoints.sqlite")
os.environ["GMAIL_CREDENTIALS_PATH"] = str(_tmp_dir / "credentials.json")
os.environ["GMAIL_TOKEN_PATH"] = str(_tmp_dir / "token.json")
os.environ["ENABLE_POLLER"] = "false"

# Dummy AWS credentials — moto intercepts every boto3 call, but boto3 still requires
# *some* credentials to be present before it will let a request through to be intercepted.
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")

_mock_aws = mock_aws()
_mock_aws.start()

from scripts.dynamodb_setup import create_tables, seed_teams  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def seeded_db():
    create_tables()
    seed_teams()
    yield


@pytest.fixture
def mock_gmail(monkeypatch):
    import gmail_client

    fake_service = MagicMock(name="gmail_service")
    send_mock = MagicMock(return_value="fake-sent-id")
    mark_processed_mock = MagicMock()

    monkeypatch.setattr(gmail_client, "get_gmail_service", lambda *a, **k: fake_service)
    monkeypatch.setattr(gmail_client, "send_email", send_mock)
    monkeypatch.setattr(gmail_client, "mark_processed", mark_processed_mock)

    return {
        "service": fake_service,
        "send_email": send_mock,
        "mark_processed": mark_processed_mock,
    }


class _StubLLM:
    """Stands in for ChatAnthropic: .with_structured_output(...).invoke(...) -> fixed result."""

    def __init__(self, result):
        self._result = result

    def with_structured_output(self, schema):
        return self

    def invoke(self, messages):
        return self._result


@pytest.fixture
def stub_llm():
    return _StubLLM


@pytest.fixture
def patch_agents(monkeypatch, stub_llm):
    """Stub all three agent LLM calls with fixed, deterministic results. A fixture (not a
    plain importable helper) so test_graph_flow.py and test_api.py can share it without
    cross-importing tests.conftest as a module — that risks conftest's module-level
    mock_aws().start() running twice under two different module identities."""

    def _patch(*, is_escalation=True, team="Engineering"):
        from agents.router import RoutingResult
        from agents.summarizer import SummaryResult
        from agents.triage import TriageResult

        monkeypatch.setattr(
            "agents.triage.get_llm",
            lambda: stub_llm(
                TriageResult(
                    is_genuine_escalation=is_escalation,
                    severity="high",
                    urgency_notes="Customer threatening to churn.",
                    key_facts=["Customer X reported repeated outages."],
                )
            ),
        )
        monkeypatch.setattr(
            "agents.summarizer.get_llm",
            lambda: stub_llm(
                SummaryResult(summary="Customer X is hitting repeated outages and is at risk of churn.")
            ),
        )
        monkeypatch.setattr(
            "agents.router.get_llm",
            lambda: stub_llm(
                RoutingResult(team_name=team, recommended_action="Investigate the outage root cause today.")
            ),
        )

    return _patch
