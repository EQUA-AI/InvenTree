"""Authorization boundary tests for the EQUA demo metrics commands/services.

Every retained surface — cleanup planning (readback), cleanup execution and
each command entry — must require an authenticated, active operator holding
``tasks.plan_workorder`` (the verify/apply standard) plus fail-closed current
machine scope for every session membership, claims already marked inactive
included, before any disclosure or mutation. Execution rechecks the boundary
under the session lock before any effect, and the cleaned-session repeat
readback is not a bypass.

Denials are read-only in every dimension: no ledger row moves, no claim flips,
no receipt is written, and no plan or cleanup artifact is created on disk.
"""

import datetime as dt
import io
import json
import tempfile
import uuid
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from tasks.scope import MaintenanceScope

from aimms_testing import requires_postgres
from assets.demo_metrics import cleanup, replay
from assets.demo_metrics.demo_test_support import FIXTURE_PATH, DemoMetricsEnvMixin
from assets.demo_metrics_models import DemoMetricsSession
from assets.health_models import MachineSignalState
from assets.models import Client

#: username -> client codes the resolver grants (missing entry resolves to
#: nothing and must fail closed, exactly like an unresolved deployment scope).
TEST_ACTOR_SCOPE_CODES = {}


def _test_scope_resolver(actor):
    """Per-actor scopes from client codes; unknown actors resolve to no scope.

    Command actors are freshly loaded from the database, so the in-memory
    ``maintenance_scopes`` attribute never reaches them; this resolver is the
    deployment-shaped path those actors actually resolve through.
    """
    codes = TEST_ACTOR_SCOPE_CODES.get(actor.get_username())
    if codes is None:
        return set()
    return {
        MaintenanceScope(customer_id=None, site_key=None, client_id=client.pk)
        for client in Client.objects.filter(code__in=codes, active=True)
    }


@requires_postgres
@override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=_test_scope_resolver)
class CleanupAuthorizationTest(DemoMetricsEnvMixin, TestCase):
    """The cleanup boundary: role, scope, fail-closed, and no-write denials."""

    def setUp(self):
        """Apply the demo session and record the pre-denial row counts."""
        TEST_ACTOR_SCOPE_CODES.clear()
        self.addCleanup(TEST_ACTOR_SCOPE_CODES.clear)
        TEST_ACTOR_SCOPE_CODES['demo-operator'] = {'internal'}
        self.build_demo_env()
        self.apply_demo()
        self.session = self.session()
        self.ledger_before = self.session.ledger_objects.count()
        self.receipts_before = self.session.receipts.count()

    def make_actor(self, username, *, superuser, scope_codes):
        """Create an operator; ``scope_codes`` of None leaves scope unresolved."""
        actor = get_user_model().objects.create_user(
            username=username,
            email=f'{username}@example.com',
            password='pw',
            is_superuser=superuser,
        )
        if scope_codes is not None:
            TEST_ACTOR_SCOPE_CODES[username] = set(scope_codes)
        return actor

    def make_wrong_tenant(self):
        """A second tenant that the demo session machines never live in."""
        tenant, _created = Client.objects.get_or_create(
            name='Other Tenant', defaults={'code': 'other'}
        )
        return tenant

    def approved_hash(self):
        """The cleanup plan hash an authorized operator would approve."""
        return cleanup.build_cleanup_plan(self.session, self.actor)['plan_hash']

    def assert_denied_without_writes(self):
        """No disclosure artifacts and no database effect happened."""
        session = DemoMetricsSession.objects.get(pk=self.session.pk)
        self.assertEqual(session.status, DemoMetricsSession.Status.ACTIVE)
        self.assertTrue(session.machines.filter(claim_active=True).exists())
        self.assertEqual(session.ledger_objects.count(), self.ledger_before)
        self.assertEqual(session.receipts.count(), self.receipts_before)

    def test_role_denied_actor_cannot_plan_or_execute_cleanup(self):
        """In-scope but role-less actors neither read the plan nor mutate."""
        roleless = self.make_actor(
            'authz-roleless', superuser=False, scope_codes={'internal'}
        )
        plan_hash = self.approved_hash()
        with self.assertRaises(cleanup.CleanupError) as caught:
            cleanup.build_cleanup_plan(self.session, roleless)
        self.assertEqual(caught.exception.code, 'ACTOR_UNAUTHORIZED')
        with self.assertRaises(cleanup.CleanupError) as caught:
            cleanup.apply_cleanup(
                self.session, roleless, approved_cleanup_sha256=plan_hash
            )
        self.assertEqual(caught.exception.code, 'ACTOR_UNAUTHORIZED')
        self.assert_denied_without_writes()

    def test_superuser_with_wrong_scope_is_refused_for_plan_and_apply(self):
        """Role grants never widen scope: the wrong tenant is denied."""
        self.make_wrong_tenant()
        outsider = self.make_actor(
            'authz-outsider', superuser=True, scope_codes={'other'}
        )
        plan_hash = self.approved_hash()
        with self.assertRaises(cleanup.CleanupError) as caught:
            cleanup.build_cleanup_plan(self.session, outsider)
        self.assertEqual(caught.exception.code, 'ACTOR_SCOPE')
        self.assertIn('A01', str(caught.exception))
        with self.assertRaises(cleanup.CleanupError) as caught:
            cleanup.apply_cleanup(
                self.session, outsider, approved_cleanup_sha256=plan_hash
            )
        self.assertEqual(caught.exception.code, 'ACTOR_SCOPE')
        self.assert_denied_without_writes()

    def test_unresolved_scope_fails_closed(self):
        """An actor whose scope resolves to nothing authorizes nothing."""
        unresolved = self.make_actor(
            'authz-unresolved', superuser=True, scope_codes=None
        )
        plan_hash = self.approved_hash()
        with self.assertRaises(cleanup.CleanupError) as caught:
            cleanup.build_cleanup_plan(self.session, unresolved)
        self.assertEqual(caught.exception.code, 'ACTOR_SCOPE')
        with self.assertRaises(cleanup.CleanupError) as caught:
            cleanup.apply_cleanup(
                self.session, unresolved, approved_cleanup_sha256=plan_hash
            )
        self.assertEqual(caught.exception.code, 'ACTOR_SCOPE')
        self.assert_denied_without_writes()

    def test_revoked_scope_denies_execution_after_planning(self):
        """A scope revoked after planning denies the execution."""
        operator = self.make_actor(
            'authz-revoked', superuser=True, scope_codes={'internal'}
        )
        plan_hash = cleanup.build_cleanup_plan(self.session, operator)['plan_hash']
        TEST_ACTOR_SCOPE_CODES.pop('authz-revoked', None)
        with self.assertRaises(cleanup.CleanupError) as caught:
            cleanup.apply_cleanup(
                self.session, operator, approved_cleanup_sha256=plan_hash
            )
        self.assertEqual(caught.exception.code, 'ACTOR_SCOPE')
        self.assert_denied_without_writes()

    def test_authorized_actor_still_plans_and_applies(self):
        """The authorized in-scope operator keeps working (positive control)."""
        plan = cleanup.build_cleanup_plan(self.session, self.actor)
        self.assertGreater(len(plan['deletions']), 0)
        result = cleanup.apply_cleanup(
            self.session, self.actor, approved_cleanup_sha256=plan['plan_hash']
        )
        self.assertNotIn('already_cleaned', result)
        session = DemoMetricsSession.objects.get(pk=self.session.pk)
        self.assertEqual(session.status, DemoMetricsSession.Status.CLEANED)
        self.assertFalse(session.machines.filter(claim_active=True).exists())

    def test_cleaned_repeat_readback_does_not_bypass_authorization(self):
        """The already-cleaned readback is disclosure, not an auth bypass."""
        plan = cleanup.build_cleanup_plan(self.session, self.actor)
        cleanup.apply_cleanup(
            self.session, self.actor, approved_cleanup_sha256=plan['plan_hash']
        )
        self.make_wrong_tenant()
        outsider = self.make_actor(
            'authz-readback', superuser=True, scope_codes={'other'}
        )
        with self.assertRaises(cleanup.CleanupError) as caught:
            cleanup.build_cleanup_plan(self.session, outsider)
        self.assertEqual(caught.exception.code, 'ACTOR_SCOPE')
        with self.assertRaises(cleanup.CleanupError) as caught:
            cleanup.apply_cleanup(
                self.session, outsider, approved_cleanup_sha256=plan['plan_hash']
            )
        self.assertEqual(caught.exception.code, 'ACTOR_SCOPE')
        # The authorized operator still gets the repeat readback.
        readback_hash = cleanup.build_cleanup_plan(self.session, self.actor)[
            'plan_hash'
        ]
        repeat = cleanup.apply_cleanup(
            self.session, self.actor, approved_cleanup_sha256=readback_hash
        )
        self.assertTrue(repeat['already_cleaned'])

    def test_inactive_claim_membership_is_still_scope_checked(self):
        """Memberships with inactive claims are scope-checked like active ones."""
        self.make_wrong_tenant()
        other = Client.objects.get(code='other')
        machine = self.machines['B03']
        machine.client = other
        machine.save(update_fields=['client'])
        membership = self.session.machines.get(alias='B03')
        membership.claim_active = False
        membership.save(update_fields=['claim_active'])
        with self.assertRaises(cleanup.CleanupError) as caught:
            cleanup.build_cleanup_plan(self.session, self.actor)
        self.assertEqual(caught.exception.code, 'ACTOR_SCOPE')
        self.assertIn('B03', str(caught.exception))
        with self.assertRaises(cleanup.CleanupError) as caught:
            cleanup.apply_cleanup(
                self.session, self.actor, approved_cleanup_sha256='0' * 64
            )
        self.assertEqual(caught.exception.code, 'ACTOR_SCOPE')

    def test_scope_is_rechecked_under_the_lock_before_any_effect(self):
        """Execution re-checks scope under the lock, before any effect."""
        real_check = cleanup._require_actor_scope
        state = {'calls': 0}

        def revoke_after_first_check(actor, session):
            state['calls'] += 1
            result = real_check(actor, session)
            if state['calls'] == 1:
                TEST_ACTOR_SCOPE_CODES.pop(actor.get_username(), None)
            return result

        plan_hash = self.approved_hash()
        with mock.patch(
            'assets.demo_metrics.cleanup._require_actor_scope', revoke_after_first_check
        ):
            with self.assertRaises(cleanup.CleanupError) as caught:
                cleanup.apply_cleanup(
                    self.session, self.actor, approved_cleanup_sha256=plan_hash
                )
        self.assertEqual(caught.exception.code, 'ACTOR_SCOPE')
        self.assertGreaterEqual(state['calls'], 2)
        self.assert_denied_without_writes()


@requires_postgres
@override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=_test_scope_resolver)
class DemoMetricsCommandAuthorizationTest(DemoMetricsEnvMixin, TestCase):
    """Command entries authorize before any lookup, readback or artifact."""

    def setUp(self):
        """Apply the demo session and stage a scratch directory for artifacts."""
        TEST_ACTOR_SCOPE_CODES.clear()
        self.addCleanup(TEST_ACTOR_SCOPE_CODES.clear)
        TEST_ACTOR_SCOPE_CODES['demo-operator'] = {'internal'}
        self.build_demo_env()
        self.apply_demo()
        self.session = self.session()
        self.ledger_before = self.session.ledger_objects.count()
        self.receipts_before = self.session.receipts.count()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def make_actor(self, username, *, superuser, scope_codes):
        """Create an operator; ``scope_codes`` of None leaves scope unresolved."""
        actor = get_user_model().objects.create_user(
            username=username,
            email=f'{username}@example.com',
            password='pw',
            is_superuser=superuser,
        )
        if scope_codes is not None:
            TEST_ACTOR_SCOPE_CODES[username] = set(scope_codes)
        return actor

    def run_cleanup(self, username, out_path, *, apply=False, plan_hash=None):
        """Invoke cleanup_demo_metrics with CLI-style arguments."""
        args = [
            '--session',
            self.session.session_key,
            '--actor',
            username,
            '--out',
            str(out_path),
        ]
        if apply:
            args = [
                '--session',
                self.session.session_key,
                '--actor',
                username,
                '--apply',
                '--approved-cleanup-sha256',
                plan_hash,
            ]
        call_command('cleanup_demo_metrics', *args)

    def run_plan(self, username, out_path):
        """Invoke plan_demo_metrics against the staged mapping."""
        mapping_path = self.tmp / 'mapping.json'
        mapping_path.write_text(json.dumps(self.mapping_data), encoding='utf-8')
        call_command(
            'plan_demo_metrics',
            '--fixture',
            str(FIXTURE_PATH),
            '--mapping',
            str(mapping_path),
            '--out',
            str(out_path),
            '--actor',
            username,
        )

    def test_cleanup_plan_denies_roleless_actor_and_writes_no_artifact(self):
        """Plan readback is closed to role-less actors, with no artifact."""
        roleless = self.make_actor(
            'cmd-roleless', superuser=False, scope_codes={'internal'}
        )
        out_path = self.tmp / 'cleanup-plan.json'
        with self.assertRaises(CommandError) as caught:
            self.run_cleanup(roleless.username, out_path)
        self.assertIn('ACTOR_UNAUTHORIZED', str(caught.exception))
        self.assertFalse(out_path.exists())

    def test_cleanup_plan_denies_wrong_scope_superuser_without_artifact(self):
        """A superuser out of scope gets no inventory and no artifact."""
        self.make_actor('cmd-outsider', superuser=True, scope_codes=set())
        outsider = get_user_model().objects.get(username='cmd-outsider')
        Client.objects.get_or_create(name='Other Tenant', defaults={'code': 'other'})
        TEST_ACTOR_SCOPE_CODES['cmd-outsider'] = {'other'}
        out_path = self.tmp / 'cleanup-plan.json'
        with self.assertRaises(CommandError) as caught:
            self.run_cleanup(outsider.username, out_path)
        self.assertIn('ACTOR_SCOPE', str(caught.exception))
        self.assertFalse(out_path.exists())

    def test_cleanup_plan_allows_the_authorized_actor(self):
        """Positive control: the authorized operator receives the readback."""
        out_path = self.tmp / 'cleanup-plan.json'
        self.run_cleanup(self.actor.username, out_path)
        self.assertTrue(out_path.exists())
        body = json.loads(out_path.read_text(encoding='utf-8'))
        self.assertIn('plan_hash', body)

    def test_cleanup_apply_denial_makes_no_writes(self):
        """A denied apply leaves the session, claims and receipts untouched."""
        plan_hash = cleanup.build_cleanup_plan(self.session, self.actor)['plan_hash']
        outsider = self.make_actor(
            'cmd-apply-outsider', superuser=True, scope_codes=None
        )
        with self.assertRaises(CommandError) as caught:
            self.run_cleanup(
                outsider.username,
                self.tmp / 'unused.json',
                apply=True,
                plan_hash=plan_hash,
            )
        self.assertIn('ACTOR_SCOPE', str(caught.exception))
        session = DemoMetricsSession.objects.get(pk=self.session.pk)
        self.assertEqual(session.status, DemoMetricsSession.Status.ACTIVE)
        self.assertTrue(session.machines.filter(claim_active=True).exists())
        self.assertEqual(session.ledger_objects.count(), self.ledger_before)
        self.assertEqual(session.receipts.count(), self.receipts_before)

    def test_plan_command_denies_roleless_actor_before_writing_the_plan(self):
        """plan_demo_metrics requires the role before it writes any artifact."""
        roleless = self.make_actor(
            'cmd-plan-roleless', superuser=False, scope_codes={'internal'}
        )
        out_path = self.tmp / 'plan.json'
        with self.assertRaises(CommandError) as caught:
            self.run_plan(roleless.username, out_path)
        self.assertIn('ACTOR_UNAUTHORIZED', str(caught.exception))
        self.assertFalse(out_path.exists())

    def test_plan_command_allows_the_authorized_actor(self):
        """Positive control: the authorized operator still gets a plan."""
        out_path = self.tmp / 'plan.json'
        self.run_plan(self.actor.username, out_path)
        self.assertTrue(out_path.exists())

    def test_stop_and_replay_authorize_before_session_lookup(self):
        """Sibling commands authorize first: no lookup, no state change."""
        roleless = self.make_actor(
            'cmd-feed-roleless', superuser=False, scope_codes={'internal'}
        )
        with self.assertRaises(CommandError) as caught:
            call_command(
                'stop_demo_metrics',
                '--session',
                'no-such-session',
                '--actor',
                roleless.username,
            )
        self.assertIn('ACTOR_UNAUTHORIZED', str(caught.exception))
        with self.assertRaises(CommandError) as caught:
            call_command(
                'replay_demo_metrics',
                '--session',
                self.session.session_key,
                '--actor',
                roleless.username,
                '--interval-seconds',
                '0',
                '--max-duration-seconds',
                '1',
            )
        self.assertIn('ACTOR_UNAUTHORIZED', str(caught.exception))
        session = DemoMetricsSession.objects.get(pk=self.session.pk)
        self.assertEqual(session.status, DemoMetricsSession.Status.ACTIVE)
        self.assertEqual(session.receipts.count(), self.receipts_before)

    def test_ambiguous_session_slug_is_never_disclosed_to_an_unauthorized_caller(self):
        """Caller authorization precedes any session lookup or ambiguity.

        Two datasets may share one session slug; the ambiguity itself is
        disclosure, so a caller who fails the role gate receives
        ``ACTOR_UNAUTHORIZED`` — never ``SESSION_AMBIGUOUS`` — and mutates
        nothing, exactly like the unknown-session lookup order.
        """
        clone = DemoMetricsSession.objects.get(pk=self.session.pk)
        clone.id = uuid.uuid4()
        clone.dataset_key = 'equa-other-dataset'
        clone.save()
        roleless = self.make_actor(
            'cmd-ambiguous-roleless', superuser=False, scope_codes={'internal'}
        )
        for command in ('stop_demo_metrics', 'cleanup_demo_metrics'):
            with self.assertRaises(CommandError) as caught:
                call_command(
                    command,
                    '--session',
                    self.session.session_key,
                    '--actor',
                    roleless.username,
                )
            self.assertIn('ACTOR_UNAUTHORIZED', str(caught.exception))
            self.assertNotIn('SESSION_AMBIGUOUS', str(caught.exception))
        for pk in (self.session.pk, clone.pk):
            row = DemoMetricsSession.objects.get(pk=pk)
            self.assertEqual(row.status, DemoMetricsSession.Status.ACTIVE)
        self.assertEqual(self.session.receipts.count(), self.receipts_before)


@requires_postgres
@override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=_test_scope_resolver)
class SessionScopeBoundaryTest(DemoMetricsEnvMixin, TestCase):
    """verify/stop/feed-start/plan scope boundary: disclosure and mutations.

    The current scope of every session membership — inactive claims included —
    is checked under the session lock before any receipt/ledger read, status
    write or repeat readback. A privileged actor with the wrong tenant or an
    unresolved scope receives no report data and changes nothing.
    """

    def setUp(self):
        """Apply the demo session and stage a scratch directory."""
        TEST_ACTOR_SCOPE_CODES.clear()
        self.addCleanup(TEST_ACTOR_SCOPE_CODES.clear)
        TEST_ACTOR_SCOPE_CODES['demo-operator'] = {'internal'}
        self.build_demo_env()
        self.apply_demo()
        self.session = self.session()
        self.ledger_before = self.session.ledger_objects.count()
        self.receipts_before = self.session.receipts.count()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def make_actor(self, username, *, superuser, scope_codes):
        """Create an operator; ``scope_codes`` of None leaves scope unresolved."""
        actor = get_user_model().objects.create_user(
            username=username,
            email=f'{username}@example.com',
            password='pw',
            is_superuser=superuser,
        )
        if scope_codes is not None:
            TEST_ACTOR_SCOPE_CODES[username] = set(scope_codes)
        return actor

    def make_wrong_tenant_actor(self, username):
        """A privileged operator scoped to a tenant the session never lives in."""
        Client.objects.get_or_create(name='Other Tenant', defaults={'code': 'other'})
        return self.make_actor(username, superuser=True, scope_codes={'other'})

    def assert_session_untouched(self):
        """Session status, claims, receipts and ledger are all unchanged."""
        session = DemoMetricsSession.objects.get(pk=self.session.pk)
        self.assertEqual(session.status, DemoMetricsSession.Status.ACTIVE)
        self.assertTrue(session.machines.filter(claim_active=True).exists())
        self.assertEqual(session.ledger_objects.count(), self.ledger_before)
        self.assertEqual(session.receipts.count(), self.receipts_before)

    def run_verify(self, username):
        """Run verify_demo_metrics capturing stdout; return the captured text."""
        stream = io.StringIO()
        call_command(
            'verify_demo_metrics',
            '--session',
            self.session.session_key,
            '--actor',
            username,
            stdout=stream,
        )
        return stream.getvalue()

    def verify_denied(self, username):
        """The verify run is refused with an empty stdout and no writes."""
        stream = io.StringIO()
        with self.assertRaises(CommandError) as caught:
            call_command(
                'verify_demo_metrics',
                '--session',
                self.session.session_key,
                '--actor',
                username,
                stdout=stream,
            )
        self.assertIn('ACTOR_SCOPE', str(caught.exception))
        self.assertEqual(stream.getvalue(), '')
        self.assert_session_untouched()

    def move_alias_out_of_tenant(self, alias, *, claim_active):
        """Move one membership's machine into the other tenant."""
        other, _created = Client.objects.get_or_create(
            name='Other Tenant', defaults={'code': 'other'}
        )
        machine = self.machines[alias]
        machine.client = other
        machine.save(update_fields=['client'])
        membership = self.session.machines.get(alias=alias)
        membership.claim_active = claim_active
        membership.save(update_fields=['claim_active'])

    def test_verify_denies_wrong_tenant_superuser_with_empty_stdout(self):
        """An out-of-scope privileged actor gets no verification report."""
        outsider = self.make_wrong_tenant_actor('scope-verify-outsider')
        self.verify_denied(outsider.username)

    def test_verify_denies_unresolved_scope_superuser(self):
        """An unresolved scope fails closed before any receipt is read."""
        unresolved = self.make_actor(
            'scope-verify-unresolved', superuser=True, scope_codes=None
        )
        self.verify_denied(unresolved.username)

    def test_verify_scope_checks_inactive_claim_memberships(self):
        """An inactive claim still names a machine the report discloses."""
        self.move_alias_out_of_tenant('B03', claim_active=False)
        self.verify_denied(self.actor.username)

    def test_verify_still_reports_for_the_authorized_actor(self):
        """Positive control: the in-scope operator keeps the report."""
        output = self.run_verify(self.actor.username)
        report, _end = json.JSONDecoder().raw_decode(output)
        self.assertTrue(report['acceptance']['ok'])

    def test_stop_denies_wrong_tenant_without_status_or_receipt_writes(self):
        """A denied stop changes no status and writes no receipt, twice."""
        outsider = self.make_wrong_tenant_actor('scope-stop-outsider')
        for _attempt in range(2):
            with self.assertRaises(replay.ReplayError) as caught:
                replay.stop_session(self.session, outsider)
            self.assertEqual(caught.exception.code, 'ACTOR_SCOPE')
        self.assert_session_untouched()

    def test_repeated_stop_readback_is_not_given_to_a_denied_actor(self):
        """The already-stopped readback is disclosure, not an auth bypass."""
        replay.stop_session(self.session, self.actor)
        TEST_ACTOR_SCOPE_CODES.pop('demo-operator', None)
        for _attempt in range(2):
            with self.assertRaises(replay.ReplayError) as caught:
                replay.stop_session(self.session, self.actor)
            self.assertEqual(caught.exception.code, 'ACTOR_SCOPE')
        session = DemoMetricsSession.objects.get(pk=self.session.pk)
        self.assertEqual(session.status, DemoMetricsSession.Status.STOPPED)
        self.assertEqual(session.receipts.count(), self.receipts_before + 1)

    def test_feed_start_denies_wrong_tenant_without_creating_the_claim(self):
        """A denied feed start writes no claim, on the first or any repeat."""
        outsider = self.make_wrong_tenant_actor('scope-feed-outsider')
        for _attempt in range(2):
            with self.assertRaises(replay.ReplayError) as caught:
                replay.start_feed(self.session, outsider)
            self.assertEqual(caught.exception.code, 'ACTOR_SCOPE')
        self.assert_session_untouched()

    def test_repeated_feed_start_readback_is_not_given_to_a_denied_actor(self):
        """The already-started readback never reaches a revoked operator."""
        replay.start_feed(self.session, self.actor)
        receipts_after_start = self.session.receipts.count()
        TEST_ACTOR_SCOPE_CODES.pop('demo-operator', None)
        for _attempt in range(2):
            with self.assertRaises(replay.ReplayError) as caught:
                replay.start_feed(self.session, self.actor)
            self.assertEqual(caught.exception.code, 'ACTOR_SCOPE')
        session = DemoMetricsSession.objects.get(pk=self.session.pk)
        self.assertEqual(session.status, DemoMetricsSession.Status.ACTIVE)
        self.assertEqual(session.receipts.count(), receipts_after_start)

    def test_foreign_feed_claim_is_never_disclosed_to_a_denied_actor(self):
        """A scope-denied actor gets ACTOR_SCOPE, never the claim oracle.

        FEED_CONFLICT versus ACTOR_SCOPE on a denied start would disclose
        whether another operator's durable feed claim exists; the denial is
        uniform (ACTOR_SCOPE) regardless of the claim state.
        """
        outsider = self.make_wrong_tenant_actor('scope-feed-conflict')
        replay.start_feed(self.session, self.actor)
        receipts_after_start = self.session.receipts.count()
        for _attempt in range(2):
            with self.assertRaises(replay.ReplayError) as caught:
                replay.start_feed(self.session, outsider)
            self.assertEqual(caught.exception.code, 'ACTOR_SCOPE')
        self.assertEqual(self.session.receipts.count(), receipts_after_start)

    def test_authorized_second_operator_keeps_the_feed_conflict_contract(self):
        """A genuinely authorized second operator still sees FEED_CONFLICT."""
        second = self.make_actor(
            'scope-feed-second', superuser=True, scope_codes={'internal'}
        )
        replay.start_feed(self.session, self.actor)
        receipts_after_start = self.session.receipts.count()
        with self.assertRaises(replay.ReplayError) as caught:
            replay.start_feed(self.session, second)
        self.assertEqual(caught.exception.code, 'FEED_CONFLICT')
        self.assertEqual(self.session.receipts.count(), receipts_after_start)

    def test_denied_feed_start_queries_no_receipt_claim_state(self):
        """A denied start never queries the receipt claim seam at all."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from assets.demo_metrics_models import DemoMetricsReceipt

        outsider = self.make_wrong_tenant_actor('scope-feed-noquery')
        replay.start_feed(self.session, self.actor)
        table = DemoMetricsReceipt._meta.db_table
        with CaptureQueriesContext(connection) as queries:
            with self.assertRaises(replay.ReplayError) as caught:
                replay.start_feed(self.session, outsider)
        self.assertEqual(caught.exception.code, 'ACTOR_SCOPE')
        self.assertFalse(
            [query for query in queries.captured_queries if table in query['sql']],
            'denied feed start must not query the receipt claim state',
        )

    def test_denied_batch_denies_scope_before_feedable_and_config_drift(self):
        """ACTOR_SCOPE precedes expired/stopped/config-drift for a denial."""
        TEST_ACTOR_SCOPE_CODES.pop('demo-operator', None)
        # Expired session: no status write and no SESSION_EXPIRED disclosure.
        session = DemoMetricsSession.objects.get(pk=self.session.pk)
        session.expires_at = timezone.now() - dt.timedelta(minutes=1)
        session.save(update_fields=['expires_at'])
        for _attempt in range(2):
            with self.assertRaises(replay.ReplayError) as caught:
                replay.replay_batch(self.session, self.actor, batch_key='batch-1')
            self.assertEqual(caught.exception.code, 'ACTOR_SCOPE')
        session = DemoMetricsSession.objects.get(pk=self.session.pk)
        self.assertEqual(session.status, DemoMetricsSession.Status.ACTIVE)
        # Config drift is not disclosed to a denied actor either.
        session.expires_at = timezone.now() + dt.timedelta(hours=1)
        session.save(update_fields=['expires_at'])
        state = MachineSignalState.objects.get(
            binding__external_key=(
                f'equa-demo-metrics-v1/{self.session.session_key}/'
                'A01/bearing_temperature'
            )
        )
        state.binding.external_key = 'operator.retuned.tag'  # codespell:ignore retuned
        state.binding.save(update_fields=['external_key'])
        with self.assertRaises(replay.ReplayError) as caught:
            replay.replay_batch(self.session, self.actor, batch_key='batch-1')
        self.assertEqual(caught.exception.code, 'ACTOR_SCOPE')

    def test_denied_batch_on_a_stopped_session_denies_scope_first(self):
        """A stopped session discloses nothing to a scope-denied actor."""
        replay.stop_session(self.session, self.actor)
        TEST_ACTOR_SCOPE_CODES.pop('demo-operator', None)
        with self.assertRaises(replay.ReplayError) as caught:
            replay.replay_batch(self.session, self.actor, batch_key='batch-1')
        self.assertEqual(caught.exception.code, 'ACTOR_SCOPE')

    def test_machine_change_after_the_gate_denies_scope_before_any_drift_code(self):
        """A machine changed after the gate denies ACTOR_SCOPE, not drift.

        The session lock does not serialize machine client/placement edits:
        a concurrent change can land after the all-membership scope gate
        (``_session_scope_error``) and before the locked machine reload that
        feeds the drift checks. The wrappers below inject each change
        immediately after the real gate returns ``None`` — proving every
        initial membership check genuinely passed — and the reloaded machine
        must then deny ``ACTOR_SCOPE`` before any ``CLIENT_CHANGED`` /
        ``PLACEMENT_CHANGED`` disclosure.
        """
        real_gate = replay._session_scope_error
        other, _created = Client.objects.get_or_create(
            name='Other Tenant', defaults={'code': 'other'}
        )
        machine = self.machines['A01']

        def run_with_injection(inject, batch_key):
            """Replay one batch with ``inject`` landing in the race window."""
            state = {'calls': 0, 'gate_result': 'not-called'}

            def gate_then_inject(actor, session):
                result = real_gate(actor, session)
                state['calls'] += 1
                if state['calls'] == 1:
                    state['gate_result'] = result
                    inject()
                return result

            with mock.patch(
                'assets.demo_metrics.replay._session_scope_error', gate_then_inject
            ):
                with self.assertRaises(replay.ReplayError) as caught:
                    replay.replay_batch(self.session, self.actor, batch_key=batch_key)
            self.assertEqual(state['calls'], 1)
            self.assertIsNone(
                state['gate_result'],
                'the initial all-membership scope gate must genuinely pass',
            )
            return caught.exception.code

        def concurrent_client_change():
            """The machine leaves the actor's scope mid-batch."""
            machine.client = other
            machine.save(update_fields=['client'])

        def concurrent_placement_change_with_lost_scope():
            """The machine moved and the operator's scope vanished mid-batch."""
            machine.physical_location = self.locations['A-LINE']
            machine.save(update_fields=['physical_location'])
            TEST_ACTOR_SCOPE_CODES.pop('demo-operator', None)

        codes = [
            run_with_injection(concurrent_client_change, 'batch-client-drift'),
            run_with_injection(
                concurrent_placement_change_with_lost_scope, 'batch-placement-drift'
            ),
        ]
        self.assertEqual(
            codes,
            ['ACTOR_SCOPE', 'ACTOR_SCOPE'],
            'a machine changed after the gate must deny scope before drift codes',
        )
        self.assert_session_untouched()

    def test_inactive_claim_membership_still_gates_feed_and_stop(self):
        """Inactive memberships gate the session-level replay mutations too."""
        self.move_alias_out_of_tenant('B03', claim_active=False)
        for action in (
            lambda: replay.start_feed(self.session, self.actor),
            lambda: replay.stop_session(self.session, self.actor),
        ):
            with self.assertRaises(replay.ReplayError) as caught:
                action()
            self.assertEqual(caught.exception.code, 'ACTOR_SCOPE')
        self.assert_session_untouched()

    def test_plan_denies_wrong_tenant_superuser_without_artifact(self):
        """A plan for out-of-scope targets is never written to disk."""
        outsider = self.make_wrong_tenant_actor('scope-plan-outsider')
        mapping_path = self.tmp / 'mapping.json'
        mapping_path.write_text(json.dumps(self.mapping_data), encoding='utf-8')
        out_path = self.tmp / 'plan.json'
        with self.assertRaises(CommandError) as caught:
            call_command(
                'plan_demo_metrics',
                '--fixture',
                str(FIXTURE_PATH),
                '--mapping',
                str(mapping_path),
                '--out',
                str(out_path),
                '--actor',
                outsider.username,
            )
        self.assertIn('ACTOR_SCOPE', str(caught.exception))
        self.assertFalse(out_path.exists())

    def test_plan_denies_unresolved_scope_superuser_without_artifact(self):
        """An unresolved scope produces no plan artifact either."""
        unresolved = self.make_actor(
            'scope-plan-unresolved', superuser=True, scope_codes=None
        )
        mapping_path = self.tmp / 'mapping.json'
        mapping_path.write_text(json.dumps(self.mapping_data), encoding='utf-8')
        out_path = self.tmp / 'plan.json'
        with self.assertRaises(CommandError) as caught:
            call_command(
                'plan_demo_metrics',
                '--fixture',
                str(FIXTURE_PATH),
                '--mapping',
                str(mapping_path),
                '--out',
                str(out_path),
                '--actor',
                unresolved.username,
            )
        self.assertIn('ACTOR_SCOPE', str(caught.exception))
        self.assertFalse(out_path.exists())

    def plan_denied_without_artifact(self, username, mapping_data):
        """The plan command refuses the actor and writes no artifact."""
        mapping_path = self.tmp / 'mapping.json'
        mapping_path.write_text(json.dumps(mapping_data), encoding='utf-8')
        out_path = self.tmp / 'plan.json'
        with self.assertRaises(CommandError) as caught:
            call_command(
                'plan_demo_metrics',
                '--fixture',
                str(FIXTURE_PATH),
                '--mapping',
                str(mapping_path),
                '--out',
                str(out_path),
                '--actor',
                username,
            )
        self.assertIn('ACTOR_SCOPE', str(caught.exception))
        self.assertFalse(out_path.exists())

    def drifted_client_mapping(self):
        """Move every live machine out of the mapped client (CLIENT_MISMATCH)."""
        drifted, _created = Client.objects.get_or_create(
            name='Drifted Tenant', defaults={'code': 'drifted'}
        )
        for machine in self.machines.values():
            machine.client = drifted
            machine.save(update_fields=['client'])
        return self.mapping_data

    def mismatched_version_mapping(self):
        """A mapping whose expected placement version no target matches."""
        data = json.loads(json.dumps(self.mapping_data))
        for entry in data['machines'].values():
            entry['expected_version'] = 99
        return data

    def test_plan_denies_the_wrong_tenant_when_targets_report_client_mismatch(self):
        """An early CLIENT_MISMATCH exit never bypasses the scope boundary."""
        outsider = self.make_wrong_tenant_actor('scope-plan-client-drift')
        self.plan_denied_without_artifact(
            outsider.username, self.drifted_client_mapping()
        )

    def test_plan_denies_the_wrong_tenant_when_targets_report_placement_change(self):
        """An early PLACEMENT_CHANGED exit never bypasses the scope boundary."""
        outsider = self.make_wrong_tenant_actor('scope-plan-placement-drift')
        self.plan_denied_without_artifact(
            outsider.username, self.mismatched_version_mapping()
        )

    def test_plan_denies_the_wrong_tenant_when_every_target_is_inactive(self):
        """An early MACHINE_INACTIVE exit never bypasses the scope boundary."""
        outsider = self.make_wrong_tenant_actor('scope-plan-inactive')
        for machine in self.machines.values():
            machine.active = False
            machine.save(update_fields=['active'])
        self.plan_denied_without_artifact(outsider.username, self.mapping_data)

    def test_unresolved_scope_fails_closed_when_targets_early_exit(self):
        """Unresolved scope fails closed even if resolution short circuits."""
        unresolved = self.make_actor(
            'scope-plan-unresolved-exit', superuser=True, scope_codes=None
        )
        self.plan_denied_without_artifact(
            unresolved.username, self.mismatched_version_mapping()
        )

    def test_plan_still_writes_in_scope_conflict_artifacts(self):
        """In-scope conflicts remain recorded in a written artifact."""
        mapping_path = self.tmp / 'mapping.json'
        mapping_path.write_text(
            json.dumps(self.mismatched_version_mapping()), encoding='utf-8'
        )
        out_path = self.tmp / 'plan.json'
        call_command(
            'plan_demo_metrics',
            '--fixture',
            str(FIXTURE_PATH),
            '--mapping',
            str(mapping_path),
            '--out',
            str(out_path),
            '--actor',
            self.actor.username,
        )
        self.assertTrue(out_path.exists())
        plan = json.loads(out_path.read_text(encoding='utf-8'))
        codes = {item['code'] for item in plan['effects']['conflict']}
        self.assertIn('PLACEMENT_CHANGED', codes)
        self.assertFalse({'OUT_OF_SCOPE', 'SCOPE_UNRESOLVED'} & codes)

    def test_denied_plan_reads_no_telemetry_anomaly_or_ownership_state(self):
        """A denied resolution performs no protected state reads."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from assets.demo_metrics_models import DemoMetricsMachine
        from assets.models import AssetMachine

        outsider = self.make_wrong_tenant_actor('scope-plan-no-protected-reads')
        # ``rel.model`` is the FK target (AssetMachine); the protected reads
        # live in the FK-owning models, reached via ``rel.field.model``.
        forbidden = {
            AssetMachine.signal_bindings.rel.field.model._meta.db_table,
            AssetMachine.anomalies.rel.field.model._meta.db_table,
            AssetMachine.machine_parts.rel.field.model._meta.db_table,
            DemoMetricsMachine._meta.db_table,
        }
        mapping_path = self.tmp / 'mapping.json'
        # Clean mapping: every target passes the state checks, so any
        # telemetry/anomaly/ownership read happens before the scope denial.
        mapping_path.write_text(json.dumps(self.mapping_data), encoding='utf-8')
        out_path = self.tmp / 'plan.json'
        with CaptureQueriesContext(connection) as queries:
            with self.assertRaises(CommandError):
                call_command(
                    'plan_demo_metrics',
                    '--fixture',
                    str(FIXTURE_PATH),
                    '--mapping',
                    str(mapping_path),
                    '--out',
                    str(out_path),
                    '--actor',
                    outsider.username,
                )
        leaked = [
            query['sql']
            for query in queries.captured_queries
            if any(table in query['sql'] for table in forbidden)
        ]
        self.assertEqual(
            leaked, [], 'denied plan resolution must not read protected state'
        )
