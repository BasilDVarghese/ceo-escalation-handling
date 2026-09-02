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


def _bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    # --- Claude / Anthropic (direct client — kept as the fallback provider) ---
    anthropic_api_key: str = field(default_factory=lambda: _require("ANTHROPIC_API_KEY"))
    claude_model: str = field(default_factory=lambda: _env("CLAUDE_MODEL", "claude-opus-5"))
    claude_effort: str = field(default_factory=lambda: _env("CLAUDE_EFFORT", "medium"))

    # --- LLM provider selection (Bedrock primary, direct Anthropic fallback) ---
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "bedrock"))
    bedrock_model_id: str = field(default_factory=lambda: _env("BEDROCK_MODEL_ID", ""))
    aws_region: str = field(default_factory=lambda: _env("AWS_REGION", ""))

    # --- DynamoDB (routing taxonomy + escalation audit trail) ---
    dynamodb_endpoint_url: str = field(default_factory=lambda: _env("DYNAMODB_ENDPOINT_URL", ""))
    dynamodb_escalations_table: str = field(
        default_factory=lambda: _env("DYNAMODB_ESCALATIONS_TABLE", "escalations")
    )
    dynamodb_teams_table: str = field(
        default_factory=lambda: _env("DYNAMODB_TEAMS_TABLE", "teams")
    )

    # --- Gmail ---
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

    # --- LangGraph checkpoint persistence ---
    checkpoint_db_path: str = field(
        default_factory=lambda: _env("CHECKPOINT_DB_PATH", "data/checkpoints.sqlite")
    )

    fallback_team: str = field(default_factory=lambda: _env("FALLBACK_TEAM", "Customer Support"))

    # --- JWT / dev auth (api.py) ---
    jwt_secret_key: str = field(default_factory=lambda: _env("JWT_SECRET_KEY", "dev-secret-change-me"))
    jwt_algorithm: str = field(default_factory=lambda: _env("JWT_ALGORITHM", "HS256"))
    jwt_expire_minutes: int = field(default_factory=lambda: int(_env("JWT_EXPIRE_MINUTES", "30")))

    # Dev-only hardcoded users (operator1 / approver1) — NOT a real identity provider.
    # Override via env for anything beyond local demo use; a fallback default keeps
    # `docker compose up && uvicorn api:app` working out of the box.
    dev_operator_password: str = field(
        default_factory=lambda: _env("DEV_OPERATOR_PASSWORD", "operator-dev-password")
    )
    dev_approver_password: str = field(
        default_factory=lambda: _env("DEV_APPROVER_PASSWORD", "approver-dev-password")
    )

    # --- FastAPI service ---
    enable_poller: bool = field(default_factory=lambda: _bool_env("ENABLE_POLLER", True))


def get_config() -> Config:
    """Build a fresh Config from the current environment (useful for tests)."""
    return Config()


CONFIG = get_config()
