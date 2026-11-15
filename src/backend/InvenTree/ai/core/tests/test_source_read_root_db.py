"""Actual authenticated root adoption; native history and authority stay real."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from ai.core.integrations import source_reads as boundary
from ai.core.streaming import EventCollector, EventType, InMemoryEventEmitter
from ai.core.tests.test_integration_s3_content_boundary_db import S3AIRequestFixture
from ai.core.tests.test_integration_s6_history_boundary_db import S6AIRequestFixture
from ai.core.workflows.root import RootWorkflow
from asgiref.sync import sync_to_async
from django.test import TestCase, override_settings
from machine_health.services.integration_history import HistoryAuthorizationError
from machine_health.services.integration_s6_adapter import FixtureHistoryAdapter
from machine_health.services.integration_s6_wire import SourceSerializationError


class RootReadFixture:
    """Fake only unused routing/model work; source, middleware and root are real."""

    def root(self):
        """Make model-free native fallback observable without external calls."""
        self.native = SimpleNamespace(
            execute=AsyncMock(
                return_value=SimpleNamespace(
                    success=True, formatted_response="Native fixture answer"
                )
            )
        )
        self.router = SimpleNamespace(
            route=AsyncMock(
                return_value=SimpleNamespace(
                    use_fast_path=False,
                    fast_path_result=None,
                    workflow_type=SimpleNamespace(name="FIXTURE"),
                    get_workflow_id=lambda: "wf8",
                )
            )
        )
        self.registry = SimpleNamespace(get_workflow=Mock(return_value=self.native))
        memory = SimpleNamespace(
            get_or_create_state=lambda *_: SimpleNamespace(increment_turn=lambda: None),
            gather_context=AsyncMock(return_value={}),
        )
        return RootWorkflow(self.router, self.registry, memory)

    async def run_root(self, *, context=None, hook=None):
        """Run the actual root emitter and generator, not a consumer stand-in."""
        settings = SimpleNamespace(
            turn_wall_clock_cap_s=30,
            turn_routing_budget_s=5,
            feature_tool_events=False,
            feature_voice_fast_path=False,
            feature_token_streaming=False,
        )
        root = self.root()
        self.emitter = InMemoryEventEmitter()
        self.collector = EventCollector()
        await self.emitter.subscribe(self.collector)
        if hook is not None:
            await self.emitter.subscribe(SimpleNamespace(handle=hook))
        selected_context = {"modality": "text", "pinned_workflow_id": "wf8"}
        selected_context.update(context or {})
        with patch("ai.core.workflows.root.get_settings", return_value=settings):
            return [
                chunk
                async for chunk in root.run_stream(
                    "Read the selected recorded source",
                    thread_id="root-source-fixture",
                    user_id=str(self.actor.pk),
                    emitter=self.emitter,
                    context=selected_context,
                )
            ]

    def event_types(self):
        return [event.event_type for event in self.collector.events]


class RecordedHistoryRootTests(RootReadFixture, S6AIRequestFixture, TestCase):
    """No authorization patches: signed/session identity and native DB are real."""

    def setUp(self):
        super().setUp()
        self.record = replace(self.record, sample=replace(self.record.sample, value=12.5))
        self.adapter = FixtureHistoryAdapter.from_records(
            source=self.identity,
            mapping=self.mapping,
            coverage=self.window,
            generation="fixture-generation",
            records=(self.record,),
        )

    def selection(self, **changes):
        values = {
            "environment": "s6-test",
            "machine_pk": self.machine.pk,
            "source_pk": self.source.pk,
            "binding_pk": self.binding.pk,
            "window": self.window,
            "adapter": self.adapter,
        }
        values.update(changes)
        return boundary.HistoryReadSelection(**values)

    def read_adapter(self, select=None):
        selection = self.selection()
        return boundary.SourceReadAdapter(select=select or (lambda _request: selection))

    def test_default_off_ignores_context_enrollment_and_preserves_native_root(self):
        """Neither a prompt nor arbitrary context can install the server adapter."""
        with (
            override_settings(AIMMS_AI_SOURCE_READ_ADAPTER=None),
            patch.object(boundary, "_prepare", side_effect=AssertionError("Unexpected source")),
        ):

            async def operation():
                return await self.run_root(
                    context={"source_read": self.selection(), "source_adapter": self.read_adapter()}
                )

            self.assertEqual(self.run_boundary(operation), ["Native fixture answer"])
        self.native.execute.assert_awaited_once()
        self.assertNotIn(EventType.STATE_DELTA, self.event_types())

    def test_unsupported_selection_routes_natively_before_source_dispatch(self):
        """An explicit unsupported shape is distinct from a selected read fault."""
        selector = Mock(return_value=None)
        with (
            override_settings(AIMMS_AI_SOURCE_READ_ADAPTER=self.read_adapter(selector)),
            patch.object(boundary, "_prepare", side_effect=AssertionError("Unexpected dispatch")),
        ):
            self.assertEqual(self.run_boundary(self.run_root), ["Native fixture answer"])
        selector.assert_called_once()
        self.native.execute.assert_awaited_once()

    def test_actual_root_delivers_recorded_canonical_evidence_without_router_or_model(self):
        """Real source facts reach the existing native STATE_DELTA/text contract."""
        for method in ("signed", "session"):
            with (
                self.subTest(method=method),
                override_settings(AIMMS_AI_SOURCE_READ_ADAPTER=self.read_adapter()),
            ):
                chunks = self.run_boundary(self.run_root, method=method)
                self.assertEqual(len(chunks), 1)
                self.assertIn("12.5", chunks[0])
                packets = self.collector.get_events(EventType.STATE_DELTA)
                self.assertEqual(len(packets), 1)
                packet = packets[0].data
                self.assertEqual(packet["kind"], "recorded_history_analysis")
                self.assertEqual(packet["canonical_response"]["response_version"], 2)
                self.assertFalse(packet["canonical_response"]["speak"])
                self.assertEqual(packet["canonical_response"]["recommended_actions"], [])
                self.assertIn(EventType.RUN_FINISHED, self.event_types())
                self.router.route.assert_not_awaited()
                self.registry.get_workflow.assert_not_called()
                self.native.execute.assert_not_awaited()

    def test_selected_unsupported_capability_is_failed_without_native_fallback(self):
        """Selecting no usable adapter cannot become an empty or native success."""
        selection = self.selection(adapter=None)
        with (
            override_settings(AIMMS_AI_SOURCE_READ_ADAPTER=self.read_adapter(lambda _: selection)),
            self.assertRaises(boundary.SourceReadError),
        ):
            self.run_boundary(self.run_root)
        self.native.execute.assert_not_awaited()
        self.assertIn(EventType.ERROR, self.event_types())
        self.assertNotIn(EventType.RUN_FINISHED, self.event_types())
        self.assertNotIn(EventType.TEXT_MESSAGE_CONTENT, self.event_types())

    def test_revoked_grant_cannot_use_selected_source(self):
        """The root selector never substitutes for the source's independent grant."""
        from assets.models import ClientScopeGrant

        ClientScopeGrant.objects.filter(pk=self.grant.pk).delete()
        with (
            override_settings(AIMMS_AI_SOURCE_READ_ADAPTER=self.read_adapter()),
            self.assertRaises(boundary.SourceReadError),
        ):
            self.run_boundary(self.run_root)
        self.assertNotIn(EventType.STATE_DELTA, self.event_types())
        self.native.execute.assert_not_awaited()

    def test_unrelated_client_grant_does_not_authorize_the_source(self):
        """A valid current grant for another client is insufficient."""
        type(self.grant).objects.filter(pk=self.grant.pk).update(client=self.foreign_tenant)
        with (
            override_settings(AIMMS_AI_SOURCE_READ_ADAPTER=self.read_adapter()),
            self.assertRaises(boundary.SourceReadError),
        ):
            self.run_boundary(self.run_root)
        self.assertNotIn(EventType.STATE_DELTA, self.event_types())
        self.native.execute.assert_not_awaited()

    def test_narrower_native_scope_is_required_independently_of_the_grant(self):
        """The original explicit grant survives while native scope is narrowed."""
        from tasks.scope import MaintenanceScope

        with (
            override_settings(
                AIMMS_AI_SOURCE_READ_ADAPTER=self.read_adapter(),
                AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda _actor: {
                    MaintenanceScope(
                        customer_id=None, site_key=None, client_id=self.foreign_tenant.pk
                    )
                },
            ),
            self.assertRaises(boundary.SourceReadError),
        ):
            self.run_boundary(self.run_root)
        self.assertTrue(type(self.grant).objects.filter(pk=self.grant.pk).exists())
        self.assertNotIn(EventType.STATE_DELTA, self.event_types())

    def test_transferred_source_does_not_survive_old_actor_authority(self):
        """A source record moved to another client cannot reuse the prior grant."""
        type(self.machine).objects.filter(pk=self.machine.pk).update(client=self.foreign_tenant)
        with (
            override_settings(AIMMS_AI_SOURCE_READ_ADAPTER=self.read_adapter()),
            self.assertRaises(boundary.SourceReadError),
        ):
            self.run_boundary(self.run_root)
        self.assertNotIn(EventType.STATE_DELTA, self.event_types())
        self.native.execute.assert_not_awaited()

    def test_enrollment_change_after_event_withholds_later_channels(self):
        """Already emitted state is not revoked; later text/yield is guarded."""
        from django.conf import settings

        async def change(event):  # noqa: RUF029 - native event handler protocol
            if event.event_type is EventType.STATE_DELTA:
                settings.AIMMS_AI_SOURCE_READ_ADAPTER = None

        async def operation():
            return await self.run_root(hook=change)

        with (
            override_settings(AIMMS_AI_SOURCE_READ_ADAPTER=self.read_adapter()),
            self.assertRaises(SourceSerializationError),
        ):
            self.run_boundary(operation)
        self.assertIn(EventType.STATE_DELTA, self.event_types())
        self.assertNotIn(EventType.TEXT_MESSAGE_CONTENT, self.event_types())
        self.assertNotIn(EventType.RUN_FINISHED, self.event_types())

    def test_grant_revocation_during_native_stream_withholds_later_text(self):
        """Native source reauthorization remains active after root adoption."""

        async def revoke(event):
            if event.event_type is EventType.TEXT_MESSAGE_START:
                await sync_to_async(type(self.grant).objects.filter(pk=self.grant.pk).delete)()

        async def operation():
            return await self.run_root(hook=revoke)

        with (
            override_settings(AIMMS_AI_SOURCE_READ_ADAPTER=self.read_adapter()),
            self.assertRaises(HistoryAuthorizationError),
        ):
            self.run_boundary(operation)
        self.assertNotIn(EventType.TEXT_MESSAGE_CONTENT, self.event_types())
        self.assertNotIn(EventType.RUN_FINISHED, self.event_types())

    def test_voice_and_write_pins_remain_native_before_selector(self):
        """The read seam cannot intercept voice or a named write workflow."""
        for context in ({"modality": "voice"}, {"pinned_workflow_id": "wf4"}):
            selector = Mock(side_effect=AssertionError("Unsupported shape selected"))
            with (
                self.subTest(context=context),
                override_settings(AIMMS_AI_SOURCE_READ_ADAPTER=self.read_adapter(selector)),
            ):

                async def operation(context=context):
                    return await self.run_root(context=context)

                self.assertEqual(self.run_boundary(operation), ["Native fixture answer"])
                selector.assert_not_called()

    def test_invalid_selection_and_selector_fault_never_become_success(self):
        """Provider exception content is absent from emitted failure events."""
        for selector in (
            Mock(return_value={"environment": "s6-test", "source_pk": 1}),
            Mock(side_effect=RuntimeError("synthetic-private-provider-detail")),
        ):
            with (
                self.subTest(selector=selector),
                override_settings(AIMMS_AI_SOURCE_READ_ADAPTER=self.read_adapter(selector)),
                self.assertRaises((boundary.SourceReadError, RuntimeError)),
            ):
                self.run_boundary(self.run_root)
            self.native.execute.assert_not_awaited()
            self.assertNotIn(EventType.RUN_FINISHED, self.event_types())
            self.assertNotIn("synthetic-private-provider-detail", repr(self.collector.events))

    def test_selection_mutation_during_delivery_is_refused(self):
        """A frozen class label cannot bless changed server selection fields."""
        selection = self.selection()

        async def mutate(event):  # noqa: RUF029 - native event handler protocol
            if event.event_type is EventType.STATE_DELTA:
                object.__setattr__(selection, "environment", "another-source")

        async def operation():
            return await self.run_root(hook=mutate)

        with (
            override_settings(AIMMS_AI_SOURCE_READ_ADAPTER=self.read_adapter(lambda _: selection)),
            self.assertRaises(SourceSerializationError),
        ):
            self.run_boundary(operation)
        self.assertNotIn(EventType.TEXT_MESSAGE_CONTENT, self.event_types())

    def test_enrollment_changed_during_reauthentication_prevents_selection(self):
        """The configuration check also runs after awaited native auth work."""
        from django.conf import settings

        native_revalidate = boundary.revalidate_current_ai_principal
        selector = Mock(return_value=self.selection())

        async def revalidate():
            principal = await native_revalidate()
            settings.AIMMS_AI_SOURCE_READ_ADAPTER = None
            return principal

        with (
            override_settings(AIMMS_AI_SOURCE_READ_ADAPTER=self.read_adapter(selector)),
            patch.object(boundary, "revalidate_current_ai_principal", side_effect=revalidate),
            self.assertRaises(boundary.SourceReadError),
        ):
            self.run_boundary(self.run_root)
        selector.assert_not_called()
        self.native.execute.assert_not_awaited()

    def test_cancelled_selector_remains_cancellation(self):
        """Cancellation does not become a fallback or successful empty answer."""
        import asyncio

        selector = Mock(side_effect=asyncio.CancelledError)
        with (
            override_settings(AIMMS_AI_SOURCE_READ_ADAPTER=self.read_adapter(selector)),
            self.assertRaises(asyncio.CancelledError),
        ):
            self.run_boundary(self.run_root)
        self.native.execute.assert_not_awaited()
        self.assertNotIn(EventType.RUN_FINISHED, self.event_types())


class WithheldContentRootTests(RootReadFixture, S3AIRequestFixture, TestCase):
    """Actual historical withholding; no simulated attribution is installed."""

    def test_root_does_not_turn_native_work_visibility_into_historical_proof(self):
        """A selected unproven historical record fails without native fallback."""
        from tasks.scope import scoped_work_orders

        self.assertTrue(scoped_work_orders(self.actor).filter(pk=self.work.pk).exists())
        selection = boundary.ContentReadSelection(
            environment="s3-test", operation="work.core", source_pk=self.work.pk
        )
        policy = boundary.SourceReadAdapter(select=lambda _: selection)
        with (
            override_settings(AIMMS_AI_SOURCE_READ_ADAPTER=policy),
            self.assertRaises(boundary.SourceReadError),
        ):
            self.run_boundary(self.run_root)
        self.native.execute.assert_not_awaited()
        self.assertNotIn(EventType.STATE_DELTA, self.event_types())
        self.assertNotIn(EventType.TEXT_MESSAGE_CONTENT, self.event_types())
