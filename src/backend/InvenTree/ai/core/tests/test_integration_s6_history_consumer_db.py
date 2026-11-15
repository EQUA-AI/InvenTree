"""Actual S6 source to native typed facts, synthesis, rendering and delivery."""

from __future__ import annotations

import importlib.util
import json
from contextvars import Context
from dataclasses import replace
from importlib import import_module
from unittest.mock import patch

from ai.core.tests import test_integration_s6_history_boundary_db as fixtures
from asgiref.sync import sync_to_async
from django.test import TestCase
from integration_state.tests.assertions import assert_literal_bool
from machine_health.services.integration_s6_adapter import FixtureHistoryAdapter


class RecordedHistoryConsumerTests(fixtures.S6AIRequestFixture, TestCase):
    """Only the source transport/data are fake; native authority stays real."""

    def setUp(self):
        """Keep the real versioned producer envelope and change only its value."""
        super().setUp()
        self.record = replace(self.record, sample=replace(self.record.sample, value=12.5))
        self.adapter = FixtureHistoryAdapter.from_records(
            source=self.identity,
            mapping=self.mapping,
            coverage=self.window,
            generation="fixture-generation",
            records=(self.record,),
        )

    def consumer_module(self):
        name = "ai.core.integrations.integration_s6_history"
        self.assertIsNotNone(importlib.util.find_spec(name), "The S4 S6 consumer is required")
        return import_module(name)

    def analysis(self, **changes):
        module = self.consumer_module()
        arguments = {
            "environment": "s6-test",
            "machine_pk": self.machine.pk,
            "source_pk": self.source.pk,
            "binding_pk": self.binding.pk,
            "window": self.window,
            "adapter": self.adapter,
        }
        arguments.update(changes)
        return module.read_recorded_history_analysis(**arguments)

    def test_actual_source_becomes_native_typed_facts_and_honest_model_projection(self):
        """Preserve original values/provenance without exposing source internals."""
        module = self.consumer_module()
        self.assertTrue(callable(getattr(module, "read_recorded_history_analysis", None)))

        def operation():
            analysis = self.analysis()
            self.assertEqual(analysis.state, "available")
            view = analysis.synthesis_view()
            samples = [fact for fact in view["facts"] if fact["kind"] == "record_field"]
            self.assertEqual(len(samples), 1)
            self.assertEqual(samples[0]["values"]["value"], "12.5")
            self.assertEqual(
                samples[0]["values"]["observed_at"], self.record.sample.observed_at.isoformat()
            )
            coverage = view["coverage"]
            self.assertEqual(coverage["requested_window"], [-1, 4])
            assert_literal_bool(self, coverage["source_window_complete"], True)
            assert_literal_bool(self, coverage["complete_population"], False)
            assert_literal_bool(self, coverage["revision_complete"], False)
            self.assertEqual(coverage["telemetry_presentation"], "recorded_history")
            assert_literal_bool(self, coverage["display_shifted"], False)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_recorded_history_renders_through_native_catalog_and_validator(self):
        """A genuine recorded value reaches canonical native evidence, not prose."""
        module = self.consumer_module()

        def operation():
            analysis = self.analysis()
            self.assertTrue(
                callable(getattr(analysis, "render", None)), "Native rendering is required"
            )
            observed = []
            native_validator = module.validate_analysis

            def validate(**kwargs):
                result = native_validator(**kwargs)
                observed.append(result)
                return result

            with patch.object(module, "validate_analysis", side_effect=validate):
                result = analysis.render()
            self.assertEqual(len(observed), 1)
            self.assertIs(observed[0].outcome, module.CheckOutcome.PASS)
            self.assertEqual(
                {item.check for item in observed[0].results},
                {f"C{index:02d}" for index in range(1, 15)},
            )
            self.assertEqual(result["response_version"], 2)
            self.assertEqual(result["response_state"], "complete")
            self.assertIn("12.5", result["detailed_response"])
            self.assertIn(self.record.sample.observed_at.isoformat(), result["detailed_response"])
            self.assertIn(self.record.ingested_at.isoformat(), result["detailed_response"])
            self.assertTrue(result["evidence"])
            self.assertFalse(result["speak"])
            self.assertEqual(result["recommended_actions"], [])
            self.assertFalse(result["incomplete_reasons"])
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_history_catalog_is_context_local_and_literals_remain_native_safe(self):
        """History opt-in never changes ordinary native template membership."""
        from ai.core.analysis import renderer
        from ai.core.tests.test_analysis_renderer import (
            test_template_literals_contain_no_digits_or_code_shaped_tokens,
        )

        catalog = renderer.RENDER_TEMPLATES
        ordinary = set(catalog)
        self.assertNotIn("analysis.recorded_history_sample", ordinary)
        with (
            self.assertRaisesRegex(RuntimeError, "synthetic-catalog-exit"),
            renderer.recorded_history_templates(),
        ):
            active = set(catalog)
            self.assertIn("analysis.recorded_history_sample", active)
            self.assertEqual(set(catalog.keys()), active)
            self.assertEqual({item.key for item in catalog.values()}, active)
            self.assertEqual(set(dict(catalog)), active)
            self.assertEqual(set(catalog.copy()), active)
            self.assertEqual(len(catalog), len(active))
            self.assertEqual(Context().run(lambda: set(catalog)), ordinary)
            test_template_literals_contain_no_digits_or_code_shaped_tokens()
            raise RuntimeError("synthetic-catalog-exit")
        self.assertEqual(set(catalog), ordinary)

    def test_uninserted_rendered_identifier_still_fails_native_validation(self):
        """Closing real source fences must not authorize invented rendered codes."""
        module = self.consumer_module()

        def operation():
            analysis = self.analysis()
            self.assertIn("12.5", analysis.render()["detailed_response"])
            native_render = module.render_answer

            def changed(*args, **kwargs):
                value = native_render(*args, **kwargs)
                return replace(value, detailed_response=value.detailed_response + " ZZ-UNSEEN-999")

            with (
                patch.object(module, "render_answer", side_effect=changed),
                self.assertRaises(module.SourceSerializationError),
            ):
                analysis.render()
            self.assertIn("12.5", analysis.render()["detailed_response"])
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_scientific_and_signed_zero_measurements_keep_original_numeric_value(self):
        """The native value renderer must not trim an exponent or signed zero."""
        for raw in (12.5, 1.2e-10, 1.2e20, -1.2e-10, 0.0, -0.0):
            with self.subTest(raw=raw):
                record = replace(self.record, sample=replace(self.record.sample, value=raw))
                adapter = FixtureHistoryAdapter.from_records(
                    source=self.identity,
                    mapping=self.mapping,
                    coverage=self.window,
                    generation="fixture-generation",
                    records=(record,),
                )

                def operation(adapter=adapter, raw=raw):
                    analysis = self.analysis(adapter=adapter)
                    values = analysis.synthesis_view()["facts"][1]["values"]
                    self.assertEqual(values["value"], str(raw))
                    self.assertEqual(values["value_kind"], "number")
                    self.assertIn(str(raw), analysis.render()["detailed_response"])
                    return True

                assert_literal_bool(self, self.run_boundary(operation), True)

    def test_complete_window_count_is_a_native_source_bound_calculation(self):
        """A window count is calculated locally, never global completeness."""
        from ai.core.analysis.evidence import CalculationOutput

        def operation():
            analysis = self.analysis()
            view = analysis.synthesis_view()
            self.assertEqual(len(view["calculations"]), 1)
            count = view["calculations"][0]
            self.assertEqual(count["operation"], "count")
            self.assertEqual(count["values"], {"count": "1"})
            assert_literal_bool(self, count["complete_population"], False)
            stored = analysis._store.calculations[count["calculation_id"]]
            self.assertIs(type(stored), CalculationOutput)
            self.assertEqual(stored.input_refs, ("fact_2",))
            result = analysis.render()
            calculated = [
                claim
                for claim in result["payload"]["claims"]
                if claim["evidence_classification"] == "calculated"
            ]
            self.assertEqual(len(calculated), 1)
            self.assertEqual(calculated[0]["calculation_output_refs"], [count["calculation_id"]])
            self.assertIn("exact returned observation count is 1", result["detailed_response"])
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_empty_unsupported_and_partial_windows_never_share_a_success_total(self):
        """A genuine empty source window is distinct from refusal and a gap."""
        module = self.consumer_module()
        empty = FixtureHistoryAdapter.from_records(
            source=self.identity,
            mapping=self.mapping,
            coverage=self.window,
            generation="fixture-generation",
            records=(),
        )
        other = replace(
            self.record,
            sample=replace(
                self.record.sample, source_observation_id="literal-event:1", source_sequence=1
            ),
        )
        tied = FixtureHistoryAdapter.from_records(
            source=self.identity,
            mapping=self.mapping,
            coverage=self.window,
            generation="fixture-generation",
            records=(self.record, other),
        )

        def operation():
            analysis = self.analysis(adapter=empty)
            view = analysis.synthesis_view()
            assert_literal_bool(self, view["coverage"]["source_window_complete"], True)
            self.assertEqual(view["calculations"][0]["values"]["count"], "0")
            result = analysis.render()
            self.assertEqual(result["response_state"], "complete")
            self.assertNotIn("No matching records exist", result["detailed_response"])
            refused = self.analysis(adapter=None)
            self.assertEqual(refused.state, "unsupported")
            with self.assertRaises(module.HistoryCapabilityError):
                refused.synthesis_view()
            partial = self.analysis(adapter=tied, max_samples_per_read=1, max_reads=1)
            view = partial.synthesis_view()
            assert_literal_bool(self, view["coverage"]["source_window_complete"], False)
            self.assertTrue(view["coverage"]["gaps"])
            self.assertEqual(view["calculations"], [])
            result = partial.render()
            self.assertEqual(result["response_state"], "partial")
            self.assertTrue(result["incomplete_reasons"])
            self.assertFalse(result["speak"])
            self.assertFalse(result["recommended_actions"])
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_native_turn_selection_narrows_intake_and_retained_result_reuse(self):
        """A genuine native target selection is not replaced by client grants."""
        from ai.core.analysis.scope_context import bind_target_scope, turn_scope_context
        from assets.registry import ensure_pump

        module = self.consumer_module()
        other = ensure_pump(self.station, "P2")

        def operation():
            token = turn_scope_context.set(None)
            try:
                bind_target_scope(
                    None, machine_id=self.machine.pk, thread_pk=None, turn_pk="synthetic-turn"
                )
                analysis = self.analysis()
                self.assertIn("12.5", analysis.render()["detailed_response"])
                bind_target_scope(
                    None, machine_id=other.pk, thread_pk=None, turn_pk="synthetic-other-turn"
                )
                for action in ("intake", "model", "render"):
                    with self.subTest(action=action):
                        with (
                            patch.object(
                                module,
                                "read_recorded_history_for_current_ai",
                                wraps=module.read_recorded_history_for_current_ai,
                            ) as source_read,
                            self.assertRaises(module.HistoryAuthorizationError),
                        ):
                            if action == "intake":
                                self.analysis()
                            elif action == "model":
                                analysis.synthesis_view()
                            else:
                                analysis.render()
                        self.assertEqual(source_read.call_count, 0)
                return True
            finally:
                turn_scope_context.reset(token)

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_unmapped_native_date_and_source_class_filters_are_not_silently_ignored(self):
        """The held scope vocabulary is not permission for a broader source read."""
        from ai.core.analysis.scope_context import bind_target_scope, turn_scope_context

        module = self.consumer_module()

        def operation():
            original = turn_scope_context.set(None)
            try:
                allowed = bind_target_scope(
                    None, machine_id=self.machine.pk, thread_pk=None, turn_pk="synthetic-turn"
                )
                self.assertEqual(self.analysis().state, "available")
                for change in (
                    {"date_from": "1970-01-01"},
                    {"date_to": "1970-01-02"},
                    {"source_classes": frozenset({"work_order"})},
                ):
                    with self.subTest(change=change):
                        token = turn_scope_context.set(replace(allowed, **change))
                        try:
                            with (
                                patch.object(
                                    module,
                                    "read_recorded_history_for_current_ai",
                                    wraps=module.read_recorded_history_for_current_ai,
                                ) as source_read,
                                self.assertRaises(module.HistoryCapabilityError),
                            ):
                                self.analysis()
                            self.assertEqual(source_read.call_count, 0)
                        finally:
                            turn_scope_context.reset(token)
                self.assertIn("12.5", self.analysis().render()["detailed_response"])
                return True
            finally:
                turn_scope_context.reset(original)

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_actual_native_event_and_yield_channels_carry_the_same_validated_history(self):
        """Native AG-UI delivery carries bounded history, not an invented population."""
        from ai.core.streaming import EventCollector, EventType, InMemoryEventEmitter, RunContext

        async def operation():
            analysis = await sync_to_async(self.analysis, thread_sensitive=True)()
            self.assertTrue(callable(getattr(analysis, "run_stream", None)))
            emitter = InMemoryEventEmitter()
            collector = EventCollector("synthetic-history-thread")
            unsubscribe = await emitter.subscribe(collector)
            try:
                context = RunContext(
                    emitter=emitter, thread_id="synthetic-history-thread", run_id="synthetic-run"
                )
                chunks = [chunk async for chunk in analysis.run_stream(context)]
                self.assertEqual(len(chunks), 1)
                events = collector.events
                self.assertEqual(
                    [event.event_type for event in events],
                    [
                        EventType.STATE_DELTA,
                        EventType.TEXT_MESSAGE_START,
                        EventType.TEXT_MESSAGE_CONTENT,
                        EventType.TEXT_MESSAGE_END,
                    ],
                )
                packet = events[0].data
                self.assertEqual(packet["kind"], "recorded_history_analysis")
                self.assertEqual(packet["schema_version"], "aimms.s6.history-analysis.v1")
                self.assertEqual(packet["canonical_response"]["response_version"], 2)
                self.assertEqual(packet["coverage"]["requested_window"], [-1, 4])
                assert_literal_bool(self, packet["coverage"]["complete_population"], False)
                self.assertEqual(len(packet["citation_manifest"]), 2)
                self.assertEqual(events[2].data["delta"], chunks[0])
                self.assertEqual(packet["canonical_response"]["detailed_response"], chunks[0])
                self.assertIn(self.record.sample.observed_at.isoformat(), chunks[0])
                self.assertIn(self.record.ingested_at.isoformat(), chunks[0])
                self.assertEqual(events[1].data["messageId"], events[3].data["messageId"])
                return True
            finally:
                unsubscribe()

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_native_event_constructor_fault_rechecks_before_raising_content_free_error(self):
        """An encoder/constructor fault is neither stale authority nor private prose."""
        from ai.core import auth
        from ai.core.streaming import EventCollector, InMemoryEventEmitter, RunContext

        module = self.consumer_module()
        for revoke in (False, True):
            with self.subTest(revoke=revoke):

                async def operation(revoke=revoke):
                    analysis = await sync_to_async(self.analysis, thread_sensitive=True)()
                    emitter = InMemoryEventEmitter()
                    collector = EventCollector()
                    unsubscribe = await emitter.subscribe(collector)
                    tokens, chunks = [], []

                    def broken(**_kwargs):
                        if revoke:
                            tokens.append(
                                auth.principal_context.set(replace(auth.get_current_principal()))
                            )
                        raise RuntimeError("synthetic-private-event-constructor")

                    try:
                        error = (
                            module.HistoryAuthorizationError
                            if revoke
                            else module.SourceSerializationError
                        )
                        with (
                            patch.object(module, "AGUIEvent", side_effect=broken) as constructor,
                            self.assertRaises(error) as raised,
                        ):
                            async for chunk in analysis.run_stream(RunContext(emitter=emitter)):
                                chunks.append(chunk)
                        self.assertEqual(constructor.call_count, 1)
                        self.assertNotIn(
                            "synthetic-private-event-constructor", str(raised.exception)
                        )
                        self.assertEqual(collector.events, [])
                        self.assertEqual(chunks, [])
                        return True
                    finally:
                        for token in reversed(tokens):
                            auth.principal_context.reset(token)
                        unsubscribe()

                assert_literal_bool(self, self.run_boundary(operation), True)

    def test_real_point_revocation_after_each_native_event_withholds_later_channels(self):
        """Eight native event/auth combinations, not eight independent testcases."""
        from ai.core.streaming import EventCollector, EventType, InMemoryEventEmitter, RunContext
        from assets.models import DictionaryPoint

        module = self.consumer_module()
        stages = (
            EventType.STATE_DELTA,
            EventType.TEXT_MESSAGE_START,
            EventType.TEXT_MESSAGE_CONTENT,
            EventType.TEXT_MESSAGE_END,
        )
        for method in ("signed", "session"):
            for stage in stages:
                with self.subTest(method=method, stage=stage.value):

                    async def operation(stage=stage):
                        emitter = InMemoryEventEmitter()
                        collector = EventCollector()
                        hits, chunks = [], []

                        class RevokePoint:
                            async def handle(_self, event):
                                if event.event_type is stage:
                                    hits.append(stage)
                                    await sync_to_async(
                                        DictionaryPoint.objects.filter(pk=self.point.pk).update,
                                        thread_sensitive=True,
                                    )(status="draft")

                        unsubscribe = await emitter.subscribe(collector)
                        unsubscribe_revoke = await emitter.subscribe(RevokePoint())
                        try:
                            with patch.object(
                                module,
                                "read_recorded_history_for_current_ai",
                                wraps=module.read_recorded_history_for_current_ai,
                            ) as source_read:
                                analysis = await sync_to_async(
                                    self.analysis, thread_sensitive=True
                                )()
                                with self.assertRaises(module.HistoryAuthorizationError):
                                    async for chunk in analysis.run_stream(
                                        RunContext(emitter=emitter)
                                    ):
                                        chunks.append(chunk)
                            calls = source_read.call_count
                        finally:
                            await sync_to_async(
                                DictionaryPoint.objects.filter(pk=self.point.pk).update,
                                thread_sensitive=True,
                            )(status="approved")
                            unsubscribe_revoke()
                            unsubscribe()
                        self.assertEqual(hits, [stage])
                        self.assertEqual(calls, 1)
                        self.assertEqual(chunks, [])
                        self.assertEqual(
                            [event.event_type for event in collector.events],
                            list(stages[: stages.index(stage) + 1]),
                        )
                        restored = await sync_to_async(analysis.render, thread_sensitive=True)()
                        self.assertIn("12.5", restored["detailed_response"])
                        return True

                    assert_literal_bool(self, self.run_boundary(operation, method=method), True)

    def test_native_delivery_route_is_bound_after_each_await_including_final_event(self):
        """An authorized old-route event cannot authorize a new-route yield."""
        from ai.core.streaming import EventCollector, EventType, InMemoryEventEmitter, RunContext

        module = self.consumer_module()
        stages = (
            EventType.STATE_DELTA,
            EventType.TEXT_MESSAGE_START,
            EventType.TEXT_MESSAGE_CONTENT,
            EventType.TEXT_MESSAGE_END,
        )
        for stage in stages:
            for field in ("thread_id", "run_id", "agent_name", "emitter"):
                with self.subTest(stage=stage.value, field=field):

                    async def operation(stage=stage, field=field):
                        analysis = await sync_to_async(self.analysis, thread_sensitive=True)()
                        emitter = InMemoryEventEmitter()
                        collector = EventCollector()
                        context = RunContext(
                            emitter=emitter,
                            thread_id="synthetic-original-thread",
                            run_id="synthetic-original-run",
                            agent_name="synthetic-history-reader",
                        )
                        hits, chunks = [], []

                        class ChangeRoute:
                            async def handle(_self, event):
                                if event.event_type is stage:
                                    hits.append((stage, field))
                                    replacement = (
                                        InMemoryEventEmitter()
                                        if field == "emitter"
                                        else "synthetic-other-route"
                                    )
                                    setattr(context, field, replacement)

                        unsubscribe = await emitter.subscribe(collector)
                        unsubscribe_change = await emitter.subscribe(ChangeRoute())
                        try:
                            with self.assertRaises(module.HistoryAuthorizationError):
                                async for chunk in analysis.run_stream(context):
                                    chunks.append(chunk)
                        finally:
                            unsubscribe_change()
                            unsubscribe()
                        self.assertEqual(hits, [(stage, field)])
                        self.assertEqual(chunks, [])
                        self.assertEqual(
                            [event.event_type for event in collector.events],
                            list(stages[: stages.index(stage) + 1]),
                        )
                        restored = await sync_to_async(analysis.render, thread_sensitive=True)()
                        self.assertIn("12.5", restored["detailed_response"])
                        return True

                    assert_literal_bool(self, self.run_boundary(operation), True)

    def test_encoder_cannot_forge_model_canonical_or_delivery_values(self):
        """Fresh source/store checks alone do not bind an encoder's returned bytes."""
        module = self.consumer_module()
        original = module.bounded_json
        for surface in ("model", "canonical", "delivery"):
            with self.subTest(surface=surface):

                def operation(surface=surface):
                    analysis = self.analysis()
                    self.assertIn("12.5", analysis.render()["detailed_response"])
                    hits = []

                    def changed(body, **kwargs):
                        encoded = original(body, **kwargs)
                        value = json.loads(encoded)
                        if surface == "model" and "facts" in value:
                            value["facts"][1]["values"]["value"] = "synthetic-forged-value"
                        elif surface == "canonical" and value.get("kind") == "evidence_analysis":
                            value["detailed_response"] = "synthetic-forged-value"
                        elif (
                            surface == "delivery"
                            and value.get("kind") == "recorded_history_analysis"
                        ):
                            value["coverage"]["requested_window"] = [999, 1000]
                        else:
                            return encoded
                        hits.append(surface)
                        return original(value, **kwargs)

                    fault = None
                    with patch.object(module, "bounded_json", side_effect=changed):
                        try:
                            if surface == "model":
                                analysis.synthesis_view()
                            elif surface == "canonical":
                                analysis.render()
                            else:
                                analysis.delivery_payload()
                        except module.SourceSerializationError as error:
                            fault = error
                    self.assertEqual(hits, [surface])
                    self.assertIsInstance(fault, module.SourceSerializationError)
                    self.assertIn("12.5", analysis.render()["detailed_response"])
                    return True

                assert_literal_bool(self, self.run_boundary(operation), True)

    def test_canonical_constructor_cannot_replace_already_validated_source_text(self):
        """A valid native schema instance is not proof its text passed the gate."""
        module = self.consumer_module()
        native_constructor = module.CanonicalEvidenceAnalysisResponseV2.model_validate_json

        def operation():
            analysis = self.analysis()
            self.assertIn("12.5", analysis.render()["detailed_response"])
            hits = []

            def changed(*args, **kwargs):
                result = native_constructor(*args, **kwargs)
                result.detailed_response = "synthetic-forged-canonical"
                hits.append(True)
                return result

            fault = None
            with patch.object(
                module.CanonicalEvidenceAnalysisResponseV2,
                "model_validate_json",
                side_effect=changed,
            ):
                try:
                    analysis.render()
                except module.SourceSerializationError as error:
                    fault = error
            self.assertEqual(hits, [True])
            self.assertIsInstance(fault, module.SourceSerializationError)
            self.assertIn("12.5", analysis.render()["detailed_response"])
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_fenced_source_text_with_numbers_renders_without_relaxing_the_gate(self):
        """Only tokens from the actual source slot may close native value checks."""
        raw = "  AL-SYNTHETIC-23 and 45  "
        record = replace(self.record, sample=replace(self.record.sample, value=raw))
        adapter = FixtureHistoryAdapter.from_records(
            source=self.identity,
            mapping=self.mapping,
            coverage=self.window,
            generation="fixture-generation",
            records=(record,),
        )

        def operation():
            analysis = self.analysis(adapter=adapter)
            value = analysis.synthesis_view()["facts"][1]["values"]
            self.assertEqual(value["value_kind"], "fenced_text")
            self.assertIn(json.dumps(raw), value["value"])
            result = analysis.render()
            self.assertIn(json.dumps(raw), result["detailed_response"])
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_nullable_blank_and_whitespace_quality_remain_distinct_source_text(self):
        """Native fencing must not strip significant source metadata whitespace."""
        from assets.ai_read import fence

        for raw in (None, "", "  ", "  synthetic quality  "):
            with self.subTest(raw=raw):
                origin = "unknown" if raw is None else "source_reported"
                record = replace(
                    self.record,
                    sample=replace(self.record.sample, quality=raw, quality_origin=origin),
                )
                adapter = FixtureHistoryAdapter.from_records(
                    source=self.identity,
                    mapping=self.mapping,
                    coverage=self.window,
                    generation="fixture-generation",
                    records=(record,),
                )

                def operation(adapter=adapter, raw=raw, origin=origin):
                    analysis = self.analysis(adapter=adapter)
                    values = analysis.synthesis_view()["facts"][1]["values"]
                    expected = "not recorded" if raw is None else fence(json.dumps(raw))
                    self.assertEqual(values["quality"], expected)
                    self.assertEqual(values["quality_origin"], origin)
                    self.assertIn(expected, analysis.render()["detailed_response"])
                    return True

                assert_literal_bool(self, self.run_boundary(operation), True)

    def test_fact_numeric_sign_cannot_be_relabelled_after_source_validation(self):
        """Equal floating-point zeroes are not the same original source scalar."""
        module = self.consumer_module()
        for raw in (0.0, -0.0):
            with self.subTest(raw=repr(raw)):
                record = replace(self.record, sample=replace(self.record.sample, value=raw))
                adapter = FixtureHistoryAdapter.from_records(
                    source=self.identity,
                    mapping=self.mapping,
                    coverage=self.window,
                    generation="fixture-generation",
                    records=(record,),
                )

                def operation(adapter=adapter, raw=raw):
                    analysis = self.analysis(adapter=adapter)
                    self.assertEqual(
                        analysis.synthesis_view()["facts"][1]["values"]["value"], str(raw)
                    )
                    original = analysis._store.facts["fact_2"]
                    changed = dict(original.values)
                    changed["value"] = replace(changed["value"], raw=-raw)
                    analysis._store.facts["fact_2"] = replace(original, values=changed)
                    try:
                        for surface in ("synthesis_view", "render", "delivery_payload"):
                            with (
                                self.subTest(surface=surface),
                                self.assertRaises(module.SourceSerializationError),
                            ):
                                getattr(analysis, surface)()
                    finally:
                        analysis._store.facts["fact_2"] = original
                    self.assertEqual(
                        analysis.synthesis_view()["facts"][1]["values"]["value"], str(raw)
                    )
                    return True

                assert_literal_bool(self, self.run_boundary(operation), True)

    def test_fact_identity_must_be_literal_even_when_comparison_is_spoofed(self):
        """Native fact IDs must bind to the source-derived store coordinate."""
        module = self.consumer_module()

        class NonLiteral(str):
            def __eq__(self, _other):
                return True

            def __ne__(self, _other):
                return False

            __hash__ = str.__hash__

        def operation():
            analysis = self.analysis()
            self.assertIn("12.5", analysis.render()["detailed_response"])
            original = analysis._store.facts["fact_2"]
            for label in ("fact_2", "foreign-fact"):
                with self.subTest(label=label):
                    analysis._store.facts["fact_2"] = replace(original, fact_id=NonLiteral(label))
                    try:
                        with self.assertRaises(module.SourceSerializationError):
                            analysis.synthesis_view()
                    finally:
                        analysis._store.facts["fact_2"] = original
            self.assertIn("12.5", analysis.render()["detailed_response"])
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_native_evidence_mutations_are_rejected_on_every_synchronous_surface(self):
        """Actual source-derived facts, calculations and coverage stay bound."""
        module = self.consumer_module()

        def operation():
            analysis = self.analysis()
            store = analysis._store
            facts, calculations = store.facts, store.calculations
            fact, calculation = facts["fact_2"], calculations["calc_1"]
            changes = []
            for field, value in (
                ("fact_id", "foreign_fact"),
                ("kind", "coverage"),
                ("source_class", "native_health"),
                ("source_id", "foreign_source"),
                ("source_revision", "foreign_revision"),
                ("locator", {"field": "foreign_field"}),
                ("retrieval_id", "foreign_retrieval"),
                ("as_of", "1970-01-01T00:00:00Z"),
                ("authorization_class", "foreign_authority"),
                ("entity_refs", ("foreign_entity",)),
                ("machine_id", self.machine.pk + 1),
                ("controlled", True),
            ):
                changes.append((
                    "fact_" + field,
                    "facts",
                    {**facts, "fact_2": replace(fact, **{field: value})},
                ))
            for field, value in (
                ("value_type", "text"),
                ("raw", 99.5),
                ("unit", "foreign_unit"),
            ):
                values = dict(fact.values)
                values["value"] = replace(values["value"], **{field: value})
                changes.append((
                    "measurement_" + field,
                    "facts",
                    {**facts, "fact_2": replace(fact, values=values)},
                ))
            for field in ("observed_at", "ingested_at", "quality_origin", "unit_origin"):
                values = dict(fact.values)
                values[field] = replace(values[field], raw="foreign_source_value")
                changes.append((
                    field,
                    "facts",
                    {**facts, "fact_2": replace(fact, values=values)},
                ))
            for field, value in (
                ("calculation_id", "foreign_calculation"),
                ("operation", "sum"),
                ("input_refs", ("fact_1",)),
                ("evidence_set_handle", "foreign_evidence_set"),
                ("complete_population", True),
                ("values", {"count": module.FactValue("int", 99)}),
            ):
                changes.append((
                    "calculation_" + field,
                    "calculations",
                    {"calc_1": replace(calculation, **{field: value})},
                ))
            for field, value in (
                ("requested_window", [0, 4]),
                ("complete_intervals", []),
                ("source_window_complete", False),
                ("returned_count", 99),
                ("complete_population", True),
                ("revision_complete", True),
            ):
                changes.append((
                    "coverage_" + field,
                    "_primary_coverage",
                    {**store.coverage_meta(), field: value},
                ))
            changes.extend((
                ("missing_fact", "facts", {"fact_1": facts["fact_1"]}),
                ("extra_fact", "facts", {**facts, "fact_3": fact}),
                ("missing_calculation", "calculations", {}),
                ("extra_calculation", "calculations", {**calculations, "calc_2": calculation}),
                ("foreign_evidence", "evidence_sets", {"foreign": object()}),
                ("private_envelope", "envelopes", [{"private": "synthetic-private-envelope"}]),
            ))
            surfaces = ("synthesis_view", "render", "delivery_payload")
            activated = []
            for name, attribute, changed in changes:
                for surface in surfaces:
                    with (
                        self.subTest(mutation=name, surface=surface),
                        patch.object(store, attribute, changed),
                        self.assertRaises(module.SourceSerializationError),
                    ):
                        activated.append((name, surface))
                        getattr(analysis, surface)()
                    self.assertEqual(
                        analysis.synthesis_view()["facts"][1]["values"]["value"], "12.5"
                    )
            self.assertEqual(len(activated), len(changes) * len(surfaces))
            self.assertEqual(len(set(activated)), len(activated))
            return True

        # Keep this long binding matrix inside one genuinely authenticated
        # invocation without the unrelated 60-second signed-fixture expiry.
        # Dedicated boundary controls retain signed-subject expiry coverage.
        assert_literal_bool(self, self.run_boundary(operation, method="session"), True)

    def test_native_calculation_projection_is_allowlisted_and_content_bound(self):
        """Extra native metadata is withheld; changed admitted values are refused."""
        module = self.consumer_module()
        native_view = module.EvidenceStore.synthesis_view

        def operation():
            analysis = self.analysis()

            def expanded(store, **kwargs):
                value = native_view(store, **kwargs)
                value["calculations"][0]["private_context"] = "synthetic-private-calculation"
                value["calculations"][0]["values"]["private_value"] = "synthetic-private-slot"
                value["coverage"]["private_context"] = "synthetic-private-coverage"
                return value

            with patch.object(module.EvidenceStore, "synthesis_view", expanded):
                value = analysis.synthesis_view()
            self.assertNotIn("synthetic-private", json.dumps(value))
            self.assertEqual(value["calculations"][0]["values"], {"count": "1"})
            for target in ("fact", "calculation"):
                with self.subTest(target=target):
                    hits = []

                    def forged(store, target=target, hits=hits, **kwargs):
                        value = native_view(store, **kwargs)
                        if target == "fact":
                            value["facts"][1]["source_id"] = "foreign_source"
                        else:
                            value["calculations"][0]["values"]["count"] = "99"
                        hits.append(target)
                        return value

                    with (
                        patch.object(module.EvidenceStore, "synthesis_view", forged),
                        self.assertRaises(module.SourceSerializationError),
                    ):
                        analysis.synthesis_view()
                    self.assertEqual(hits, [target])
            self.assertEqual(analysis.synthesis_view()["calculations"][0]["values"], {"count": "1"})
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_retained_analysis_cannot_cross_authentication_invocations_or_actors(self):
        """Neither a same-user cache hit nor another granted user grants reuse."""
        from assets.models import ClientScopeGrant

        module = self.consumer_module()
        retained = self.run_boundary(self.analysis)
        surfaces = ("synthesis_view", "render", "delivery_payload")
        for surface in surfaces:
            with (
                self.subTest(outside_invocation=surface),
                self.assertRaises(module.HistoryAuthorizationError),
            ):
                getattr(retained, surface)()
        original_actor = self.actor
        other = type(original_actor).objects.create_user(username="s6-consumer-other-actor")
        other.groups.add(*original_actor.groups.all())
        ClientScopeGrant.objects.create(user=other, client=self.tenant)
        for actor in (original_actor, other):
            with self.subTest(actor=actor.pk):
                self.actor = actor
                try:

                    def operation():
                        fresh = self.analysis()
                        self.assertIn("12.5", fresh.render()["detailed_response"])
                        for surface in surfaces:
                            with (
                                self.subTest(surface=surface),
                                self.assertRaises(module.HistoryAuthorizationError),
                            ):
                                getattr(retained, surface)()
                        detached = fresh.synthesis_view()
                        detached["facts"][1]["values"]["value"] = "synthetic-client-mutation"
                        packet = fresh.delivery_payload()
                        packet["coverage"]["requested_window"][0] = 99
                        packet["canonical_response"]["detailed_response"] = (
                            "synthetic-client-mutation"
                        )
                        self.assertEqual(
                            fresh.synthesis_view()["facts"][1]["values"]["value"], "12.5"
                        )
                        self.assertEqual(
                            fresh.delivery_payload()["coverage"]["requested_window"], [-1, 4]
                        )
                        return True

                    assert_literal_bool(self, self.run_boundary(operation), True)
                finally:
                    self.actor = original_actor

    def test_changed_fixture_invalidates_retained_analysis_without_cache_or_fallback(self):
        """Fresh reads dispatch again; a retained source result is not authority."""
        module = self.consumer_module()
        changed_record = replace(
            self.record,
            sample=replace(self.record.sample, source_generation="fixture-generation-changed"),
        )
        fresh_adapter = FixtureHistoryAdapter.from_records(
            source=self.identity,
            mapping=self.mapping,
            coverage=self.window,
            generation="fixture-generation-changed",
            records=(changed_record,),
        )

        def operation():
            with patch.object(
                module,
                "read_recorded_history_for_current_ai",
                wraps=module.read_recorded_history_for_current_ai,
            ) as source_read:
                analysis = self.analysis()
                self.assertEqual(
                    analysis.synthesis_view()["calculations"][0]["values"], {"count": "1"}
                )
                generation = self.adapter.generation
                object.__setattr__(self.adapter, "generation", "fixture-generation-changed")
                try:
                    for surface in ("synthesis_view", "render", "delivery_payload"):
                        with (
                            self.subTest(surface=surface),
                            self.assertRaises(module.HistoryAuthorizationError),
                        ):
                            getattr(analysis, surface)()
                    self.assertEqual(source_read.call_count, 1)
                    # A corrupted adapter is not a qualified new snapshot.
                    self.assertEqual(self.analysis().state, "unsupported")
                    fresh = self.analysis(adapter=fresh_adapter)
                    self.assertEqual(fresh.synthesis_view()["facts"][1]["values"]["value"], "12.5")
                    self.assertEqual(source_read.call_count, 3)
                    self.assertNotEqual(
                        fresh._store.facts["fact_2"].source_revision,
                        analysis._store.facts["fact_2"].source_revision,
                    )
                finally:
                    object.__setattr__(self.adapter, "generation", generation)
                self.assertIn("12.5", analysis.render()["detailed_response"])
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_consumer_capability_bounds_refuse_without_empty_success_or_fallback(self):
        """A qualified source can still exceed the narrower analysis contract."""
        module = self.consumer_module()
        self.assertEqual(module.MAX_ANALYSIS_OBSERVATIONS, 16)
        records = [
            (replace(self.record, sample=replace(self.record.sample, value=value)),)
            for value in ({"nested": [False, None]}, "x" * 513)
        ]
        records.append(
            tuple(
                replace(
                    self.record,
                    sample=replace(
                        self.record.sample,
                        source_observation_id=f"literal-event:{index}",
                        source_sequence=index,
                    ),
                )
                for index in range(17)
            )
        )
        for population in records:
            with self.subTest(population_size=len(population)):
                adapter = FixtureHistoryAdapter.from_records(
                    source=self.identity,
                    mapping=self.mapping,
                    coverage=self.window,
                    generation="fixture-generation",
                    records=population,
                )

                def operation(adapter=adapter):
                    native_read = module.read_recorded_history_for_current_ai
                    observed = []

                    def observe(**kwargs):
                        result = native_read(**kwargs)
                        observed.append(result.outcome.state)
                        return result

                    with (
                        patch.object(module, "read_recorded_history_for_current_ai", observe),
                        self.assertRaises(module.HistoryCapabilityError),
                    ):
                        self.analysis(adapter=adapter)
                    self.assertEqual(observed, ["available"])
                    self.assertEqual(self.analysis().state, "available")
                    return True

                assert_literal_bool(self, self.run_boundary(operation), True)

    def test_null_false_zero_blank_and_unknown_receipt_remain_distinct(self):
        """The native output retains scalar meaning and never invents a receipt."""
        from assets.ai_read import fence

        for raw, kind, rendered in (
            (None, "null", "not recorded"),
            (False, "boolean", "no"),
            (0, "integer", "0"),
            ("", "fenced_text", fence(json.dumps(""))),
        ):
            with self.subTest(raw=repr(raw)):
                record = replace(
                    self.record,
                    sample=replace(self.record.sample, value=raw),
                    ingested_at=None,
                    ingest_time_origin="unknown",
                )
                adapter = FixtureHistoryAdapter.from_records(
                    source=self.identity,
                    mapping=self.mapping,
                    coverage=self.window,
                    generation="fixture-generation",
                    records=(record,),
                )

                def operation(adapter=adapter, kind=kind, rendered=rendered):
                    analysis = self.analysis(adapter=adapter)
                    values = analysis.synthesis_view()["facts"][1]["values"]
                    self.assertEqual(values["value"], rendered)
                    self.assertEqual(values["value_kind"], kind)
                    self.assertEqual(
                        values["observed_at"], self.record.sample.observed_at.isoformat()
                    )
                    self.assertEqual(values["ingested_at"], "not recorded")
                    self.assertEqual(values["ingest_time_origin"], "unknown")
                    self.assertEqual(values["time_origin"], "source_reported")
                    packet = analysis.delivery_payload()
                    text = packet["canonical_response"]["detailed_response"]
                    self.assertIn(rendered, text)
                    self.assertIn(self.record.sample.observed_at.isoformat(), text)
                    self.assertNotIn(self.record.ingested_at.isoformat(), text)
                    return True

                assert_literal_bool(self, self.run_boundary(operation), True)

    def test_encoder_faults_recheck_binding_and_preserve_process_control(self):
        """Projection faults are content-free, never successful empty carriers."""
        from ai.core import auth

        module = self.consumer_module()
        original = module.bounded_json
        for surface in ("synthesis_view", "render", "delivery_payload"):
            for mode in ("none", "ordinary_error", "memory_error"):
                for revoke in (False, True):
                    with self.subTest(surface=surface, mode=mode, revoke=revoke):

                        def operation(surface=surface, mode=mode, revoke=revoke):
                            analysis = self.analysis()
                            hits, tokens = [], []
                            fault = (
                                MemoryError()
                                if mode == "memory_error"
                                else RuntimeError("synthetic-private-projection-fault")
                            )

                            def broken(body, **kwargs):
                                encoded = original(body, **kwargs)
                                matches = (
                                    (surface == "synthesis_view" and "facts" in body)
                                    or (
                                        surface == "render"
                                        and body.get("kind") == "evidence_analysis"
                                    )
                                    or (
                                        surface == "delivery_payload"
                                        and body.get("kind") == "recorded_history_analysis"
                                    )
                                )
                                if not matches:
                                    return encoded
                                hits.append(surface)
                                if revoke:
                                    tokens.append(
                                        auth.principal_context.set(
                                            replace(auth.get_current_principal())
                                        )
                                    )
                                if mode == "none":
                                    return None
                                raise fault

                            expected = (
                                MemoryError
                                if mode == "memory_error"
                                else module.HistoryAuthorizationError
                                if revoke
                                else module.SourceSerializationError
                            )
                            try:
                                with (
                                    patch.object(module, "bounded_json", side_effect=broken),
                                    self.assertRaises(expected) as raised,
                                ):
                                    getattr(analysis, surface)()
                                self.assertEqual(hits, [surface])
                                self.assertNotIn("synthetic-private", str(raised.exception))
                                if mode == "memory_error":
                                    self.assertIs(raised.exception, fault)
                            finally:
                                for token in reversed(tokens):
                                    auth.principal_context.reset(token)
                            self.assertEqual(
                                analysis.synthesis_view()["facts"][1]["values"]["value"], "12.5"
                            )
                            return True

                        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_native_emitter_faults_withhold_later_channels_and_keep_cancellation(self):
        """Failure after authorized earlier events cannot release a later yield."""
        from asyncio import CancelledError

        from ai.core.streaming import EventCollector, EventType, InMemoryEventEmitter, RunContext
        from assets.models import DictionaryPoint

        module = self.consumer_module()
        for mode, revoke in (
            ("ordinary_error", False),
            ("ordinary_error", True),
            ("memory_error", False),
            ("cancelled", False),
        ):
            with self.subTest(mode=mode, revoke=revoke):

                async def operation(mode=mode, revoke=revoke):
                    analysis = await sync_to_async(self.analysis, thread_sensitive=True)()
                    collector = EventCollector()
                    hits, chunks = [], []
                    fault = (
                        MemoryError()
                        if mode == "memory_error"
                        else CancelledError()
                        if mode == "cancelled"
                        else RuntimeError("synthetic-private-emitter-fault")
                    )

                    class FaultingEmitter(InMemoryEventEmitter):
                        async def emit(_self, event):
                            if event.event_type is not EventType.TEXT_MESSAGE_CONTENT:
                                await super().emit(event)
                                return
                            hits.append(event.event_type)
                            if revoke:
                                await sync_to_async(
                                    DictionaryPoint.objects.filter(pk=self.point.pk).update,
                                    thread_sensitive=True,
                                )(status="draft")
                            raise fault

                    emitter = FaultingEmitter()
                    unsubscribe = await emitter.subscribe(collector)
                    expected = (
                        type(fault)
                        if mode in ("memory_error", "cancelled")
                        else module.HistoryAuthorizationError
                        if revoke
                        else module.SourceSerializationError
                    )
                    try:
                        with self.assertRaises(expected) as raised:
                            async for chunk in analysis.run_stream(RunContext(emitter=emitter)):
                                chunks.append(chunk)
                        self.assertEqual(hits, [EventType.TEXT_MESSAGE_CONTENT])
                        self.assertEqual(chunks, [])
                        self.assertEqual(
                            [event.event_type for event in collector.events],
                            [EventType.STATE_DELTA, EventType.TEXT_MESSAGE_START],
                        )
                        self.assertNotIn("synthetic-private", str(raised.exception))
                        if mode in ("memory_error", "cancelled"):
                            self.assertIs(raised.exception, fault)
                    finally:
                        await sync_to_async(
                            DictionaryPoint.objects.filter(pk=self.point.pk).update,
                            thread_sensitive=True,
                        )(status="approved")
                        unsubscribe()
                    restored = await sync_to_async(analysis.render, thread_sensitive=True)()
                    self.assertIn("12.5", restored["detailed_response"])
                    return True

                assert_literal_bool(self, self.run_boundary(operation), True)

    def test_native_projection_expansion_cannot_expose_new_private_fields(self):
        """Native view growth is not implicit approval of new AI-visible values."""
        module = self.consumer_module()

        def operation():
            analysis = self.analysis()
            self.assertEqual(analysis.synthesis_view()["facts"][1]["values"]["value"], "12.5")
            original = module.EvidenceStore.synthesis_view

            def expanded(store, **kwargs):
                view = original(store, **kwargs)
                view["facts"][1]["values"]["private_native_extra"] = "synthetic-private-native"
                view["facts"][1]["private_context"] = "synthetic-private-context"
                return view

            with patch.object(module.EvidenceStore, "synthesis_view", expanded):
                view = analysis.synthesis_view()
            self.assertNotIn("synthetic-private-native", json.dumps(view))
            self.assertNotIn("synthetic-private-context", json.dumps(view))
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)
