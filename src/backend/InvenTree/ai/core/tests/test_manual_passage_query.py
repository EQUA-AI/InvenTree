"""Original voice passage topics stay bound to fresh native Work authority."""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from ai.core.auth import AIPrincipal
from ai.core.focus.contract import FocusTarget, focus_to_stored
from ai.core.reasoning.repair_source_context import (
    bind_repair_sources,
    inspection_requested,
    manual_passage_query,
)
from ai.core.tools.diagnostics import (
    MANUAL_READ_CAPABILITY,
    DiagnosticArgumentsError,
    DiagnosticAuthorizationError,
    DiagnosticRecordRoot,
    ReadAuthorization,
    ReaderResult,
    build_diagnostic_context,
    get_diagnostic_tool_registry,
)

NOW = datetime(2026, 7, 15, 4, 0, tzinfo=UTC)
REQUEST = "Open the WEG manual passage on cooler fouling. What does it actually say, and does that guidance apply to this motor?"


def principal():
    return AIPrincipal(
        subject="user:7",
        actor="user:7",
        user_pk="7",
        username="reader",
        authentication_method="session",
        scope="site:test",
        policy_version="test",
        is_staff=False,
        is_superuser=False,
    )


def diagnostic():
    return build_diagnostic_context(
        principal(),
        server_record_roots=(
            DiagnosticRecordRoot(
                entity_type="repair_packet",
                entity_id=27,
                expected_revision=NOW.isoformat(),
                linked_machine_id=44,
            ),
        ),
        server_allowed_capabilities=(MANUAL_READ_CAPABILITY,),
        issued_at=NOW,
    )


@pytest.mark.parametrize(
    ("utterance", "topic"),
    [
        (REQUEST, "cooler fouling"),
        ("Open the manual passage about bearing lubrication.", "bearing lubrication"),
        (
            "Please open the attached motor manual passage regarding section 4.6.3 cleaning. Does it apply?",
            "section 4.6.3 cleaning",
        ),
    ],
)
def test_exact_original_topic_without_request_boilerplate(utterance, topic):
    assert manual_passage_query(utterance) == topic
    assert topic in utterance


@pytest.mark.parametrize(
    "utterance",
    [
        "Open the WEG manual.",
        "What is the maintenance plan?",
        "Open inspection record E03.",
        "Do not open the manual passage on fouling.",
        "Open the manual passage on fouling and approve the work.",
        "Open the manual passage on fouling; start the pump.",
        "Open the manual passage on fouling and confirm isolation.",
        "Open the manual passage on fouling for another motor.",
        "Compare the manual passage on fouling across machines.",
        "Open the manual passage on " + "x" * 1000,
        None,
    ],
)
def test_other_requests_and_combined_effects_cannot_bind_a_topic(utterance):
    assert manual_passage_query(utterance) == ""


@pytest.mark.parametrize(
    "utterance",
    [
        "E03 reports airside cooler deposits and no visible damage. What is the leading cause and are replacement parts justified?",
        "E-zero-three shows deposits. Could this explain the cause?",
        "Open inspection record E03.",
    ],
)
def test_current_e03_findings_question_offers_native_source_verification(utterance):
    assert inspection_requested(utterance) is True


@pytest.mark.parametrize(
    "utterance",
    [
        "E03 reports deposits.",
        "Do not use E03. What is the cause?",
        "Don't read E03. Are parts justified?",
        "E05 reports deposits. What is the cause?",
        "The last answer quoted E03 reports deposits.",
        "E03 reports deposits. What is the weather?",
        None,
    ],
)
def test_e03_mentions_without_source_analysis_do_not_enable_reader(utterance):
    assert inspection_requested(utterance) is False


@pytest.fixture
def owned_work(monkeypatch):
    actor = principal()
    owner = SimpleNamespace(pk=7)
    packet = SimpleNamespace(pk=27, updated_at=NOW)
    work = SimpleNamespace(pk=84, machine_id=44)
    stored = focus_to_stored(
        FocusTarget(
            kind="work_order",
            id=84,
            work_order_id=84,
            machine_id=44,
            label="WO-84",
        )
    )
    snapshot = {"focus": stored, "version": 5}
    observed = []

    def thread_filter(**kwargs):
        observed.append(kwargs)
        matches = kwargs == {
            "pk": "thread-test",
            "owner": owner,
            "scope_key": actor.scope,
            "conversation_focus": stored,
            "focus_version": 5,
        }
        return SimpleNamespace(first=lambda: SimpleNamespace(pk="thread-test") if matches else None)

    monkeypatch.setattr(
        "django.contrib.auth.get_user_model",
        lambda: SimpleNamespace(
            objects=SimpleNamespace(filter=lambda **_kwargs: SimpleNamespace(first=lambda: owner)),
        ),
    )
    monkeypatch.setattr("aichat.models.ChatThread.objects.filter", thread_filter)
    monkeypatch.setattr(
        "tasks.services.repair_scope_drafts._repair_source_work",
        lambda *_: (work, None, None, packet),
    )
    return SimpleNamespace(actor=actor, snapshot=snapshot, observed=observed)


def bind(context, owned_work, **changes):
    values = {
        "actor": owned_work.actor,
        "thread_id": "thread-test",
        "focus_snapshot": owned_work.snapshot,
        "content": REQUEST,
        "modality": "voice",
    }
    values.update(changes)
    return bind_repair_sources(context, **values)


def test_native_binding_keeps_entire_original_request_without_promoting_it(owned_work):
    context = bind(diagnostic(), owned_work)
    assert context.original_manual_passage_request == REQUEST
    assert context.focused_repair_packet_id == 27
    assert context.prepared_inspection_requested is False
    assert context.capabilities == diagnostic().capabilities
    assert context.record_roots == diagnostic().record_roots


@pytest.mark.parametrize(
    "change",
    [
        "text",
        "missing_focus",
        "stale_focus",
        "different_work",
        "different_actor",
        "wrong_machine",
        "wrong_revision",
        "other_intent",
    ],
)
def test_source_binding_resets_each_turn_and_requires_exact_trusted_focus(owned_work, change):
    context = replace(diagnostic(), original_manual_passage_request=REQUEST)
    values = {}
    if change == "text":
        values["modality"] = "text"
    elif change == "missing_focus":
        values["focus_snapshot"] = None
    elif change == "stale_focus":
        values["focus_snapshot"] = {**owned_work.snapshot, "version": 4}
    elif change == "different_work":
        values["thread_id"] = "other-thread"
    elif change == "different_actor":
        values["actor"] = replace(owned_work.actor, user_pk="8")
    elif change == "wrong_machine":
        context = replace(
            context, record_roots=(replace(context.record_roots[0], linked_machine_id=45),)
        )
    elif change == "wrong_revision":
        context = replace(
            context, record_roots=(replace(context.record_roots[0], expected_revision="stale"),)
        )
    else:
        values["content"] = "What did the last work order do?"
    assert bind(context, owned_work, **values).original_manual_passage_request == ""


class Reader:
    def __init__(self, *, deny=False):
        self.deny = deny
        self.authorizations = []
        self.reads = []

    def rehydrate_actor(self, _principal):
        return object()

    def authorize(self, *, context, capability, root, check_id, arguments, **_kwargs):
        self.authorizations.append(arguments.model_dump())
        if self.deny:
            return None
        return ReadAuthorization(
            check_id=check_id,
            actor_id=context.actor,
            capability=capability,
            entity_type=root.entity_type,
            entity_id=root.entity_id,
            current_revision=root.expected_revision,
            authorization_class=root.authorization_class,
            scoped=True,
            linked_machine_id=root.linked_machine_id,
            checked_at=NOW,
        )

    def read(self, *, arguments, **_kwargs):
        self.reads.append(arguments.model_dump())
        return ReaderResult(abstention_reason="No fixture evidence.")


def arguments():
    return {
        "repair_packet_id": 27,
        "expected_revision": NOW.isoformat(),
        "query": "generic cooling maintenance",
        "attachment_id": 12,
        "limit": 2,
    }


def test_one_real_model_tool_call_uses_original_topic_only_after_authorization(owned_work):
    context = bind(diagnostic(), owned_work)
    reader = Reader()
    registry = get_diagnostic_tool_registry(reader=reader, clock=lambda: NOW)
    model_arguments = arguments()
    result = registry.execute("search_related_work_manuals", model_arguments, context=context)
    assert len(reader.authorizations) == len(reader.reads) == 1
    assert reader.authorizations[0] == model_arguments
    assert reader.reads[0] == {**model_arguments, "query": "cooler fouling"}
    assert model_arguments == arguments()
    assert result["tool"] == "search_related_work_manuals"


@pytest.mark.parametrize("denial", ["rbac", "cross_packet", "stale_context", "unknown_binding"])
def test_original_query_never_bypasses_existing_read_authority(owned_work, denial):
    context = bind(diagnostic(), owned_work)
    reader = Reader(deny=denial == "rbac")
    args = arguments()
    if denial == "cross_packet":
        args["repair_packet_id"] = 28
    elif denial == "stale_context":
        context = replace(context, issued_at=NOW.replace(year=2025))
    elif denial == "unknown_binding":
        context = replace(context, focused_repair_packet_id=None)
    registry = get_diagnostic_tool_registry(reader=reader, clock=lambda: NOW)
    with pytest.raises(DiagnosticAuthorizationError):
        registry.execute("search_related_work_manuals", args, context=context)
    assert reader.reads == []


def test_model_cannot_supply_original_request_as_a_tool_argument(owned_work):
    context = bind(diagnostic(), owned_work)
    registry = get_diagnostic_tool_registry(reader=Reader(), clock=lambda: NOW)
    with pytest.raises(DiagnosticArgumentsError):
        registry.execute(
            "search_related_work_manuals",
            {**arguments(), "original_manual_passage_request": REQUEST},
            context=context,
        )
