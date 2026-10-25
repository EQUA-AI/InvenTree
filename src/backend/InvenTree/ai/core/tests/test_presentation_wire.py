"""Presentation wire envelope (strict optional OpenUI contract).

Backend-only vertical slice: the raw envelope types and the safe optional
normalization/parsing boundary. Generation, transport, persistence and
reference authorization stay out of scope.
"""

from __future__ import annotations

import json
from typing import Any

from ai.core.presentation import wire


def _valid_envelope() -> dict:
    return {
        "schema_version": 1,
        "format": "openui-lang",
        "library": "aimms-chat@1",
        "program": 'render("机器 board")',
        "references": [
            {"ref_id": "ref-entity-1", "kind": "entity", "source_key": "src-entity-1"},
            {"ref_id": "ref-evidence-1", "kind": "evidence", "source_key": "src-evidence-1"},
            {"ref_id": "ref-metric-1", "kind": "metric_dataset", "source_key": "src-metric-1"},
            {"ref_id": "ref-trend-1", "kind": "trend_dataset", "source_key": "src-trend-1"},
        ],
    }


# ---- slice 1: valid deterministic envelope round-trips ---------------------


def test_valid_envelope_round_trips_json_without_altering_payload() -> None:
    envelope = wire.validate_presentation(_valid_envelope())
    dumped = envelope.model_dump_json()
    restored = wire.AssistantPresentationV1.model_validate_json(dumped)
    assert restored == envelope
    payload = json.loads(dumped)
    assert payload == _valid_envelope()


def test_reference_kinds_preserve_historical_and_add_version_two_charts() -> None:
    assert wire.REFERENCE_KINDS == (
        "entity",
        "evidence",
        "metric_dataset",
        "trend_dataset",
        "chart_dataset",
    )
    envelope = wire.validate_presentation(_valid_envelope())
    assert [ref.kind for ref in envelope.references] == list(wire.REFERENCE_KINDS[:4])
    payload = _valid_envelope()
    payload["library"] = "aimms-chat@2"
    payload["references"].append({
        "ref_id": "chart",
        "kind": "chart_dataset",
        "source_key": "provided:series",
    })
    assert wire.validate_presentation(payload).references[-1].kind == "chart_dataset"
    payload["library"] = "aimms-chat@1"
    assert _capture(payload) is not None


# ---- slice 2: strict envelope fields and bounded limits ---------------------


def _capture(payload: object) -> Any:
    """Return the raised exception, or ``None`` when the payload was accepted."""
    try:
        wire.validate_presentation(payload)
    except Exception as exc:  # asserted precisely by callers
        return exc
    return None


def _with(**changes: object) -> dict:
    payload = _valid_envelope()
    payload.update(changes)
    return payload


def test_schema_version_must_be_exact_integer_one() -> None:
    for bad in (2, 0, -1, "1", "01", True, False, 1.0, None, [], {}):
        exc = _capture(_with(schema_version=bad))
        assert exc is not None, f"schema_version {bad!r} must be rejected"
        assert isinstance(exc, wire.PresentationValidationError)
        assert exc.reason == "unsupported_schema_version"


def test_missing_schema_version_is_rejected_as_invalid_shape() -> None:
    payload = _valid_envelope()
    del payload["schema_version"]
    exc = _capture(payload)
    assert exc is not None
    assert isinstance(exc, wire.PresentationValidationError)
    assert exc.reason == "invalid_envelope"


def test_format_and_library_must_be_exact_supported_values() -> None:
    for bad_format in ("openui", "OPENUI-LANG", "openui-lang ", ""):
        exc = _capture(_with(format=bad_format))
        assert exc is not None, f"format {bad_format!r} must be rejected"
        assert isinstance(exc, wire.PresentationValidationError)
        assert exc.reason == "unsupported_format"
    for bad_library in ("aimms-chat@99", "aimms-chat", "AIMMS-CHAT@1", ""):
        exc = _capture(_with(library=bad_library))
        assert exc is not None, f"library {bad_library!r} must be rejected"
        assert isinstance(exc, wire.PresentationValidationError)
        assert exc.reason == "unsupported_library"
    for bad in (123, None, [], {}):
        exc = _capture(_with(format=bad))
        assert exc is not None
        assert isinstance(exc, wire.PresentationValidationError)
        assert exc.reason == "invalid_envelope"


def test_extra_and_grant_like_fields_are_rejected_on_both_models() -> None:
    for bad_key in ("fallback", "fallback_text", "answer", "label", "grant", "grants", "url"):
        exc = _capture(_with(**{bad_key: "anything"}))
        assert exc is not None, f"envelope extra {bad_key!r} must be rejected"
        assert isinstance(exc, wire.PresentationValidationError)
        assert exc.reason == "invalid_envelope"
    for bad_key in ("label", "grants", "scope", "url", "fallback_text", "metadata", "description"):
        ref = {"ref_id": "ref-1", "kind": "entity", "source_key": "src-1", bad_key: "anything"}
        exc = _capture(_with(references=[ref]))
        assert exc is not None, f"reference extra {bad_key!r} must be rejected"
        assert isinstance(exc, wire.PresentationValidationError)
        assert exc.reason == "invalid_envelope"


def test_reference_identifier_types_are_strict_and_not_coerced() -> None:
    for bad in (123, 1.5, True, False, None, b"x", ["a"], {"a": 1}):
        for key in ("ref_id", "source_key"):
            ref = {"ref_id": "r", "kind": "entity", "source_key": "s", key: bad}
            exc = _capture(_with(references=[ref]))
            assert exc is not None, f"{key} {bad!r} must be rejected"
            assert isinstance(exc, wire.PresentationValidationError)
            assert exc.reason == "invalid_envelope"
        exc = _capture(_with(references=[{"ref_id": "r", "kind": bad, "source_key": "s"}]))
        assert exc is not None, f"kind {bad!r} must be rejected"
        assert isinstance(exc, wire.PresentationValidationError)
        assert exc.reason == "invalid_envelope"
    for bad_kind in ("chart", "entity ", "Entity", ""):
        exc = _capture(_with(references=[{"ref_id": "r", "kind": bad_kind, "source_key": "s"}]))
        assert exc is not None, f"kind {bad_kind!r} must be rejected"
        assert isinstance(exc, wire.PresentationValidationError)
        assert exc.reason == "invalid_envelope"


def test_reference_bounds_are_nonempty_and_bounded() -> None:
    assert wire.MAX_REF_ID_LENGTH == 128
    assert wire.MAX_SOURCE_KEY_LENGTH == 256
    for empty in ("", "   ", "\n\t"):
        exc = _capture(_with(references=[{"ref_id": empty, "kind": "entity", "source_key": "s"}]))
        assert exc is not None, "empty ref_id must be rejected"
        exc = _capture(_with(references=[{"ref_id": "r", "kind": "entity", "source_key": empty}]))
        assert exc is not None, "empty source_key must be rejected"
    ok = _capture(
        _with(references=[{"ref_id": "r" * 128, "kind": "entity", "source_key": "k" * 256}])
    )
    assert ok is None, "bounds are inclusive"
    exc = _capture(_with(references=[{"ref_id": "r" * 129, "kind": "entity", "source_key": "s"}]))
    assert exc is not None, "ref_id above bound must be rejected"
    exc = _capture(_with(references=[{"ref_id": "r", "kind": "entity", "source_key": "k" * 257}]))
    assert exc is not None, "source_key above bound must be rejected"


def test_reference_ids_must_be_unique() -> None:
    refs = [
        {"ref_id": "dup", "kind": "entity", "source_key": "s1"},
        {"ref_id": "dup", "kind": "evidence", "source_key": "s2"},
    ]
    exc = _capture(_with(references=refs))
    assert exc is not None
    assert isinstance(exc, wire.PresentationValidationError)
    assert exc.reason == "invalid_envelope"


def test_reference_count_is_capped_at_100() -> None:
    assert wire.MAX_REFERENCE_COUNT == 100
    refs = [{"ref_id": f"r-{n}", "kind": "entity", "source_key": f"s-{n}"} for n in range(100)]
    assert _capture(_with(references=refs)) is None
    refs.append({"ref_id": "r-100", "kind": "entity", "source_key": "s-100"})
    exc = _capture(_with(references=refs))
    assert exc is not None
    assert isinstance(exc, wire.PresentationValidationError)
    assert exc.reason == "invalid_envelope"


def test_program_is_limited_to_64kib_utf8_bytes_including_multibyte() -> None:
    assert wire.MAX_PROGRAM_BYTES == 65536
    ascii_program = "a" * 65536
    assert _capture(_with(program=ascii_program)) is None
    exc = _capture(_with(program="a" * 65537))
    assert exc is not None, "65537 ASCII bytes must be rejected"
    multibyte_program = "é" * 32768
    assert len(multibyte_program) == 32768  # chars stay under the byte limit
    assert len(multibyte_program.encode("utf-8")) == 65536
    assert _capture(_with(program=multibyte_program)) is None
    exc = _capture(_with(program=multibyte_program + "a"))
    assert exc is not None, "65537 UTF-8 bytes must be rejected even though chars are fewer"


def test_program_type_and_unusable_emptiness_are_rejected() -> None:
    for bad in ("", "   ", "\n\t", None, 123, 1.5, b"bytes", [], {}):
        exc = _capture(_with(program=bad))
        assert exc is not None, f"program {bad!r} must be rejected"
        assert isinstance(exc, wire.PresentationValidationError)
        assert exc.reason == "invalid_envelope"


def test_malformed_shapes_are_rejected_without_crashing() -> None:
    for bad in ([], "payload", 5, True, float("nan"), object()):
        exc = _capture(bad)
        assert exc is not None, f"payload {bad!r} must be rejected"
        assert isinstance(exc, wire.PresentationValidationError)
        assert exc.reason == "unsupported_input_type"
    for bad_refs in ({"a": 1}, "refs", 5, None, [{"ref_id": "r", "kind": "entity"}], [{}]):
        exc = _capture(_with(references=bad_refs))
        assert exc is not None, f"references {bad_refs!r} must be rejected"
        assert isinstance(exc, wire.PresentationValidationError)
        assert exc.reason == "invalid_envelope"
    # Nonemptiness is required for program/identifiers only: a valid envelope
    # that cites nothing is still a valid envelope (packet section 9).
    assert _capture(_with(references=[])) is None


def test_exact_field_sets_pin_no_duplicate_fallback_field() -> None:
    assert set(wire.AssistantPresentationV1.model_fields) == {
        "schema_version",
        "format",
        "library",
        "program",
        "references",
    }
    assert set(wire.PresentationReferenceV1.model_fields) == {"ref_id", "kind", "source_key"}


# ---- slice 3: safe optional normalization and documented boundaries ---------

SENSITIVE_MARKER = "SECRET_PROGRAM_9f3a"
SENSITIVE_ANSWER = "SECRET_ANSWER_9f3a"
SENSITIVE_LABEL = "SECRET_LABEL_9f3a"


def _sensitive_payload() -> dict:
    payload = _valid_envelope()
    payload["program"] = f'render("{SENSITIVE_MARKER}")'
    return payload


def test_normalize_returns_none_for_absent_invalid_or_unsupported_metadata() -> None:
    assert wire.normalize_presentation(None) is None
    future = _with(schema_version=2)
    for bad in (
        future,
        "not json",
        '"a string"',
        5,
        [],
        True,
        float("nan"),
        object(),
        {"bogus": 1},
    ):
        assert wire.normalize_presentation(bad) is None, f"{bad!r} must fall back to unavailable"


def test_normalize_accepts_mapping_json_string_and_model_without_mutation() -> None:
    expected = wire.validate_presentation(_valid_envelope())
    payload = _valid_envelope()
    snapshot = json.dumps(payload, sort_keys=True)
    assert wire.normalize_presentation(payload) == expected
    assert wire.normalize_presentation(json.dumps(payload)) == expected
    assert wire.normalize_presentation(expected) == expected
    assert json.dumps(payload, sort_keys=True) == snapshot


def test_presentation_diagnostic_returns_safe_fixed_codes() -> None:
    assert set(wire.PRESENTATION_DIAGNOSTIC_CODES) == {
        "valid",
        "absent",
        "unsupported_input_type",
        "unsupported_schema_version",
        "unsupported_format",
        "unsupported_library",
        "invalid_envelope",
    }
    assert wire.presentation_diagnostic(None) == "absent"
    assert wire.presentation_diagnostic(_valid_envelope()) == "valid"
    assert wire.presentation_diagnostic(_with(schema_version=2)) == "unsupported_schema_version"
    assert wire.presentation_diagnostic(_with(format="openui")) == "unsupported_format"
    assert wire.presentation_diagnostic(_with(library="aimms-chat@99")) == "unsupported_library"
    assert wire.presentation_diagnostic("not json") == "unsupported_input_type"
    assert wire.presentation_diagnostic({"bogus": 1}) == "invalid_envelope"


def test_diagnostics_and_errors_never_echo_program_answer_or_labels() -> None:
    payload = _sensitive_payload()
    payload["label"] = SENSITIVE_LABEL
    payload["answer"] = SENSITIVE_ANSWER
    exc = _capture(payload)
    assert exc is not None
    assert isinstance(exc, wire.PresentationValidationError)
    for marker in (SENSITIVE_MARKER, SENSITIVE_ANSWER, SENSITIVE_LABEL):
        assert marker not in str(exc)
        assert marker not in exc.reason
    assert exc.reason in wire.PRESENTATION_DIAGNOSTIC_CODES
    assert wire.normalize_presentation(payload) is None
    code = wire.presentation_diagnostic(payload)
    assert code in wire.PRESENTATION_DIAGNOSTIC_CODES
    for marker in (SENSITIVE_MARKER, SENSITIVE_ANSWER, SENSITIVE_LABEL):
        assert marker not in code
    oversized = _with(program=SENSITIVE_MARKER + "é" * 40000)
    exc2 = _capture(oversized)
    assert exc2 is not None
    assert SENSITIVE_MARKER not in str(exc2)
    assert SENSITIVE_MARKER not in exc2.reason


def test_module_is_stdlib_and_pydantic_only_with_no_io_fetch_or_eval() -> None:
    import inspect

    source = inspect.getsource(wire)
    for forbidden in (
        "logging",
        "socket",
        "urllib",
        "requests",
        "httpx",
        "subprocess",
        "sqlite",
        "django",
        "eval(",
        "exec(",
        "open(",
        "urlopen(",
    ):
        assert forbidden not in source, f"wire must not contain {forbidden}"
    import_lines = [ln.strip() for ln in source.splitlines() if ln.startswith(("import ", "from "))]
    allowed = (
        "from __future__ import",
        "import json",
        "from collections.abc import Mapping",
        "from typing import",
        "from pydantic import",
    )
    assert import_lines, "module must declare imports for this check to mean anything"
    for line in import_lines:
        assert line.startswith(allowed), f"unexpected import: {line}"


def test_module_docstring_documents_authorization_and_fallback_boundaries() -> None:
    docs = wire.__doc__
    for phrase in (
        "not program semantic safety",
        "not authorization",
        "authorized per-reader projection",
        "canonical fallback",
        "unavailable, not zeros",
        "200 reachable nodes",
        "depth 12",
        "explicitly supported or fall back",
    ):
        assert phrase in docs, f"module docstring must document: {phrase}"


def test_public_api_is_re_exported_and_kinds_match_the_literal() -> None:
    from typing import get_args

    from ai.core import presentation

    assert presentation.AssistantPresentationV1 is wire.AssistantPresentationV1
    assert presentation.PresentationReferenceV1 is wire.PresentationReferenceV1
    assert presentation.PresentationValidationError is wire.PresentationValidationError
    assert presentation.validate_presentation is wire.validate_presentation
    assert presentation.normalize_presentation is wire.normalize_presentation
    assert presentation.presentation_diagnostic is wire.presentation_diagnostic
    assert get_args(wire.PresentationReferenceKind) == wire.REFERENCE_KINDS


# ---- slice 4: strict direct-model entry points and instance revalidation -----


def _model_payload() -> dict:
    return {
        "schema_version": 1,
        "format": "openui-lang",
        "library": "aimms-chat@1",
        "program": "ok",
        "references": [],
    }


def test_direct_model_rejects_loose_schema_version_at_every_entry_point() -> None:
    from pydantic import ValidationError

    for bad in (True, False, 1.0, "1", "01", 2, None):
        payload = _model_payload()
        payload["schema_version"] = bad
        encoded = json.dumps(payload)
        for entry in ("constructor", "model_validate", "model_validate_json"):
            try:
                if entry == "constructor":
                    result = wire.AssistantPresentationV1(**payload)
                elif entry == "model_validate":
                    result = wire.AssistantPresentationV1.model_validate(payload)
                else:
                    result = wire.AssistantPresentationV1.model_validate_json(encoded)
            except ValidationError:
                continue
            raise AssertionError(
                f"schema_version {bad!r} must be rejected at {entry}, got {result!r}"
            )
    valid = _model_payload()
    assert wire.AssistantPresentationV1(**valid).schema_version == 1
    assert wire.AssistantPresentationV1.model_validate(valid).schema_version == 1
    assert wire.AssistantPresentationV1.model_validate_json(json.dumps(valid)).schema_version == 1


def test_model_instances_are_revalidated_not_trusted() -> None:
    envelope = wire.validate_presentation(_valid_envelope())
    assert wire.validate_presentation(envelope) == envelope
    assert wire.normalize_presentation(envelope) == envelope

    mutated = wire.validate_presentation(_valid_envelope())
    mutated.schema_version = 2
    exc = _capture(mutated)
    assert exc is not None
    assert isinstance(exc, wire.PresentationValidationError)
    assert exc.reason == "unsupported_schema_version"
    assert wire.normalize_presentation(mutated) is None
    assert wire.presentation_diagnostic(mutated) == "unsupported_schema_version"

    constructed = wire.AssistantPresentationV1.model_construct(
        schema_version=2,
        format="openui-lang",
        library="aimms-chat@1",
        program="ok",
        references=[],
    )
    exc2 = _capture(constructed)
    assert exc2 is not None
    assert isinstance(exc2, wire.PresentationValidationError)
    assert exc2.reason == "unsupported_schema_version"
    assert wire.normalize_presentation(constructed) is None

    bad_library = wire.validate_presentation(_valid_envelope())
    bad_library.library = "aimms-chat@99"
    exc3 = _capture(bad_library)
    assert exc3 is not None
    assert isinstance(exc3, wire.PresentationValidationError)
    assert exc3.reason == "unsupported_library"
    assert wire.normalize_presentation(bad_library) is None


def test_mutated_nested_values_revalidate_against_current_budgets() -> None:
    oversized = wire.validate_presentation(_valid_envelope())
    oversized.program = "a" * (wire.MAX_PROGRAM_BYTES + 1)
    exc = _capture(oversized)
    assert exc is not None
    assert isinstance(exc, wire.PresentationValidationError)
    assert exc.reason == "invalid_envelope"
    assert wire.normalize_presentation(oversized) is None

    duplicate = wire.validate_presentation(_valid_envelope())
    duplicate.references[1].ref_id = duplicate.references[0].ref_id
    exc2 = _capture(duplicate)
    assert exc2 is not None
    assert isinstance(exc2, wire.PresentationValidationError)
    assert exc2.reason == "invalid_envelope"

    bad_kind = wire.validate_presentation(_valid_envelope())
    bad_kind.references[0].kind = "chart"
    exc3 = _capture(bad_kind)
    assert exc3 is not None
    assert isinstance(exc3, wire.PresentationValidationError)
    assert exc3.reason == "invalid_envelope"

    empty_id = wire.validate_presentation(_valid_envelope())
    empty_id.references[0].ref_id = ""
    exc4 = _capture(empty_id)
    assert exc4 is not None
    assert isinstance(exc4, wire.PresentationValidationError)
    assert exc4.reason == "invalid_envelope"

    too_many = wire.validate_presentation(_valid_envelope())
    too_many.references = [
        wire.PresentationReferenceV1(ref_id=f"r-{n}", kind="entity", source_key=f"s-{n}")
        for n in range(wire.MAX_REFERENCE_COUNT + 1)
    ]
    exc5 = _capture(too_many)
    assert exc5 is not None
    assert isinstance(exc5, wire.PresentationValidationError)
    assert exc5.reason == "invalid_envelope"

    intact = wire.validate_presentation(_valid_envelope())
    assert wire.validate_presentation(intact) == intact


# ---- slice 5: P01C nested-instance revalidation and content-free diagnostics ----


def test_direct_model_entry_revalidates_mutated_top_instance() -> None:
    from pydantic import ValidationError

    intact_top = wire.AssistantPresentationV1.model_validate(_valid_envelope())
    assert wire.AssistantPresentationV1.model_validate(intact_top) == intact_top

    for key, value in (
        ("schema_version", 2),
        ("format", "openui"),
        ("library", "aimms-chat@99"),
        ("program", "a" * (wire.MAX_PROGRAM_BYTES + 1)),
        ("program", ""),
    ):
        mutated = wire.AssistantPresentationV1.model_validate(_valid_envelope())
        setattr(mutated, key, value)
        try:
            wire.AssistantPresentationV1.model_validate(mutated)
        except ValidationError:
            continue
        raise AssertionError(f"model_validate must reject mutated {key}")


def test_nested_reference_instances_are_revalidated_in_mapping_and_constructor() -> None:
    from pydantic import ValidationError

    payload = _valid_envelope()
    payload["references"] = [{"ref_id": "ref-1", "kind": "entity", "source_key": "src-1"}]
    intact_ref = wire.PresentationReferenceV1(ref_id="ref-1", kind="entity", source_key="src-1")
    accepted = wire.normalize_presentation({**payload, "references": [intact_ref]})
    assert accepted == wire.validate_presentation(payload)

    for key, value in (
        ("source_key", ""),
        ("source_key", "k" * (wire.MAX_SOURCE_KEY_LENGTH + 1)),
        ("ref_id", "   "),
        ("ref_id", "r" * (wire.MAX_REF_ID_LENGTH + 1)),
        ("kind", "chart"),
    ):
        mutated = wire.PresentationReferenceV1(ref_id="ref-1", kind="entity", source_key="src-1")
        setattr(mutated, key, value)
        mapping_input = {**payload, "references": [mutated]}
        got = wire.normalize_presentation(mapping_input)
        assert got is None, f"mapping with mutated nested {key} must fall back"
        assert wire.presentation_diagnostic(mapping_input) == "invalid_envelope"
        try:
            wire.AssistantPresentationV1(**mapping_input)
        except ValidationError:
            pass
        else:
            raise AssertionError(f"constructor must reject mutated nested {key}")

    constructed = wire.PresentationReferenceV1.model_construct(
        ref_id="", kind="chart", source_key=""
    )
    constructed_input = {**payload, "references": [constructed]}
    assert wire.normalize_presentation(constructed_input) is None
    assert wire.presentation_diagnostic(constructed_input) == "invalid_envelope"


def test_normalization_diagnostics_never_carry_dictionary_reference_payload(capsys) -> None:
    import warnings

    sentinel = "sentinel_source_key_7c2d"
    fragments = [sentinel[i : i + 6] for i in range(0, len(sentinel), 6)]
    raw_reference = {"ref_id": "ref-1", "kind": "entity", "source_key": sentinel}
    constructed = wire.AssistantPresentationV1.model_construct(
        schema_version=1,
        format="openui-lang",
        library="aimms-chat@1",
        program="ok",
        references=[raw_reference],
    )
    with warnings.catch_warnings(record=True) as raw:
        warnings.simplefilter("always")
        constructed.model_dump()
    assert raw, "control: raw model_dump must surface the serializer warning"
    assert any(fragment in str(entry.message) for entry in raw for fragment in fragments), (
        "control: the payload leak must be observable in the raw warning"
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        normalized = wire.normalize_presentation(constructed)
    assert normalized is not None, "valid representational input must stay accepted"
    expected = wire.AssistantPresentationV1(
        schema_version=1,
        format="openui-lang",
        library="aimms-chat@1",
        program="ok",
        references=[{"ref_id": "ref-1", "kind": "entity", "source_key": sentinel}],
    )
    assert normalized == expected
    assert [ref.source_key for ref in normalized.references] == [sentinel]
    for entry in caught:
        text = str(entry.message)
        for fragment in fragments:
            assert fragment not in text, "diagnostics must be content-free"
    assert not caught, "safe normalization boundary must emit no serializer warnings"
    captured = capsys.readouterr()
    for fragment in fragments:
        assert fragment not in captured.out
        assert fragment not in captured.err
