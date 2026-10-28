"""One provider speech correction cannot modify validated diagnostic evidence."""

import asyncio
import hashlib
import json
import logging
from types import SimpleNamespace

import pytest
from ai.core.tests.test_luna_diagnostics import (
    _adapter,
    _canonical_json,
    _Client,
    _envelope,
    _response,
)

SUMMARY = (
    "Unknown sensor quality may require comparison with valid peer channels and an "
    "independent reference. Approximately matched load and cooling observations cannot "
    "confirm a physical fault from a synthetic alarm. These are checks to separate "
    "possible causes, not verified repair results. Check the authoritative safety status "
    "before proceeding."
)
BOUNDARY = json.loads(_canonical_json())["safety_boundary"]
BODY = SUMMARY.removesuffix(BOUNDARY).strip()
LONG_SUMMARY = (
    "Unknown sensor quality may require comparison with valid peer channels and an "
    "independent reference. Approximately matched load and cooling observations cannot "
    "confirm a physical fault from a synthetic alarm. These are checks to separate "
    "possible causes, not verified repair results. Compare channel behavior under the "
    "same operating condition, compare load with temperature, and compare cooling "
    "response with available operating records before drawing a conclusion. The manual "
    "is a related reference whose fitted applicability remains unverified, so do not "
    "treat the proposed comparisons as confirmed findings. " + BOUNDARY
)


def _speech_adapter(client):
    class Formatter:
        def with_options(self, **kwargs):
            assert kwargs["max_retries"] == 0
            return self

        async def create(self, **kwargs):
            response = await client.responses.create(**kwargs)
            return SimpleNamespace(
                id=response.id,
                model="configured-fast-test",
                choices=[
                    SimpleNamespace(
                        finish_reason="stop", message=SimpleNamespace(content=response.output_text)
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20, total_tokens=30),
            )

        @property
        def chat(self):
            return SimpleNamespace(completions=SimpleNamespace(create=self.create))

    adapter = _adapter(client)
    adapter._speech_formatter_client_factory = Formatter
    return adapter


def _invalid_summary():
    payload = json.loads(_canonical_json())
    payload["detailed_response"] = SUMMARY
    payload["spoken_summary"] = SUMMARY.replace("Unknown ", "")
    return json.dumps(payload)


def test_one_real_provider_correction_preserves_answer_and_sources():
    client = _Client([
        _response(response_id="answer", text=_invalid_summary()),
        _response(response_id="speech", text=json.dumps({"spoken_body": BODY})),
    ])
    outcome = asyncio.run(_speech_adapter(client).reason(envelope=_envelope()))

    assert outcome.response.speak is True
    assert outcome.response.spoken_summary == SUMMARY
    assert outcome.response.detailed_response == SUMMARY
    assert outcome.response.evidence == []
    assert outcome.provenance.provider_request_id == "answer"
    assert outcome.provenance.spoken_summary_correction_request_id == "speech"
    assert len(client.responses.calls) == 2
    assert "tools" not in client.responses.calls[1]
    assert "reasoning" not in client.responses.calls[1]
    schema = client.responses.calls[1]["response_format"]["json_schema"]["schema"]
    assert set(schema["properties"]) == {"spoken_body"}
    instructions = client.responses.calls[1]["messages"][0]["content"]
    assert "name all check categories" in instructions
    assert "Exact substantive word forms available" in instructions
    assert '"approximately"' in instructions
    assert "Prefer copying complete existing clauses verbatim" in instructions
    assert outcome.provenance.spoken_summary_correction_model == "configured-fast-test"
    assert outcome.provenance.to_dict()["spoken_summary_correction_effort"] == "none"
    assert client.responses.calls[1]["max_tokens"] <= 2048
    assert outcome.response.safety_boundary == BOUNDARY
    assert outcome.provenance.to_dict()["spoken_summary_correction_auth_mode"] == "key"


@pytest.mark.parametrize(
    "correction",
    [
        {"spoken_body": BODY + " Equipment is safe."},
        {"spoken_body": BODY.replace("Approximately ", "")},
        {"spoken_body": "Unknown conditions."},
        {"spoken_body": BODY, "evidence": [{"id": "forged"}]},
        {"spoken_body": BODY + " correlation"},
        {"spoken_body": {"word": BODY}},
        {"spoken_summary": SUMMARY},
        {"spoken_body": ""},
        {"spoken_body": " ".join([BODY] * 4)},
    ],
)
def test_failed_correction_copies_whole_validated_answer_without_retry(correction):
    client = _Client([
        _response(text=_invalid_summary()),
        _response(text=json.dumps(correction)),
    ])
    outcome = asyncio.run(_speech_adapter(client).reason(envelope=_envelope()))

    assert outcome.response.response_state == "complete"
    assert outcome.response.detailed_response == SUMMARY
    assert outcome.response.speak is True
    assert outcome.response.spoken_summary == SUMMARY
    assert outcome.provenance.spoken_summary_correction_attempted is True
    assert outcome.provenance.spoken_summary_extractive_fallback is True
    assert len(client.responses.calls) == 2


def test_explicit_silent_abstention_does_not_trigger_speech():
    payload = json.loads(_canonical_json())
    payload.update(speak=False, spoken_summary="")
    client = _Client([_response(text=json.dumps(payload))])
    outcome = asyncio.run(_adapter(client).reason(envelope=_envelope()))

    assert outcome.response.speak is False
    assert outcome.provenance.spoken_summary_correction_attempted is False
    assert len(client.responses.calls) == 1


def test_one_faithful_correction_can_exceed_eighty_words_without_clipping():
    assert 80 < len(LONG_SUMMARY.split()) <= 120
    payload = json.loads(_canonical_json())
    payload.update(
        detailed_response=LONG_SUMMARY, spoken_summary=LONG_SUMMARY.replace("Approximately ", "")
    )
    client = _Client([
        _response(text=json.dumps(payload)),
        _response(text=json.dumps({"spoken_body": LONG_SUMMARY.removesuffix(BOUNDARY).strip()})),
    ])
    outcome = asyncio.run(_speech_adapter(client).reason(envelope=_envelope()))
    assert outcome.response.speak is True
    assert outcome.response.spoken_summary == LONG_SUMMARY
    assert outcome.response.detailed_response == LONG_SUMMARY
    assert outcome.response.safety_boundary == BOUNDARY
    assert len(client.responses.calls) == 2
    assert "Prefer 50 to 85 words TOTAL" in client.responses.calls[1]["messages"][0]["content"]


def test_correction_over_120_words_is_rejected_without_clipping_or_retry(caplog):
    payload = json.loads(_canonical_json())
    payload.update(
        detailed_response=LONG_SUMMARY, spoken_summary=LONG_SUMMARY.replace("Approximately ", "")
    )
    oversized = " ".join([LONG_SUMMARY.removesuffix(BOUNDARY).strip()] * 2)
    assert len((oversized + " " + BOUNDARY).split()) > 120
    client = _Client([
        _response(text=json.dumps(payload)),
        _response(text=json.dumps({"spoken_body": oversized})),
    ])
    outcome = asyncio.run(_speech_adapter(client).reason(envelope=_envelope()))
    assert outcome.response.speak is True
    assert outcome.response.spoken_summary == LONG_SUMMARY
    assert outcome.response.detailed_response == LONG_SUMMARY
    assert outcome.provenance.spoken_summary_extractive_fallback is True
    assert len(client.responses.calls) == 2
    assert "rule=schema reason=body_shape_or_word_count" in caplog.text


@pytest.mark.parametrize(
    ("body", "expected_rule"),
    [
        (BODY + " invented-token", "new_tokens"),
        (BODY.replace("Approximately ", ""), "missing_marker"),
        (
            BODY.replace("cannot confirm", "confirm").replace("not verified", "verified"),
            "missing_negation",
        ),
        (BODY.replace("Unknown", "**Unknown**"), "plaintext"),
    ],
)
def test_correction_refusal_logs_only_rule_without_candidate_values(caplog, body, expected_rule):
    client = _Client([
        _response(text=_invalid_summary()),
        _response(text=json.dumps({"spoken_body": body})),
    ])
    outcome = asyncio.run(_speech_adapter(client).reason(envelope=_envelope()))
    assert outcome.response.speak is True
    assert outcome.response.spoken_summary == SUMMARY
    assert outcome.response.detailed_response == SUMMARY
    lines = [
        row.getMessage()
        for row in caplog.records
        if row.getMessage().startswith("luna.spoken_summary_correction_refused")
    ]
    assert any(f"rule={expected_rule} " in row for row in lines)
    assert all("invented-token" not in row and body not in row for row in lines)
    assert len(client.responses.calls) == 2


def test_boundary_validation_rule_maps_to_value_free_refusal_enum():
    from ai.core.reasoning.luna_diagnostics import _speech_correction_refusal_rule
    from ai.core.reasoning.schemas import CanonicalTurnResponse
    from pydantic import ValidationError

    payload = json.loads(_canonical_json())
    payload.update(detailed_response=SUMMARY, spoken_summary=BODY)
    with pytest.raises(ValidationError) as captured:
        CanonicalTurnResponse.model_validate_json(json.dumps(payload))
    assert _speech_correction_refusal_rule(captured.value) == "boundary"


def test_value_free_skip_trace_preserves_provider_silence_with_invalid_nonempty_summary(caplog):
    caplog.set_level(logging.INFO, logger="ai.core.reasoning.luna_diagnostics")
    payload = json.loads(_invalid_summary())
    payload["speak"] = False
    client = _Client([_response(text=json.dumps(payload))])
    outcome = asyncio.run(_speech_adapter(client).reason(envelope=_envelope()))
    assert outcome.response.speak is False
    assert not outcome.provenance.spoken_summary_correction_attempted
    assert len(client.responses.calls) == 1
    logs = [
        record.getMessage()
        for record in caplog.records
        if "luna.voice_summary" in record.getMessage()
    ]
    assert any("original_speak=False summary_present=True" in row for row in logs)
    assert any("reason=provider_silence" in row for row in logs)
    assert all(SUMMARY not in row for row in logs)


def test_concise_voice_guidance_does_not_change_text_instructions():
    from ai.core.reasoning.luna_diagnostics import _VOICE_CONCISE_DIRECTIVE

    for mode in ("voice", "text"):
        client = _Client([_response(text=_canonical_json())])
        envelope = _envelope().model_copy(update={"mode": mode})
        asyncio.run(_adapter(client).reason(envelope=envelope))
        instructions = client.responses.calls[0]["instructions"]
        assert (_VOICE_CONCISE_DIRECTIVE in instructions) is (mode == "voice")
        assert ("records actually retrieved" in instructions) is (mode == "voice")
        assert ("each record's own" in instructions) is (mode == "voice")
        assert ("limited retrieval does not establish" in instructions) is (mode == "voice")
        assert ("retrieval does not establish all or only history" in instructions) is (
            mode == "voice"
        )


def test_speech_flag_contract_is_voice_only_and_never_forces_a_value():
    adapter = _adapter(_Client([]))
    for mode in ("voice", "text"):
        request = adapter._base_request(
            envelope=_envelope().model_copy(update={"mode": mode}),
            effort="medium",
            tool_context=None,
            output_token_limit=1000,
        )
        fields = request["text"]["format"]["schema"]["properties"]
        assert fields["speak"]["type"] == "boolean"
        assert "default" not in fields["speak"] and "const" not in fields["speak"]
        if mode == "voice":
            assert "choose true" in fields["speak"]["description"]
            assert "intentional withholding" in fields["speak"]["description"]
            assert (
                "When speak is false, return the empty string"
                in fields["spoken_summary"]["description"]
            )
            assert "set speak=true" in request["instructions"]
            assert "speak=false AND spoken_summary" in request["instructions"]
        else:
            assert "description" not in fields["speak"]
            assert "description" not in fields["spoken_summary"]
            assert "set speak=true" not in request["instructions"]


def test_native_django_voice_modality_is_a_literal_lowercase_voice_envelope():
    from ai.core.reasoning.luna_diagnostics import TrustedReasoningEnvelope
    from aichat.models import TurnModality

    native = TrustedReasoningEnvelope(**{
        **_envelope().model_dump(),
        "mode": TurnModality.VOICE,
    })
    assert type(native.mode) is str and native.mode == "voice"
    assert json.loads(native.model_dump_json())["mode"] == "voice"
    with pytest.raises(ValueError):
        TrustedReasoningEnvelope(**{**_envelope().model_dump(), "mode": "VOICE"})


def test_history_enriched_silent_answer_uses_one_formatter_with_cumulative_budget(monkeypatch):
    from ai.core.reasoning.luna_diagnostics import ToolLoopBudget
    from ai.core.tests.test_history_enrichment import _HistoryRegistry

    primary = json.loads(_canonical_json())
    primary["confidence"] = "medium"
    enriched = json.loads(_invalid_summary())
    enriched["confidence"] = "medium"
    client = _Client([
        _response(response_id="primary", text=json.dumps(primary), output_tokens=100),
        _response(response_id="enriched", text=json.dumps(enriched), output_tokens=100),
        _response(response_id="speech", text=json.dumps({"spoken_body": BODY})),
    ])
    from ai.core.config import get_settings

    settings = get_settings().model_copy(update={"feature_history_enrichment": True})
    monkeypatch.setattr("ai.core.config.get_settings", lambda: settings)
    adapter = _speech_adapter(client)
    adapter.budget = ToolLoopBudget(max_output_tokens=400, history_enrichment_rounds=1)
    adapter.tool_registry = _HistoryRegistry()
    envelope = _envelope(tools=("get_recent_maintenance_history", "find_similar_past_repairs"))
    from ai.core.reasoning.luna_diagnostics import AuthorizedRecord

    envelope = envelope.model_copy(
        update={
            "authorized_records": (
                AuthorizedRecord(
                    entity_type="machine", entity_id=44, expected_revision="machine-r1"
                ),
            )
        }
    )
    outcome = asyncio.run(adapter.reason(envelope=envelope, tool_context=object()))
    assert outcome.response.speak is True
    assert outcome.response.spoken_summary == SUMMARY
    assert outcome.provenance.provider_request_id == "enriched"
    assert outcome.provenance.history_enrichment_rounds == 1
    assert len(client.responses.calls) == 3
    assert client.responses.calls[-1]["max_tokens"] == 200


@pytest.mark.parametrize("owned", [True, False])
def test_formatter_closes_only_its_owned_temporary_client(monkeypatch, owned):
    from ai.core.reasoning.luna_diagnostics import LunaDiagnosticsAdapter
    from ai.core.reasoning.speech_formatter import format_voice_body

    class Client:
        closed = 0

        def with_options(self, **options):
            assert options["max_retries"] == 0
            return self

        @property
        def chat(self):
            return SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **options):
            assert "tools" not in options and "reasoning_effort" not in options
            return SimpleNamespace(
                id="formatter-run",
                model="actual-fast-model",
                usage=None,
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content='{"spoken_body":"Body"}'),
                    )
                ],
            )

        def close(self):
            self.closed += 1

    client = Client()
    monkeypatch.setattr(
        "ai.core.integrations.azure_openai_client.build_openai_client", lambda **_: client
    )
    response = asyncio.run(
        format_voice_body(
            {
                "instructions": "Format",
                "input": [],
                "max_output_tokens": 100,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "speech",
                        "strict": True,
                        "schema": {},
                    }
                },
            },
            timeout=1,
            cancel_event=None,
            wait_with_cancel=LunaDiagnosticsAdapter._wait_with_cancel,
            client_factory=None if owned else lambda: client,
        )
    )
    assert response.model == "actual-fast-model"
    assert client.closed == int(owned)


def _probe_full_answer_fallback(
    *,
    visible=SUMMARY,
    reasoning="The response abstains without authorized evidence.",
    mode="voice",
    state="complete",
    intended_speak=True,
    intended_summary="Intended summary.",
    clock=None,
    boundary=BOUNDARY,
):
    """Exercise the post-source-validation seam without any source/provider reads."""
    from ai.core.reasoning.luna_diagnostics import ReasoningOutcome, ReasoningProvenance
    from ai.core.reasoning.schemas import CanonicalTurnResponse

    payload = json.loads(_canonical_json())
    payload.update(
        detailed_response=visible,
        reasoning_summary=reasoning,
        response_state=state,
        speak=False,
        spoken_summary="",
        safety_boundary=boundary,
    )
    outcome = ReasoningOutcome(
        response=CanonicalTurnResponse.model_validate_json(json.dumps(payload)),
        provenance=ReasoningProvenance(
            invocation_mode="agent_reference",
            provider_request_id="source-validated-primary",
            effort="medium",
        ),
    )
    original = {**payload, "speak": intended_speak, "spoken_summary": intended_summary}
    client = _Client([_response(text=json.dumps({"spoken_body": BODY + " invented-token"}))])
    adapter = _speech_adapter(client)
    adapter._clock = clock or (lambda: 0)
    result = asyncio.run(
        adapter._correct_voice_summary(
            outcome=outcome,
            provider_response=_response(text=json.dumps(original)),
            envelope=_envelope().model_copy(update={"mode": mode}),
            selected_effort="medium",
            tool_context=None,
            deadline=1000,
            output_tokens_used=0,
            cancel_event=None,
        )
    )
    return result, client, outcome


def test_full_answer_fallback_preserves_every_field_and_records_actual_text_hash(caplog):
    caplog.set_level(logging.INFO, logger="ai.core.reasoning.luna_diagnostics")
    result, client, original = _probe_full_answer_fallback()
    assert result.response.speak and result.response.spoken_summary == SUMMARY
    assert result.response.model_dump(exclude={"speak", "spoken_summary"}) == (
        original.response.model_dump(exclude={"speak", "spoken_summary"})
    )
    proof = result.provenance.to_dict()
    assert proof["spoken_summary_extractive_fallback"] is True
    assert proof["spoken_summary_extractive_policy"] == "complete-visible-answer-v1"
    assert (
        proof["spoken_summary_extractive_source_sha256"]
        == hashlib.sha256(SUMMARY.encode()).hexdigest()
    )
    assert proof["spoken_summary_correction_attempted"] is True
    assert len(client.responses.calls) == 1
    logs = [r.getMessage() for r in caplog.records if "extractive_fallback" in r.getMessage()]
    assert any("applied=True rule=complete_visible_answer" in text for text in logs)
    assert all(SUMMARY not in text and "invented-token" not in text for text in logs)


@pytest.mark.parametrize(
    ("options", "expected_calls"),
    [
        ({"mode": "text"}, 0),
        ({"state": "incomplete"}, 0),
        ({"state": "failed"}, 0),
        ({"intended_speak": False, "intended_summary": ""}, 0),
        ({"intended_speak": False}, 0),
        ({"intended_summary": ""}, 1),
        ({"intended_summary": None}, 1),
    ],
)
def test_full_answer_fallback_never_overrides_modality_state_or_intent(options, expected_calls):
    result, client, original = _probe_full_answer_fallback(**options)
    assert result.response == original.response
    assert not result.provenance.spoken_summary_extractive_fallback
    assert len(client.responses.calls) == expected_calls


@pytest.mark.parametrize(
    "options",
    [
        {"visible": SUMMARY.removesuffix(BOUNDARY).strip()},
        {
            "visible": SUMMARY.replace("Unknown ", ""),
            "reasoning": "The actual condition remains unknown.",
        },
        {
            "visible": SUMMARY.replace("Approximately ", ""),
            "reasoning": "The comparison remains approximate.",
        },
        {
            "visible": SUMMARY.replace("cannot confirm", "confirm").replace(
                "not verified", "verified"
            ),
            "reasoning": "No confirmed root cause is available.",
        },
    ],
)
def test_full_public_response_preserves_complete_public_fields_without_new_words(options):
    from ai.core.turn.responses import _plain_spoken_text

    result, client, original = _probe_full_answer_fallback(**options)
    expected = _plain_spoken_text(
        "\n\n".join(
            getattr(original.response, key)
            for key in ("detailed_response", "reasoning_summary", "safety_boundary")
        )
    )
    assert result.response.speak
    assert result.response.spoken_summary == expected
    assert result.response.model_dump(exclude={"speak", "spoken_summary"}) == (
        original.response.model_dump(exclude={"speak", "spoken_summary"})
    )
    assert result.provenance.spoken_summary_extractive_fallback
    assert result.provenance.spoken_summary_extractive_mode == "complete_public_response"
    assert result.provenance.spoken_summary_extractive_policy == "complete-public-response-v1"
    assert (
        result.provenance.spoken_summary_extractive_source_sha256
        == hashlib.sha256(expected.encode()).hexdigest()
    )
    assert len(client.responses.calls) == 1


@pytest.mark.parametrize(
    "nonprose",
    [
        "\n| Condition | Required qualifier |\n| --- | --- |\n| A | Never bypass isolation |",
        "\nCondition | Required qualifier\n--- | ---\nA | Never bypass isolation",
        "\n```text\nNever bypass isolation.\n```",
        "\n~~~text\nNever bypass isolation.\n~~~",
        "\n![Never bypass isolation](diagram.png)",
        "\n<table><tr><td>Never bypass isolation.</td></tr></table>",
        "\n<svg><text>Never bypass isolation.</text></svg>",
    ],
)
def test_full_answer_fallback_refuses_rendered_content_the_normalizer_could_drop(nonprose):
    result, client, original = _probe_full_answer_fallback(visible=SUMMARY + nonprose)
    assert result.response == original.response
    assert not result.provenance.spoken_summary_extractive_fallback
    assert len(client.responses.calls) == 1


def test_actual_t03_public_record_recovers_only_by_copying_every_complete_public_field(monkeypatch):
    from ai.core.config import get_settings
    from ai.core.turn.responses import _plain_spoken_text

    settings = get_settings().model_copy(update={"feature_voice_foreground_session": True})
    monkeypatch.setattr("ai.core.config.get_settings", lambda: settings)
    visible = (
        "The WEG passage says air-air exchanger pipes should stay clean and unobstructed, "
        "while air-water radiator pipes need periodic cleaning to remove incrustation; "
        "it also discusses filter cleaning. No cooler fouling or fitted-model applicability "
        "is confirmed for this motor, so the passage does not establish that its guidance "
        "applies. Do not treat this related manual as an approved fitted-motor procedure "
        "or declare the equipment safe, isolated, approved, cleared, or restored.\n\n"
        "The passage further says a round brush may be used to remove dirt from air-air "
        "exchanger pipes. This document is a related attached reference, not an approved "
        "or verified fitted-motor manual. Its applicability to the installed motor and "
        "cooler must be confirmed before applying its instructions."
    )
    reasoning = (
        "The requested passage was found in a related attached WEG reference. Its stated "
        "cooling guidance is clear, but the source is explicitly unverified for the fitted "
        "motor and cooler. No cooler fouling or fitted-model applicability is confirmed."
    )
    boundary = (
        "Do not treat this related manual as an approved fitted-motor procedure or declare "
        "the equipment safe, isolated, approved, cleared, or restored."
    )
    result, client, original = _probe_full_answer_fallback(
        visible=visible, reasoning=reasoning, boundary=boundary
    )
    expected = _plain_spoken_text("\n\n".join((visible, reasoning, boundary)))
    assert result.response.speak and result.response.spoken_summary == expected
    assert result.response.reasoning_summary == original.response.reasoning_summary
    assert result.response.detailed_response == visible
    assert result.response.safety_boundary == boundary
    assert result.provenance.spoken_summary_extractive_mode == "complete_public_response"
    assert len(client.responses.calls) == 1


@pytest.mark.parametrize(
    "nonprose",
    [
        "\n```code\nmaterial qualifier\n```",
        "\n| A | B |",
        "<p>material qualifier</p>",
        "![qualifier](source.png)",
    ],
)
def test_whole_public_response_refuses_nonprose_when_that_record_is_needed(nonprose):
    result, client, original = _probe_full_answer_fallback(
        visible=SUMMARY.replace("Unknown ", ""),
        reasoning="The actual condition is unknown." + nonprose,
    )
    assert result.response == original.response
    assert not result.provenance.spoken_summary_extractive_fallback
    assert len(client.responses.calls) == 1


def test_whole_public_response_preserves_foreground_ceiling_without_clipping(monkeypatch):
    from ai.core.config import get_settings

    settings = get_settings().model_copy(update={"feature_voice_foreground_session": True})
    monkeypatch.setattr("ai.core.config.get_settings", lambda: settings)
    result, client, original = _probe_full_answer_fallback(
        visible=SUMMARY.replace("Unknown ", ""), reasoning="Unknown " + "public text " * 1000
    )
    assert result.response == original.response
    assert not result.provenance.spoken_summary_extractive_fallback
    assert len(client.responses.calls) == 1


def test_valid_answer_only_remains_preferred_over_longer_public_record():
    result, client, original = _probe_full_answer_fallback()
    assert result.response.spoken_summary == SUMMARY
    assert result.provenance.spoken_summary_extractive_mode == "complete_visible_answer"
    assert original.response.reasoning_summary not in result.response.spoken_summary
    assert len(client.responses.calls) == 1


def test_voice_instructions_preserve_conditional_reasoning_and_requested_manual_mechanism():
    from ai.core.reasoning.luna_diagnostics import _VOICE_CONCISE_DIRECTIVE

    assert "relative support for the competing hypotheses" in _VOICE_CONCISE_DIRECTIVE
    assert "Do not force a winner" in _VOICE_CONCISE_DIRECTIVE
    assert (
        "Generic maintenance instructions do not establish the requested mechanism"
        in _VOICE_CONCISE_DIRECTIVE
    )


def test_full_answer_fallback_reuses_foreground_ceiling_without_clipping(monkeypatch):
    from ai.core.config import get_settings
    from ai.core.turn.responses import _plain_spoken_text

    settings = get_settings().model_copy(update={"feature_voice_foreground_session": True})
    monkeypatch.setattr("ai.core.config.get_settings", lambda: settings)
    visible = (SUMMARY + " ") * 3
    assert len(visible.split()) > 120
    result, client, original = _probe_full_answer_fallback(visible=visible)
    assert result.response.speak
    assert result.response.spoken_summary == _plain_spoken_text(visible)
    assert result.response.detailed_response == original.response.detailed_response
    assert len(client.responses.calls) == 1
    oversize = (SUMMARY + " ") * 40
    assert len(_plain_spoken_text(oversize)) > 8000
    result, client, original = _probe_full_answer_fallback(visible=oversize)
    assert result.response == original.response
    assert not result.provenance.spoken_summary_extractive_fallback
    assert len(client.responses.calls) == 1


def test_full_answer_fallback_allows_format_only_normalization_without_extra_words():
    from ai.core.turn.responses import _plain_spoken_text

    visible = (
        "# Related reference\n\n"
        + SUMMARY
        + "\n\n- [Actual source](https://example.invalid/reference)"
    )
    result, client, original = _probe_full_answer_fallback(visible=visible)
    assert result.response.speak
    assert result.response.spoken_summary == _plain_spoken_text(visible)
    assert result.response.detailed_response == original.response.detailed_response
    assert len(client.responses.calls) == 1


def test_full_answer_fallback_does_not_extend_an_expired_turn_deadline():
    clock = iter([0, 1000])
    result, client, original = _probe_full_answer_fallback(clock=lambda: next(clock))
    assert result.response == original.response
    assert not result.provenance.spoken_summary_extractive_fallback
    assert len(client.responses.calls) == 1


def test_rejected_formatter_uses_complete_opening_brief_with_unchanged_canonical_fields():
    """A provider-authored brief can avoid repeating the detailed explanation."""
    visible = SUMMARY + "\n\nCompare valid peer channels with cooling observations."
    result, client, original = _probe_full_answer_fallback(
        visible=visible, reasoning="The answer compares readings."
    )
    assert result.response.spoken_summary == SUMMARY
    assert result.response.model_dump(exclude={"speak", "spoken_summary"}) == (
        original.response.model_dump(exclude={"speak", "spoken_summary"})
    )
    assert result.provenance.spoken_summary_extractive_mode == "complete_opening_brief"
    assert result.provenance.spoken_summary_extractive_policy == "complete-opening-brief-v1"
    assert (
        result.provenance.spoken_summary_extractive_source_sha256
        == hashlib.sha256(SUMMARY.encode()).hexdigest()
    )
    assert len(client.responses.calls) == 1


@pytest.mark.parametrize(
    "detail",
    [
        "The fitted wiring diagram is not verified.",
        "Retrieved maintenance records are software exercises, not physical inspections.",
        "The temperature was 93 degrees Celsius at 14:35 UTC.",
        "The trace is stale and cannot establish a current running condition.",
    ],
)
def test_shorter_fallback_preserves_unique_later_qualifiers_and_measurements(detail):
    """Broad lexical uncertainty categories cannot replace specific caveats."""
    from ai.core.turn.responses import _plain_spoken_text

    visible = SUMMARY + "\n\n" + detail
    result, client, original = _probe_full_answer_fallback(
        visible=visible, reasoning="The answer compares readings."
    )
    from ai.core.reasoning.schemas import CanonicalTurnResponse

    # The unchanged validator accepts this shorter candidate: the additional
    # guard must still preserve the later qualifier's specific context/value.
    CanonicalTurnResponse.model_validate_json(
        json.dumps({
            **original.response.model_dump(mode="json"),
            "spoken_summary": SUMMARY,
            "speak": True,
        })
    )
    assert result.response.spoken_summary == _plain_spoken_text(visible)
    assert result.provenance.spoken_summary_extractive_mode == "complete_visible_answer"
    assert result.response.detailed_response == original.response.detailed_response
    assert len(client.responses.calls) == 1


def test_bibliography_identifiers_do_not_require_a_longer_spoken_brief():
    visible = (
        SUMMARY + "\n\nCompare valid peer channels. [related attached manual #68, page 23] [SRC12]"
    )
    result, client, _ = _probe_full_answer_fallback(
        visible=visible, reasoning="The answer compares readings."
    )
    assert result.response.spoken_summary == SUMMARY
    assert result.provenance.spoken_summary_extractive_mode == "complete_opening_brief"
    assert len(client.responses.calls) == 1


def test_selected_t01_opening_cannot_drop_history_or_fitted_wiring_qualifiers(monkeypatch):
    """Replay the actual failure; its old opening is not a complete safe brief."""
    from pathlib import Path

    from ai.core.config import get_settings
    from ai.core.reasoning.schemas import CanonicalTurnResponse
    from ai.core.turn.responses import _plain_spoken_text

    settings = get_settings().model_copy(update={"feature_voice_foreground_session": True})
    monkeypatch.setattr("ai.core.config.get_settings", lambda: settings)
    actual = json.loads(
        (Path(__file__).parent / "fixtures/voice_t01_selected_actual.json").read_text()
    )
    result, client, original = _probe_full_answer_fallback(
        visible=actual["detailed_response"],
        reasoning=actual["reasoning_summary"],
        boundary=actual["safety_boundary"],
    )
    opening = actual["detailed_response"].split("\n\n", 1)[0]
    # The actual opening also lacks the canonical validator's explicit
    # negative-knowledge pattern; retain that refusal rather than weakening it.
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="material uncertainty negation"):
        CanonicalTurnResponse.model_validate_json(
            json.dumps({
                **original.response.model_dump(mode="json"),
                "spoken_summary": opening,
                "speak": True,
            })
        )
    assert result.response.spoken_summary == _plain_spoken_text(actual["detailed_response"])
    assert result.provenance.spoken_summary_extractive_mode == "complete_visible_answer"
    assert result.response.model_dump(exclude={"speak", "spoken_summary"}) == (
        original.response.model_dump(exclude={"speak", "spoken_summary"})
    )
    assert len(client.responses.calls) == 1


def test_opening_without_complete_safety_boundary_keeps_full_answer_fallback():
    from ai.core.turn.responses import _plain_spoken_text

    visible = SUMMARY.removesuffix(BOUNDARY).strip() + "\n\n" + BOUNDARY
    result, client, _ = _probe_full_answer_fallback(
        visible=visible, reasoning="The answer compares readings."
    )
    assert result.response.spoken_summary == _plain_spoken_text(visible)
    assert result.provenance.spoken_summary_extractive_mode == "complete_visible_answer"
    assert len(client.responses.calls) == 1


def test_motor_thermal_voice_order_is_requested_in_the_real_provider_instructions():
    client = _Client([_response(text=_canonical_json())])
    asyncio.run(_adapter(client).reason(envelope=_envelope()))
    instructions = client.responses.calls[0]["instructions"]
    assert (
        "first establish the event timestamp, channel identity and motor running-state validity"
        in instructions
    )
    assert (
        "second compare valid peer temperatures and electrical load at matched time and operating state"
        in instructions
    )
    assert "third confirm the installed cooling arrangement" in instructions
    assert "Keep this order in both the opening brief and numbered explanation" in instructions
    assert "not a performed inspection" in instructions
