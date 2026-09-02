"""Validates the Bedrock-primary/Anthropic-fallback wrapper actually falls back, rather than
being a decorative code path that's never exercised."""

from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock

import agents.llm as llm_module


def _set_llm_provider(monkeypatch, provider: str) -> None:
    # CONFIG is a frozen dataclass — can't setattr a field on the existing instance, so
    # rebind the module's CONFIG name to a copy with just llm_provider overridden.
    monkeypatch.setattr(llm_module, "CONFIG", dataclasses.replace(llm_module.CONFIG, llm_provider=provider))


class _FakeBoundModel:
    def __init__(self, invoke_fn):
        self._invoke_fn = invoke_fn

    def invoke(self, messages):
        return self._invoke_fn(messages)


class _FakeProvider:
    def __init__(self, invoke_fn):
        self._invoke_fn = invoke_fn
        self.with_structured_output_calls = 0

    def with_structured_output(self, schema):
        self.with_structured_output_calls += 1
        return _FakeBoundModel(self._invoke_fn)


def _ok(_messages):
    return "ok-result"


def _boom(_messages):
    raise RuntimeError("provider unavailable")


def test_falls_back_when_primary_construction_fails(monkeypatch):
    secondary = _FakeProvider(_ok)
    monkeypatch.setattr(llm_module, "_PROVIDER_BUILDERS", {
        "bedrock": lambda: (_ for _ in ()).throw(RuntimeError("no bedrock creds")),
        "anthropic": lambda: secondary,
    })
    _set_llm_provider(monkeypatch, "bedrock")

    model = llm_module.FallbackChatModel()
    bound = model.with_structured_output(object())
    assert bound.invoke([]) == "ok-result"
    assert secondary.with_structured_output_calls == 1


def test_falls_back_when_primary_invoke_fails(monkeypatch):
    primary = _FakeProvider(_boom)
    secondary = _FakeProvider(_ok)
    monkeypatch.setattr(llm_module, "_PROVIDER_BUILDERS", {
        "bedrock": lambda: primary,
        "anthropic": lambda: secondary,
    })
    _set_llm_provider(monkeypatch, "bedrock")

    model = llm_module.FallbackChatModel()
    bound = model.with_structured_output(object())
    assert bound.invoke([]) == "ok-result"
    assert secondary.with_structured_output_calls == 1


def test_secondary_never_built_when_primary_succeeds(monkeypatch):
    primary = _FakeProvider(_ok)
    secondary_builder = MagicMock(side_effect=AssertionError("secondary should never be built"))
    monkeypatch.setattr(llm_module, "_PROVIDER_BUILDERS", {
        "bedrock": lambda: primary,
        "anthropic": secondary_builder,
    })
    _set_llm_provider(monkeypatch, "bedrock")

    model = llm_module.FallbackChatModel()
    bound = model.with_structured_output(object())
    assert bound.invoke([]) == "ok-result"
    secondary_builder.assert_not_called()
