"""Exercise the installed provider SDK, including its legacy schema parser.

The 2026-10-25 failures happened before any tool dispatch: the deployed SDK
mistook an unwrapped Literal's second value for an Annotated description.
Keep every model-visible schema serializable and preserve argument validation
and the write fence. These checks must also run in the release runtime image.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from agent_framework import AIFunction
from ai.core.integrations.investigation_tools import propose_investigation_artifact
from ai.core.integrations.memory_tools import propose_memory_action
from ai.core.tools.capabilities import capability_catalog
from ai.core.tools.read_only import read_only_tool_fence
from pydantic import ValidationError


@pytest.mark.parametrize("entry", capability_catalog(), ids=lambda entry: entry.tool_id)
def test_catalog_schema_serializes_with_installed_sdk(entry):
    """Selection must never expose a tool whose request schema crashes."""
    function = AIFunction(name=entry.tool_id, func=entry.tool)
    schema = function.to_json_schema_spec()
    assert schema["function"]["name"] == entry.tool_id
    json.dumps(schema)


@pytest.mark.parametrize(
    ("tool", "field", "values", "required"),
    [
        (
            propose_memory_action,
            "action",
            ["remember", "update", "forget", "forget_all"],
            {"request_key": "qa"},
        ),
        (
            propose_investigation_artifact,
            "kind",
            ["observation", "reading_set", "photo", "report", "laboratory", "check_result"],
            {"request_key": "qa", "payload": {}},
        ),
    ],
)
def test_proposal_enums_validate_and_keep_the_write_fence(tool, field, values, required):
    """Schema repair cannot admit arbitrary actions or bypass voice fencing."""
    function = AIFunction(name=tool.__name__, func=tool)
    schema = function.to_json_schema_spec()["function"]["parameters"]
    assert schema["properties"][field]["enum"] == values
    for value in values:
        function.input_model.model_validate({field: value, **required})
    with pytest.raises(ValidationError):
        function.input_model.model_validate({field: "arbitrary_action", **required})
    with read_only_tool_fence(), pytest.raises(PermissionError, match="read-only"):
        asyncio.run(tool(**{field: values[0], **required}))
