"""Every schema sent with ``strict: True`` must satisfy strict-mode rules.

The provider validates strict schemas harder than pydantic's default export:
``required`` must list every property and ``additionalProperties`` must be
``false`` at every object level. The raw ``model_json_schema()`` output
violates both for models with defaulted fields, and the very first live
reasoning dispatch 400ed with ``invalid_json_schema`` (2026-08-03) — after
the rail had been structurally dark long enough that nothing ever exercised
the request shape. This suite walks every schema the adapter can send.
"""

from ai.core.reasoning.schemas import CanonicalTurnResponse
from ai.core.tools.diagnostics import get_diagnostic_tool_registry
from ai.core.tools.provider_schema import strict_provider_schema


def _assert_strict(schema: dict, path: str = "$") -> None:
    if schema.get("type") == "object" or "properties" in schema:
        props = schema.get("properties", {})
        assert schema.get("additionalProperties") is False, (
            f"{path}: additionalProperties must be false"
        )
        assert set(schema.get("required", [])) == set(props.keys()), (
            f"{path}: required must list every property; "
            f"missing {set(props) - set(schema.get('required', []))}"
        )
        for name, sub in props.items():
            _assert_strict(sub, f"{path}.{name}")
    for key in ("items",):
        if isinstance(schema.get(key), dict):
            _assert_strict(schema[key], f"{path}.{key}")
    for key in ("anyOf", "allOf", "oneOf"):
        for i, sub in enumerate(schema.get(key, []) or []):
            _assert_strict(sub, f"{path}.{key}[{i}]")
    for name, sub in (schema.get("$defs", {}) or {}).items():
        _assert_strict(sub, f"{path}.$defs.{name}")


def test_canonical_response_schema_is_strict_mode_valid() -> None:
    _assert_strict(strict_provider_schema(CanonicalTurnResponse))


def test_every_registry_tool_schema_is_strict_mode_valid() -> None:
    registry = get_diagnostic_tool_registry(safety_p0_enabled=True)
    checked = 0
    for definition in registry._definitions:
        _assert_strict(strict_provider_schema(definition.arguments_model), definition.name)
        checked += 1
    assert checked >= 8


def test_defaulted_fields_are_forced_into_required() -> None:
    """The exact live failure: defaulted ``limit`` fields were absent from
    ``required``, which strict mode rejects outright.
    """
    from ai.core.tools.diagnostics import GetRecentMaintenanceHistoryArguments

    schema = strict_provider_schema(GetRecentMaintenanceHistoryArguments)
    assert "limit" in schema["required"]


def test_each_call_returns_an_independent_copy() -> None:
    """A caller mutating its request payload must not poison later turns."""
    first = strict_provider_schema(CanonicalTurnResponse)
    first["properties"].clear()
    second = strict_provider_schema(CanonicalTurnResponse)
    assert second["properties"], "cache was poisoned by caller mutation"
