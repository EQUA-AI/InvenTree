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
                _citation(access_class="maintenance_authorized"),
            ])
            == []
        )

    def test_document_citation_keeps_revision_and_physical_page(self) -> None:
        entry = build_media_evidence([
            _citation(access_class="attachment_uploaded", source_sha256="a" * 64, page_number=2)
        ])[0]
        assert entry["media_type"] == "document"
        assert entry["source_revision"] == "a" * 64
        assert entry["page_index"] == 1

    def test_document_pages_and_revisions_have_distinct_citations(self) -> None:
        """Repeated chunks collapse, but another physical page or revision survives."""
        entries = build_media_evidence([
            _citation(access_class="attachment_uploaded", source_sha256=revision, page_number=page)
            for revision, page in [("a" * 64, 1), ("a" * 64, 2), ("a" * 64, 1), ("b" * 64, 1)]
        ])
        assert [(entry["source_revision"], entry["page_index"]) for entry in entries] == [
            ("a" * 64, 0),
            ("a" * 64, 1),
            ("b" * 64, 0),
        ]
        assert entries[0]["label"].endswith("document · page 1")
        assert entries[1]["label"].endswith("document · page 2")

    @pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
    def test_nonfinite_timecode_does_not_crash_or_escape_into_json(self, value) -> None:
        """Malformed timing degrades to source-level evidence."""
        entry = build_media_evidence([_citation(timecode_start_s=value, timecode_end_s=value)])[0]
        assert entry["timecode_start_s"] is None
        assert entry["timecode_end_s"] is None
        assert entry["label"].endswith(" · video")
        json.dumps(entry, allow_nan=False)

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


class TestCapturedDocumentLocators:
    """Capture ledger preserves citation revision and original physical page."""

    @staticmethod
    def _document(**overrides):
        """Return one synthetic attachment-document citation with locator metadata."""
        doc = _citation()
        doc.update({
            "access_class": "attachment_uploaded",
            "source_sha256": "a" * 64,
            "page_number": 2,
            "attachment_id": "60",
            "model_type": "assetmachine",
            "model_id": "174",
            "work_order_id": "",
            "media_type": "document",
        })
        doc.update(overrides)
        return doc

    @staticmethod
    def _captured(citations):
        """Record citations through the real capture ledger and return its manuals citations."""
        from ai.core.tools.capture_ledger import ToolCaptureLedger

        ledger = ToolCaptureLedger()
        ledger.record(
            "documents.read:search_attachment_docs",
            {"chunks": [{"citation": c} for c in citations]},
        )
        return ledger.manuals_citations()

    def test_direct_builder_control(self):
        """Direct builder maps sha and 1-based page before any capture."""
        entry = build_media_evidence([self._document()])[0]
        assert entry["source_revision"] == "a" * 64
        assert entry["page_index"] == 1
        assert entry["media_type"] == "document"
        assert entry["model_type"] == "assetmachine"
        assert entry["model_id"] == 174
        assert entry["label"] == "Machine #174 · document · page 2"

    @pytest.mark.parametrize(
        "field,expected", [("source_sha256", "a" * 64), ("page_number", "2")], ids=["sha", "page"]
    )
    def test_capture_fields(self, field, expected):
        """Ledger capture keeps source SHA and the original 1-based page string."""
        captured = self._captured([self._document()])
        assert captured[0].get(field) == expected

    @pytest.mark.parametrize(
        "field,expected",
        [("source_revision", "a" * 64), ("page_index", 1)],
        ids=["revision", "page-index"],
    )
    def test_captured_builder_fields(self, field, expected):
        """Builder receives revision and page index from real captured citations."""
        entry = build_media_evidence(self._captured([self._document()]))[0]
        assert entry[field] == expected

    def test_distinct_pages_and_revisions_survive(self):
        """Distinct page/revision pairs survive capture; repeats still collapse."""
        a, b = "a" * 64, "b" * 64
        docs = [
            self._document(source_sha256=a, page_number=1),
            self._document(source_sha256=a, page_number=2),
            self._document(source_sha256=a, page_number=1),
            self._document(source_sha256=b, page_number=1),
        ]
        built = build_media_evidence(self._captured(docs))
        assert [(e["source_revision"], e["page_index"]) for e in built] == [(a, 0), (a, 1), (b, 0)]

    def test_duplicate_citation_collapses(self):
        """An identical repeated citation still collapses to one evidence entry."""
        built = build_media_evidence(self._captured([self._document(), self._document()]))
        assert len(built) == 1

    def test_distinct_pages_reach_cap(self):
        """Distinct pages reach the media evidence cap in original page order."""
        docs = [self._document(page_number=page) for page in range(1, MAX_MEDIA_EVIDENCE + 4)]
        built = build_media_evidence(self._captured(docs))
        assert len(built) == MAX_MEDIA_EVIDENCE
        assert [e["page_index"] for e in built] == list(range(MAX_MEDIA_EVIDENCE))

    @pytest.mark.parametrize(
        "extra",
        [{}, {"source_sha256": None, "page_number": None}, {"source_sha256": "", "page_number": 0}],
        ids=["missing", "none", "zero"],
    )
    def test_legacy_missing_locator(self, extra):
        """Legacy citations without locator metadata yield no invented revision/page."""
        legacy = self._document()
        legacy.pop("source_sha256", None)
        legacy.pop("page_number", None)
        legacy.update(extra)
        entry = build_media_evidence(self._captured([legacy]))[0]
        assert entry["source_revision"] is None
        assert entry["page_index"] is None

    def test_media_locator_control(self):
        """Media evidence video coordinates survive capture unchanged."""
        raw = [chunk["citation"] for chunk in _media_payload()["chunks"]]
        built = build_media_evidence(self._captured(raw))
        assert built == build_media_evidence(raw)
        entry = next(e for e in built if e.get("segment_index") == 4)
        assert entry["segment_index"] == 4
        assert entry["timecode_start_s"] == pytest.approx(220.0, rel=0, abs=0)
        assert entry["timecode_end_s"] == pytest.approx(280.0, rel=0, abs=0)

    @pytest.mark.parametrize(
        "field,expected",
        [("source_revision", "a" * 64), ("page_index", 1)],
        ids=["revision", "page-index"],
    )
    def test_turn_metadata_fields(self, field, expected):
        """Turn seam terminal metadata carries revision and original page index."""
        _, repository = TestTurnSeam._run_turn(payload={"chunks": [{"citation": self._document()}]})
        entry = repository.terminal_calls[-1]["output_metadata"]["media_evidence"][0]
        assert entry[field] == expected
