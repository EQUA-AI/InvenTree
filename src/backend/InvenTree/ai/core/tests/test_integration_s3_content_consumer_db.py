"""S3 native analysis controls; historical positives explicitly simulate proof."""

from __future__ import annotations

import importlib.util
from importlib import import_module
from unittest.mock import patch

from ai.core.tests import test_integration_s3_content_boundary_db as fixtures
from assets.tests.integration_attribution_test_support import assume_synthetic_attribution
from django.test import TestCase
from integration_state.tests.assertions import assert_literal_bool


@assume_synthetic_attribution
class SimulatedProofS3ConsumerTests(fixtures.S3AIRequestFixture, TestCase):
    """Real ASGI/native source composition after explicitly simulated admission."""

    def consumer_module(self):
        """Resolve the actual newly allocated internal module, not a fallback."""
        name = "ai.core.integrations.integration_s3_content"
        self.assertIsNotNone(importlib.util.find_spec(name), "The native S3 consumer is required")
        return import_module(name)

    def analysis(self, **changes):
        """Use only actual current-AI intake and server-selected source arguments."""
        arguments = {
            "environment": "s3-test",
            "operation": "work.core",
            "source_pk": self.work.pk,
        }
        arguments.update(changes)
        return self.consumer_module().read_source_analysis(**arguments)

    def test_work_fields_reach_native_facts_projection_render_and_all_checks(self):
        """A native single-record observation is not whole-population completeness."""
        module = self.consumer_module()

        def operation():
            analysis = self.analysis()
            self.assertEqual(analysis.state, "available")
            view = analysis.synthesis_view()
            fields = {row["values"]["field_name"]: row for row in view["facts"]}
            self.assertEqual(len(fields), 20)
            self.assertIn(self.work.title, fields["title"]["values"]["field_value"])
            self.assertEqual(
                fields["lifecycle_version"]["values"]["field_value"],
                str(self.work.lifecycle_version),
            )
            self.assertEqual(view["calculations"], [])
            assert_literal_bool(self, view["coverage"]["complete_population"], False)
            assert_literal_bool(self, view["coverage"]["revision_complete"], False)
            verdicts = []
            native = module.validate_analysis

            def observe(**kwargs):
                result = native(**kwargs)
                verdicts.append(result)
                return result

            with patch.object(module, "validate_analysis", side_effect=observe):
                response = analysis.render()
            self.assertEqual(len(verdicts), 1)
            self.assertEqual(
                {row.check for row in verdicts[0].results}, {f"C{i:02d}" for i in range(1, 15)}
            )
            self.assertIs(verdicts[0].outcome, module.CheckOutcome.PASS)
            self.assertIn(self.work.title, response["detailed_response"])
            self.assertFalse(response["speak"])
            self.assertEqual(response["recommended_actions"], [])
            self.assertEqual(response["response_version"], 2)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_other_five_operations_preserve_native_class_owner_bytes_and_clocks(self):
        """Actual producer records retain direct-owner and file/link distinctions."""
        import hashlib
        import json
        from base64 import b64decode

        from common.models import Attachment

        self.prepare_operations()
        self.blob = b"  Synthetic source note: CODE-27\r\n"
        with self.attachment.attachment.storage.open(
            self.attachment.attachment.name, "wb"
        ) as handle:
            handle.write(self.blob)
        Attachment.objects.filter(pk=self.attachment.pk).update(file_size=len(self.blob))
        cases = (
            ("maintenance.direct", self.maintenance.pk, None),
            ("work_document.metadata", self.attachment.pk, "workorder"),
            ("machine_document.metadata", self.attachment.pk, "assetmachine"),
            ("work_document.content", self.attachment.pk, "workorder"),
            ("machine_document.content", self.attachment.pk, "assetmachine"),
        )
        for name, pk, owner in cases:
            with self.subTest(operation=name):
                self.select_owner(owner)

                def operation(name=name, pk=pk, owner=owner):
                    analysis = self.analysis(
                        operation=name,
                        source_pk=pk,
                        **({"max_bytes": 128} if name.endswith(".content") else {}),
                    )
                    view = analysis.synthesis_view()
                    fields = {row["values"]["field_name"]: row for row in view["facts"]}
                    if name == "maintenance.direct":
                        self.assertEqual(len(fields), 5)
                        self.assertEqual(fields["date"]["values"]["field_value"], "2020-01-02")
                        self.assertIn(
                            self.maintenance.summary, fields["summary"]["values"]["field_value"]
                        )
                    else:
                        self.assertTrue(
                            all(row["source_class"] == "native_attachment" for row in view["facts"])
                        )
                        self.assertTrue(all(row["controlled"] is False for row in view["facts"]))
                        domain = "work" if owner == "workorder" else "machine"
                        self.assertIn(domain, fields["owner_domain"]["values"]["field_value"])
                        self.assertIn(
                            self.attachment.link, fields["external_link"]["values"]["field_value"]
                        )
                        if name.endswith(".content"):
                            body = json.loads(analysis._source.outcome.payload)["record"]
                            self.assertEqual(
                                b64decode(body["content"]["data"], validate=True), self.blob
                            )
                            self.assertIn(
                                hashlib.sha256(self.blob).hexdigest(),
                                fields["content_sha256"]["values"]["field_value"],
                            )
                            self.assertIn(
                                "CODE-27", fields["content_text"]["values"]["field_value"]
                            )
                    self.assertEqual(analysis.render()["response_state"], "complete")
                    return True

                assert_literal_bool(self, self.run_boundary(operation), True)

    def test_native_events_and_separate_yield_share_the_validated_source_packet(self):
        """Internal delivery is source-record analysis, not UI population metadata."""
        from ai.core.streaming import EventCollector, EventType, InMemoryEventEmitter, RunContext
        from asgiref.sync import sync_to_async

        async def operation():
            analysis = await sync_to_async(self.analysis, thread_sensitive=True)()
            self.assertTrue(callable(getattr(analysis, "run_stream", None)))
            emitter = InMemoryEventEmitter()
            collector = EventCollector("synthetic-s3-thread")
            unsubscribe = await emitter.subscribe(collector)
            try:
                context = RunContext(
                    emitter=emitter, thread_id="synthetic-s3-thread", run_id="synthetic-s3-run"
                )
                chunks = [chunk async for chunk in analysis.run_stream(context)]
                self.assertEqual(len(chunks), 1)
                self.assertEqual(
                    [event.event_type for event in collector.events],
                    [
                        EventType.STATE_DELTA,
                        EventType.TEXT_MESSAGE_START,
                        EventType.TEXT_MESSAGE_CONTENT,
                        EventType.TEXT_MESSAGE_END,
                    ],
                )
                packet = collector.events[0].data
                self.assertEqual(packet["kind"], "source_record_analysis")
                self.assertEqual(packet["schema_version"], "aimms.s3.source-analysis.v1")
                assert_literal_bool(self, packet["coverage"]["complete_population"], False)
                self.assertEqual(packet["canonical_response"]["detailed_response"], chunks[0])
                self.assertEqual(collector.events[2].data["delta"], chunks[0])
                self.assertIn(self.work.title, chunks[0])
                return True
            finally:
                unsubscribe()

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_fact_constructor_faults_refresh_authority_and_hide_private_errors(self):
        """Fallible native construction is followed by source/auth checks too."""
        from assets.models import ClientScopeGrant

        module = self.consumer_module()
        for revoke in (False, True):
            with self.subTest(revoke=revoke):

                def operation(revoke=revoke):
                    self.assertEqual(self.analysis().state, "available")
                    reached = []

                    def fail(*args, **kwargs):
                        reached.append(True)
                        if revoke:
                            self.grant.delete()
                        raise ValueError("synthetic-private-fact-constructor")

                    expected = (
                        module.SourceAuthorizationError
                        if revoke
                        else module.SourceSerializationError
                    )
                    try:
                        with (
                            patch.object(module, "FactValue", side_effect=fail),
                            self.assertRaises(expected) as caught,
                        ):
                            self.analysis()
                        self.assertNotIn("synthetic-private", str(caught.exception))
                        self.assertEqual(reached, [True])
                    finally:
                        if revoke:
                            self.grant = ClientScopeGrant.objects.create(
                                user=self.actor, client=self.tenant
                            )
                    self.assertEqual(self.analysis().state, "available")
                    return True

                assert_literal_bool(self, self.run_boundary(operation), True)

    def test_retained_analysis_cannot_cross_invocation_or_actor(self):
        """Neither an old same-user fact nor a newly granted actor permits reuse."""
        from assets.models import ClientScopeGrant

        module = self.consumer_module()
        retained = self.run_boundary(self.analysis)
        for surface in ("synthesis_view", "render", "delivery_payload"):
            with self.subTest(surface=surface), self.assertRaises(module.SourceAuthorizationError):
                getattr(retained, surface)()

        def operation():
            with self.assertRaises(module.SourceAuthorizationError):
                retained.synthesis_view()
            self.assertEqual(self.analysis().state, "available")
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)
        original = self.actor
        other = type(original).objects.create_user(username="s3-consumer-other")
        other.groups.add(*original.groups.all())
        ClientScopeGrant.objects.create(user=other, client=self.tenant)
        self.actor = other
        try:
            assert_literal_bool(self, self.run_boundary(operation, method="signed"), True)
        finally:
            self.actor = original

    def test_fact_mutations_are_withheld_on_every_synchronous_surface(self):
        """Mutable native working evidence is never trusted as retained authority."""
        from dataclasses import replace

        module = self.consumer_module()

        class Equal(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                return True

        def operation():
            analysis = self.analysis()
            store = analysis._store
            fact = store.facts["fact_1"]
            changes = [
                ("facts", {**store.facts, "fact_1": replace(fact, source_revision="foreign")}),
                ("facts", {**store.facts, "fact_1": replace(fact, fact_id=Equal("foreign"))}),
                ("facts", {**store.facts, "fact_1": replace(fact, controlled=True)}),
                (
                    "facts",
                    {
                        **store.facts,
                        "fact_1": replace(
                            fact,
                            values={
                                **fact.values,
                                "field_value": module.FactValue("text", "forged"),
                            },
                        ),
                    },
                ),
                ("facts", {}),
                ("calculations", {"foreign": object()}),
                ("envelopes", [{"private": "synthetic-private-envelope"}]),
                ("_primary_coverage", {**store.coverage_meta(), "complete_population": True}),
            ]
            activated = []
            for index, (attribute, changed) in enumerate(changes):
                for surface in ("synthesis_view", "render", "delivery_payload"):
                    with self.subTest(change=index, surface=surface):
                        with (
                            patch.object(store, attribute, changed),
                            self.assertRaises(module.SourceSerializationError),
                        ):
                            activated.append((index, surface))
                            getattr(analysis, surface)()
                        self.assertTrue(analysis.synthesis_view()["facts"])
            self.assertEqual(len(activated), len(changes) * 3)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_model_growth_is_withheld_and_changed_admitted_values_refuse(self):
        """Native DTO additions cannot implicitly widen the model allowlist."""
        import json

        module = self.consumer_module()
        native = module.EvidenceStore.synthesis_view

        def operation():
            analysis = self.analysis()

            def expanded(store, **kwargs):
                result = native(store, **kwargs)
                result["facts"][0]["private_context"] = "synthetic-private-context"
                result["facts"][0]["values"]["private_field"] = "synthetic-private-value"
                result["coverage"]["private_field"] = "synthetic-private-coverage"
                return result

            with patch.object(module.EvidenceStore, "synthesis_view", expanded):
                self.assertNotIn("synthetic-private", json.dumps(analysis.synthesis_view()))
            reached = []

            def forged(store, **kwargs):
                result = native(store, **kwargs)
                result["facts"][0]["values"]["field_value"] = "forged"
                reached.append(True)
                return result

            with (
                patch.object(module.EvidenceStore, "synthesis_view", forged),
                self.assertRaises(module.SourceSerializationError),
            ):
                analysis.synthesis_view()
            self.assertEqual(reached, [True])
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_encoder_canonical_and_rendered_content_are_independently_bound(self):
        """Current source permission does not certify forged native output."""
        from dataclasses import replace

        module = self.consumer_module()
        native_canonical = module.CanonicalEvidenceAnalysisResponseV2.model_validate_json
        native_render = module.render_answer

        def operation():
            analysis = self.analysis()
            for surface in ("synthesis_view", "render", "delivery_payload"):
                with (
                    self.subTest(encoder_surface=surface),
                    patch.object(module, "_encode", return_value=b"{}"),
                    self.assertRaises(module.SourceSerializationError),
                ):
                    getattr(analysis, surface)()

            def canonical(data, **kwargs):
                value = native_canonical(data, **kwargs)
                return value.model_copy(update={"detailed_response": "forged native answer"})

            with (
                patch.object(
                    module.CanonicalEvidenceAnalysisResponseV2,
                    "model_validate_json",
                    side_effect=canonical,
                ),
                self.assertRaises(module.SourceSerializationError),
            ):
                analysis.render()

            def render(*args, **kwargs):
                value = native_render(*args, **kwargs)
                return replace(value, detailed_response=value.detailed_response + " INVENTED-777")

            with (
                patch.object(module, "render_answer", side_effect=render),
                self.assertRaises(module.SourceSerializationError),
            ):
                analysis.render()
            self.assertIn(self.work.title, analysis.render()["detailed_response"])
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_scalar_null_blank_whitespace_false_zero_and_source_clocks_stay_distinct(self):
        """The selected primitive retains its type as well as its visible value."""
        from tasks.models import WorkOrder

        WorkOrder.objects.filter(pk=self.work.pk).update(
            reference="",
            description="  CODE-27\n ",
            is_active=False,
            estimated_minutes=0,
            due_date=None,
        )

        def operation():
            analysis = self.analysis()
            values = {
                row["values"]["field_name"]: row["values"]
                for row in analysis.synthesis_view()["facts"]
            }
            self.assertEqual(values["reference"]["field_type"], "fenced_text")
            self.assertIn('""', values["reference"]["field_value"])
            self.assertIn('"  CODE-27\\n "', values["description"]["field_value"])
            self.assertEqual(values["is_active"]["field_type"], "boolean")
            self.assertEqual(values["is_active"]["field_value"], "no")
            self.assertEqual(values["estimated_minutes"]["field_type"], "integer")
            self.assertEqual(values["estimated_minutes"]["field_value"], "0")
            self.assertEqual(values["due_date"]["field_type"], "null")
            self.assertEqual(values["due_date"]["field_value"], "not recorded")
            self.assertEqual(values["created_at"]["field_value"], self.work.created_at.isoformat())
            self.assertIn("CODE-27", analysis.render()["detailed_response"])
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_binary_and_oversized_text_refuse_without_empty_success_or_fallback(self):
        """Literal UTF-8 projection is not arbitrary document extraction."""
        from tasks.models import WorkOrder

        module = self.consumer_module()
        self.prepare_operations()
        self.select_owner("workorder")

        def operation():
            with self.assertRaises(module.SourceCapabilityError):
                self.analysis(
                    operation="work_document.content", source_pk=self.attachment.pk, max_bytes=128
                )
            WorkOrder.objects.filter(pk=self.work.pk).update(description="x" * 513)
            try:
                with self.assertRaises(module.SourceCapabilityError):
                    self.analysis()
            finally:
                WorkOrder.objects.filter(pk=self.work.pk).update(description=self.work.description)
            unknown = self.analysis(operation="unsupported.source")
            self.assertEqual(unknown.state, "unsupported")
            with self.assertRaises(module.SourceCapabilityError):
                unknown.synthesis_view()
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_source_catalog_is_context_local_and_preserves_recorded_history_overlay(self):
        """Both native opt-ins nest without leaking registrations or weakening literals."""
        from contextvars import Context

        from ai.core.analysis import renderer
        from ai.core.tests.test_analysis_renderer import (
            test_template_literals_contain_no_digits_or_code_shaped_tokens,
        )

        catalog = renderer.RENDER_TEMPLATES
        ordinary = set(catalog)
        with renderer.source_record_templates():
            self.assertIn("analysis.source_record_field", catalog)
            self.assertNotIn("analysis.recorded_history_sample", catalog)
            self.assertEqual(Context().run(lambda: set(catalog)), ordinary)
            with renderer.recorded_history_templates():
                self.assertIn("analysis.recorded_history_sample", catalog)
                self.assertIn("analysis.source_record_field", catalog)
                self.assertEqual(set(catalog.copy()), set(dict(catalog)))
                test_template_literals_contain_no_digits_or_code_shaped_tokens()
        self.assertEqual(set(catalog), ordinary)

    def test_unsupported_explicit_date_and_class_selections_refuse_before_source_dispatch(self):
        """Direct fields cannot invent an omitted historical machine relationship."""
        from dataclasses import replace

        from ai.core.analysis.scope import MODE_EXPLICIT, MODE_LEGACY
        from ai.core.analysis.scope_context import TurnScopeContext, turn_scope_context

        module = self.consumer_module()
        explicit = TurnScopeContext(
            mode=MODE_EXPLICIT,
            machine_ids=frozenset({self.machine.pk}),
            machine_serials=frozenset(),
            date_from=None,
            date_to=None,
            source_classes=frozenset(),
            scope_hash="synthetic-s3-scope",
            scope_version=1,
            snapshot_id="synthetic-s3-snapshot",
            thread_pk=None,
            display_label="Synthetic scope",
            shadow=False,
            enforce=False,
        )
        legacy = replace(explicit, mode=MODE_LEGACY, machine_ids=frozenset())
        for scope in (
            explicit,
            replace(legacy, date_from="2020-01-01"),
            replace(legacy, source_classes=frozenset({"work_order"})),
        ):
            with self.subTest(scope=scope):
                token = turn_scope_context.set(scope)
                try:
                    with (
                        patch.object(module, "read_source_for_current_ai") as read,
                        self.assertRaises(module.SourceCapabilityError),
                    ):
                        self.analysis()
                    read.assert_not_called()
                finally:
                    turn_scope_context.reset(token)

    def test_encoder_fault_rechecks_grant_and_preserves_memory_error(self):
        """Private failures remain content-free; lost source authority wins."""
        from assets.models import ClientScopeGrant

        module = self.consumer_module()

        def operation():
            analysis = self.analysis()
            fatal = MemoryError("synthetic-process-control")
            with (
                patch.object(module, "_encode", side_effect=fatal),
                self.assertRaises(MemoryError) as caught,
            ):
                analysis.synthesis_view()
            self.assertIs(caught.exception, fatal)
            with (
                patch.object(module, "FactValue", side_effect=fatal),
                self.assertRaises(MemoryError) as caught,
            ):
                self.analysis()
            self.assertIs(caught.exception, fatal)
            for revoke in (False, True):
                with self.subTest(revoke=revoke):

                    def failed(value, revoke=revoke):
                        if revoke:
                            self.grant.delete()
                        raise ValueError("synthetic-private-encoder")

                    expected = (
                        module.SourceAuthorizationError
                        if revoke
                        else module.SourceSerializationError
                    )
                    try:
                        with (
                            patch.object(module, "_encode", side_effect=failed),
                            self.assertRaises(expected) as caught,
                        ):
                            analysis.synthesis_view()
                        self.assertNotIn("synthetic-private", str(caught.exception))
                    finally:
                        if revoke:
                            self.grant = ClientScopeGrant.objects.create(
                                user=self.actor, client=self.tenant
                            )
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_native_event_content_substitution_and_process_control_are_not_swallowed(self):
        """A valid event schema cannot certify a changed source packet."""
        import asyncio

        from ai.core.streaming import EventCollector, InMemoryEventEmitter, RunContext
        from asgiref.sync import sync_to_async

        module = self.consumer_module()
        native = module.AGUIEvent

        async def operation():
            analysis = await sync_to_async(self.analysis, thread_sensitive=True)()
            emitter = InMemoryEventEmitter()
            collector = EventCollector()
            unsubscribe = await emitter.subscribe(collector)
            try:

                def forged(**kwargs):
                    value = native(**kwargs)
                    value.data = {"forged": True}
                    return value

                with (
                    patch.object(module, "AGUIEvent", side_effect=forged) as constructor,
                    self.assertRaises(module.SourceSerializationError),
                ):
                    async for _chunk in analysis.run_stream(RunContext(emitter=emitter)):
                        self.fail("Forged event reached the yield channel")
                self.assertEqual(constructor.call_count, 1)
                self.assertEqual(collector.events, [])
                cancellation = asyncio.CancelledError()
                with (
                    patch.object(emitter, "emit", side_effect=cancellation),
                    self.assertRaises(asyncio.CancelledError) as caught,
                ):
                    async for _chunk in analysis.run_stream(RunContext(emitter=emitter)):
                        self.fail("Cancellation reached the yield channel")
                self.assertIs(caught.exception, cancellation)
                return True
            finally:
                unsubscribe()

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_final_await_rechecks_source_and_captured_delivery_route_before_yield(self):
        """Earlier authorized events do not authorize later rebound-route delivery."""
        from ai.core.streaming import EventCollector, EventType, InMemoryEventEmitter, RunContext
        from asgiref.sync import sync_to_async
        from assets.models import ClientScopeGrant

        module = self.consumer_module()
        for kind in ("grant", "route"):
            with self.subTest(change=kind):

                async def operation(kind=kind):
                    analysis = await sync_to_async(self.analysis, thread_sensitive=True)()
                    emitter = InMemoryEventEmitter()
                    collector = EventCollector()
                    unsubscribe = await emitter.subscribe(collector)
                    context = RunContext(emitter=emitter, thread_id="original-route")
                    native = emitter.emit
                    chunks, activated = [], []

                    async def changed(event):
                        await native(event)
                        if event.event_type is EventType.TEXT_MESSAGE_END:
                            activated.append(kind)
                            if kind == "grant":
                                await sync_to_async(self.grant.delete, thread_sensitive=True)()
                            else:
                                context.thread_id = "rebound-route"

                    try:
                        with (
                            patch.object(emitter, "emit", new=changed),
                            self.assertRaises(module.SourceAuthorizationError),
                        ):
                            async for chunk in analysis.run_stream(context):
                                chunks.append(chunk)
                        self.assertEqual(activated, [kind])
                        self.assertEqual(len(collector.events), 4)
                        self.assertEqual(chunks, [])
                        return True
                    finally:
                        unsubscribe()
                        if kind == "grant":
                            self.grant = await sync_to_async(
                                ClientScopeGrant.objects.create, thread_sensitive=True
                            )(user=self.actor, client=self.tenant)

                assert_literal_bool(self, self.run_boundary(operation), True)


class RealPolicyS3ConsumerTests(fixtures.S3AIRequestFixture, TestCase):
    """No simulated historical gate: all six real-policy reads stay withheld."""

    def test_all_real_policy_reads_refuse_before_model_or_storage_disclosure(self):
        """Native authentication/current grants never supply absent historical proof."""
        from ai.core.integrations import integration_s3_content as module

        operations = self.prepare_operations()
        names = {
            "read_work_document_metadata_json": "work_document.metadata",
            "read_machine_document_metadata_json": "machine_document.metadata",
            "read_work_document_content_json": "work_document.content",
            "read_machine_document_content_json": "machine_document.content",
            "read_work_details_json": "work.core",
            "read_maintenance_details_json": "maintenance.direct",
        }
        for name, kwargs, owner in operations:
            with self.subTest(operation=name):
                self.select_owner(owner)
                pk = next(
                    kwargs[key]
                    for key in ("work_pk", "record_pk", "attachment_pk")
                    if key in kwargs
                )

                def operation(name=name, pk=pk, kwargs=kwargs):
                    with (
                        patch.object(module.EvidenceStore, "add_fact") as added,
                        patch("django.core.files.storage.default_storage.open") as opened,
                    ):
                        analysis = module.read_source_analysis(
                            environment="s3-test",
                            operation=names[name],
                            source_pk=pk,
                            max_bytes=kwargs.get("max_bytes"),
                        )
                        self.assertEqual(analysis.state, "unavailable")
                        for surface in ("synthesis_view", "render", "delivery_payload"):
                            with self.assertRaises(module.SourceAuthorizationError):
                                getattr(analysis, surface)()
                    added.assert_not_called()
                    opened.assert_not_called()
                    return True

                assert_literal_bool(self, self.run_boundary(operation), True)
