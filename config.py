"""Centralized configuration, loaded from environment variables / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            "Copy .env.example to .env and fill it in."
        )
    return value


@dataclass(frozen=True)
class Config:
    anthropic_api_key: str = field(default_factory=lambda: _require("ANTHROPIC_API_KEY"))
    claude_model: str = field(default_factory=lambda: _env("CLAUDE_MODEL", "claude-opus-5"))
    claude_effort: str = field(default_factory=lambda: _env("CLAUDE_EFFORT", "medium"))

    database_url: str = field(default_factory=lambda: _require("DATABASE_URL"))

    gmail_credentials_path: str = field(
        default_factory=lambda: _env("GMAIL_CREDENTIALS_PATH", "data/credentials.json")
    )
    gmail_token_path: str = field(
        default_factory=lambda: _env("GMAIL_TOKEN_PATH", "data/token.json")
    )
    gmail_poll_interval_minutes: int = field(
        default_factory=lambda: int(_env("GMAIL_POLL_INTERVAL_MINUTES", "5"))
    )
    gmail_query: str = field(
        default_factory=lambda: _env("GMAIL_QUERY_FILTER", "is:unread label:escalation")
    )
    gmail_processed_label: str = field(
        default_factory=lambda: _env("GMAIL_PROCESSED_LABEL", "escalation-processed")
    )
    sender_email: str = field(
        default_factory=lambda: _env("SENDER_EMAIL", "ceo-assistant@example.com")
    )

    checkpoint_db_path: str = field(
        default_factory=lambda: _env("CHECKPOINT_DB_PATH", "data/checkpoints.sqlite")
    )

    fallback_team: str = field(default_factory=lambda: _env("FALLBACK_TEAM", "Customer Support"))


def get_config() -> Config:
    """Build a fresh Config from the current environment (useful for tests)."""
    return Config()


CONFIG = get_config()
