"""R4: the server-authored media-evidence manifest and its turn seam."""

from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django
import pytest

django.setup()

from ai.core.media_evidence import (  # noqa: E402
    MAX_MEDIA_EVIDENCE,
    build_media_evidence,
)

_FILENAME = "hx200-seal-video.mp4"


def _citation(**overrides):
    """One ledger-shaped citation (all values strings, like the capture)."""
    base = {
        "document": "seal-video-hx200",
        "document_id": "",
        "revision": "",
        "section_path": "",
        "chunk_id": "att-9-abc123def456-s0",
        "asset_id": "SER-PS1-001",
        "access_class": "evidence_recording",
        "source_file_name": _FILENAME,
        "media_type": "video_segment",
        "work_order_id": "104",
        "timecode_start_s": "0.0",
        "timecode_end_s": "60.0",
        "attachment_id": "9",
        "model_type": "workorder",
        "model_id": "104",
        "segment_index": "0",
    }
    base.update(overrides)
    return base


class TestBuildMediaEvidence:
    """Evidence-only, deduplicated, bounded, labelled from server data."""

    def test_dedupe_on_attachment_and_segment_preserves_ledger_order(self) -> None:
        entries = build_media_evidence([
            _citation(attachment_id="9", segment_index="0"),
            _citation(attachment_id="9", segment_index="0"),
            _citation(attachment_id="12", segment_index="1"),
            _citation(attachment_id="9", segment_index="1"),
        ])
        assert [(e["attachment_id"], e["segment_index"]) for e in entries] == [
            (9, 0),
            (12, 1),
            (9, 1),
        ]

    def test_capped_at_max_media_evidence(self) -> None:
        entries = build_media_evidence([
            _citation(attachment_id=str(index)) for index in range(1, MAX_MEDIA_EVIDENCE + 4)
        ])
        assert len(entries) == MAX_MEDIA_EVIDENCE
        assert [e["attachment_id"] for e in entries] == list(range(1, MAX_MEDIA_EVIDENCE + 1))

    def test_video_label_is_wo_anchor_plus_mm_ss(self) -> None:
        entries = build_media_evidence([
            _citation(timecode_start_s="272.0", timecode_end_s="332.0", segment_index="4")
        ])
        assert entries[0]["label"] == "WO #104 · 04:32"

    def test_video_label_at_zero_seconds_is_00_00_not_photo(self) -> None:
        """0.0 is a real first-segment timecode, never the missing default."""
        entries = build_media_evidence([_citation(timecode_start_s="0.0")])
        assert entries[0]["label"] == "WO #104 · 00:00"

    def test_numeric_zero_timecode_is_preserved(self) -> None:
        """Typed callers retain 0.0 just like the stringifying ledger."""
        entries = build_media_evidence([_citation(timecode_start_s=0.0, timecode_end_s=60.0)])
        assert entries[0]["timecode_start_s"] == pytest.approx(0.0)
        assert entries[0]["label"] == "WO #104 · 00:00"

    def test_image_label_says_photo(self) -> None:
        entries = build_media_evidence([
            _citation(
                media_type="image",
                timecode_start_s="",
                timecode_end_s="",
                segment_index="",
                source_file_name="nameplate-hx200.png",
            )
        ])
        assert entries[0]["label"] == "WO #104 · photo"

    def test_machine_owned_evidence_anchors_on_the_machine(self) -> None:
        entries = build_media_evidence([
            _citation(
                work_order_id="",
                model_type="assetmachine",
                model_id="12",
                timecode_start_s="55.0",
            )
        ])
        assert entries[0]["label"].startswith("Machine #12 · ")

    def test_no_wo_and_no_machine_falls_back_to_evidence_anchor(self) -> None:
        entries = build_media_evidence([_citation(work_order_id="", model_type="", model_id="")])
        assert entries[0]["label"].startswith("Evidence #9")

    def test_non_evidence_access_classes_are_dropped(self) -> None:
        assert (
            build_media_evidence([
                _citation(access_class=""),
                _citation(access_class="attachment_uploaded"),
            ])
            == []
        )

    def test_unusable_attachment_ids_are_dropped(self) -> None:
        assert (
            build_media_evidence([
                _citation(attachment_id=""),
                _citation(attachment_id="0"),
                _citation(attachment_id="-3"),
                _citation(attachment_id="garbage"),
            ])
            == []
        )

    def test_empty_segment_index_coerces_to_zero(self) -> None:
        """Image rows carry '' — they occupy (attachment, 0) for dedupe."""
        entries = build_media_evidence([
            _citation(media_type="image", segment_index=""),
            _citation(media_type="image", segment_index=""),
        ])
        assert [e["segment_index"] for e in entries] == [0]

    def test_labels_never_leak_the_source_file_name(self) -> None:
        """The captured filename is attacker-authored text; ids only."""
        entries = build_media_evidence([
            _citation(),
            _citation(attachment_id="12", media_type="image", segment_index=""),
            _citation(attachment_id="13", work_order_id="", model_type="", model_id=""),
        ])
        assert entries
        for entry in entries:
            assert _FILENAME not in entry["label"]
            assert "hx200-seal-video" not in entry["label"]

    def test_timecodes_coerce_to_float_or_none(self) -> None:
        entries = build_media_evidence([
            _citation(timecode_start_s="55.0", timecode_end_s=""),
        ])
        assert entries[0]["timecode_start_s"] == pytest.approx(55.0)
        assert entries[0]["timecode_end_s"] is None

    def test_entry_shape_carries_typed_coordinates(self) -> None:
        entry = build_media_evidence([_citation()])[0]
        assert entry["attachment_id"] == 9
        assert entry["model_type"] == "workorder"
        assert entry["model_id"] == 104
        assert entry["work_order_id"] == 104
        assert entry["media_type"] == "video_segment"
        assert entry["segment_index"] == 0
        assert entry["timecode_start_s"] == pytest.approx(0.0)
        assert entry["timecode_end_s"] == pytest.approx(60.0)
        assert "source_file_name" not in entry


def _media_payload():
    """A search_evidence_media tool result as the capability layer emits it."""
    return {
        "chunks": [
            {
                "excerpt": "Operator seats the seal at the 04:32 mark.",
                "score": 2.9,
                "citation": {
                    "document": "seal-video-hx200",
                    "source_file_name": _FILENAME,
                    "chunk_id": "att-9-abc123def456-s4",
                    "access_class": "evidence_recording",
                    "media_type": "video_segment",
                    "work_order_id": 104,
                    "model_type": "workorder",
                    "model_id": 104,
                    "attachment_id": 9,
                    "segment_index": 4,
                    "timecode_start_s": 220.0,
                    "timecode_end_s": 280.0,
                    "asset_id": "SER-PS1-001",
                    "excerpt_hash": "abc",
                },
            }
        ],
        "total": 1,
        "machine_filter": "HX-200",
    }


class TestTurnSeam:
    """The manifest attaches at the terminal seam with a clean event."""

    @staticmethod
    def _run_turn(*, flag=True, payload=None):
        from ai.core.tests.test_normalized_turn_service import (
            _context,
            _principal,
            _Repository,
            _TestTurnService,
            _Workflow,
        )
        from ai.core.tools.capture_ledger import record_tool_result

        class _EvidenceWorkflow(_Workflow):
            """Record a media tool result into the turn's bound ledger."""

            async def run_stream(self, **kwargs):
                if payload is not None:
                    record_tool_result("evidence.read:search_evidence_media", payload)
                async for chunk in super().run_stream(**kwargs):
                    yield chunk

        repository = _Repository()
        service = _TestTurnService(
            workflow_factory=lambda: _EvidenceWorkflow(),
            repository_factory=lambda actor, context: repository,  # noqa: ARG005
            diagnostic_context_factory=lambda **kwargs: SimpleNamespace(  # noqa: ARG005
                record_roots=[], capabilities=()
            ),
        )
        settings = SimpleNamespace(
            feature_media_evidence=flag,
            feature_entity_manifest=False,
            manual_grounding_mode="off",
            chat_history_messages=0,
            chat_history_max_message_chars=0,
            chat_history_max_total_chars=0,
            feature_turn_usage_persistence=False,
        )
        with patch("ai.core.config.get_settings", return_value=settings):
            result = asyncio.run(
                service.process(
                    actor=_principal(),
                    thread_id="thread_media_evidence",
                    content="What did the repair video show?",
                    modality="text",
                    trusted_context=_context(),
                    modality_metadata={},
                    idempotency_key="media-evidence:one",
                    correlation_id=_context().correlation_id,
                )
            )
        return result, repository

    @staticmethod
    def _media_events(terminal):
        return [
            event
            for event in terminal["canonical_result"]["events"]
            if event.get("kind") == "media_evidence"
        ]

    def test_populated_ledger_persists_and_the_event_is_hygienic(self) -> None:
        """Chips reach output_metadata and replayable events, safely shaped."""
        _, repository = self._run_turn(payload=_media_payload())
        terminal = repository.terminal_calls[-1]
        entry = terminal["output_metadata"]["media_evidence"][0]
        assert entry["attachment_id"] == 9
        assert entry["segment_index"] == 4
        assert entry["work_order_id"] == 104
        assert entry["timecode_start_s"] == pytest.approx(220.0)
        assert entry["timecode_end_s"] == pytest.approx(280.0)
        assert entry["label"] == "WO #104 · 03:40"

        events = self._media_events(terminal)
        assert len(events) == 1
        event = events[0]
        assert event["type"] == "STATE_DELTA"
        # Stale-client hygiene: these keys render as transcript text.
        assert not ({"content", "delta", "choices", "message"} & set(event))
        serialized = json.dumps(event)
        assert '"attachment_id": 9' in serialized
        # The uploader-chosen filename never reaches the wire.
        assert "hx200-seal-video" not in serialized

    def test_empty_ledger_attaches_nothing(self) -> None:
        """No evidence tool ran: no key, no event."""
        _, repository = self._run_turn(payload=None)
        terminal = repository.terminal_calls[-1]
        assert "media_evidence" not in terminal["output_metadata"]
        assert self._media_events(terminal) == []

    def test_kill_switch_suppresses_the_manifest(self) -> None:
        """Flag off: no media_evidence key even with a populated ledger."""
        _, repository = self._run_turn(flag=False, payload=_media_payload())
        terminal = repository.terminal_calls[-1]
        assert "media_evidence" not in terminal["output_metadata"]
        assert self._media_events(terminal) == []
