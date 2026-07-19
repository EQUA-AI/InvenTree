"""Finding lifecycle command and notification tests (US3, FR-RR-006/007/010)."""

from datetime import timedelta

from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.utils import timezone

from .risk_models import (
    RiskFindingState,
    RiskNotificationDelivery,
    RiskScanRun,
    RiskScanStatus,
)
from .risk_scope import RiskScopeError
from .risk_services import (
    ASSIGN_TARGET_NOT_VISIBLE,
    DISMISS_REASON_REQUIRED,
    FINDING_STATE_CONFLICT,
    IDEMPOTENCY_CONFLICT,
    SNOOZE_INVALID,
    RiskCommandError,
    deliver_pending_notifications,
    execute_finding_command,
)
from .risk_testing import (
    RISK_FLAGS,
    SCOPES_BY_USERNAME,
    RiskEnvMixin,
    fresh,
    grant_permissions,
)

ALL_COMMAND_PERMS = [
    'view_riskfinding',
    'acknowledge_riskfinding',
    'assign_riskfinding',
    'snooze_riskfinding',
    'dismiss_riskfinding',
]


@override_settings(**RISK_FLAGS)
class FindingCommandTest(RiskEnvMixin, TestCase):
    """Ownership commands: permissioned, versioned, idempotent, evented."""

    def setUp(self):
        """Build the environment and a commandable finding."""
        self.build_env()
        self.addCleanup(self.teardown_scopes)
        grant_permissions(self.actor, ALL_COMMAND_PERMS)
        self.actor = fresh(self.actor)
        SCOPES_BY_USERNAME['risk-actor'] = {self.scope}
        self.finding = self.make_finding()

    def command(
        self, command, *, actor=None, expected_version=None, key='k1', **arguments
    ):
        """Execute a command with sensible defaults."""
        return execute_finding_command(
            actor or self.actor,
            self.finding.pk,
            command,
            expected_version=expected_version or self.finding.version,
            idempotency_key=key,
            arguments=arguments,
        )

    def test_acknowledge_records_event_and_bumps_version(self):
        """Acknowledge transitions state and appends an immutable event."""
        result = self.command('acknowledge')
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.state, RiskFindingState.ACKNOWLEDGED)
        self.assertEqual(self.finding.version, 2)
        self.assertEqual(result['event_type'], 'acknowledged')
        self.assertEqual(
            self.finding.events.filter(event_type='acknowledged').count(), 1
        )

    def test_source_record_never_touched(self):
        """Commands change nothing outside the radar's own tables."""
        machine_updated = self.machine.updated_at
        self.command('acknowledge')
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.updated_at, machine_updated)

    def test_permission_required_per_command(self):
        """Each command demands its own permission."""
        viewer = fresh(self.service)
        grant_permissions(viewer, ['view_riskfinding'])
        viewer = fresh(viewer)
        SCOPES_BY_USERNAME['risk-service'] = {self.scope}
        with self.assertRaises(PermissionDenied):
            self.command('acknowledge', actor=viewer)

    def test_stale_version_conflicts(self):
        """A mismatched expected version yields FINDING_STATE_CONFLICT."""
        with self.assertRaises(RiskCommandError) as ctx:
            self.command('acknowledge', expected_version=99)
        self.assertEqual(ctx.exception.code, FINDING_STATE_CONFLICT)

    def test_idempotent_replay_and_conflict(self):
        """Exact replays return the original result; reuse conflicts."""
        first = self.command('acknowledge', key='same-key')
        replay = self.command(
            'acknowledge',
            key='same-key',
            expected_version=99,  # ignored on replay
        )
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['event_id'], first['event_id'])
        self.assertEqual(
            self.finding.events.filter(event_type='acknowledged').count(), 1
        )
        with self.assertRaises(RiskCommandError) as ctx:
            self.command(
                'snooze',
                key='same-key',
                snooze_until=(timezone.now() + timedelta(days=1)).isoformat(),
            )
        self.assertEqual(ctx.exception.code, IDEMPOTENCY_CONFLICT)

    def test_snooze_requires_future_expiry(self):
        """Snooze validates its expiry and sets the state."""
        with self.assertRaises(RiskCommandError) as ctx:
            self.command(
                'snooze', snooze_until=(timezone.now() - timedelta(hours=1)).isoformat()
            )
        self.assertEqual(ctx.exception.code, SNOOZE_INVALID)
        until = timezone.now() + timedelta(hours=8)
        self.command('snooze', key='k2', snooze_until=until.isoformat())
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.state, RiskFindingState.SNOOZED)
        self.assertIsNotNone(self.finding.snooze_until)

    def test_dismiss_requires_reason_and_sets_recheck(self):
        """Dismissal demands a reason and carries a recheck timestamp."""
        with self.assertRaises(RiskCommandError) as ctx:
            self.command('dismiss')
        self.assertEqual(ctx.exception.code, DISMISS_REASON_REQUIRED)
        self.command('dismiss', key='k3', reason='Known duplicate', recheck_hours=24)
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.state, RiskFindingState.DISMISSED)
        self.assertIsNotNone(self.finding.dismiss_recheck_at)

    def test_resolved_finding_rejects_commands(self):
        """Commands are invalid on non-active findings."""
        self.finding.state = RiskFindingState.RESOLVED
        self.finding.save(update_fields=['state'])
        with self.assertRaises(RiskCommandError) as ctx:
            self.command('acknowledge')
        self.assertEqual(ctx.exception.code, FINDING_STATE_CONFLICT)

    def test_actor_without_scope_is_rejected(self):
        """A viewer without the finding's scope cannot command it."""
        SCOPES_BY_USERNAME['risk-actor'] = {self.other_scope}
        with self.assertRaises(RiskScopeError):
            self.command('acknowledge')

    def test_assignment_validates_target_visibility(self):
        """Assignment rejects targets lacking scope or permissions."""
        no_scope = fresh(self.service)
        grant_permissions(no_scope, ['view_riskfinding'])
        SCOPES_BY_USERNAME.pop('risk-service', None)
        with self.assertRaises(RiskCommandError) as ctx:
            self.command('assign', owner_id=no_scope.pk)
        self.assertEqual(ctx.exception.code, ASSIGN_TARGET_NOT_VISIBLE)

        no_perm_scoped = fresh(self.service)
        SCOPES_BY_USERNAME['risk-service'] = {self.scope}
        no_perm_scoped.user_permissions.clear()
        with self.assertRaises(RiskCommandError):
            self.command('assign', owner_id=no_perm_scoped.pk)

    def test_assignment_success_sets_owner(self):
        """A visible, scoped target becomes the owner."""
        target = fresh(self.service)
        grant_permissions(target, ['view_riskfinding'])
        SCOPES_BY_USERNAME['risk-service'] = {self.scope}
        result = self.command('assign', owner_id=target.pk)
        self.assertEqual(result['owner_id'], target.pk)
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.owner_id, target.pk)


@override_settings(**RISK_FLAGS, AIMMS_RISK_NOTIFICATIONS_ENABLED=True)
class NotificationTest(RiskEnvMixin, TestCase):
    """Transition-driven notification intents and delivery."""

    def setUp(self):
        """Build the environment and an assignable finding."""
        self.build_env()
        self.addCleanup(self.teardown_scopes)
        grant_permissions(self.actor, ALL_COMMAND_PERMS)
        self.actor = fresh(self.actor)
        SCOPES_BY_USERNAME['risk-actor'] = {self.scope}
        self.finding = self.make_finding()

    def _assign_to_actor(self, key='n1'):
        """Assign the finding to the actor (an eligible recipient)."""
        self.finding.refresh_from_db()
        return execute_finding_command(
            self.actor,
            self.finding.pk,
            'assign',
            expected_version=self.finding.version,
            idempotency_key=key,
            arguments={'owner_id': self.actor.pk},
        )

    def test_assignment_stages_pending_intent(self):
        """Assignment inserts one pending intent, atomically deduped."""
        self._assign_to_actor()
        intents = RiskNotificationDelivery.objects.filter(recipient=self.actor)
        self.assertEqual(intents.count(), 1)
        self.assertEqual(intents.first().state, 'pending')

    def test_sweeper_delivers_pending(self):
        """The sweeper delivers due intents exactly once."""
        self._assign_to_actor()
        self.assertEqual(deliver_pending_notifications(), 1)
        intent = RiskNotificationDelivery.objects.get(recipient=self.actor)
        self.assertEqual(intent.state, 'sent')
        self.assertIsNotNone(intent.sent_at)
        # Replay is a no-op: the occurrence was already delivered.
        self.assertEqual(deliver_pending_notifications(), 0)

    def test_revoked_visibility_suppresses(self):
        """A recipient who lost scope is suppressed with a reason."""
        self._assign_to_actor()
        SCOPES_BY_USERNAME['risk-actor'] = {self.other_scope}
        self.assertEqual(deliver_pending_notifications(), 0)
        intent = RiskNotificationDelivery.objects.get(recipient=self.actor)
        self.assertEqual(intent.state, 'suppressed')
        self.assertIn('visibility', intent.suppression_reason)

    def test_failed_scan_notice_rechecks_scope(self):
        """Failed-scan notices cannot disclose a scope after access is revoked."""
        run = RiskScanRun.objects.create(
            rule=self.finding.rule_revision,
            rule_version=self.finding.rule_version,
            activation_generation=self.finding.rule_revision.activation_generation,
            scope_key=self.scope_key,
            service_identity=self.service,
            lease_token='failed-scan',
            started_at=timezone.now(),
            completed_at=timezone.now(),
            status=RiskScanStatus.FAILED,
        )
        RiskNotificationDelivery.objects.create(
            scan_run=run,
            recipient=self.actor,
            channel='ui',
            occurrence_key='failed-scan-scope-revoked',
            state='pending',
            not_before=timezone.now(),
        )
        SCOPES_BY_USERNAME['risk-actor'] = {self.other_scope}

        self.assertEqual(deliver_pending_notifications(), 0)
        intent = RiskNotificationDelivery.objects.get(
            occurrence_key='failed-scan-scope-revoked'
        )
        self.assertEqual(intent.state, 'suppressed')
        self.assertIn('visibility', intent.suppression_reason)

    def test_quiet_hours_defer_delivery(self):
        """Quiet hours push not_before instead of sending."""
        self._assign_to_actor()
        intent = RiskNotificationDelivery.objects.get(recipient=self.actor)
        now = timezone.now()
        window = {'start': f'{now.hour:02d}:00', 'end': f'{(now.hour + 1) % 24:02d}:59'}
        intent.policy_snapshot = {**intent.policy_snapshot, 'quiet_hours': window}
        intent.save(update_fields=['policy_snapshot'])
        self.assertEqual(deliver_pending_notifications(), 0)
        intent.refresh_from_db()
        self.assertEqual(intent.state, 'pending')
        self.assertGreater(intent.not_before, now)

    def test_flag_off_stages_nothing(self):
        """With notifications disabled, no intents are created."""
        with override_settings(AIMMS_RISK_NOTIFICATIONS_ENABLED=False):
            self._assign_to_actor(key='n2')
        self.assertEqual(RiskNotificationDelivery.objects.count(), 0)
