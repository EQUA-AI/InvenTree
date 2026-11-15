"""Future native attestation reaches real ASGI/root without simulated proof."""

from importlib import import_module

from ai.core import auth
from ai.core.integrations.integration_s3_content import SourceAuthorizationError
from ai.core.integrations.source_reads import ContentReadSelection, SourceReadAdapter
from ai.core.streaming import EventType
from ai.core.tests.test_integration_s6_history_boundary_db import S6AIRequestFixture
from ai.core.tests.test_source_read_root_db import RootReadFixture
from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
from django.test import TestCase, override_settings
from integration_state.tests.test_native_authority import NativeAuthorityFixture


class NativeAttestedRootTests(RootReadFixture, NativeAuthorityFixture, TestCase):
    """Fake only unused conversation/router work, never source or native auth."""

    def setUp(self):
        """Create real native future proof and real session/signed credentials."""
        super().setUp()
        self.attestation = self.attest()
        self.approve(self.attestation)
        self.actor = self.owner
        self.enterContext(override_settings(SESSION_ENGINE="django.contrib.sessions.backends.db"))
        self.policy = auth.AIBoundaryPolicy(
            single_site_policy_key="native-attested-source-policy",
            policy_version="native-attested-v1",
            signed_subject_max_age_seconds=60,
            signed_subject_salt="native-attested.synthetic.subject.v1",
            signed_subject_audience="native-attested-root-fixture",
            allowed_origins=("https://app.example.test",),
        )
        self.session = import_module(settings.SESSION_ENGINE).SessionStore()
        self.session[SESSION_KEY] = str(self.owner.pk)
        self.session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
        self.session[HASH_SESSION_KEY] = self.owner.get_session_auth_hash()
        self.session.save()
        selection = ContentReadSelection(
            environment=self.namespace.environment,
            operation="work.core",
            source_pk=self.new_work.pk,
        )
        self.enterContext(
            override_settings(
                AIMMS_AI_SOURCE_READ_ADAPTER=SourceReadAdapter(select=lambda _: selection)
            )
        )

    def test_reviewed_future_work_reaches_native_root_facts_and_canonical_events(self):
        """Source-owned proof supports useful positives under actual credentials."""
        for method in ("signed", "session"):
            with self.subTest(method=method):
                chunks = S6AIRequestFixture.run_boundary(self, self.run_root, method=method)
                self.assertEqual(len(chunks), 1)
                self.assertIn(self.new_work.title, chunks[0])
                packets = self.collector.get_events(EventType.STATE_DELTA)
                self.assertEqual(len(packets), 1)
                self.assertEqual(packets[0].data["canonical_response"]["response_version"], 2)
                self.assertEqual(packets[0].data["canonical_response"]["recommended_actions"], [])
                self.assertIn(EventType.RUN_FINISHED, self.event_types())
                self.native.execute.assert_not_awaited()
                self.router.route.assert_not_awaited()

    def test_current_grant_revocation_during_attested_delivery_stops_later_channels(self):
        """An attestation is data provenance, never a cached read grant."""

        async def revoke(event):
            if event.event_type is EventType.TEXT_MESSAGE_START:
                await sync_to_async(
                    type(self.owner_grant).objects.filter(pk=self.owner_grant.pk).delete
                )()

        async def operation():
            return await self.run_root(hook=revoke)

        with self.assertRaises(SourceAuthorizationError):
            S6AIRequestFixture.run_boundary(self, operation)
        self.assertNotIn(EventType.TEXT_MESSAGE_CONTENT, self.event_types())
        self.assertNotIn(EventType.RUN_FINISHED, self.event_types())

    def test_withdrawal_during_attested_delivery_stops_later_channels(self):
        """Retained source correction is rechecked after an actual event await."""
        from integration_state import native_authority

        async def withdraw(event):
            if event.event_type is EventType.TEXT_MESSAGE_START:
                await sync_to_async(native_authority.withdraw_authority_request)(
                    self.request(),
                    request_pk=self.attestation.pk,
                    expected_request_digest=self.attestation.binding_digest,
                    reason_reference="synthetic-mid-delivery-correction",
                )

        async def operation():
            return await self.run_root(hook=withdraw)

        with self.assertRaises(SourceAuthorizationError):
            S6AIRequestFixture.run_boundary(self, operation)
        self.assertNotIn(EventType.TEXT_MESSAGE_CONTENT, self.event_types())
        self.assertNotIn(EventType.RUN_FINISHED, self.event_types())
