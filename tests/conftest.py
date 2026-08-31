"""Shared test fixtures.

Env vars are set at import time, before any project module is imported, since
config.py reads them at import time into a module-level singleton.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_tmp_dir = Path(tempfile.mkdtemp(prefix="ceo-escalation-tests-"))
os.environ["ANTHROPIC_API_KEY"] = "test-key"
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_dir / 'test.db'}"
os.environ["CHECKPOINT_DB_PATH"] = str(_tmp_dir / "checkpoints.sqlite")
os.environ["GMAIL_CREDENTIALS_PATH"] = str(_tmp_dir / "credentials.json")
os.environ["GMAIL_TOKEN_PATH"] = str(_tmp_dir / "token.json")

from scripts.seed import seed_teams  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def seeded_db():
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
