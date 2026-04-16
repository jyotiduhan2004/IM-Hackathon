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
    # Tier A wholesale-rewrote the prompt — assert structural anchors instead
    # of the old free-text phrases.
    assert "You are a wiki compiler." in kwargs["system_prompt"]
    assert "<background>" in kwargs["system_prompt"]
    assert "<page_types>" in kwargs["system_prompt"]
    assert "Runtime context" in kwargs["system_prompt"]
    assert kwargs.get("memory") is None
    # New Tier A wiring: permissions + middleware + subagents all present.
    assert len(kwargs.get("permissions") or []) >= 1
    assert len(kwargs.get("middleware") or []) >= 2
    assert len(kwargs.get("subagents") or []) >= 1


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
    # Metadata is now ENRICHED with F3 mount-sanity keys (cwd / raw_dir /
    # wiki_dir / view_root / mounted_raw_file_count / missing_raw_paths_count)
    # alongside whatever the caller passed. Assert caller keys survive and
    # the mount keys are present.
    metadata = captured["config"]["metadata"]
    assert metadata["compile_model"] == "z-ai/glm-5"
    for expected_key in (
        "cwd",
        "raw_dir",
        "wiki_dir",
        "view_root",
        "mounted_raw_file_count",
        "missing_raw_paths_count",
    ):
        assert expected_key in metadata
    assert captured["config"]["tags"] == ["email-kb", "compile"]
    assert captured["config"]["callbacks"] == ["lf-handler", "cache-handler", "tool-handler"]
