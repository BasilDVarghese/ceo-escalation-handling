"""Shared Claude client factory — keeps model/thinking/effort config in one place.

Each agent node still makes its own distinct LLM call with its own system prompt
and structured-output schema; only the underlying client configuration is shared.
"""

from __future__ import annotations

from langchain_anthropic import ChatAnthropic

from config import CONFIG


def get_llm() -> ChatAnthropic:
    try:
        return ChatAnthropic(
            model=CONFIG.claude_model,
            anthropic_api_key=CONFIG.anthropic_api_key,
            thinking={"type": "adaptive"},
            output_config={"effort": CONFIG.claude_effort},
            max_tokens=4096,
        )
    except TypeError:
        # Fallback for langchain-anthropic versions that don't yet expose
        # output_config as a first-class kwarg — pass it through model_kwargs instead.
        return ChatAnthropic(
            model=CONFIG.claude_model,
            anthropic_api_key=CONFIG.anthropic_api_key,
            thinking={"type": "adaptive"},
            max_tokens=4096,
            model_kwargs={"output_config": {"effort": CONFIG.claude_effort}},
        )
