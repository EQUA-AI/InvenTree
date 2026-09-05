"""M1 PR H (plan §9.4 Q73): the interim topology gate — two signals or nothing."""

from __future__ import annotations

import pytest
from ai.core.i18n_templates import TOPOLOGY_UNAVAILABLE, deterministic_template
from ai.core.memory import topology_gate
from ai.core.quota import slo

NAMES = ("Analysis Eval SI-3000 Inverter A", "Influent Pump Station No. 1")


@pytest.mark.parametrize(
    "text",
    [
        "What feeds the Analysis Eval SI-3000 Inverter A?",
        "Which breaker isolates the Analysis Eval SI-3000 Inverter A?",
        "What is upstream of the influent pump station?",
        "does anything depend on the analysis eval SI-3000 inverter A",
        "Is there an alternate supply for the influent pump station?",
    ],
)
def test_relation_plus_equipment_is_a_topology_question(text):
    assert topology_gate.is_topology_question(text, equipment_names=NAMES)


@pytest.mark.parametrize(
    "text",
    [
        "What feeds this?",  # relation, no identifiable equipment
        "Tell me about the SI-3000 Inverter A",  # equipment, no relation
        "Show open work orders for inverter A",
        "isolate the breaker",  # relation only
        "",
    ],
)
def test_one_signal_alone_never_fires(text):
    assert not topology_gate.is_topology_question(text, equipment_names=NAMES)
    assert not topology_gate.is_topology_question(
        "What feeds the SI-3000 Inverter A?", equipment_names=()
    )


def test_relation_terms_are_the_rider_list_and_word_bounded():
    assert {"upstream", "downstream", "fed by", "breaker", "depends on", "alternate supply"} <= set(
        topology_gate.RELATION_TERMS
    )
    assert topology_gate.has_relation_term("the DOWNSTREAM valve")
    assert not topology_gate.has_relation_term("feedstock levels")  # 'feeds' is bounded


def test_the_sentence_exists_in_four_locales_and_falls_back_to_english():
    en = deterministic_template(TOPOLOGY_UNAVAILABLE, "en")
    assert en.startswith("No published topology exists for this client yet")
    for locale in ("es", "de", "fr"):
        assert deterministic_template(TOPOLOGY_UNAVAILABLE, locale) != en
    assert deterministic_template(TOPOLOGY_UNAVAILABLE, "pt-BR") == en
    assert deterministic_template(TOPOLOGY_UNAVAILABLE, None) == en


def test_topology_unavailable_is_a_deterministic_slo_class():
    assert slo.slo_class_for("topology_unavailable", None) == "deterministic"


# --------------------------------------------------------------------------- #
# Dispatch: the gate answers before any workflow runs                          #
# --------------------------------------------------------------------------- #
def test_the_gate_answers_verbatim_and_never_runs_a_workflow(monkeypatch):
    import asyncio
    import os
    import uuid

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
    import django

    django.setup()
    from django.core.management import call_command

    call_command("migrate", verbosity=0, interactive=False)
    from types import SimpleNamespace

    from ai.core.tests import test_route_facts as rf
    from ai.core.turn import execution
    from aichat.models import ChatMessage

    # The predicate is unit-tested above; here it is forced so the dispatch
    # is exercised without a live diagnostic context.
    monkeypatch.setattr(
        execution,
        "_equipment_names",
        lambda _run: ["Analysis Eval SI-3000 Inverter A"],
    )
    monkeypatch.setattr(
        "ai.core.memory.topology_gate.is_topology_question",
        lambda text, **_kwargs: "feeds" in text.lower(),
    )
    user = rf._user()
    thread_id = f"topo_{uuid.uuid4().hex[:12]}"
    workflow = rf._ScriptedWorkflow(["Scripted answer."])
    service = rf._service(workflow)

    async def run():
        gated = await rf._turn(service, user, thread_id, "What feeds inverter A?", "topo:1")
        plain = await rf._turn(
            service, user, thread_id, "Open work orders for inverter A", "topo:2"
        )
        return gated, plain

    gated, plain = asyncio.run(run())
    assert gated.message.startswith("No published topology exists for this client yet")
    assert gated.workflow_used == "topology_unavailable"
    assert plain.workflow_used == "wf8"
    assistant = list(
        ChatMessage.objects.filter(thread_id=thread_id, role="assistant").order_by("sequence")
    )
    assert assistant[0].metadata["workflow_id"] == "topology_unavailable"
    assert assistant[0].metadata["response_state"] == "incomplete"
    assert assistant[1].metadata["workflow_id"] == "wf8"
    # Only the ordinary turn reached the workflow double.
    assert len(workflow.replies) == 0 and assistant[1].content == "Scripted answer."
    del SimpleNamespace
