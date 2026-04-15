"""Locks in the typed-schema fix for `create_entities`.

Before 2026-04-15 the tool signature was
``entities: list[dict[str, Any]]`` which serialised to JSON schema
``{items: {type: 'object', additionalProperties: true}}`` — no required
inner fields. Langfuse traces caught grok-4.1-fast AND z-ai/glm-4.6
emitting arrays of empty dicts (`[{}, {}, ..., {}]`) that satisfied the
schema but carried no data; the tool produced a wall of
`invalid_email` errors back to the model, which then burned context
retrying with more empty dicts.

Fix: `entities: list[EntityRequest]` (Pydantic model) with `email`
required. LangChain validates inputs against this schema before the
tool body runs, so empty-object items now raise a ValidationError the
LLM sees as a structured error — actionable feedback instead of N
repeated per-item errors.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from src.compile.compiler import EntityRequest
from src.compile.compiler import create_entities


def test_inner_schema_marks_email_required() -> None:
    """The JSON schema sent to LiteLLM must list `email` as required on
    each `entities` item so the LLM can't emit schema-valid empty dicts."""
    schema = create_entities.args_schema.model_json_schema()
    # Pydantic inlines the EntityRequest definition under $defs; resolve it.
    defs = schema.get("$defs", {})
    entity_schema = defs.get("EntityRequest")
    assert entity_schema is not None, (
        f"Expected EntityRequest in $defs, got keys: {sorted(defs.keys())}. "
        "If the schema shape changed, update this test."
    )
    assert "email" in entity_schema.get("required", []), (
        "`email` must be listed as required on each entity item. "
        "Without this, the LLM gets told `{}` is a valid entity, which "
        "is exactly the bug this tool exists to prevent."
    )


def test_empty_entity_rejected_by_pydantic() -> None:
    """Instantiating `EntityRequest()` without email is a schema error."""
    with pytest.raises(ValidationError, match="email"):
        EntityRequest()  # type: ignore[call-arg]


def test_empty_string_email_rejected() -> None:
    """A blank string violates `min_length=3`."""
    with pytest.raises(ValidationError):
        EntityRequest(email="")


def test_valid_entity_accepts_defaults() -> None:
    """Only email is required; display_name and force default sanely."""
    e = EntityRequest(email="amit@indiamart.com")
    assert e.email == "amit@indiamart.com"
    assert e.display_name == ""
    assert e.force is False


def test_tool_invoke_rejects_all_empty_array() -> None:
    """Calling the tool with `[{}]` must surface a schema error — not
    silently pass through as N invalid_email results.

    LangChain's @tool validates args against the Pydantic schema before
    invoking the function body. The LLM sees the error via LangGraph's
    ToolNode and can correct course instead of retrying with more empty
    dicts."""
    with pytest.raises((ValidationError, ValueError, TypeError)):
        create_entities.invoke(
            {"raw_paths": ["raw/fake.md"], "entities": [{}]},
        )


def test_tool_invoke_rejects_mixed_valid_and_empty() -> None:
    """Even one empty dict in the list is a schema violation for the whole
    call — the LLM should resend the call cleanly rather than having the
    valid entries go through and the empty ones silently error out."""
    with pytest.raises((ValidationError, ValueError, TypeError)):
        create_entities.invoke(
            {
                "raw_paths": ["raw/fake.md"],
                "entities": [
                    {"email": "amit@indiamart.com"},
                    {},
                ],
            },
        )
