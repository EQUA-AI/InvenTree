"""S17 gap-fill: the tracing attribute allowlist is the privacy choke point.

`span_attrs` is the single path attributes take onto spans; these pins
make a content-bearing key or an unbounded value a test failure instead
of a silent telemetry leak.
"""

from __future__ import annotations

from ai.core.tracing import _ALLOWED_ATTRS, _MAX_ATTR_LEN, span_attrs


def test_unknown_keys_are_dropped_never_raised():
    attrs = span_attrs(
        workflow_id="wf8",
        prompt="the entire user question would leak here",
        machine_name="Inverter Alpha",
    )
    assert attrs == {"aimms.workflow_id": "wf8"}


def test_bare_keys_gain_the_namespace_prefix():
    assert span_attrs(modality="text") == {"aimms.modality": "text"}
    assert span_attrs(**{"aimms.modality": "voice"}) == {"aimms.modality": "voice"}


def test_none_values_are_dropped_and_scalars_coerced():
    attrs = span_attrs(
        workflow_id=None,
        coverage_complete=True,
        scope_version=7,
        outcome_code=Exception("boom"),
    )
    assert "aimms.workflow_id" not in attrs
    assert attrs["aimms.coverage_complete"] == 1
    assert attrs["aimms.scope_version"] == 7
    assert attrs["aimms.outcome_code"] == "boom"


def test_values_are_truncated_to_the_bound():
    attrs = span_attrs(outcome_code="x" * (10 * _MAX_ATTR_LEN))
    assert len(attrs["aimms.outcome_code"]) == _MAX_ATTR_LEN


def test_the_vocabulary_is_frozen():
    """Adding a telemetry key is a reviewed decision, not a drive-by."""
    assert (
        frozenset({
            "aimms.correlation_id",
            "aimms.session_correlation_id",
            "aimms.thread_id",
            "aimms.turn_id",
            "aimms.modality",
            "aimms.workflow_id",
            "aimms.response_state",
            "aimms.outcome_code",
            "aimms.route_mode",
            "aimms.tool_name",
            "aimms.decision_code",
            "aimms.proposal_id",
            "aimms.action_type",
            "aimms.turn_sequence",
            "aimms.task_intent",
            "aimms.effect_intent",
            "aimms.scope_mode",
            "aimms.scope_version",
            "aimms.scope_hash_prefix",
            "aimms.scope_machine_count",
            "aimms.scope_rejections",
            "aimms.validator_outcome",
            "aimms.coverage_complete",
            "aimms.quota_profile",
            "aimms.admission_outcome",
            "aimms.pilot_stop_reason",
            "aimms.slo_class",
            "aimms.slo_breach",
            # M1 PR F (§9.5): memory-section budget telemetry.
            "aimms.memory_db_round_trips",
            "aimms.memory_wall_ms",
            "aimms.memory_degrade_reason",
            "aimms.topology_depth",
            "aimms.memory_stage_breach",
        })
        == _ALLOWED_ATTRS
    )
    # Nothing in the vocabulary names a content-bearing field.
    for key in _ALLOWED_ATTRS:
        assert not any(token in key for token in ("content", "text", "prompt", "query", "name_")), (
            key
        )
