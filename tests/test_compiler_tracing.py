"""Tests for compiler tracing configuration."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from src.compile import compiler as compiler_mod


def test_get_langfuse_handler_enables_trace_updates(monkeypatch: Any) -> None:
    monkeypatch.setattr(compiler_mod.settings, "langfuse_enabled", True)
    monkeypatch.setattr(compiler_mod.settings, "langfuse_public_key", "pk-test")
    monkeypatch.setattr(compiler_mod.settings, "langfuse_secret_key", "sk-test")
    monkeypatch.setattr(compiler_mod.settings, "langfuse_host", "https://langfuse.example.com")

    captured: dict[str, Any] = {}

    class DummyHandler:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    with patch("langfuse.langchain.CallbackHandler", DummyHandler):
        handler = compiler_mod.get_langfuse_handler()

    assert handler is not None
    assert captured["update_trace"] is True


def test_create_compiler_passes_custom_system_prompt() -> None:
    captured: dict[str, Any] = {}

    def _fake_create_deep_agent(*args: object, **kwargs: object) -> object:
        captured["kwargs"] = kwargs
        return object()

    with patch("deepagents.create_deep_agent", side_effect=_fake_create_deep_agent):
        compiler_mod.create_compiler(raw_dir="raw", wiki_dir="wiki")

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert "system_prompt" in kwargs
    assert "You are a wiki compiler." in kwargs["system_prompt"]
    assert "Do NOT call" in kwargs["system_prompt"]
    assert "`list_uncompiled_emails`" in kwargs["system_prompt"]
    assert kwargs.get("memory") is None


def test_run_compilation_passes_trace_config(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class FakeAgent:
        def invoke(self, payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
            captured["payload"] = payload
            captured["config"] = config
            return {"ok": True}

    fake_agent = FakeAgent()
    monkeypatch.setattr(compiler_mod, "create_compiler", lambda **_kwargs: fake_agent)
    monkeypatch.setattr(compiler_mod, "get_langfuse_handler", lambda **_kwargs: "lf-handler")

    result = compiler_mod.run_compilation(
        instruction="Compile this batch.",
        model_name="z-ai/glm-5",
        run_name="compile:z-ai-glm-5:t1",
        trace_metadata={"compile_model": "z-ai/glm-5"},
        trace_tags=["email-kb", "compile"],
        cache_stats="cache-handler",
        tool_log="tool-handler",
    )

    assert result == {"ok": True}
    assert captured["payload"]["messages"][0]["content"] == "Compile this batch."
    assert captured["config"]["run_name"] == "compile:z-ai-glm-5:t1"
    assert captured["config"]["metadata"] == {"compile_model": "z-ai/glm-5"}
    assert captured["config"]["tags"] == ["email-kb", "compile"]
    assert captured["config"]["callbacks"] == ["lf-handler", "cache-handler", "tool-handler"]
