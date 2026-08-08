"""S28: the server-observed entity manifest and its turn seam."""

from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

from ai.core.entities import (  # noqa: E402
    MAX_ENTITIES,
    build_entity_manifest,
)


def _root(entity_type="machine", entity_id=44, display_name="Influent Pump Station No. 1"):
    return SimpleNamespace(entity_type=entity_type, entity_id=entity_id, display_name=display_name)


class TestBuildEntityManifest:
    """Server-observed only, mapped, deduplicated, bounded."""

    def test_record_roots_become_chips_with_display_names(self) -> None:
        entities = build_entity_manifest(canonical={}, record_roots=[_root()])
        assert entities == [
            {
                "model": "assetmachine",
                "pk": 44,
                "label": "Influent Pump Station No. 1",
                "source": "record_root",
            }
        ]

    def test_validated_evidence_contributes(self) -> None:
        canonical = {
            "canonical_response": {
                "evidence": [
                    {
                        "source_type": "asset_machine",
                        "source_id": "44",
                        "summary": "Pump context",
                    },
                    {"source_type": "work_order", "source_id": 9, "summary": "WO"},
                ]
            }
        }
        entities = build_entity_manifest(canonical=canonical, record_roots=())
        assert [(e["model"], e["pk"]) for e in entities] == [
            ("assetmachine", 44),
            ("workorder", 9),
        ]

    def test_unmapped_source_types_are_dropped_not_guessed(self) -> None:
        canonical = {
            "canonical_response": {
                "evidence": [
                    {"source_type": "work_order_closeout", "source_id": 5},
                    {"source_type": "machine_signal_state", "source_id": 3},
                ]
            }
        }
        assert build_entity_manifest(canonical=canonical, record_roots=()) == []

    def test_free_text_ids_never_contribute(self) -> None:
        """A model mentioning an id in prose cannot place a chip."""
        canonical = {
            "message": "See machine 99 and work order 123.",
            "canonical_response": {"evidence": []},
        }
        assert build_entity_manifest(canonical=canonical, record_roots=()) == []

    def test_dedupe_and_bound(self) -> None:
        roots = [_root(entity_id=44)] * 3 + [_root(entity_id=index) for index in range(1, 30)]
        observed = {str(root.entity_id) for root in roots}
        entities = build_entity_manifest(canonical={}, record_roots=roots, observed_ids=observed)
        assert len(entities) == MAX_ENTITIES
        assert len({(e["model"], e["pk"]) for e in entities}) == MAX_ENTITIES

    def test_invalid_ids_are_dropped(self) -> None:
        roots = [_root(entity_id="not-a-pk"), _root(entity_id=-4), _root(entity_id=7)]
        entities = build_entity_manifest(canonical={}, record_roots=roots)
        assert [e["pk"] for e in entities] == [7]

    def test_fleet_sized_root_listing_without_observation_yields_no_chips(self) -> None:
        """A text turn's roots are the whole fleet — that is not 'about'."""
        roots = [_root(entity_id=index) for index in range(1, 13)]
        assert build_entity_manifest(canonical={}, record_roots=roots) == []

    def test_observed_ids_filter_roots_to_touched_records(self) -> None:
        """Only machines a tool actually returned become chips."""
        roots = [_root(entity_id=index) for index in range(1, 13)]
        entities = build_entity_manifest(
            canonical={}, record_roots=roots, observed_ids={"7", "TC-INF-PS1-001"}
        )
        assert [e["pk"] for e in entities] == [7]


class TestTurnSeam:
    """The manifest attaches at the terminal seam with a clean event."""

    @staticmethod
    def _run_turn(*, flag=True):
        from ai.core.tests.test_normalized_turn_service import (
            _context,
            _principal,
            _Repository,
            _TestTurnService,
            _Workflow,
        )

        repository = _Repository()
        service = _TestTurnService(
            workflow_factory=lambda: _Workflow(),
            repository_factory=lambda actor, context: repository,  # noqa: ARG005
            diagnostic_context_factory=lambda **kwargs: SimpleNamespace(  # noqa: ARG005
                record_roots=[_root()], capabilities=()
            ),
        )
        settings = SimpleNamespace(
            feature_entity_manifest=flag,
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
                    thread_id="thread_entities",
                    content="Tell me about the pump",
                    modality="text",
                    trusted_context=_context(),
                    modality_metadata={},
                    idempotency_key="entities:one",
                    correlation_id=_context().correlation_id,
                )
            )
        return result, repository

    def test_manifest_persists_and_the_event_is_hygienic(self) -> None:
        """Chips reach output_metadata and replayable events, safely shaped."""
        _, repository = self._run_turn()
        terminal = repository.terminal_calls[-1]
        metadata = terminal["output_metadata"]
        assert metadata["entities"][0]["model"] == "assetmachine"
        assert metadata["entities"][0]["pk"] == 44

        manifest_events = [
            event
            for event in terminal["canonical_result"]["events"]
            if event.get("kind") == "entity_manifest"
        ]
        assert len(manifest_events) == 1
        event = manifest_events[0]
        assert event["type"] == "STATE_DELTA"
        # Stale-client hygiene: these keys render as transcript text.
        assert not ({"content", "delta", "choices", "message"} & set(event))
        serialized = json.dumps(event)
        assert "Influent Pump Station" in serialized

    def test_kill_switch_suppresses_the_manifest(self) -> None:
        """Flag off: no entities key, no manifest event."""
        _, repository = self._run_turn(flag=False)
        terminal = repository.terminal_calls[-1]
        assert "entities" not in terminal["output_metadata"]
        assert not [
            event
            for event in terminal["canonical_result"]["events"]
            if event.get("kind") == "entity_manifest"
        ]
