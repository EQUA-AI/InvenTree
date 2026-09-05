"""M1 PR G (§9.11 / GR-16): the Context used record persists, streams, and projects.

Owner-only rule (ii): memory rows (summary, preferences, facts) reach the
thread owner; a grantee sees the turn and corpus rows only. The record is
ids and counts (<= 2 KB) — never an item's text.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

import pytest  # noqa: E402
from ai.core.app import CONTEXT_USED_OWNER_ONLY_KEYS, _context_used_projection  # noqa: E402
from ai.core.streaming import EventType  # noqa: E402
from ai.core.tests import test_route_facts as rf  # noqa: E402
from django.core.management import call_command  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _database():
    call_command("migrate", verbosity=0, interactive=False)
    yield


def test_owner_sees_memory_rows_and_a_grantee_does_not():
    record = {
        "recent_turns": {"used": 2, "available": 3},
        "summary": {"through_sequence": 4},
        "preferences_used": 0,
        "facts_used": 0,
        "corpora": {"controlled": {"state": "not_consulted", "n": 0}},
        "topology": "not_available",
    }
    owner = _context_used_projection(record, shared=False)
    assert owner == record
    grantee = _context_used_projection(record, shared=True)
    assert set(CONTEXT_USED_OWNER_ONLY_KEYS).isdisjoint(grantee)
    assert grantee["recent_turns"] == {"used": 2, "available": 3}
    assert grantee["corpora"]["controlled"]["state"] == "not_consulted"
    assert _context_used_projection(None, shared=False) is None
    assert _context_used_projection("junk", shared=False) is None


def test_a_real_turn_persists_and_streams_the_record():
    from aichat.models import ChatMessage

    user = rf._user()
    thread_id = f"cu_{uuid.uuid4().hex[:12]}"
    service = rf._service(rf._ScriptedWorkflow(["First reply.", "Second reply."]))
    captured: list = []

    class _Emitter:
        async def subscribe(self, capture):
            return lambda: None

        async def emit(self, event):
            captured.append(event)

    async def run():
        await rf._turn(service, user, thread_id, "What is on file for inverter A?", "cu:1")
        await rf._turn(service, user, thread_id, "And the superseded revision?", "cu:2")

    asyncio.run(run())
    assistant = list(
        ChatMessage.objects.filter(thread_id=thread_id, role="assistant").order_by("sequence")
    )
    assert len(assistant) == 2
    for message in assistant:
        record = message.metadata["context_used"]
        assert set(record) >= {"recent_turns", "summary", "corpora", "topology", "retrieval_plan"}
        assert record["topology"] == "not_available"
        assert record["summary"] == "none"  # the island runs no compaction
        assert all(entry["state"] == "not_consulted" for entry in record["corpora"].values())
        assert len(json.dumps(record)) <= 2048
        assert "inverter" not in json.dumps(record).lower()  # ids and counts only
    # The second turn saw the first exchange as recent turns.
    assert assistant[1].metadata["context_used"]["recent_turns"]["used"] == 2
    assert assistant[0].metadata["context_used"]["recent_turns"]["used"] == 0


def test_the_state_delta_carries_the_same_record(monkeypatch):
    """The live wire and the persisted record are one object shape."""
    from ai.core.turn import finalize

    emitted: list = []

    class _Emitter:
        async def emit(self, event):
            emitted.append(event)

    class _Bundle:
        def context_used(self, snapshot):
            return {
                "recent_turns": {"used": 1, "available": 1},
                "summary": "none",
                "snapshot": snapshot is not None,
            }

    run = type("Run", (), {})()
    run.context_bundle = _Bundle()
    run.retrieval_snapshot = {"envelopes": []}
    run.emitter = _Emitter()
    run.thread = type("T", (), {"pk": "t1"})()
    run.turn = type("U", (), {"pk": "u1"})()
    canonical = asyncio.run(finalize._attach_context_used(run, {}))
    assert canonical["context_used"]["snapshot"] is True
    assert len(emitted) == 1
    event = emitted[0]
    assert event.event_type == EventType.STATE_DELTA
    assert event.data["kind"] == "context_used"
    assert event.data["recent_turns"] == {"used": 1, "available": 1}
    assert event.run_id == "contextUsed:u1"
    # No bundle -> no record, no event, no failure.
    bare = type("Run", (), {"context_bundle": None})()
    assert asyncio.run(finalize._attach_context_used(bare, {"x": 1})) == {"x": 1}

    # A turn that used nothing persists the record but stays silent on the
    # live stream (the AG-UI order golden keeps RUN_FINISHED last).
    class _EmptyBundle:
        def context_used(self, snapshot):
            return {
                "recent_turns": {"used": 0, "available": 0},
                "summary": "none",
                "corpora": {"controlled": {"state": "not_consulted", "n": 0}},
                "truncation": {},
            }

    quiet = type("Run", (), {})()
    quiet.context_bundle = _EmptyBundle()
    quiet.retrieval_snapshot = None
    quiet.emitter = _Emitter()
    quiet.thread = run.thread
    quiet.turn = run.turn
    before = len(emitted)
    canonical = asyncio.run(finalize._attach_context_used(quiet, {}))
    assert canonical["context_used"]["recent_turns"] == {"used": 0, "available": 0}
    assert len(emitted) == before
