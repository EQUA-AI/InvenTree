"""M1 exit-gate file: the ContextAssembler seam (plan §9.2-9.6, GR-31/34/35).

Grows with every Part C PR. PR B pins: replay parity with the pre-builder
``_conversation_history`` rendering (compaction on/off, budgets), bundle
hash identity across accessors, the one-statement recall (== 1 query,
<= 3 always), the import boundary, the RecallFilter table covering every
TaskIntent, the estimator fallback, and the degrade paths.
"""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import uuid
from types import SimpleNamespace

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from ai.core.config import Settings
from ai.core.memory import context_assembler, recall_filter, token_estimator, vocabulary
from ai.core.memory.context_assembler import (
    REPLAY_CARRIER,
    SUMMARY_NOTE_LABEL,
    ContextAssembler,
    RecallWindow,
    RoutingFields,
)
from ai.core.turn.history import _budgeted_history
from ai.core.turn_service import NormalizedTurnService
from django.core.management import call_command
from django.test.utils import CaptureQueriesContext

MEMORY_DIR = pathlib.Path(context_assembler.__file__).resolve().parent


@pytest.fixture(scope="module", autouse=True)
def _database():
    call_command("migrate", verbosity=0, interactive=False)
    yield


# --------------------------------------------------------------------------- #
# The pre-builder rendering, kept here as the parity oracle                   #
# --------------------------------------------------------------------------- #
def _legacy_history(repository, thread_id, settings) -> list[dict[str, str]]:
    """Verbatim logic of NormalizedTurnService._conversation_history before PR B."""
    limit = int(settings.chat_history_messages)
    if limit <= 0:
        return []
    recent = repository.recent_messages(thread_id, limit, exclude_latest=1)
    summary_note = None
    if getattr(settings, "feature_thread_compaction", False):
        thread = repository.get(thread_id)
        watermark = int(getattr(thread, "summary_through_sequence", 0) or 0)
        summary = str(getattr(thread, "summary", "") or "")
        if watermark and summary.strip():
            from ai.core.tools.diagnostics import fence_untrusted_content

            summary_note = {
                "role": "user",
                # PR D: the body rides inside the marker fence, label outside.
                "content": SUMMARY_NOTE_LABEL + "\n" + fence_untrusted_content(summary.strip()),
            }
            recent = [m for m in recent if getattr(m, "sequence", 0) > watermark]
    history = [
        {"role": str(m.role), "content": str(m.content)} for m in recent if str(m.content).strip()
    ]
    budgeted = _budgeted_history(
        history,
        max_message_chars=int(settings.chat_history_max_message_chars),
        max_total_chars=int(settings.chat_history_max_total_chars),
        reserved_chars=len(summary_note["content"]) if summary_note else 0,
    )
    if summary_note is not None:
        budgeted = [summary_note, *budgeted]
    return budgeted


class _TestTurnService(NormalizedTurnService):
    @staticmethod
    async def _call_sync(function, *args, **kwargs):
        return function(*args, **kwargs)


class _Repository:
    """Fake repository: 20 sequenced messages, a compacted thread row."""

    def __init__(self, *, watermark: int, summary: str, count: int = 20):
        self._thread = SimpleNamespace(
            pk="thread_c", summary=summary, summary_through_sequence=watermark
        )
        self._messages = [
            SimpleNamespace(
                role="user" if i % 2 else "assistant",
                content=f"message {i} " + ("x" * (i * 7)),
                sequence=i,
            )
            for i in range(1, count + 1)
        ]

    def get(self, thread_id):
        return self._thread

    def recent_messages(self, thread_id, limit, exclude_latest=0):
        rows = self._messages[: len(self._messages) - exclude_latest]
        return rows[-limit:]


def _settings(**overrides) -> Settings:
    base = {
        "CHAT_HISTORY_MESSAGES": 12,
        "CHAT_HISTORY_MAX_MESSAGE_CHARS": 60,
        "CHAT_HISTORY_MAX_TOTAL_CHARS": 400,
        "FEATURE_THREAD_COMPACTION": True,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


SUMMARY = 'Pump 3 diagnosis\n{"label": "Pump 3 diagnosis", "machine_facts": ["seal worn"]}'


async def _build(repository, settings, **kwargs):
    return await ContextAssembler().build(
        repository=repository,
        thread_id="thread_c",
        turn_id="turn_1",
        settings=settings,
        call_sync=_TestTurnService._call_sync,
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# Replay parity                                                                #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"FEATURE_THREAD_COMPACTION": False},
        {"CHAT_HISTORY_MAX_TOTAL_CHARS": 100000, "CHAT_HISTORY_MAX_MESSAGE_CHARS": 4000},
        {"CHAT_HISTORY_MESSAGES": 3},
        {"CHAT_HISTORY_MESSAGES": 0},
    ],
    ids=["compaction_on", "compaction_off", "no_budget_pressure", "tiny_window", "disabled"],
)
def test_replay_dict_matches_the_pre_builder_rendering(overrides):
    settings = _settings(**overrides)
    for watermark, summary in ((12, SUMMARY), (0, ""), (19, SUMMARY)):
        repository = _Repository(watermark=watermark, summary=summary)
        bundle = asyncio.run(_build(repository, settings))
        assert bundle.replay_dict() == _legacy_history(repository, "thread_c", settings)


def test_the_compat_wrapper_and_the_run_path_render_the_same_dict(monkeypatch):
    monkeypatch.setattr("ai.core.config.get_settings", _settings)
    service = _TestTurnService(workflow_factory=lambda: None)
    repository = _Repository(watermark=12, summary=SUMMARY)
    run = SimpleNamespace(
        context_bundle=None,
        repository=repository,
        thread=SimpleNamespace(pk="thread_c"),
        turn=SimpleNamespace(pk="turn_9"),
        task_intent=None,
        actor=SimpleNamespace(is_staff=False, is_superuser=False),
        question_resolution=None,
        modality="text",
        trusted_context=SimpleNamespace(locale="en"),
        server_pinned_workflow=None,
    )
    via_wrapper = asyncio.run(service._conversation_history(repository, "thread_c"))
    bundle = asyncio.run(service.build_context_bundle(run))
    assert bundle.replay_dict() == via_wrapper
    assert run.context_bundle is bundle
    # Memoized: a second call within the turn returns the same object.
    assert asyncio.run(service.build_context_bundle(run)) is bundle
    assert bundle.replay_dict()[0]["content"].startswith(SUMMARY_NOTE_LABEL)


def test_bundle_hash_is_identical_across_accessors_and_stable():
    settings = _settings()
    repository = _Repository(watermark=12, summary=SUMMARY)
    first = asyncio.run(_build(repository, settings))
    second = asyncio.run(_build(repository, settings))
    assert first.hash == second.hash
    assert len(first.hash) == 64
    # A different watermark or transcript changes the hash.
    other = asyncio.run(_build(_Repository(watermark=14, summary=SUMMARY), settings))
    assert other.hash != first.hash
    # Timing never enters the hash.
    assert first.wall_ms >= 0


def test_replay_carrier_is_the_dict_in_m1():
    assert REPLAY_CARRIER == "dict"


# --------------------------------------------------------------------------- #
# Sections, items and labels                                                   #
# --------------------------------------------------------------------------- #
def test_every_slot_is_declared_with_a_reason():
    bundle = asyncio.run(_build(_Repository(watermark=0, summary=""), _settings()))
    assert set(bundle.sections) == {slot.value for slot in vocabulary.Slot}
    assert bundle.section("thread_summary").reason == "no_watermark_yet"
    assert bundle.section("user_preferences").reason == "no_preference_store"
    assert bundle.section("topology_context").reason == "graph_not_yet_available"
    assert bundle.section("recent_turns").reason == "populated"
    off = asyncio.run(
        _build(
            _Repository(watermark=12, summary=SUMMARY), _settings(FEATURE_THREAD_COMPACTION=False)
        )
    )
    assert off.section("thread_summary").reason == "compaction_off"
    assert off.summary_item is None


def test_items_carry_provenance_and_trust_labels():
    bundle = asyncio.run(_build(_Repository(watermark=12, summary=SUMMARY), _settings()))
    summary = bundle.summary_item
    assert summary is not None
    assert summary.item_id == "summary:12"
    assert summary.content_trust == "untrusted_fenced"
    assert summary.text.startswith(SUMMARY_NOTE_LABEL + "\n[UNTRUSTED-CONTENT-BEGIN]")
    assert summary.text.count("[UNTRUSTED-CONTENT-BEGIN]") == 1
    # No emitted item may carry the PR B interim label any more.
    for section in bundle.sections.values():
        for item in section.items:
            assert item.content_trust != "untrusted_unfenced"
    assert summary.verification_class == "compacted_summary"
    assert summary.source_pointer == "thread:thread_c#summary@12"
    assert summary.role == "user"
    for item in bundle.recent_turns:
        assert item.content_trust == "transcript"
        assert item.item_id == f"msg:{item.sequence}"
        assert item.sequence > 12
        assert item.tokens > 0 and item.chars > 0
        assert len(item.content_hash) == 64
    # Budget truncation is visible as a count, the only cause of absence.
    assert bundle.section("recent_turns").dropped >= 1
    assert bundle.section("recent_turns").available == 12


def test_context_used_is_bounded_ids_and_counts():
    import json

    bundle = asyncio.run(_build(_Repository(watermark=12, summary=SUMMARY), _settings()))
    record = bundle.context_used({"envelopes": [{"tool": "x"}]})
    assert record["summary"] == {"through_sequence": 12}
    assert record["recent_turns"]["available"] == 12
    assert record["corpora"]["controlled"] == {"state": "not_consulted", "n": 0}
    assert record["retrieval_envelopes"] == 1
    assert len(json.dumps(record)) <= 2048
    assert "seal worn" not in json.dumps(record)


def test_thread_summary_text_is_fenced_in_both_shapes():
    fenced = asyncio.run(_build(_Repository(watermark=12, summary=SUMMARY), _settings()))
    text = fenced.thread_summary_text()
    assert text.startswith("[UNTRUSTED-CONTENT-BEGIN]") and text.endswith("[UNTRUSTED-CONTENT-END]")
    assert SUMMARY_NOTE_LABEL not in text
    digest = asyncio.run(
        _build(_Repository(watermark=0, summary=""), _settings())
    ).thread_summary_text()
    assert digest.startswith("[UNTRUSTED-CONTENT-BEGIN]")
    assert len(digest) <= 600 + len("[UNTRUSTED-CONTENT-BEGIN]\n\n[UNTRUSTED-CONTENT-END]")


def test_routing_fields_render_without_history_text():
    bundle = asyncio.run(
        _build(
            _Repository(watermark=12, summary=SUMMARY),
            _settings(),
            task_intent="record_retrieval",
            routing_fields=RoutingFields(
                modality="text", task_intent="record_retrieval", client_codes=("internal",)
            ),
        )
    )
    rendered = bundle.render_routing_fields()
    assert rendered.splitlines()[0] == "modality=text"
    assert "task_intent=record_retrieval" in rendered
    assert "client_codes=internal" in rendered
    assert "message" not in rendered and "seal worn" not in rendered
    assert "Pump 3 diagnosis" in bundle.thread_summary_text()
    # Without a compacted summary the classifier gets a bounded, fenced digest.
    plain = asyncio.run(_build(_Repository(watermark=0, summary=""), _settings()))
    assert 0 < len(plain.thread_summary_text()) <= 600 + 60


# --------------------------------------------------------------------------- #
# Budget (GR-31): one statement on the real repository                        #
# --------------------------------------------------------------------------- #
def _seed_thread(*, messages: int, summary: str = "", watermark: int = 0):
    from aichat.models import ChatThread, MessageRole
    from aichat.services.threads import ThreadRepository
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user(username=f"mem-{uuid.uuid4().hex[:8]}")
    repository = ThreadRepository(actor=user.pk, scope_key="site:pilot")
    thread_id = f"mem_{uuid.uuid4().hex[:12]}"
    repository.get_or_create(thread_id)
    for index in range(messages):
        repository.append(
            thread_id,
            role=MessageRole.USER if index % 2 == 0 else MessageRole.ASSISTANT,
            content=f"row {index + 1}",
        )
    if summary:
        ChatThread.objects.filter(pk=thread_id).update(
            summary=summary, summary_through_sequence=watermark
        )
    return repository, thread_id


def test_recall_window_is_exactly_one_query_and_carries_the_summary():
    from django.db import connection

    repository, thread_id = _seed_thread(messages=8, summary=SUMMARY, watermark=3)
    with CaptureQueriesContext(connection) as captured:
        window = repository.recall_window(thread_id, limit=12, exclude_latest=1)
    assert len(captured) == 1
    assert isinstance(window, RecallWindow)
    assert window.summary == SUMMARY and window.watermark == 3
    assert [row.sequence for row in window.rows] == [1, 2, 3, 4, 5, 6, 7]  # newest excluded
    assert window.rows[0].content == "row 1"
    assert window.next_sequence == 9


def test_builder_round_trips_never_exceed_three_and_are_one_on_the_real_repository():
    """The builder's only DB work is the recall statement; assembly is pure."""
    from django.db import connection

    repository, thread_id = _seed_thread(messages=6, summary=SUMMARY, watermark=2)
    assembler = ContextAssembler()
    with CaptureQueriesContext(connection) as captured:
        window = ContextAssembler.recall(repository, thread_id, limit=12, compaction=True)
        bundle = assembler.assemble(
            window,
            thread_id=thread_id,
            turn_id="t",
            compaction=True,
            max_message_chars=4000,
            max_total_chars=100000,
            task_intent=None,
            routing_fields=RoutingFields(),
            db_round_trips=window.db_round_trips,
        )
    assert len(captured) == 1 <= 3
    assert bundle.db_round_trips == 1
    assert bundle.summary_item is not None
    assert [item.sequence for item in bundle.recent_turns] == [3, 4, 5]
    assert bundle.replay_dict()[1:] == [
        {"role": "user", "content": "row 3"},
        {"role": "assistant", "content": "row 4"},
        {"role": "user", "content": "row 5"},
    ]


def test_build_runs_the_recall_off_the_event_loop_on_the_real_repository():
    """The production hop (sync_to_async) never trips SynchronousOnlyOperation."""
    repository, thread_id = _seed_thread(messages=4, summary=SUMMARY, watermark=1)
    settings = _settings(CHAT_HISTORY_MAX_TOTAL_CHARS=100000, CHAT_HISTORY_MAX_MESSAGE_CHARS=4000)
    bundle = asyncio.run(
        ContextAssembler().build(
            repository=repository,
            thread_id=thread_id,
            turn_id="t",
            settings=settings,
            call_sync=NormalizedTurnService._call_sync,
        )
    )
    assert bundle.degrade_reason == "none"
    assert bundle.db_round_trips == 1
    assert [item.sequence for item in bundle.recent_turns] == [2, 3]
    assert bundle.summary_item is not None


def test_recall_window_respects_the_boundary():
    from aichat.services.threads import ThreadRepository

    repository, thread_id = _seed_thread(messages=4)
    stranger = ThreadRepository(actor=repository.actor_id + 1000, scope_key="site:pilot")
    window = stranger.recall_window(thread_id, limit=12)
    assert window.rows == () and window.summary == ""


def test_the_builder_module_never_writes():
    source = (MEMORY_DIR / "context_assembler.py").read_text(encoding="utf-8")
    for needle in (".save(", ".create(", "bulk_", ".update(", ".delete("):
        assert needle not in source, needle


# --------------------------------------------------------------------------- #
# Degrade paths                                                                #
# --------------------------------------------------------------------------- #
def test_recall_error_degrades_to_no_history_with_a_reason():
    class _Broken:
        def recent_messages(self, *a, **k):
            raise RuntimeError("db down")

    bundle = asyncio.run(_build(_Broken(), _settings()))
    assert bundle.degrade_reason == "recall_error"
    assert bundle.replay_dict() == []
    assert bundle.section("recent_turns").reason == "recall_error"
    assert bundle.section("thread_summary").reason == "recall_error"


def test_recall_timeout_degrades_and_the_turn_proceeds():
    async def slow_call_sync(function, *args, **kwargs):
        await asyncio.sleep(0.2)
        return function(*args, **kwargs)

    bundle = asyncio.run(
        ContextAssembler().build(
            repository=_Repository(watermark=0, summary=""),
            thread_id="thread_c",
            turn_id="t",
            settings=_settings(),
            call_sync=slow_call_sync,
            timeout_s=0.01,
        )
    )
    assert bundle.degrade_reason == "budget_timeout"
    assert bundle.replay_dict() == []
    assert bundle.wall_ms >= 0


def test_zero_history_limit_is_a_declared_reason_not_an_error():
    bundle = asyncio.run(
        _build(_Repository(watermark=0, summary=""), _settings(CHAT_HISTORY_MESSAGES=0))
    )
    assert bundle.degrade_reason == "no_history_limit"
    assert bundle.section("recent_turns").reason == "no_history_limit"


def test_summary_read_failure_stays_plain_history_on_the_legacy_hop():
    """S38 semantics survive: a failed summary read replays the plain transcript."""
    repository = _Repository(watermark=12, summary=SUMMARY)
    repository.get = lambda _thread_id: (_ for _ in ()).throw(RuntimeError("db down"))
    bundle = asyncio.run(_build(repository, _settings()))
    assert bundle.degrade_reason == "none"
    assert bundle.summary_item is None
    assert bundle.section("thread_summary").reason == "no_watermark_yet"
    replay = bundle.replay_dict()
    assert replay and all(SUMMARY_NOTE_LABEL not in entry["content"] for entry in replay)


# --------------------------------------------------------------------------- #
# Import boundary, RecallFilter, estimator                                     #
# --------------------------------------------------------------------------- #
def test_no_memory_module_imports_agent_framework_outside_the_adapter():
    """GR-35: the SDK is imported under ai/core/memory/ only by maf_adapter."""
    import ast

    for path in MEMORY_DIR.rglob("*.py"):
        if "maf_adapter" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            assert not any(name.split(".")[0] == "agent_framework" for name in names), (
                f"{path.relative_to(MEMORY_DIR)}:{node.lineno}"
            )


def test_recall_filter_table_covers_every_task_intent():
    from ai.core.analysis.intent import TaskIntent

    for intent in TaskIntent:
        assert intent.value in recall_filter.INTENT_MEMORY_TYPES, intent
        selected = recall_filter.recall_filter_for(intent.value)
        assert selected.memory_types <= vocabulary.OPERATIONAL_MEMORY_TYPES
        assert selected.boost_topics <= {t.value for t in vocabulary.Topic}
    assert (
        recall_filter.recall_filter_for("general").memory_types
        == vocabulary.OPERATIONAL_MEMORY_TYPES
    )
    assert (
        recall_filter.recall_filter_for("nonsense").memory_types
        == vocabulary.OPERATIONAL_MEMORY_TYPES
    )
    off = recall_filter.recall_filter_for("diagnostic", type_filter_enabled=False)
    assert (
        off.memory_types == vocabulary.OPERATIONAL_MEMORY_TYPES and off.type_filter_enabled is False
    )
    assert recall_filter.recall_filter_for("diagnostic").reauthorize(("a", "b")) == ("a", "b")


def test_retrieval_plan_records_the_filter_from_the_routed_intent():
    bundle = asyncio.run(
        _build(_Repository(watermark=0, summary=""), _settings(), task_intent="safety_lookup")
    )
    assert bundle.retrieval_plan.task_intent == "safety_lookup"
    assert bundle.retrieval_plan.boost_topics == ("safety",)
    assert "contact_role" in bundle.retrieval_plan.memory_types
    assert bundle.retrieval_plan.corpora_considered == (
        "controlled",
        "uploaded",
        "media",
        "repair_history",
    )


def test_energy_topics_match_the_lockout_energy_sources():
    try:
        from repair.models import LockoutPoint
    except Exception:
        pytest.skip("repair app not installed in the island")
    sources = {value for value, _label in LockoutPoint.EnergySource.choices} - {"other"}
    assert sources == set(vocabulary.ENERGY_TOPICS)


def test_estimator_falls_back_to_chars_and_protected_sections_still_emit(monkeypatch):
    from ai.core import usage

    monkeypatch.setitem(sys.modules, "tiktoken", None)
    usage._token_encoder.cache_clear()
    try:
        estimator = token_estimator.default_estimator()
        assert estimator.kind == "chars"
        assembler = ContextAssembler(estimator=estimator)
        bundle = asyncio.run(
            assembler.build(
                repository=_Repository(watermark=12, summary=SUMMARY),
                thread_id="thread_c",
                turn_id="t",
                settings=_settings(),
                call_sync=_TestTurnService._call_sync,
            )
        )
    finally:
        usage._token_encoder.cache_clear()
    assert bundle.estimator_kind == "chars"
    # Protected: the memory block and the newest two antecedents always emit.
    replay = bundle.replay_dict()
    assert replay[0]["content"].startswith(SUMMARY_NOTE_LABEL)
    assert len(bundle.recent_turns) >= 2
    # And the char ceilings hold regardless of the estimator.
    assert all(len(item.text) <= 60 + len("… [truncated]") for item in bundle.recent_turns)
    assert sum(len(e["content"]) for e in replay) <= 400 + len(replay[0]["content"]) + 200


def test_context_builder_usage_event_is_integers_only(monkeypatch):
    from ai.core import usage

    monkeypatch.setattr("ai.core.config.get_settings", _settings)
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "ai.core.turn_service.record_usage",
        lambda source, metrics: events.append((source, metrics)),
    )
    service = _TestTurnService(workflow_factory=lambda: None)
    asyncio.run(
        service._conversation_history(_Repository(watermark=12, summary=SUMMARY), "thread_c")
    )
    sources = [source for source, _ in events]
    assert "context_builder" in sources and "history_replay" in sources
    builder = next(metrics for source, metrics in events if source == "context_builder")
    assert all(isinstance(value, int) and not isinstance(value, bool) for value in builder.values())
    assert builder["summary_present"] == 1 and builder["db_round_trips"] == 2
    assert usage.CANONICAL_TOKEN_KEYS  # the event keys are non-canonical by design
    assert not set(builder) & set(usage.CANONICAL_TOKEN_KEYS)
