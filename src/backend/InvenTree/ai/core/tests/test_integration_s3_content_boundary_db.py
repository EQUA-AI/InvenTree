"""Actual ASGI intake for S3; historical positives explicitly simulate proof."""

from __future__ import annotations

from importlib import import_module
from unittest.mock import patch

from ai.core import auth
from ai.core.tests import test_integration_s6_history_boundary_db as transport
from assets.tests.integration_attribution_test_support import assume_synthetic_attribution
from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
from django.test import TestCase, override_settings
from integration_state.tests.assertions import assert_literal_bool
from tasks import integration_s3_reader as reader
from tasks.tests.test_integration_s3_wire_db import WireFixture


class S3AIRequestFixture(WireFixture):
    """Reuse plain source fixtures and the genuine middleware transport only."""

    def setUp(self):
        """Keep every authority/source record local, synthetic and native."""
        super().setUp()
        self.enterContext(
            override_settings(
                AUTHENTICATION_BACKENDS=["django.contrib.auth.backends.ModelBackend"],
                SESSION_ENGINE="django.contrib.sessions.backends.db",
                AIMMS_MAINTENANCE_SCOPE_RESOLVER="tasks.scope.granted_client_scope_resolver",
                AIMMS_SINGLE_SITE_CLIENT_CODE="missing-s3-consumer-fallback",
            )
        )
        self.policy = auth.AIBoundaryPolicy(
            single_site_policy_key="s3-consumer-policy",  # gitleaks:allow -- Non-secret synthetic policy identifier.
            policy_version="s3-consumer-policy-v1",
            signed_subject_max_age_seconds=60,
            signed_subject_salt="s3.consumer.synthetic.subject.v1",
            signed_subject_audience="s3-consumer-fixture",
            allowed_origins=("https://app.example.test",),
        )
        self.session = import_module(settings.SESSION_ENGINE).SessionStore()
        self.session[SESSION_KEY] = str(self.actor.pk)
        self.session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
        self.session[HASH_SESSION_KEY] = self.actor.get_session_auth_hash()
        self.session.save()

    def run_boundary(self, operation, *, method="session", extra_headers=()):
        """Use real DB sessions by default; signed-subject controls opt in."""
        return transport.S6AIRequestFixture.run_boundary(
            self, operation, method=method, extra_headers=extra_headers
        )

    def ai_read(self, **changes):
        """Call the independently authenticated S3 entry, never a forged DRF GET."""
        entry = getattr(reader, "read_source_for_current_ai", None)
        self.assertTrue(callable(entry), "A genuine current-AI S3 source entry is required")
        arguments = {
            "environment": "s3-test",
            "operation": "work.core",
            "source_pk": self.work.pk,
        }
        arguments.update(changes)
        return entry(**arguments)


class RealPolicyS3BoundaryTests(S3AIRequestFixture, TestCase):
    """No proof patch is installed for real-policy withholding controls."""

    def test_actual_asgi_keeps_unproven_history_unavailable_without_storage_io(self):
        """Authenticating a real actor cannot supply missing historical proof."""
        from assets.integration_authority import integration_actor_from_current_ai
        from tasks.scope import scoped_work_orders

        def operation():
            self.assertEqual(
                integration_actor_from_current_ai(required_role="work_order").pk,
                self.actor.pk,
            )
            self.assertTrue(scoped_work_orders(self.actor).filter(pk=self.work.pk).exists())
            with patch("django.core.files.storage.default_storage.open") as opened:
                observed = self.ai_read()
            opened.assert_not_called()
            self.assertEqual(observed.outcome.state, "unavailable")
            self.assertIsNone(observed.outcome.payload)
            assert_literal_bool(self, observed.outcome.population_complete, False)
            assert_literal_bool(self, observed.outcome.revision_complete, False)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)


@assume_synthetic_attribution
class SimulatedProofS3BoundaryTests(S3AIRequestFixture, TestCase):
    """Current auth/grants/roles are real; historical admission is simulated."""

    def test_source_observation_is_actual_native_bytes_and_expires_with_invocation(self):
        """Both actual credential mechanisms reach the real source and lifetime."""
        from tasks.integration_s3_wire import read_work_details_json

        for method in ("signed", "session"):
            with self.subTest(method=method):

                def operation():
                    observed = self.ai_read()
                    self.assertEqual(observed.outcome.state, "available")
                    self.assertEqual(
                        observed.outcome.payload,
                        read_work_details_json(
                            self.actor, environment="s3-test", work_pk=self.work.pk
                        ),
                    )
                    assert_literal_bool(self, observed.reauthorize(), True)
                    return observed

                observed = self.run_boundary(operation, method=method)
                assert_literal_bool(self, observed.reauthorize(), False)

    def test_bare_principal_and_unknown_operation_never_dispatch_source(self):
        """Identity labels and unsupported capabilities confer no source access."""
        principal = self.run_boundary(auth.get_current_principal)
        token = auth.principal_context.set(principal)
        try:
            with patch.object(reader.wire, "read_work_details_json") as read:
                self.assertEqual(self.ai_read().outcome.state, "unavailable")
            read.assert_not_called()
        finally:
            auth.principal_context.reset(token)

        def operation():
            with patch.object(reader.wire, "read_work_details_json") as read:
                value = self.ai_read(operation="unsupported.source")
            read.assert_not_called()
            self.assertEqual(value.outcome.state, "unsupported")
            self.assertIsNone(value.outcome.payload)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_current_role_grant_account_and_source_changes_revoke_observation(self):
        """Retained bytes do not replace current native authority or source data."""
        from assets.models import ClientScopeGrant
        from tasks.models import WorkOrder
        from users.models import RuleSet

        for kind in ("role", "grant", "account", "source"):
            with self.subTest(change=kind):

                def operation(kind=kind):
                    observed = self.ai_read()
                    assert_literal_bool(self, observed.reauthorize(), True)
                    try:
                        if kind == "role":
                            RuleSet.objects.filter(pk=self.rule.pk).update(can_view=False)
                        elif kind == "grant":
                            self.grant.delete()
                        elif kind == "account":
                            type(self.actor).objects.filter(pk=self.actor.pk).update(
                                is_active=False
                            )
                        else:
                            WorkOrder.objects.filter(pk=self.work.pk).update(title="Changed source")
                        refused = observed.reauthorize()
                    finally:
                        RuleSet.objects.filter(pk=self.rule.pk).update(can_view=True)
                        type(self.actor).objects.filter(pk=self.actor.pk).update(is_active=True)
                        WorkOrder.objects.filter(pk=self.work.pk).update(title=self.work.title)
                        if kind == "grant":
                            self.grant = ClientScopeGrant.objects.create(
                                user=self.actor, client=self.tenant
                            )
                    assert_literal_bool(self, refused, False)
                    assert_literal_bool(self, observed.reauthorize(), True)
                    return True

                assert_literal_bool(self, self.run_boundary(operation), True)

    def test_session_revocation_withholds_source_while_native_grant_remains(self):
        """A fresh active user/role cannot stand in for the deleted real session."""
        from assets.models import ClientScopeGrant

        def operation():
            observed = self.ai_read()
            assert_literal_bool(self, observed.reauthorize(), True)
            self.session.delete(self.session.session_key)
            self.assertTrue(ClientScopeGrant.objects.filter(pk=self.grant.pk).exists())
            assert_literal_bool(self, observed.reauthorize(), False)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_same_reference_changed_bytes_invalidate_retained_content(self):
        """Equal native metadata is not a proof that retained original bytes match."""
        from tasks.integration_s3_wire import read_work_document_metadata_json

        self.prepare_operations()
        self.select_owner("workorder")

        def operation():
            observed = self.ai_read(
                operation="work_document.content", source_pk=self.attachment.pk, max_bytes=128
            )
            self.assertEqual(observed.outcome.state, "available")
            before = read_work_document_metadata_json(
                self.actor, environment="s3-test", attachment_pk=self.attachment.pk
            )
            with self.attachment.attachment.storage.open(
                self.attachment.attachment.name, "wb"
            ) as handle:
                handle.write(b"X" * len(self.blob))
            self.assertEqual(
                read_work_document_metadata_json(
                    self.actor, environment="s3-test", attachment_pk=self.attachment.pk
                ),
                before,
            )
            assert_literal_bool(self, observed.reauthorize(), False)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)
