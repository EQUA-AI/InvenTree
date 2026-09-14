"""V3 numeric, privacy, clock and replay contracts (no provider calls)."""

import json
import logging
from unittest.mock import MagicMock, patch

import pytest
from ai.core.tracing import decision_log_ids, span_attrs, turn_span
from ai.core.turn.request import turn_request_fingerprint
from ai.core.voice.timing import VoiceClientTiming, VoiceTimingReport, measurement, stage
from pydantic import ValidationError


@pytest.mark.parametrize(
    "value", [True, False, "1", -1, 300001, float("nan"), float("inf"), {}, []]
)
def test_interval_rejects_unusable_values(value):
    with pytest.raises(ValidationError):
        VoiceClientTiming(speech_to_final_ms=value)


def test_optional_numeric_metadata_and_extra_key_rejection():
    assert VoiceClientTiming().model_dump(exclude_none=True) == {}
    assert VoiceClientTiming(speech_to_final_ms=1.25).speech_to_final_ms == pytest.approx(1.25)
    for key in (
        "transcript",
        "spoken_summary",
        "preview",
        "consent",
        "recipient",
        "supplier",
        "audio",
    ):
        with pytest.raises(ValidationError):
            VoiceClientTiming(**{key: "PRIVATE_SENTINEL"})


def test_timing_never_changes_business_fingerprint():
    values = {"content": "test", "modality": "voice", "trusted_context": {}}
    before = {"language": "en-US", "voice_live_item_id": "item", "transcription_confidence": 0.9}
    assert turn_request_fingerprint(**values, modality_metadata=before) == turn_request_fingerprint(
        **values, modality_metadata={**before, "voice_timing": {"final_to_submit_ms": 20}}
    )


def test_new_attributes_are_numeric_or_opaque_never_content():
    secret = "PRIVATE_SENTINEL"
    assert span_attrs(
        ms_tool=2.5,
        ms_route=True,
        ms_result=float("nan"),
        decision_id=secret,
        operation_id=Exception(secret),
        utterance_id=secret,
        decision_state=secret,
        transcript=secret,
        spoken_summary=secret,
    ) == {"aimms.ms_tool": 2.5}


def test_rendered_decision_log_fields_do_not_export_projection_content(caplog):
    secret = "PRIVATE_TRANSCRIPT_SUMMARY_PREVIEW_CONSENT_RECIPIENT_SUPPLIER"
    logger = logging.getLogger("ai.core.turn_service")
    with caplog.at_level(logging.INFO, logger="ai.core.turn_service"):
        logger.info(
            "ai.turn decision=%s operation=%s",
            *decision_log_ids({
                "decision_id": secret,
                "operation_id": RuntimeError(secret),
                "spoken_summary": secret,
                "preview": secret,
                "consent": secret,
                "recipient": secret,
                "supplier": secret,
            }),
        )
        logger.info("ai.turn decision=%s operation=%s", *decision_log_ids(RuntimeError(secret)))
    assert secret not in caplog.text
    assert "ai.turn decision=- operation=-" in caplog.text


def test_runtime_export_never_receives_business_exception_or_content(caplog):
    tracer = MagicMock()
    secret = "PRIVATE_TRANSCRIPT_SUMMARY_PREVIEW_CONSENT_RECIPIENT_SUPPLIER"
    error = RuntimeError(secret)
    with patch("ai.core.tracing._tracer", return_value=tracer):  # noqa: SIM117 - exporter spans only the body
        with pytest.raises(RuntimeError) as caught:
            with turn_span(
                "aimms.voice.turn",
                transcript=secret,
                spoken_summary=secret,
                preview=secret,
                consent=secret,
                recipient=secret,
                supplier=secret,
                outcome_code=error,
            ):
                raise error
    assert caught.value is error
    call = tracer.start_as_current_span.call_args
    assert call.kwargs["record_exception"] is False
    assert call.kwargs["set_status_on_exception"] is False
    tracer.start_as_current_span.return_value.__exit__.assert_called_once_with(None, None, None)
    assert secret not in str(tracer.mock_calls) + caplog.text


def test_server_clock_stages_and_root_are_numeric():
    tracer = MagicMock()
    with (
        patch("ai.core.tracing._tracer", return_value=tracer),
        patch("ai.core.voice.timing.time.perf_counter", side_effect=[10, 10.01, 10.03, 10.04]),
        measurement(),
        stage("route"),
    ):
        pass
    names = [call.args[0] for call in tracer.start_as_current_span.call_args_list]
    assert names == ["aimms.voice.turn", "aimms.voice.route"]
    attrs = tracer.start_as_current_span.return_value.__enter__.return_value.set_attribute.call_args_list
    assert any(
        call.args[0] == "aimms.ms_route" and call.args[1] == pytest.approx(20) for call in attrs
    )
    assert any(
        call.args[0] == "aimms.ms_result" and call.args[1] == pytest.approx(40) for call in attrs
    )


def test_exporter_failure_does_not_fail_body():
    with patch("ai.core.tracing._tracer", side_effect=RuntimeError("PRIVATE_SENTINEL")):  # noqa: SIM117
        with measurement(), stage("tool"):
            result = "business completed"
    assert result == "business completed"


def test_multiple_tool_durations_accumulate_without_cross_clock_arithmetic():
    tracer = MagicMock()
    with (
        patch("ai.core.tracing._tracer", return_value=tracer),
        patch(
            "ai.core.voice.timing.time.perf_counter",
            side_effect=[10, 10, 10.01, 10.02, 10.04, 10.05],
        ),
        measurement(),
    ):
        with stage("tool"):
            pass
        with stage("tool"):
            pass
    attrs = tracer.start_as_current_span.return_value.__enter__.return_value.set_attribute.call_args_list
    assert any(
        call.args[0] == "aimms.ms_tool" and call.args[1] == pytest.approx(30) for call in attrs
    )


def test_report_contract_is_fixed_size_and_content_free():
    report = {
        "epoch": "a" * 32,
        "utterance_id": "11111111-1111-1111-1111-111111111111",
        "spoken_hash": "b" * 64,
        "provenance": "rtp_energy_proxy",
        "timing": {},
    }
    assert len(VoiceTimingReport(**report).model_dump_json()) < 1000
    with pytest.raises(ValidationError):
        VoiceTimingReport(**report, transcript="PRIVATE_SENTINEL")
    with pytest.raises(ValidationError):
        VoiceTimingReport(**report, first_playback_epoch_ms=True)
    assert "PRIVATE_SENTINEL" not in json.dumps(VoiceTimingReport(**report).model_dump())
