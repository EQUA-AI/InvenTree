"""Governed AI read tools for machine health.

The point of these is that a model can *read* condition data but nothing more.
Every observation carries its freshness and quality so stale telemetry cannot be
summarized as current, reads are capability-gated separately from the machine
dossier, and nothing here can raise, escalate or clear an anomaly.
"""

import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from tasks.scope import MaintenanceScope

from assets.health_models import (
    AnomalySeverity,
    HealthSource,
    MachineSignalBinding,
    MachineSignalState,
    SignalQuality,
    SourceType,
)
from assets.models import AssetMachine, Client
from machine_health.services.anomalies import fingerprint_for, record_anomaly

from . import services

HEALTH_CAPABILITY = 'diagnostics.health.read'


#: The health reader runs behind the same entity ACL as every other diagnostic
#: read, so the test actor needs a real scope rather than a stubbed one. The
#: entity ACL resolves a machine through its client, so the grants are client
#: scopes.
_GRANTED_CLIENT_IDS: list[int] = []


def _capabilities(actor):
    """Grant every diagnostic capability to any authenticated test actor."""
    return services._DIAGNOSTIC_CAPABILITIES


def _scopes(actor):
    """Return the maintenance scopes granted to the current test actor."""
    return {
        MaintenanceScope(customer_id=None, site_key=None, client_id=client_id)
        for client_id in _GRANTED_CLIENT_IDS
    }


class _Authorization:
    """The decision the registry passes to a reader, as an attribute object."""

    def __init__(self, decision):
        for key, value in decision.items():
            setattr(self, key, value)
        self.linked_machine_id = decision.get('linked_machine_id')


def _authorize(actor, machine):
    """Run the real ACL path and wrap its decision for the reader."""
    decision = services.authorize_diagnostic_read(
        actor=actor,
        capability=HEALTH_CAPABILITY,
        entity_type='machine',
        entity_id=machine.pk,
        expected_revision=services._diagnostic_revision(machine),
        linked_machine_id=None,
        check_id=uuid.uuid4().hex,
    )
    assert decision is not None, 'test actor should be authorized'
    return _Authorization(decision)


@override_settings(
    AIMMS_DIAGNOSTIC_CAPABILITY_RESOLVER=(
        'repair.test_health_diagnostics._capabilities'
    ),
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='repair.test_health_diagnostics._scopes',
)
class HealthDiagnosticReadTest(TestCase):
    """The health summary and anomaly readers."""

    def setUp(self):
        """Create an actor, a machine with one bounded signal, and an anomaly."""
        suffix = uuid.uuid4().hex[:8]
        self.actor = get_user_model().objects.create_superuser(
            username=f'reader-{suffix}', email=f'{suffix}@example.com', password='pw'
        )
        self.client_tenant = Client.objects.create(
            name=f'Tenant {suffix}', code=f'tenant-{suffix}'
        )
        _GRANTED_CLIENT_IDS[:] = [self.client_tenant.pk]
        self.addCleanup(_GRANTED_CLIENT_IDS.clear)
        self.machine = AssetMachine.objects.create(
            name=f'Blower {suffix}', client=self.client_tenant
        )
        self.source = HealthSource.objects.create(
            name=f'SCADA {suffix}',
            source_type=SourceType.SCADA,
            freshness_threshold_seconds=900,
        )
        self.binding = MachineSignalBinding.objects.create(
            machine=self.machine,
            source=self.source,
            external_key=f'BL-{suffix}.VIB',
            display_name='Blower drive vibration',
            unit='mm/s',
            warn_max=6.0,
            critical_max=9.0,
        )
        self.now = timezone.now()

    def _authorization(self):
        return _authorize(self.actor, self.machine)

    def _set_signal(self, value, *, age_seconds=0, quality=SignalQuality.GOOD):
        MachineSignalState.objects.update_or_create(
            binding=self.binding,
            defaults={
                'value': {'value': value, 'unit': 'mm/s'},
                'observed_at': self.now - timedelta(seconds=age_seconds),
                'received_at': self.now,
                'quality': quality,
            },
        )

    def _read_summary(self, authorization=None):
        return services.read_diagnostic_health_summary(
            actor=self.actor,
            authorization=authorization or self._authorization(),
            machine_id=self.machine.pk,
            expected_revision=services._diagnostic_revision(self.machine),
        )

    def test_summary_reports_condition_with_freshness(self):
        """A fresh reading is cited with its quality and staleness."""
        self._set_signal(3.0)

        result = self._read_summary()

        claims = [item['claim'] for item in result['evidence']]
        self.assertTrue(any('"state": "normal"' in claim for claim in claims))
        self.assertTrue(any('"stale": false' in claim for claim in claims))

    def test_stale_telemetry_is_labelled_stale(self):
        """Aged data is never handed to a model as the current state."""
        self._set_signal(3.0, age_seconds=7200)

        result = self._read_summary()

        claims = ' '.join(item['claim'] for item in result['evidence'])
        self.assertIn('"stale": true', claims)
        self.assertIn('"state": "offline"', claims)

    def test_poor_quality_is_carried_through(self):
        """Quality travels with the observation rather than being dropped."""
        self._set_signal(3.0, quality=SignalQuality.BAD)

        result = self._read_summary()

        claims = ' '.join(item['claim'] for item in result['evidence'])
        self.assertIn('"quality": "bad"', claims)
        self.assertIn('"degraded_data": true', claims)

    def test_unmapped_machine_reads_as_unconfigured(self):
        """No source means unknown, not healthy."""
        self.binding.delete()

        result = self._read_summary()

        claims = ' '.join(item['claim'] for item in result['evidence'])
        self.assertIn('"configured": false', claims)
        self.assertIn('"state": "unknown"', claims)

    def test_signal_values_are_marked_untrusted(self):
        """Labels come from an external control system; they are not instructions."""
        self._set_signal(3.0)

        result = self._read_summary()

        signal_claims = [
            item
            for item in result['evidence']
            if item['source_type'] == 'machine_signal_state'
        ]
        self.assertTrue(signal_claims)
        self.assertTrue(all(item['untrusted'] for item in signal_claims))

    def test_wrong_entity_authorization_abstains(self):
        """An authorization for another record yields nothing."""
        other = AssetMachine.objects.create(
            name=f'Other {uuid.uuid4().hex[:6]}', client=self.client_tenant
        )
        self._set_signal(3.0)

        result = self._read_summary(authorization=_authorize(self.actor, other))

        self.assertEqual(result['evidence'], ())
        self.assertTrue(result['abstention_reason'])

    def test_anomaly_reader_carries_detector_provenance(self):
        """A summary can say what raised the condition."""
        record_anomaly(
            machine=self.machine,
            fingerprint=fingerprint_for('diag', 'vib'),
            title='Vibration above limit',
            severity=AnomalySeverity.CRITICAL,
            detector='threshold',
            detector_version='1',
        )

        result = services.read_diagnostic_health_anomalies(
            actor=self.actor,
            authorization=self._authorization(),
            machine_id=self.machine.pk,
            expected_revision=services._diagnostic_revision(self.machine),
            limit=10,
        )

        [evidence] = result['evidence']
        self.assertIn('"detector": "threshold"', evidence['claim'])
        self.assertIn('"severity": "critical"', evidence['claim'])
        self.assertEqual(evidence['source_type'], 'machine_anomaly')

    def test_anomaly_reader_returns_only_active_conditions(self):
        """Resolved conditions are not presented as current problems."""
        anomaly, _ = record_anomaly(
            machine=self.machine,
            fingerprint=fingerprint_for('diag', 'resolved'),
            title='Old condition',
            severity=AnomalySeverity.WARNING,
        )
        anomaly.status = 'resolved'
        anomaly.save(update_fields=['status'])

        result = services.read_diagnostic_health_anomalies(
            actor=self.actor,
            authorization=self._authorization(),
            machine_id=self.machine.pk,
            expected_revision=services._diagnostic_revision(self.machine),
            limit=10,
        )

        self.assertEqual(result['evidence'], ())


class HealthCapabilityTest(TestCase):
    """Health is a separate grant from the machine dossier."""

    def test_health_capability_is_registered(self):
        """A deployment can grant health reads independently."""
        self.assertIn(HEALTH_CAPABILITY, services._DIAGNOSTIC_CAPABILITIES)

    def test_registry_exposes_only_read_tools(self):
        """The health tools are reads; nothing in the registry writes."""
        from ai.core.tools.diagnostics import BASE_DIAGNOSTIC_TOOL_NAMES

        self.assertIn('get_machine_health_summary', BASE_DIAGNOSTIC_TOOL_NAMES)
        self.assertIn('get_machine_health_anomalies', BASE_DIAGNOSTIC_TOOL_NAMES)
        for name in BASE_DIAGNOSTIC_TOOL_NAMES:
            self.assertFalse(
                any(verb in name for verb in ('create', 'update', 'start', 'write'))
            )
