"""Real ASGI authentication to S1/S6, with local synthetic source records."""

from __future__ import annotations

import time
from contextvars import copy_context
from dataclasses import replace
from importlib import import_module
from inspect import iscoroutinefunction
from unittest.mock import patch

from ai.core import auth
from asgiref.sync import async_to_sync, sync_to_async
from assets import integration_authority
from assets.models import ClientScopeGrant, DictionaryPoint
from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
from django.test import TestCase, override_settings
from integration_state.tests.assertions import assert_literal_bool
from machine_health.services import integration_s6_reader as source_reader
from machine_health.tests.test_integration_s6_reader_db import S6RequestFixture
from tasks.scope import MaintenanceScope
from users.models import RuleSet


class S6AIRequestFixture(S6RequestFixture):
    """Share the existing plain, non-TestCase source fixture, not its tests."""

    def setUp(self):
        """Select real Django DB sessions/backend only within the new fixture."""
        super().setUp()
        self.enterContext(
            override_settings(
                AUTHENTICATION_BACKENDS=["django.contrib.auth.backends.ModelBackend"],
                SESSION_ENGINE="django.contrib.sessions.backends.db",
                AIMMS_MAINTENANCE_SCOPE_RESOLVER="tasks.scope.granted_client_scope_resolver",
                AIMMS_SINGLE_SITE_CLIENT_CODE="missing-s6-fixture-fallback",
            )
        )
        self.policy = auth.AIBoundaryPolicy(
            single_site_policy_key="consumer-site-policy",
            policy_version="consumer-policy-v1",
            signed_subject_max_age_seconds=60,
            signed_subject_salt="s6.consumer.synthetic.subject.v1",
            signed_subject_audience="s6-consumer-fixture",
            allowed_origins=("https://app.example.test",),
        )
        self.session = import_module(settings.SESSION_ENGINE).SessionStore()
        self.session[SESSION_KEY] = str(self.actor.pk)
        self.session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
        self.session[HASH_SESSION_KEY] = self.actor.get_session_auth_hash()
        self.session.save()

    def run_boundary(self, operation, *, method="signed", extra_headers=()):
        """Run the real middleware; never install a principal or actor by hand."""
        observed, sent = [], []
        if method == "session":
            credentials = (
                b"cookie",
                f"{settings.SESSION_COOKIE_NAME}={self.session.session_key}".encode(),
            )
        elif method == "signed":
            credentials = (
                b"authorization",
                (
                    "Bearer " + auth.sign_interactive_subject(self.actor, policy=self.policy)
                ).encode(),
            )
        else:
            raise ValueError("Select a fixture authentication mechanism")
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/synthetic-s6-consumer/",
            "root_path": "/api/ai",
            "scheme": "https",
            "server": ("testserver", 443),
            "headers": [credentials, *extra_headers],
            "query_string": b"",
        }

        async def app(_scope, _receive, _send):
            if iscoroutinefunction(operation):
                result = await operation()
            else:
                result = await sync_to_async(operation, thread_sensitive=True)()
            observed.append(result)

        async def receive():  # noqa: RUF029
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):  # noqa: RUF029
            sent.append(message)

        async_to_sync(auth.AIBoundaryAuthMiddleware(app, policy=self.policy))(scope, receive, send)
        self.assertEqual(sent, [], "The authenticated fixture must reach its inner app")
        self.assertEqual(len(observed), 1)
        self.assertIsNone(auth.get_current_principal())
        return observed[0]

    def ai_read(self, **changes):
        """Use the independent AI source entry, with no DRF request adapter."""
        arguments = {
            "environment": "s6-test",
            "machine_pk": self.machine.pk,
            "source_pk": self.source.pk,
            "binding_pk": self.binding.pk,
            "window": self.window,
            "adapter": self.adapter,
        }
        arguments.update(changes)
        return source_reader.read_recorded_history_for_current_ai(**arguments)


class CurrentAIHistoryBoundaryTests(S6AIRequestFixture, TestCase):
    """No synthetic identity may replace the real boundary's authentication."""

    def test_actual_signed_and_session_subject_establish_current_native_actor(self):
        """A native current user/role is resolved from either actual mechanism."""
        helper = getattr(integration_authority, "integration_actor_from_current_ai", None)
        self.assertTrue(callable(helper), "A genuine ASGI-to-S1 intake is required")
        assert callable(helper)
        for method in ("signed", "session"):
            with self.subTest(method=method):
                actor = self.run_boundary(lambda: helper(required_role="work_order"), method=method)
                self.assertEqual(actor.pk, self.actor.pk)
                self.assertIsNot(actor, self.actor)

    def test_bare_genuine_principal_after_request_is_not_authentication(self):
        """Even the real prior identity object cannot mint a later read grant."""
        principal = self.run_boundary(auth.get_current_principal)
        token = auth.principal_context.set(principal)
        try:
            self.assertIsNone(
                integration_authority.integration_actor_from_current_ai(required_role="work_order")
            )
        finally:
            auth.principal_context.reset(token)

    def test_equal_copied_principal_inside_request_is_refused(self):
        """Scalar equality is not this invocation's authentication identity."""

        async def operation():
            original = auth.get_current_principal()
            self.assertIs(await auth.revalidate_current_ai_principal(), original)
            token = auth.principal_context.set(replace(original))
            try:
                return await auth.revalidate_current_ai_principal()
            finally:
                auth.principal_context.reset(token)

        self.assertIsNone(self.run_boundary(operation))

    def test_copied_task_context_expires_with_middleware_invocation(self):
        """A copied ContextVar map must observe the original lifetime ending."""
        captured = self.run_boundary(copy_context)
        result = captured.run(
            lambda: integration_authority.integration_actor_from_current_ai(
                required_role="work_order"
            )
        )
        self.assertIsNone(result)

    def test_session_deletion_rechecks_actual_session_store(self):
        """Native role and client grant remain positive when the session dies."""

        def operation():
            helper = integration_authority.integration_actor_from_current_ai
            self.assertEqual(helper(required_role="work_order").pk, self.actor.pk)
            self.session.delete(self.session.session_key)
            return helper(required_role="work_order")

        self.assertIsNone(self.run_boundary(operation, method="session"))
        self.assertTrue(type(self.grant).objects.filter(pk=self.grant.pk).exists())

    def test_password_hash_change_refuses_both_mechanisms(self):
        """Reauthentication, not a cached active-user lookup, guards the read."""
        for method in ("signed", "session"):
            with self.subTest(method=method):
                old = self.actor.password

                def operation():
                    helper = integration_authority.integration_actor_from_current_ai
                    self.assertEqual(helper(required_role="work_order").pk, self.actor.pk)
                    self.actor.set_unusable_password()
                    self.actor.save(update_fields=("password",))
                    return helper(required_role="work_order")

                try:
                    self.assertIsNone(self.run_boundary(operation, method=method))
                finally:
                    self.actor.password = old
                    self.actor.save(update_fields=("password",))

    def test_real_role_revocation_and_restoration(self):
        """Do not substitute authentication or an explicit grant for the role."""

        def operation():
            helper = integration_authority.integration_actor_from_current_ai
            self.assertEqual(helper(required_role="work_order").pk, self.actor.pk)
            RuleSet.objects.filter(pk=self.rule.pk).update(can_view=False)
            try:
                self.assertIsNone(helper(required_role="work_order"))
                self.assertTrue(type(self.grant).objects.filter(pk=self.grant.pk).exists())
            finally:
                RuleSet.objects.filter(pk=self.rule.pk).update(can_view=True)
            return helper(required_role="work_order").pk

        self.assertEqual(self.run_boundary(operation), self.actor.pk)

    def test_signed_subject_expires_during_same_request(self):
        """A long-running invocation must recheck the original subject age."""

        async def operation():
            self.assertIsNotNone(await auth.revalidate_current_ai_principal())
            future = time.time() + self.policy.signed_subject_max_age_seconds + 1
            with patch("django.core.signing.time.time", return_value=future):
                return await auth.revalidate_current_ai_principal()

        self.assertIsNone(self.run_boundary(operation))

    def test_context_change_inside_fallible_authentication_is_refused(self):
        """Check the original task binding again after native authentication."""

        async def operation():
            original = auth._authenticate

            async def changed(headers, policy):
                result = await original(headers, policy)
                auth.principal_context.set(replace(result))
                return result

            with patch.object(auth, "_authenticate", side_effect=changed):
                return await auth.revalidate_current_ai_principal()

        self.assertIsNone(self.run_boundary(operation))

    def test_authentication_fault_is_content_free_and_memory_error_propagates(self):
        """An authentication backend fault is not source success or leaked text."""

        async def operation():
            with patch.object(
                auth, "_authenticate", side_effect=RuntimeError("synthetic-private-auth")
            ):
                self.assertIsNone(await auth.revalidate_current_ai_principal())
            with (
                patch.object(auth, "_authenticate", side_effect=MemoryError),
                self.assertRaises(MemoryError),
            ):
                await auth.revalidate_current_ai_principal()
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_actual_ai_boundary_reads_nonempty_source_without_fake_drf_request(self):
        """An actual authenticated AI invocation reaches the real source path."""
        entry = getattr(source_reader, "read_recorded_history_for_current_ai", None)
        self.assertTrue(callable(entry), "An independent current-AI S6 intake is required")

        def operation():
            with patch.object(
                source_reader,
                "integration_actor_from_request",
                side_effect=AssertionError("AI must not manufacture a DRF request"),
            ):
                observed = self.ai_read()
            self.assertEqual(observed.outcome.state, "available")
            assert_literal_bool(self, observed.reauthorize(), True)
            self.assertIn(b"literal-event:0", observed.outcome.payload)
            return observed

        observed = self.run_boundary(operation)
        assert_literal_bool(self, observed.reauthorize(), False)

    def test_native_scope_and_explicit_integration_grant_remain_independent(self):
        """A real ASGI identity does not widen either current scope boundary."""
        allowed = {MaintenanceScope(None, None, self.tenant.pk)}
        foreign = {MaintenanceScope(None, None, self.foreign_tenant.pk)}

        def operation():
            with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda _actor: allowed):
                self.assertEqual(self.ai_read().outcome.state, "available")
                self.grant.delete()
                try:
                    denied = self.ai_read().outcome
                    self.assertEqual(denied.state, "unavailable")
                    self.assertIsNone(denied.payload)
                finally:
                    self.grant = ClientScopeGrant.objects.create(
                        user=self.actor, client=self.tenant
                    )
            with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda _actor: foreign):
                self.assertTrue(ClientScopeGrant.objects.filter(pk=self.grant.pk).exists())
                self.assertEqual(self.ai_read().outcome.state, "unavailable")
            return self.ai_read().outcome.state

        self.assertEqual(self.run_boundary(operation), "available")

    def test_final_private_observation_constructor_cannot_preserve_revoked_payload(self):
        """Recheck independent authority after constructing the private handle."""
        original = source_reader._AIReadObservation

        def operation():
            self.assertEqual(self.ai_read().outcome.state, "available")

            def changed(outcome, check):
                result = original(outcome, check)
                if outcome.state == "available":
                    self.grant.delete()
                return result

            with patch.object(source_reader, "_AIReadObservation", side_effect=changed):
                denied = self.ai_read()
            self.assertEqual(denied.outcome.state, "unavailable")
            self.assertIsNone(denied.outcome.payload)
            assert_literal_bool(self, denied.reauthorize(), False)
            self.grant = ClientScopeGrant.objects.create(user=self.actor, client=self.tenant)
            return self.ai_read().outcome.state

        self.assertEqual(self.run_boundary(operation), "available")

    def test_native_point_revocation_invalidates_returned_observation(self):
        """The captured result is not later source or mapping authority."""

        def operation():
            observed = self.ai_read()
            assert_literal_bool(self, observed.reauthorize(), True)
            DictionaryPoint.objects.filter(pk=self.point.pk).update(status="draft")
            try:
                assert_literal_bool(self, observed.reauthorize(), False)
                self.assertEqual(self.ai_read().outcome.state, "unavailable")
            finally:
                DictionaryPoint.objects.filter(pk=self.point.pk).update(status="approved")
            return self.ai_read().outcome.state

        self.assertEqual(self.run_boundary(operation), "available")

    def test_reauthorization_refreshes_after_fallible_source_validation(self):
        """Revocation inside a successful payload check must still withhold."""

        def operation():
            observed = self.ai_read()
            assert_literal_bool(self, observed.reauthorize(), True)
            original = source_reader.wire.check_history_payload

            def changed(*args, **kwargs):
                result = original(*args, **kwargs)
                self.grant.delete()
                return result

            with patch.object(source_reader.wire, "check_history_payload", side_effect=changed):
                assert_literal_bool(self, observed.reauthorize(), False)
            self.grant = ClientScopeGrant.objects.create(user=self.actor, client=self.tenant)
            return self.ai_read().outcome.state

        self.assertEqual(self.run_boundary(operation), "available")

    def test_missing_boundary_and_missing_adapter_are_not_empty_success(self):
        """Separate unresolved authentication from an unsupported source."""
        self.assertEqual(self.ai_read().outcome.state, "unavailable")
        observed = self.run_boundary(lambda: self.ai_read(adapter=None))
        self.assertEqual(observed.outcome.state, "unsupported")
        self.assertIsNone(observed.outcome.payload)
        assert_literal_bool(self, observed.reauthorize(), False)

    def test_public_ai_source_intake_does_not_accept_an_actor_or_callback(self):
        """No tool argument can select whose grant or authorization is used."""
        for arguments in ({"actor_pk": self.actor.pk}, {"authorize": lambda: True}):
            with self.subTest(argument=tuple(arguments)), self.assertRaises(TypeError):
                self.run_boundary(lambda arguments=arguments: self.ai_read(**arguments))
