"""Scan-engine semantics tests (SC-RR-002 / SC-RR-003).

Uses a scripted rule so every engine path — staging, promotion, complete-
scan-only resolution, lease fencing, cap breaking, reopen and supersession
— can be exercised deterministically.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from .risk_models import (
    RiskFinding,
    RiskFindingState,
    RiskScanCandidate,
    RiskScanLease,
    RiskScanRun,
    RiskScanStatus,
)
from .risk_rules import RULE_SPECS
from .risk_services import (
    RULE_DISABLED,
    SCAN_LEASE_HELD,
    RiskCommandError,
    _finalize_run,
    compute_fingerprint,
    derive_severity,
    dispatch_scans,
    run_rule_scan,
    update_rule_configuration,
)
from .risk_testing import (
    RISK_FLAGS,
    RiskEnvMixin,
    ScriptedRule,
    grant_permissions,
    make_candidate,
    scripted_spec,
)


@override_settings(**RISK_FLAGS, AIMMS_RISK_RULES_ENABLED=['SCRIPTED_RULE'])
class ScanEngineTest(RiskEnvMixin, TestCase):
    """End-to-end engine semantics with a scripted rule."""

    def setUp(self):
        """Register the scripted rule and enable it for the client scope.

        The scripted rule is machine-sourced (``asset_machine``), so it is
        enabled and scanned under the machine tenant's client scope.
        """
        self.build_env()
        self.addCleanup(self.teardown_scopes)
        self.rule = ScriptedRule()
        RULE_SPECS[self.rule.code] = scripted_spec(self.rule)
        self.addCleanup(RULE_SPECS.pop, self.rule.code, None)
        self.enable_rule(self.rule.code, scopes=[self.client_scope_key])

    def scan(self):
        """Run one scan as the service identity."""
        return run_rule_scan(
            self.rule.code, self.client_scope, service_identity=self.service
        )

    def findings(self):
        """Return this rule's findings for the client scope."""
        return RiskFinding.objects.filter(
            rule_code=self.rule.code, scope_key=self.client_scope_key
        )

    def test_upsert_resolve_reopen_cycle(self):
        """Identical conditions never duplicate; clearance resolves; recurrence reopens."""
        self.rule.script = [[make_candidate('m1'), make_candidate('m2')]]
        run = self.scan()
        self.assertEqual(run.status, RiskScanStatus.COMPLETE)
        self.assertEqual(self.findings().count(), 2)
        finding = self.findings().get(source_id='m1')
        self.assertEqual(finding.state, RiskFindingState.OPEN)
        self.assertEqual(finding.events.filter(event_type='detected').count(), 1)

        # Same conditions again: zero duplicates, last_seen advances.
        self.rule.script = [[make_candidate('m1'), make_candidate('m2')]]
        self.scan()
        self.assertEqual(self.findings().count(), 2)
        finding.refresh_from_db()
        self.assertEqual(finding.reopen_count, 0)

        # m2 clears: complete scan resolves it, m1 stays open.
        self.rule.script = [[make_candidate('m1')]]
        run3 = self.scan()
        self.assertEqual(run3.resolve_count, 1)
        resolved = self.findings().get(source_id='m2')
        self.assertEqual(resolved.state, RiskFindingState.RESOLVED)
        self.assertEqual(resolved.events.filter(event_type='resolved').count(), 1)

        # m2 recurs: the same finding row reopens visibly.
        self.rule.script = [[make_candidate('m1'), make_candidate('m2')]]
        self.scan()
        resolved.refresh_from_db()
        self.assertEqual(resolved.state, RiskFindingState.OPEN)
        self.assertEqual(resolved.reopen_count, 1)
        self.assertEqual(self.findings().count(), 2)

    def test_action_link_replacement_is_idempotent(self):
        """A rescan replaces the complete link set without duplicates."""
        first = {
            'label': 'Open machine',
            'target_kind': 'asset_machine',
            'target_id': '1',
            'route': '/machines/machine/1/',
        }
        replacement = {
            'label': 'Open replacement machine',
            'target_kind': 'asset_machine',
            'target_id': '2',
            'route': '/machines/machine/2/',
        }
        self.rule.script = [[make_candidate('linked', action_links=[first])]]
        self.scan()
        finding = self.findings().get(source_id='linked')
        self.assertEqual(
            list(finding.action_links.values_list('target_id', flat=True)), ['1']
        )

        self.rule.script = [[make_candidate('linked', action_links=[replacement])]]
        self.scan()
        self.scan()
        self.assertEqual(
            list(finding.action_links.values_list('target_id', 'label', 'route')),
            [('2', 'Open replacement machine', '/machines/machine/2/')],
        )

    def test_failed_scan_resolves_nothing(self):
        """A failed scan discards staging and cannot resolve findings."""
        self.rule.script = [[make_candidate('m1')]]
        self.scan()
        self.assertEqual(self.findings().count(), 1)

        self.rule.script = [[], RuntimeError('source exploded')]
        with self.assertRaises(RiskCommandError):
            self.scan()
        run = RiskScanRun.objects.filter(rule__code=self.rule.code).latest('started_at')
        self.assertEqual(run.status, RiskScanStatus.FAILED)
        self.assertEqual(RiskScanCandidate.objects.filter(run=run).count(), 0)
        finding = self.findings().get(source_id='m1')
        self.assertEqual(finding.state, RiskFindingState.OPEN)

    def test_incomplete_snapshot_never_finalizes(self):
        """A rule that never completes its snapshot cannot promote or resolve."""
        self.rule.script = [[make_candidate('m1')]]
        self.scan()
        self.rule.script = [[]]
        self.rule.never_complete = True
        with self.assertRaises(RiskCommandError):
            self.scan()
        self.rule.never_complete = False
        finding = self.findings().get(source_id='m1')
        self.assertEqual(finding.state, RiskFindingState.OPEN)

    @override_settings(AIMMS_RISK_SCAN_UPSERT_CAP=2)
    def test_candidate_cap_aborts(self):
        """The storm breaker aborts the run and touches nothing."""
        self.rule.script = [[make_candidate(f'm{i}') for i in range(3)]]
        with self.assertRaises(RiskCommandError) as ctx:
            self.scan()
        self.assertEqual(ctx.exception.code, 'SCAN_CANDIDATE_CAP')
        run = RiskScanRun.objects.filter(rule__code=self.rule.code).latest('started_at')
        self.assertEqual(run.status, RiskScanStatus.ABORTED)
        self.assertEqual(self.findings().count(), 0)
        self.assertEqual(RiskScanCandidate.objects.count(), 0)

    def test_live_lease_blocks_concurrent_scan(self):
        """An unexpired foreign lease refuses a second scan."""
        now = timezone.now()
        RiskScanLease.objects.create(
            rule_code=self.rule.code,
            scope_key=self.client_scope_key,
            owner='other-worker',
            lease_token='foreign',
            expires_at=now + timedelta(minutes=5),
            heartbeat_at=now,
        )
        self.rule.script = [[make_candidate('m1')]]
        with self.assertRaises(RiskCommandError) as ctx:
            self.scan()
        self.assertEqual(ctx.exception.code, SCAN_LEASE_HELD)
        self.assertEqual(self.findings().count(), 0)

    def test_expired_lease_takeover(self):
        """A stale lease is taken over and the scan completes."""
        now = timezone.now()
        RiskScanLease.objects.create(
            rule_code=self.rule.code,
            scope_key=self.client_scope_key,
            owner='dead-worker',
            lease_token='stale',
            expires_at=now - timedelta(minutes=5),
            heartbeat_at=now - timedelta(minutes=15),
        )
        self.rule.script = [[make_candidate('m1')]]
        run = self.scan()
        self.assertEqual(run.status, RiskScanStatus.COMPLETE)
        self.assertEqual(self.findings().count(), 1)

    def test_lease_takeover_mid_scan_fences_finalization(self):
        """A worker that loses its lease mid-scan cannot finalize."""

        def steal_lease():
            RiskScanLease.objects.filter(
                rule_code=self.rule.code, scope_key=self.client_scope_key
            ).update(
                lease_token='stolen', expires_at=timezone.now() + timedelta(minutes=10)
            )

        self.rule.script = [[make_candidate('m1')], steal_lease, [make_candidate('m2')]]
        with self.assertRaises(RiskCommandError) as ctx:
            self.scan()
        self.assertEqual(ctx.exception.code, SCAN_LEASE_HELD)
        self.assertEqual(self.findings().count(), 0)

    def test_completed_run_cannot_finalize_again(self):
        """A replayed finalization cannot resolve its own promoted findings."""
        self.rule.script = [[make_candidate('m1')]]
        run = self.scan()
        finding = self.findings().get(source_id='m1')
        now = timezone.now()
        RiskScanLease.objects.create(
            rule_code=self.rule.code,
            scope_key=self.client_scope_key,
            owner='replayed-worker',
            lease_token=run.lease_token,
            expires_at=now + timedelta(minutes=5),
            heartbeat_at=now,
        )

        with self.assertRaises(RiskCommandError) as ctx:
            _finalize_run(
                run,
                run.rule,
                RULE_SPECS[self.rule.code],
                token=run.lease_token,
                watermark=run.watermark,
                source_as_of=now,
                candidate_total=0,
            )

        self.assertEqual(ctx.exception.code, SCAN_LEASE_HELD)
        finding.refresh_from_db()
        self.assertEqual(finding.state, RiskFindingState.OPEN)

    def test_superseded_revision_fences_finalization(self):
        """A revision activated mid-scan fences the late worker's finalize."""
        admin = get_user_model().objects.create_user(
            username='risk-admin', email='a@example.com', password='pw'
        )
        grant_permissions(admin, ['administer_riskrules'])
        admin = get_user_model().objects.get(pk=admin.pk)

        def activate_new_revision():
            update_rule_configuration(
                admin,
                self.rule.code,
                changes={'enabled': True, 'enabled_scopes': [self.client_scope_key]},
                reason='mid-scan supersession',
            )

        self.rule.script = [
            [make_candidate('m1')],
            activate_new_revision,
            [make_candidate('m2')],
        ]
        with self.assertRaises(RiskCommandError):
            self.scan()
        self.assertEqual(self.findings().count(), 0)

    def test_new_revision_supersedes_old_findings(self):
        """The first complete new-revision scan retires old-revision findings."""
        self.rule.script = [[make_candidate('m1')]]
        self.scan()
        old = self.findings().get(source_id='m1')
        self.assertEqual(old.rule_version, 1)

        admin = get_user_model().objects.create_user(
            username='risk-admin2', email='a2@example.com', password='pw'
        )
        grant_permissions(admin, ['administer_riskrules'])
        admin = get_user_model().objects.get(pk=admin.pk)
        update_rule_configuration(
            admin,
            self.rule.code,
            changes={'enabled': True, 'enabled_scopes': [self.client_scope_key]},
            reason='tighten thresholds',
        )
        self.rule.script = [[make_candidate('m1')]]
        self.scan()
        old.refresh_from_db()
        self.assertEqual(old.state, RiskFindingState.RESOLVED)
        self.assertEqual(old.events.filter(event_type='superseded').count(), 1)
        replacement = self.findings().get(rule_version=2, source_id='m1')
        self.assertEqual(replacement.state, RiskFindingState.OPEN)

    def test_condition_started_preserved_across_churn(self):
        """Recreated source rows keep the original condition clock."""
        origin = timezone.now() - timedelta(hours=48)
        self.rule.script = [[make_candidate('m1', condition_started_at=origin)]]
        self.scan()
        self.rule.script = [[make_candidate('m1')]]  # churned: newer started_at
        self.scan()
        finding = self.findings().get(source_id='m1')
        self.assertEqual(finding.condition_started_at, origin)

    def test_reopen_starts_a_fresh_episode_clock(self):
        """Recurrence after resolution resets condition_started_at."""
        origin = timezone.now() - timedelta(hours=100)
        self.rule.script = [[make_candidate('m1', condition_started_at=origin)]]
        self.scan()
        self.rule.script = [[]]
        self.scan()  # condition clears -> resolved
        recurrence = timezone.now() - timedelta(hours=1)
        self.rule.script = [[make_candidate('m1', condition_started_at=recurrence)]]
        self.scan()
        finding = self.findings().get(source_id='m1')
        self.assertEqual(finding.state, RiskFindingState.OPEN)
        self.assertEqual(finding.reopen_count, 1)
        self.assertEqual(finding.condition_started_at, recurrence)

    def test_absent_dismissed_finding_waits_for_recheck_window(self):
        """A flapping condition cannot launder itself out of dismissal."""
        self.rule.script = [[make_candidate('m1')]]
        self.scan()
        finding = self.findings().get(source_id='m1')
        RiskFinding.objects.filter(pk=finding.pk).update(
            state=RiskFindingState.DISMISSED,
            dismiss_recheck_at=timezone.now() + timedelta(hours=12),
        )
        # Condition briefly absent: the dismissal must hold, not resolve.
        self.rule.script = [[]]
        run = self.scan()
        self.assertEqual(run.resolve_count, 0)
        finding.refresh_from_db()
        self.assertEqual(finding.state, RiskFindingState.DISMISSED)

        # After the recheck window, a still-absent condition resolves.
        RiskFinding.objects.filter(pk=finding.pk).update(
            dismiss_recheck_at=timezone.now() - timedelta(minutes=1)
        )
        self.rule.script = [[]]
        run = self.scan()
        self.assertEqual(run.resolve_count, 1)

    def test_open_min_age_gates_new_findings_only(self):
        """Young conditions stay unopened; open episodes survive churn."""
        self.enable_rule(
            self.rule.code,
            config={'open_min_age_hours': 24},
            scopes=[self.client_scope_key],
        )
        young = timezone.now() - timedelta(hours=1)
        old = timezone.now() - timedelta(hours=48)
        self.rule.script = [
            [
                make_candidate('young', condition_started_at=young),
                make_candidate('old', condition_started_at=old),
            ]
        ]
        self.scan()
        self.assertEqual(
            set(self.findings().values_list('source_id', flat=True)), {'old'}
        )
        # Churn resets the candidate clock: the open episode must neither
        # resolve nor lose its original start.
        churned = timezone.now() - timedelta(minutes=5)
        self.rule.script = [[make_candidate('old', condition_started_at=churned)]]
        run = self.scan()
        self.assertEqual(run.resolve_count, 0)
        finding = self.findings().get(source_id='old')
        self.assertEqual(finding.state, RiskFindingState.OPEN)
        self.assertEqual(finding.condition_started_at, old)

    def test_resolution_grace_defers(self):
        """Grace keeps a just-seen finding open across one absent scan."""
        self.enable_rule(
            self.rule.code,
            config={'resolution_grace_seconds': 3600},
            scopes=[self.client_scope_key],
        )
        self.rule.script = [[make_candidate('m1')]]
        self.scan()
        self.rule.script = [[]]
        run = self.scan()
        self.assertEqual(run.resolve_count, 0)
        finding = self.findings().get(source_id='m1')
        self.assertEqual(finding.state, RiskFindingState.OPEN)

        # Once the absence outlives the grace interval, resolution happens.
        RiskFinding.objects.filter(pk=finding.pk).update(
            last_seen=timezone.now() - timedelta(hours=2)
        )
        self.rule.script = [[]]
        run = self.scan()
        self.assertEqual(run.resolve_count, 1)

    def test_snooze_expiry_returns_to_queue(self):
        """An expired snooze becomes visible again on the next scan."""
        self.rule.script = [[make_candidate('m1')]]
        self.scan()
        finding = self.findings().get(source_id='m1')
        RiskFinding.objects.filter(pk=finding.pk).update(
            state=RiskFindingState.SNOOZED,
            snooze_until=timezone.now() - timedelta(minutes=1),
        )
        self.rule.script = [[make_candidate('m1')]]
        self.scan()
        finding.refresh_from_db()
        self.assertEqual(finding.state, RiskFindingState.OPEN)
        self.assertIsNone(finding.snooze_until)

    def test_dismissal_recheck_window(self):
        """Dismissal hides until recheck; recurrence after it reopens."""
        self.rule.script = [[make_candidate('m1')]]
        self.scan()
        finding = self.findings().get(source_id='m1')
        RiskFinding.objects.filter(pk=finding.pk).update(
            state=RiskFindingState.DISMISSED,
            dismiss_recheck_at=timezone.now() + timedelta(hours=1),
        )
        self.rule.script = [[make_candidate('m1')]]
        self.scan()
        finding.refresh_from_db()
        self.assertEqual(finding.state, RiskFindingState.DISMISSED)

        RiskFinding.objects.filter(pk=finding.pk).update(
            dismiss_recheck_at=timezone.now() - timedelta(minutes=1)
        )
        self.rule.script = [[make_candidate('m1')]]
        self.scan()
        finding.refresh_from_db()
        self.assertEqual(finding.state, RiskFindingState.OPEN)
        self.assertEqual(finding.reopen_count, 1)

    def test_watermark_advances_only_on_complete(self):
        """The run watermark comes from the final complete page."""
        self.rule.script = [[make_candidate('m1')]]
        run = self.scan()
        self.assertEqual(run.watermark.get('strategy'), 'full_snapshot')
        self.assertTrue(run.watermark.get('as_of'))

    def test_disabled_gates_refuse_scan(self):
        """Every failed enablement gate refuses to scan."""
        with override_settings(AIMMS_RISK_RULES_ENABLED=[]):
            with self.assertRaises(RiskCommandError) as ctx:
                self.scan()
            self.assertEqual(ctx.exception.code, RULE_DISABLED)
        with override_settings(AIMMS_RISK_RADAR_ENABLED=False):
            with self.assertRaises(RiskCommandError):
                self.scan()

    def test_scope_not_enumerated_for_principal(self):
        """A scope outside the scanner principal's set aborts the scan."""
        self.rule.script = [[make_candidate('m1')]]
        with self.assertRaises(RiskCommandError) as ctx:
            run_rule_scan(
                self.rule.code, self.other_client_scope, service_identity=self.service
            )
        self.assertEqual(ctx.exception.code, RULE_DISABLED)
        # Enable the other client's scope on the revision: now the scope
        # gate passes but the principal enumeration still fails closed.
        self.enable_rule(
            self.rule.code, scopes=[self.client_scope_key, self.other_client_scope_key]
        )
        with self.assertRaises(RiskCommandError) as ctx:
            run_rule_scan(
                self.rule.code, self.other_client_scope, service_identity=self.service
            )
        self.assertEqual(ctx.exception.code, 'SCOPE_UNRESOLVED')


@override_settings(**RISK_FLAGS, AIMMS_RISK_RULES_ENABLED=['SCRIPTED_RULE'])
class DispatchTest(RiskEnvMixin, TestCase):
    """Cadence dispatchers enumerate only the principal's scopes."""

    def setUp(self):
        """Register and enable the scripted rule for the client scope only.

        The principal holds two scopes (customer + client); enabling the
        machine-sourced rule for just the client scope key keeps dispatch
        deterministic at one scan.
        """
        self.build_env()
        self.addCleanup(self.teardown_scopes)
        self.rule = ScriptedRule()
        RULE_SPECS[self.rule.code] = scripted_spec(self.rule)
        self.addCleanup(RULE_SPECS.pop, self.rule.code, None)
        self.enable_rule(self.rule.code, scopes=[self.client_scope_key])

    def test_dispatch_runs_enabled_rules(self):
        """Dispatch fans out and (synchronously in tests) completes scans."""
        self.rule.script = [[make_candidate('d1')]]
        with override_settings(AIMMS_RISK_SERVICE_USER_ID=str(self.service.pk)):
            dispatched = dispatch_scans('hourly')
        self.assertEqual(dispatched, 1)
        findings = RiskFinding.objects.filter(rule_code=self.rule.code)
        self.assertEqual(findings.count(), 1)
        self.assertEqual(findings.get().scope_key, self.client_scope_key)

    def test_dispatch_without_principal_is_noop(self):
        """No scanner principal: nothing dispatches, nothing leaks."""
        self.assertEqual(dispatch_scans('hourly'), 0)

    def test_dispatch_respects_master_flag(self):
        """The master flag gates dispatch entirely."""
        with override_settings(AIMMS_RISK_RADAR_ENABLED=False):
            self.assertEqual(dispatch_scans('hourly'), 0)


@override_settings(
    **RISK_FLAGS, AIMMS_RISK_RULES_ENABLED=['PACKET_STALLED', 'WO_BLOCKED_PARTS']
)
class EndToEndTest(RiskEnvMixin, TestCase):
    """A real source condition flows to a finding and back to resolution."""

    def setUp(self):
        """Build the environment and enable the real stall rule.

        The stalled packet lives on a client-owned machine with no work
        order, so the packet is owned by the machine's client and the rule
        is enabled and scanned under the client scope.
        """
        self.build_env()
        self.addCleanup(self.teardown_scopes)
        self.enable_rule(
            'PACKET_STALLED', config={'stall_hours': 48}, scopes=[self.client_scope_key]
        )

    def test_source_condition_to_finding_to_resolution(self):
        """Stalled packet → finding with governed link → progress → resolved."""
        from repair.models import RepairPacket, RepairPacketEvent

        packet = RepairPacket.objects.create(
            fault_summary='bearing noise', machine=self.machine
        )
        RepairPacket.objects.filter(pk=packet.pk).update(
            created_at=timezone.now() - timedelta(hours=72)
        )
        run = run_rule_scan(
            'PACKET_STALLED', self.client_scope, service_identity=self.service
        )
        self.assertEqual(run.status, RiskScanStatus.COMPLETE)
        finding = RiskFinding.objects.get(
            rule_code='PACKET_STALLED', source_id=str(packet.pk)
        )
        self.assertEqual(finding.state, RiskFindingState.OPEN)
        self.assertEqual(finding.scope_key, self.client_scope_key)
        links = list(finding.action_links.values_list('route', flat=True))
        self.assertEqual(links, [f'/repair/packets/{packet.pk}/'])

        # The source progresses: entering a new lifecycle status starts a
        # fresh episode, so the old status-bound finding resolves on the
        # next complete scan.
        packet.status = 'diagnosed'
        packet.save(update_fields=['status'])
        RepairPacketEvent.objects.create(
            packet=packet,
            event_type='advanced',
            from_status='draft',
            to_status='diagnosed',
        )
        run2 = run_rule_scan(
            'PACKET_STALLED', self.client_scope, service_identity=self.service
        )
        self.assertEqual(run2.resolve_count, 1)
        finding.refresh_from_db()
        self.assertEqual(finding.state, RiskFindingState.RESOLVED)

    def test_dormant_rule_refuses_to_scan(self):
        """A dormant rule (no registered evaluator) never evaluates."""
        self.enable_rule('WO_BLOCKED_PARTS')
        with self.assertRaises(RiskCommandError) as ctx:
            run_rule_scan('WO_BLOCKED_PARTS', self.scope, service_identity=self.service)
        self.assertEqual(ctx.exception.code, RULE_DISABLED)
        self.assertIn('dormant', ctx.exception.detail)


class FingerprintTest(TestCase):
    """Fingerprint stability and uniqueness (SC-RR-003)."""

    def test_stable_across_display_changes(self):
        """Mutable display text never affects identity."""
        first = make_candidate('x', title='Old title', summary='old')
        second = make_candidate('x', title='New title', summary='new')
        self.assertEqual(
            compute_fingerprint('c1', 'RULE', 1, first),
            compute_fingerprint('c1', 'RULE', 1, second),
        )

    def test_varies_by_identity_components(self):
        """Scope, code, version, and discriminator all separate identities."""
        base = make_candidate('x')
        reference = compute_fingerprint('c1', 'RULE', 1, base)
        self.assertNotEqual(reference, compute_fingerprint('c2', 'RULE', 1, base))
        self.assertNotEqual(reference, compute_fingerprint('c1', 'OTHER', 1, base))
        self.assertNotEqual(reference, compute_fingerprint('c1', 'RULE', 2, base))
        self.assertNotEqual(
            reference, compute_fingerprint('c1', 'RULE', 1, make_candidate('y'))
        )


class SeverityPolicyTest(TestCase):
    """Documented severity derivation (RR-ADR-005)."""

    def test_base_severity(self):
        """The base factor drives the label."""
        self.assertEqual(derive_severity({'base': 'high'}), 'high')
        self.assertEqual(derive_severity({}), 'medium')

    def test_criticality_escalates_but_never_reduces(self):
        """Source criticality can only raise severity."""
        self.assertEqual(
            derive_severity({'base': 'medium', 'criticality': 'critical'}), 'critical'
        )
        self.assertEqual(
            derive_severity({'base': 'high', 'criticality': 'low'}), 'high'
        )
