"""S3: task/effect intent classification and the analysis-route override.

The battery's dominant misroute was analysis questions absorbed by the
diagnostic patterns; the YAML-driven suite here pins every recorded
misroute id to its expected intent using SYNTHETIC paraphrases (the frozen
battery text never enters the repo — teaching to the blind eval would
invalidate it). A YAML id without a paraphrase fails loudly.
"""

# ruff: noqa: E402

from __future__ import annotations

import os
import time

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import asyncio
from pathlib import Path
from unittest import mock

import pytest
import yaml
from ai.core.analysis import intent as intent_module
from ai.core.analysis.intent import (
    ANALYSIS_INTENTS,
    EffectIntent,
    IntentDecision,
    TaskIntent,
    classify,
    classify_rules,
)

_CASES = yaml.safe_load(
    (Path(__file__).resolve().parents[1] / "evals" / "analysis_battery_cases.yaml").read_text()
)

#: Synthetic paraphrases of the battery misroute SHAPES, keyed by battery id.
#: Deliberately non-verbatim (see module docstring).
REPRESENTATIVE_PROMPTS: dict[str, str] = {
    "Q35": "Show the work orders where a fault code was recorded for the inverters",
    "Q36": "List the maintenance records that mention an overtemperature issue",
    "Q37": "How many work orders were opened for each inverter?",
    "Q38": "Which failure appears most often in the maintenance history?",
    "Q40": "How many repairs were completed across the fleet last year?",
    "Q41": "Did the number of faults change over time for these records?",
    "Q45": "How often do the records show the same symptoms returning?",
    "Q46": "Is the time between breakdowns getting worse over the past year?",
    "Q48": "What is the total count of unresolved issues in the work orders?",
    "Q51": "Show the trend of maintenance jobs per month for the fleet",
    "Q52": "Which machine had the most work orders?",
    "Q54": "What is the pattern of repeat failures in the service history?",
    "Q61": "Did the recorded repairs follow the maintenance intervals the manual requires?",
    "Q63": "Compare the last repair records against the service guide procedure",
    "Q65": "Was the work in these service records done according to the manual?",
}

_MISROUTE_CASES = {case["id"]: case for case in _CASES["misrouted_as_diagnostic"]}


def test_every_yaml_misroute_id_has_a_prompt() -> None:
    """A new YAML case without a paraphrase must fail loudly, not silently."""
    assert set(_MISROUTE_CASES) == set(REPRESENTATIVE_PROMPTS)


@pytest.mark.parametrize("case_id", sorted(_MISROUTE_CASES))
def test_misrouted_battery_shapes_classify_off_diagnostic(case_id: str) -> None:
    decision = classify_rules(REPRESENTATIVE_PROMPTS[case_id])
    assert decision is not None, f"{case_id}: rules were inconclusive"
    assert decision.intent.value == _MISROUTE_CASES[case_id]["expected_intent"], (
        f"{case_id}: got {decision.intent}"
    )
    assert decision.intent is not TaskIntent.DIAGNOSTIC
    assert decision.effect is EffectIntent.READ_ONLY
    assert decision.source == "rules"


class TestEffectSeparation:
    """Q64/Q74: read questions must never classify as governed effects."""

    def test_presentation_artifact_request_is_read_only(self) -> None:
        decision = classify_rules("Create a table of the maintenance records with their outcomes")
        assert decision is not None
        assert decision.effect is EffectIntent.READ_ONLY
        assert decision.intent is not TaskIntent.GOVERNED_ACTION

    def test_part_advice_is_read_only(self) -> None:
        decision = classify_rules("Which replacement part should we order for the cooling fan?")
        assert decision is not None
        assert decision.intent is TaskIntent.PART_ADVICE
        assert decision.effect is EffectIntent.READ_ONLY

    def test_true_effect_is_governed_action(self) -> None:
        decision = classify_rules("Please create a work order for the pump")
        assert decision is not None
        assert decision.intent is TaskIntent.GOVERNED_ACTION
        assert decision.effect is EffectIntent.EFFECT_REQUEST


class TestFamilies:
    def test_source_inventory(self) -> None:
        decision = classify_rules("Which manuals do you have for the inverters?")
        assert decision is not None
        assert decision.intent is TaskIntent.SOURCE_INVENTORY

    def test_doc_prescription_frequency_is_manual_fact_not_aggregate(self) -> None:
        """ "How often" beside a DOC noun asks what the doc prescribes.

        The aggregate branch's doc arm sent both R5 golden interval items
        ("how often does the uploaded manual require a teardown?") to
        fleet_aggregate, whose records executor and intent pack carry no
        document tools (live, 2026-09-01). Aggregates are records-only.
        """
        decision = classify_rules(
            "How often does the uploaded HX-200 manual require a full teardown?"
        )
        assert decision is not None
        assert decision.intent is TaskIntent.MANUAL_FACT

    def test_records_frequency_stays_fleet_aggregate(self) -> None:
        decision = classify_rules("How often were repairs logged for the inverters?")
        assert decision is not None
        assert decision.intent is TaskIntent.FLEET_AGGREGATE

    def test_doc_prescription_with_record_noun_is_still_manual_fact(self) -> None:
        """A deontic marker pins the doc as subject even past a record noun.

        "maintenance" is a _RECORD_NOUN, so the plain manual-fact arm's
        ``not has_records`` bar rejected this shape and the aggregate branch
        claimed it (adversarial review, 2026-09-01).
        """
        decision = classify_rules("How often does the manual require maintenance on the HX-200?")
        assert decision is not None
        assert decision.intent is TaskIntent.MANUAL_FACT

    @pytest.mark.parametrize(
        "prompt",
        [
            # Doc-anchored EVENT counts are records aggregates: the doc noun
            # is only a source/time anchor and there is no deontic marker
            # (adversarial review, 2026-09-01 — the records-only draft of
            # the aggregate branch misrouted all three to manual_fact or
            # diagnostic).
            "How often was each pump inspected per the procedures?",
            "How many teardowns did we do on the HX-200 since the last manual revision?",
            "Per the datasheets, how often do these motors overheat across the fleet?",
        ],
    )
    def test_doc_anchored_event_counts_stay_fleet_aggregate(self, prompt: str) -> None:
        decision = classify_rules(prompt)
        assert decision is not None
        assert decision.intent is TaskIntent.FLEET_AGGREGATE

    def test_manual_fact_without_history_nouns(self) -> None:
        decision = classify_rules("What torque does the manual specify for the terminals?")
        assert decision is not None
        assert decision.intent is TaskIntent.MANUAL_FACT

    def test_safety_lookup_is_not_diagnostic_or_shortcut(self) -> None:
        decision = classify_rules(
            "What does the lockout procedure require before opening the cabinet?"
        )
        assert decision is not None
        assert decision.intent is TaskIntent.SAFETY_LOOKUP

    def test_diagnostic_still_wins_for_live_symptoms(self) -> None:
        decision = classify_rules("Why does the inverter keep tripping right now?")
        assert decision is not None
        assert decision.intent is TaskIntent.DIAGNOSTIC

    def test_greeting_is_inconclusive(self) -> None:
        assert classify_rules("Hello there!") is None

    def test_empty_is_inconclusive(self) -> None:
        assert classify_rules("") is None
        assert classify_rules(None) is None  # type: ignore[arg-type]


class TestClassifierFallback:
    def _run(self, **kwargs):
        return asyncio.run(classify("tell me something interesting", **kwargs))

    def test_rules_win_without_a_model_call(self) -> None:
        with mock.patch.object(intent_module, "_classify_with_model", side_effect=AssertionError):
            decision = asyncio.run(classify("How many work orders were opened last month?"))
        assert decision.source == "rules"

    def test_llm_disabled_returns_safe_fallback(self) -> None:
        decision = self._run(allow_llm=False)
        assert decision.intent is TaskIntent.GENERAL
        assert decision.effect is EffectIntent.READ_ONLY
        assert decision.source == "fallback_default"

    def test_classifier_success_is_used(self) -> None:
        verdict = IntentDecision(
            intent=TaskIntent.MANUAL_FACT,
            effect=EffectIntent.READ_ONLY,
            confidence=0.6,
            reason_codes=("classifier",),
            source="classifier",
        )
        with mock.patch.object(intent_module, "_classify_with_model", return_value=verdict):
            assert self._run() is verdict

    def test_classifier_failure_degrades_safely(self) -> None:
        with mock.patch.object(
            intent_module, "_classify_with_model", side_effect=RuntimeError("boom")
        ):
            decision = self._run()
        assert decision.intent is TaskIntent.GENERAL
        assert decision.source == "fallback_default"


def test_rule_pass_is_linear_time() -> None:
    """The injection-guard budget: pathological input stays under 100ms."""
    pathological = ("work order manual compare history " * 120) + "?" * 40
    started = time.perf_counter()
    for _ in range(50):
        classify_rules(pathological)
    assert (time.perf_counter() - started) < 0.1 * 50  # generous CI margin


class TestCapabilitySelection:
    """S3: typed intent outranks lexical scoring and skips history carryover."""

    def _select(self, query: str, **kwargs):
        from ai.core.tools.capabilities import select_capabilities

        return select_capabilities(query, authenticated=True, **kwargs)

    @pytest.mark.parametrize(
        ("task_intent", "expected_primary"),
        [
            ("fleet_aggregate", "analytics.read"),
            ("trend_analysis", "analytics.read"),
            ("record_retrieval", "maintenance.read"),
            ("manual_wo_comparison", "maintenance.read"),
            # S8a: inventory questions lead with the registry tool.
            ("source_inventory", "sources.read"),
            ("manual_fact", "manuals.read"),
        ],
    )
    def test_intent_seeds_the_pack_selection(self, task_intent: str, expected_primary: str) -> None:
        selection = self._select("how are things looking", task_intent=task_intent)
        assert selection.pack_ids
        assert selection.pack_ids[0] == expected_primary
        assert "task_intent" in selection.signals

    def test_intent_skips_history_carryover(self) -> None:
        # Anaphoric query + supplier-flavored history would carry the prior
        # subject; a typed analysis intent must never inherit it (M6-class
        # contamination control).
        history = [
            {"role": "user", "content": "what purchase orders are open for the supplier"},
            {"role": "assistant", "content": "Two purchase orders are open."},
        ]
        selection = self._select(
            "and how many of those were there",
            context={"conversation_history": history},
            task_intent="fleet_aggregate",
        )
        assert selection.pack_ids[0] == "analytics.read"
        assert "history_subject" not in selection.signals

    def test_write_intent_still_short_circuits(self) -> None:
        selection = self._select(
            "create a purchase order for ten fans", task_intent="fleet_aggregate"
        )
        assert selection.requires_specialist

    def test_no_intent_keeps_legacy_selection(self) -> None:
        selection = self._select("how many parts are in stock")
        assert "task_intent" not in selection.signals


class TestRoutingIntegration:
    """Shadow keeps the legacy route; enforce swaps to the analysis rail."""

    def _run_build_route(
        self,
        *,
        shadow: bool,
        enforce: bool,
        content: str,
        gate: str = "enforce",
        holdback: str = "",
    ):
        from types import SimpleNamespace

        from ai.core.turn import routing as routing_stage

        factory_calls: list[str] = []

        class _Service:
            async def _build_diagnostic_context(self, **kwargs):
                factory_calls.append("built")
                return SimpleNamespace(record_roots=(), capabilities=())

            def _route_turn(self, **kwargs):
                from ai.core.agents.voice_routing import (
                    ReasoningEffort,
                    RouteMode,
                    RouteReason,
                    VoiceRouteDecision,
                )

                return VoiceRouteDecision(
                    mode=RouteMode.FAST_PATH,
                    effort=ReasoningEffort.LOW,
                    reason_codes=(RouteReason.GENERAL_REQUEST,),
                    target_workflow_id="wf8",
                )

        run = SimpleNamespace(
            content=content,
            routing_content="",
            question_resolution=None,
            injection_canonical=None,
            analysis_scope={"scope": {"mode": "explicit_assets"}, "version": 1, "hash": "ab" * 32},
            task_intent=None,
            diagnostic_context=None,
            route=None,
            modality="text",
            correlation_id="corr-x",
            actor=SimpleNamespace(),
            trusted_context=SimpleNamespace(locale="en"),
            metadata={},
        )
        settings = SimpleNamespace(
            feature_ai_analysis_router_shadow=shadow,
            feature_ai_analysis_router_enforce=enforce,
            evidence_gate_mode=gate,
            aimms_analysis_intent_holdback=holdback,
        )
        with mock.patch("ai.core.config.get_settings", return_value=settings):
            asyncio.run(routing_stage.build_route(_Service(), run))
        return run, factory_calls

    def test_shadow_keeps_legacy_route_and_builds_context(self) -> None:
        run, factory_calls = self._run_build_route(
            shadow=True, enforce=False, content="How many work orders were opened last month?"
        )
        assert run.task_intent is not None
        assert run.task_intent.intent in ANALYSIS_INTENTS
        assert run.route.mode.value == "fast_path"
        assert factory_calls == ["built"]

    def test_enforce_routes_analysis_and_skips_context(self) -> None:
        # A ROUTED intent (record_retrieval — shipped executor) under full
        # enforce (router + evidence gate) takes the analysis rail.
        run, factory_calls = self._run_build_route(
            shadow=True, enforce=True, content="Show me work order WO-1234"
        )
        assert run.route.mode.value == "analysis"
        assert run.route.target_workflow_id is None
        assert run.route.to_dict()["task_intent"] == run.task_intent.intent.value
        assert factory_calls == []

    def test_comparison_routes_under_enforce(self) -> None:
        # S9: manual_wo_comparison has a shipped validated executor — the
        # unshipped-intent legacy pin retired with it (every analysis
        # family now routes; the no-refusal invariant lives on in the
        # gate-rollback and holdback pins below).
        run, factory_calls = self._run_build_route(
            shadow=True,
            enforce=True,
            content="Did work order WO-1234 follow the service manual procedure?",
        )
        assert run.task_intent is not None
        assert run.task_intent.intent.value == "manual_wo_comparison"
        assert run.route.mode.value == "analysis"
        assert factory_calls == []

    def test_shipped_aggregate_routes_under_enforce(self) -> None:
        # S7: fleet_aggregate has a shipped validated executor — under full
        # enforce it takes the analysis rail like record_retrieval does.
        run, factory_calls = self._run_build_route(
            shadow=True, enforce=True, content="How many work orders were opened last month?"
        )
        assert run.task_intent is not None
        assert run.task_intent.intent.value == "fleet_aggregate"
        assert run.route.mode.value == "analysis"
        assert factory_calls == []

    def test_holdback_keeps_a_shipped_intent_on_the_legacy_route(self) -> None:
        # S7 rollout knob: a held-back intent behaves exactly like an
        # unshipped one — legacy rail, diagnostic context built, no refusal
        # — so ops can stage each executor's enforce flip per intent.
        run, factory_calls = self._run_build_route(
            shadow=True,
            enforce=True,
            content="How many work orders were opened last month?",
            holdback="fleet_aggregate, trend_analysis",
        )
        assert run.task_intent is not None
        assert run.task_intent.intent.value == "fleet_aggregate"
        assert run.route.mode.value == "fast_path"
        assert factory_calls == ["built"]

    def test_gate_rollback_returns_analysis_to_legacy(self) -> None:
        # An incident rollback of the evidence gate (enforce -> shadow) must
        # return EVERY analysis intent to the legacy rail, never abstain.
        run, factory_calls = self._run_build_route(
            shadow=True, enforce=True, gate="shadow", content="Show me work order WO-1234"
        )
        assert run.route.mode.value == "fast_path"
        assert factory_calls == ["built"]

    def test_enforce_leaves_non_analysis_turns_alone(self) -> None:
        run, factory_calls = self._run_build_route(
            shadow=True, enforce=True, content="Why does the pump keep tripping right now?"
        )
        assert run.route.mode.value == "fast_path"
        assert factory_calls == ["built"]

    def test_flags_off_skips_classification_entirely(self) -> None:
        run, factory_calls = self._run_build_route(
            shadow=False, enforce=False, content="How many work orders were opened last month?"
        )
        assert run.task_intent is None
        assert run.route.mode.value == "fast_path"
        assert factory_calls == ["built"]


def test_analysis_canonical_is_a_valid_incomplete_response() -> None:
    """The abstention passes the strict canonical schema, no actions/speech."""
    from ai.core.turn.responses import _canonical_analysis_unavailable

    response = _canonical_analysis_unavailable(locale="en")
    assert response.kind == "evidence_analysis_unavailable"
    assert response.response_state.value == "incomplete"
    assert response.speak is False
    assert response.spoken_summary == ""
    assert response.recommended_actions == []
    # Honest copy: says the analysis did not run; never fakes capability.
    assert "did not run" in response.detailed_response
