"""Real source authority survives transcript storage, current reads and replay."""

import copy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from ai.core.integrations import source_transcripts as transcripts
from ai.core.integrations.source_reads import HistoryReadSelection, SourceReadAdapter
from ai.core.streaming import EventType
from ai.core.tests.test_integration_s6_history_boundary_db import S6AIRequestFixture
from ai.core.tests.test_source_read_root_db import RootReadFixture
from aichat.models import TurnModality
from aichat.services.threads import ThreadRepository
from django.test import TestCase, TransactionTestCase, override_settings
from integration_state.tests.test_native_authority import NativeAuthorityFixture
from machine_health.services.integration_s6_adapter import FixtureHistoryAdapter


class SourceTranscriptFixture(RootReadFixture, S6AIRequestFixture):
    """Keep actual auth, grants, native source, storage and current projections."""

    def setUp(self):
        """Use only synthetic native records and the existing fixture capability."""
        super().setUp()
        self.record = replace(self.record, sample=replace(self.record.sample, value=12.5))
        self.adapter = FixtureHistoryAdapter.from_records(
            source=self.identity,
            mapping=self.mapping,
            coverage=self.window,
            generation="fixture-generation",
            records=(self.record,),
        )
        self.selection = HistoryReadSelection(
            environment="s6-test",
            machine_pk=self.machine.pk,
            source_pk=self.source.pk,
            binding_pk=self.binding.pk,
            window=self.window,
            adapter=self.adapter,
        )
        self.reader = SourceReadAdapter(
            select=lambda _: self.selection, restore=lambda _: self.selection
        )
        self.enterContext(override_settings(AIMMS_AI_SOURCE_READ_ADAPTER=self.reader))
        self.repository = ThreadRepository(self.actor, self.policy.single_site_policy_key)
        self.thread, _ = self.repository.get_or_create(title="Synthetic source transcript")
        self.capture = transcripts.SourceCapture()

        async def operation():
            with transcripts.bind_source_capture(self.capture):
                return await self.run_root()

        chunks = self.run_boundary(operation)
        self.content = "".join(chunks)
        self.public = transcripts.observation(self.capture.packet)
        self.row = self.repository.append(
            self.thread.pk,
            role="assistant",
            content=self.content,
            metadata={
                transcripts.REF_KEY: self.capture.reference,
                transcripts.PUBLIC_KEY: self.public,
            },
        )
        self.canonical = {
            "thread_id": self.thread.pk,
            "turn_id": "source-transcript-test",
            "message": self.content,
            "canonical_response": self.capture.packet["canonical_response"],
            transcripts.REF_KEY: self.capture.reference,
            transcripts.PUBLIC_KEY: self.public,
            "events": [e.to_dict() for e in self.collector.events],
        }
        self.canonical["_source_events_sha256"] = transcripts._hash(self.canonical["events"])


class SourceTranscriptTests(SourceTranscriptFixture, TestCase):
    """Current-source projections use a real transactional native fixture."""

    def test_authorized_storage_reload_replay_export_and_search_preserve_source(self):
        """A genuine stored observation stays useful under current native authority."""
        rows, _ = self.repository.readable_message_page(self.thread.pk)
        self.assertEqual(rows[0].content, self.content)
        self.assertEqual(rows[0].metadata[transcripts.PUBLIC_KEY], self.public)
        self.assertIsNone(self.public["population_count"])
        self.assertFalse(self.public["complete_population"])
        self.assertFalse(self.public["revision_complete"])
        self.assertEqual(
            transcripts.project_canonical(self.canonical, self.actor.pk), self.canonical
        )
        exported = list(self.repository.export_transcript_records())
        self.assertIn(self.content, [r.get("content") for r in exported])
        self.assertEqual(
            next(r[transcripts.PUBLIC_KEY] for r in exported if r.get("type") == "message"),
            self.public,
        )
        self.assertNotIn(transcripts.REF_KEY, str(exported))
        self.assertEqual([t.pk for t in self.repository.search("12.5")], [self.thread.pk])

    def test_revocation_withholds_all_stored_surfaces_without_rewriting_audit(self):
        """A transcript owner cannot borrow old source grants from a snapshot."""
        self.grant.delete()
        self.assertEqual(
            self.repository.readable_messages(self.thread.pk)[0].content, transcripts.UNAVAILABLE
        )
        self.assertEqual(
            self.repository.messages(self.thread.pk)[0].content, transcripts.UNAVAILABLE
        )
        self.assertEqual(
            self.repository.readable_message_page(self.thread.pk)[0][0].content,
            transcripts.UNAVAILABLE,
        )
        projected = transcripts.project_canonical(self.canonical, self.actor.pk)
        self.assertEqual(projected["message"], transcripts.UNAVAILABLE)
        self.assertNotIn("12.5", str(projected))
        self.assertNotIn("12.5", str(list(self.repository.export_transcript_records())))
        self.assertEqual(self.repository.search("12.5"), [])
        self.assertEqual(self.repository.list_page(query="12.5"), ([], None))
        self.row.refresh_from_db()
        self.assertEqual(self.row.content, self.content)
        self.assertEqual(self.row.metadata[transcripts.REF_KEY], self.capture.reference)

    def test_public_metadata_and_changed_stored_events_are_not_authority(self):
        """Copying metadata cannot bypass source checks or retain altered contents."""
        forged = SimpleNamespace(
            content=self.content, metadata={transcripts.PUBLIC_KEY: self.public}
        )
        self.assertEqual(
            transcripts.project_message(forged, self.actor.pk).content, transcripts.UNAVAILABLE
        )
        changed = copy.deepcopy(self.canonical)
        changed["events"][0]["poison"] = "altered source"
        self.assertEqual(
            transcripts.project_canonical(changed, self.actor.pk)["message"],
            transcripts.UNAVAILABLE,
        )
        self.row.content += " altered"
        self.assertEqual(
            transcripts.project_message(self.row, self.actor.pk).content, transcripts.UNAVAILABLE
        )

    def test_missing_restore_and_changed_selection_withhold_without_native_fallback(self):
        """Only a current server resolver can restore the same selected source."""
        for reader in (
            None,
            SourceReadAdapter(select=lambda _: self.selection),
            SourceReadAdapter(select=lambda _: self.selection, restore=lambda _: None),
        ):
            with (
                self.subTest(reader=type(reader).__name__),
                override_settings(AIMMS_AI_SOURCE_READ_ADAPTER=reader),
            ):
                self.assertEqual(
                    transcripts.project_message(self.row, self.actor.pk).content,
                    transcripts.UNAVAILABLE,
                )
        wrong = replace(self.selection, binding_pk=self.binding.pk + 100)
        with override_settings(
            AIMMS_AI_SOURCE_READ_ADAPTER=SourceReadAdapter(
                select=lambda _: wrong, restore=lambda _: wrong
            )
        ):
            self.assertEqual(
                transcripts.project_message(self.row, self.actor.pk).content,
                transcripts.UNAVAILABLE,
            )

    def test_source_answers_do_not_enter_unbound_model_recall(self):
        """Stored source values cannot be laundered into a durable summary/history."""
        self.repository.append(self.thread.pk, role="user", content="A normal owner message")
        self.assertNotIn(
            self.content, [r.content for r in self.repository.recent_messages(self.thread.pk, 10)]
        )
        window = self.repository.recall_window(self.thread.pk, limit=10, exclude_latest=0)
        self.assertNotIn(self.content, [r.content for r in window.rows])
        self.assertIn("A normal owner message", [r.content for r in window.rows])
        self.assertEqual([r.pk for r in self.repository.search("normal owner")], [self.thread.pk])
        self.assertEqual(
            [r.pk for r in self.repository.list_page(query="normal owner")[0]], [self.thread.pk]
        )

    def test_projection_budget_withholds_instead_of_assuming_freshness(self):
        """An exhausted bounded projection never serves an unchecked snapshot."""
        budget = transcripts.ProjectionBudget(remaining=0)
        self.assertEqual(
            transcripts.project_message(self.row, self.actor.pk, budget=budget).content,
            transcripts.UNAVAILABLE,
        )
        self.assertEqual(self.public["kind"], "recorded_history")
        self.assertNotIn(EventType.RUN_ERROR, self.event_types())

    def test_live_finalization_cannot_replace_the_captured_answer_or_reference(self):
        """A later generic seam cannot silently turn one source observation into another."""
        from ai.core.integrations.source_reads import SourceReadError

        async def operation():
            capture = transcripts.SourceCapture()
            with transcripts.bind_source_capture(capture):
                chunks = await self.run_root()
            canonical = {
                "message": "".join(chunks),
                "canonical_response": copy.deepcopy(capture.packet["canonical_response"]),
                transcripts.REF_KEY: copy.deepcopy(capture.reference),
                transcripts.PUBLIC_KEY: transcripts.observation(capture.packet),
            }
            run = SimpleNamespace(extras={"source_capture": capture})
            self.assertIs(
                await transcripts.live_canonical(run, canonical, required=True), canonical
            )
            changed = copy.deepcopy(canonical)
            changed["message"] = "A later unbound replacement"
            with self.assertRaises(SourceReadError):
                await transcripts.live_canonical(run, changed, required=True)
            self.assertEqual(
                (await transcripts.live_canonical(run, changed))["message"], transcripts.UNAVAILABLE
            )
            changed = copy.deepcopy(canonical)
            changed[transcripts.REF_KEY]["content_sha256"] = transcripts._hash("replacement")
            with self.assertRaises(SourceReadError):
                await transcripts.live_canonical(run, changed, required=True)
            capture.packet["canonical_response"]["detailed_response"] = (
                "Mutated private observation"
            )
            with self.assertRaises(SourceReadError):
                await transcripts.live_canonical(run, canonical, required=True)
            return True

        self.assertTrue(self.run_boundary(operation))

    def test_more_than_eight_source_answers_remain_reachable_by_page_and_export(self):
        """A bounded projection uses real pagination rather than losing later bodies."""
        for _ in range(9):
            self.repository.append(
                self.thread.pk, role="assistant", content=self.content, metadata=self.row.metadata
            )
        collected, before = [], None
        while True:
            page, more = self.repository.readable_message_page(
                self.thread.pk, limit=50, before_sequence=before
            )
            self.assertGreater(len(page), 0)
            self.assertLessEqual(len(page), 8)
            collected += page
            self.assertLessEqual(len(collected), 10)
            self.assertTrue(all(row.content == self.content for row in page))
            if not more:
                break
            before = page[0].sequence
        self.assertEqual(len({row.pk for row in collected}), 10)
        exported = [
            r for r in self.repository.export_transcript_records() if r["type"] == "message"
        ]
        self.assertEqual(len(exported), 10)
        self.assertTrue(all(row["content"] == self.content for row in exported))

    def test_restore_callback_cannot_change_its_own_capability_during_resolution(self):
        """A configuration mutation cannot borrow the preceding capability."""

        def mutate(_):
            object.__setattr__(self.reader, "restore", lambda _: self.selection)
            return self.selection

        object.__setattr__(self.reader, "restore", mutate)
        self.assertEqual(
            transcripts.project_message(self.row, self.actor.pk).content, transcripts.UNAVAILABLE
        )

    def test_changed_source_and_newly_authorized_shared_caller_are_rechecked(self):
        """The actor is not a sample revision; independent native grants still are required."""
        from assets.models import ClientScopeGrant
        from django.contrib.auth import get_user_model

        other = get_user_model().objects.create_user(username="source-shared-reader")
        other.groups.set(self.actor.groups.all())
        self.assertEqual(
            transcripts.project_message(self.row, other.pk).content, transcripts.UNAVAILABLE
        )
        ClientScopeGrant.objects.create(user=other, client=self.tenant)
        self.assertEqual(transcripts.project_message(self.row, other.pk).content, self.content)
        changed = replace(self.record, sample=replace(self.record.sample, value=99.75))
        changed_adapter = FixtureHistoryAdapter.from_records(
            source=self.identity,
            mapping=self.mapping,
            coverage=self.window,
            generation="fixture-generation",
            records=(changed,),
        )
        self.selection = replace(self.selection, adapter=changed_adapter)
        self.assertEqual(
            transcripts.project_message(self.row, other.pk).content, transcripts.UNAVAILABLE
        )


class SourceNormalizedTurnTests(SourceTranscriptFixture, TransactionTestCase):
    """Production connection hygiene runs outside a TestCase-owned atomic block."""

    def test_native_normalized_turn_persistence_reload_and_revoked_replay(self):
        """Actual source, root, turn service and native repository compose under ASGI auth."""
        from ai.core.auth import get_current_principal
        from ai.core.config import get_settings
        from ai.core.streaming import EventCollector, InMemoryEventEmitter
        from ai.core.trusted_context import build_trusted_turn_context
        from ai.core.turn_service import NormalizedTurnService

        configured = get_settings().model_copy(
            update={
                "feature_ai_analysis_router_shadow": False,
                "feature_ai_analysis_router_enforce": False,
                "feature_tool_events": False,
                "feature_voice_fast_path": False,
                "feature_token_streaming": False,
            }
        )
        service = NormalizedTurnService(workflow_factory=self.root)
        turn_key = "source-native-normalized"
        drop_after_observation = False
        observed = []

        async def operation():
            principal = get_current_principal()
            trusted = build_trusted_turn_context(
                principal,
                single_site_policy_key=self.policy.single_site_policy_key,
                policy_version=self.policy.policy_version,
                correlation_id="00000000-0000-0000-0000-000000000045",
            )
            emitter = InMemoryEventEmitter()
            collector = EventCollector()
            await emitter.subscribe(collector)
            observed.append(collector)

            async def revoke(event):
                if drop_after_observation and event.data.get("kind") == "recorded_history_analysis":
                    from asgiref.sync import sync_to_async

                    await sync_to_async(self.grant.delete, thread_sensitive=True)()

            await emitter.subscribe(SimpleNamespace(handle=revoke))
            with (
                patch("ai.core.config.get_settings", return_value=configured),
                patch("ai.core.workflows.root.get_settings", return_value=configured),
            ):
                result = await service.process(
                    actor=principal,
                    thread_id=self.thread.pk,
                    content="Read selected source",
                    modality=TurnModality.TEXT,
                    trusted_context=trusted,
                    modality_metadata={},
                    idempotency_key=turn_key,
                    correlation_id=trusted.correlation_id,
                    emitter=emitter,
                    server_pinned_workflow="wf8",
                )
            return result, collector

        result, _ = self.run_boundary(operation)
        self.assertFalse(result.replayed)
        self.assertEqual(result.canonical_response["response_version"], 2)
        self.assertEqual(result.source_observation["population_count"], None)
        self.assertEqual(result.source_observation["returned_count"], 1)
        self.assertIn("12.5", result.message)
        rows = self.repository.readable_messages(self.thread.pk)
        self.assertEqual(rows[-1].metadata[transcripts.PUBLIC_KEY], result.source_observation)
        replay, _ = self.run_boundary(operation)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.message, result.message)
        self.grant.delete()
        revoked, events = self.run_boundary(operation)
        self.assertTrue(revoked.replayed)
        self.assertEqual(revoked.message, transcripts.UNAVAILABLE)
        self.assertNotIn("12.5", str([e.to_dict() for e in events.events]))
        self.assertIn(EventType.MESSAGES_SNAPSHOT, [e.event_type for e in events.events])
        from ai.core.turn_service import TurnExecutionFailed
        from assets.models import ClientScopeGrant

        self.grant = ClientScopeGrant.objects.create(user=self.actor, client=self.tenant)
        turn_key = "source-live-refusal"
        drop_after_observation = True
        with self.assertRaises(TurnExecutionFailed):
            self.run_boundary(operation)
        records = [event.to_dict() for event in observed[-1].events]
        snapshot = next(
            i for i, r in enumerate(records) if r["type"] == EventType.MESSAGES_SNAPSHOT.value
        )
        self.assertNotIn("12.5", str(records[snapshot:]))
        self.assertEqual(records[-1]["type"], EventType.RUN_ERROR.value)
        last = self.repository.readable_messages(self.thread.pk)[-1]
        self.assertNotIn("12.5", str(last.metadata))


class AttestedContentTranscriptTests(RootReadFixture, NativeAuthorityFixture, TestCase):
    """Real future-only source evidence composes with stored content projection."""

    def test_approved_work_revision_reloads_then_withdrawal_withholds_the_snapshot(self):
        """No simulated attribution, service identity or request metadata is authority."""
        from ai.core.auth import AIBoundaryPolicy
        from ai.core.integrations.source_reads import ContentReadSelection
        from integration_state import native_authority

        row = self.attest()
        self.approve(row)
        self.actor = self.owner
        self.policy = AIBoundaryPolicy(
            single_site_policy_key="content-transcript-site",
            policy_version="test-v1",
            signed_subject_max_age_seconds=60,
            signed_subject_salt="content.transcript.test",
            signed_subject_audience="content-transcript",
            allowed_origins=("https://app.example.test",),
        )
        selection = ContentReadSelection(
            environment=self.namespace.environment,
            operation="work.core",
            source_pk=self.new_work.pk,
        )
        adapter = SourceReadAdapter(select=lambda _: selection, restore=lambda _: selection)
        capture = transcripts.SourceCapture()

        async def operation():
            with transcripts.bind_source_capture(capture):
                return await self.run_root()

        with override_settings(AIMMS_AI_SOURCE_READ_ADAPTER=adapter):
            chunks = S6AIRequestFixture.run_boundary(self, operation, method="signed")
            public = transcripts.observation(capture.packet)
            self.assertEqual(public["kind"], "record")
            self.assertIsNone(public["population_count"])
            self.assertIsNone(public["window"])
            repository = ThreadRepository(self.owner, self.policy.single_site_policy_key)
            thread, _ = repository.get_or_create(title="Attested content fixture")
            message = repository.append(
                thread.pk,
                role="assistant",
                content="".join(chunks),
                metadata={
                    transcripts.REF_KEY: capture.reference,
                    transcripts.PUBLIC_KEY: public,
                },
            )
            self.assertEqual(repository.readable_messages(thread.pk)[0].content, message.content)
            native_authority.withdraw_authority_request(
                self.request(),
                request_pk=row.pk,
                expected_request_digest=row.binding_digest,
                reason_reference="synthetic-content-withdrawal",
            )
            self.assertEqual(
                repository.readable_messages(thread.pk)[0].content, transcripts.UNAVAILABLE
            )
            self.assertEqual(repository.list_page(query="Future native Work"), ([], None))
