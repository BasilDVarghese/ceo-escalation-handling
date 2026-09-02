"""Shared Claude client factory — provider-abstracted (Bedrock primary, direct Anthropic fallback).

Each agent node still makes its own distinct LLM call with its own system prompt and
structured-output schema; only the underlying client/provider configuration is shared and
lives entirely in this module. agents/triage.py, agents/summarizer.py, and agents/router.py
call `get_llm().with_structured_output(schema).invoke(messages)` exactly as before — none of
them need to know which provider actually served the call.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_anthropic import ChatAnthropic

from config import CONFIG

logger = logging.getLogger(__name__)


def _build_direct_anthropic() -> ChatAnthropic:
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


def _build_bedrock():
    from langchain_aws import ChatBedrockConverse

    kwargs: dict[str, Any] = {"model_id": CONFIG.bedrock_model_id, "max_tokens": 4096}
    if CONFIG.aws_region:
        kwargs["region_name"] = CONFIG.aws_region
    # else: falls through to boto3's standard credential/region chain.
    return ChatBedrockConverse(**kwargs)


_PROVIDER_BUILDERS = {"bedrock": _build_bedrock, "anthropic": _build_direct_anthropic}


class _BoundFallback:
    """What .with_structured_output(schema) returns: tries the primary bound model first,
    falls back to the secondary on invoke failure, logging a warning either way it happens."""

    def __init__(self, primary_bound: Any, secondary_bound_factory):
        self._primary_bound = primary_bound
        self._secondary_bound_factory = secondary_bound_factory
        self._secondary_bound: Any = None

    def invoke(self, messages):
        if self._primary_bound is not None:
            try:
                return self._primary_bound.invoke(messages)
            except Exception:
                logger.warning("Primary LLM provider invoke failed; falling back", exc_info=True)

        if self._secondary_bound is None:
            self._secondary_bound = self._secondary_bound_factory()
        if self._secondary_bound is None:
            raise RuntimeError("Both primary and secondary LLM providers are unavailable.")
        return self._secondary_bound.invoke(messages)


class FallbackChatModel:
    """Returned by get_llm(). Tries CONFIG.llm_provider first; falls back to the other
    provider on either construction failure or invocation failure."""

    def __init__(self):
        provider = CONFIG.llm_provider if CONFIG.llm_provider in _PROVIDER_BUILDERS else "bedrock"
        other = "anthropic" if provider == "bedrock" else "bedrock"

        self._secondary_builder = _PROVIDER_BUILDERS[other]
        self._secondary_cache: Any = None
        self._primary = self._safe_build(_PROVIDER_BUILDERS[provider], provider)

    @staticmethod
    def _safe_build(builder, label: str):
        try:
            return builder()
        except Exception:
            logger.warning("Failed to construct %s LLM provider", label, exc_info=True)
            return None

    def _secondary(self):
        if self._secondary_cache is None:
            self._secondary_cache = self._safe_build(self._secondary_builder, "secondary")
        return self._secondary_cache

    def with_structured_output(self, schema) -> _BoundFallback:
        primary_bound = self._primary.with_structured_output(schema) if self._primary else None
        return _BoundFallback(primary_bound, lambda: self._bind_secondary(schema))

    def _bind_secondary(self, schema):
        secondary = self._secondary()
        return secondary.with_structured_output(schema) if secondary else None


def get_llm() -> FallbackChatModel:
    return FallbackChatModel()
