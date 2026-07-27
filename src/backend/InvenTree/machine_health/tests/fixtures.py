"""Shared builders for the machine health test suites."""

import uuid

from django.utils import timezone

from assets.models import AssetMachine
from company.models import Company
from assets.health_models import (
    HealthSource,
    MachineSignalBinding,
    MachineSignalState,
    SignalQuality,
    SourceType,
)


class HealthEnvMixin:
    """Builds one machine with a webhook source and a bounded signal."""

    def build_health_env(self, *, freshness=900, with_customer=True):
        """Create a customer, machine, source and one threshold-bounded binding."""
        suffix = uuid.uuid4().hex[:8]

        self.customer = (
            Company.objects.create(name=f'Health {suffix}', is_customer=True)
            if with_customer
            else None
        )
        self.machine = AssetMachine.objects.create(
            name=f'Pump {suffix}', customer=self.customer
        )
        self.source = HealthSource.objects.create(
            name=f'SCADA {suffix}',
            source_type=SourceType.SCADA,
            connector_type='webhook',
            secret_ref=f'health/{suffix}',
            freshness_threshold_seconds=freshness,
        )
        self.binding = MachineSignalBinding.objects.create(
            machine=self.machine,
            source=self.source,
            external_key=f'PU-{suffix}.VIB',
            display_name='Pump 2 drive-end vibration',
            signal_kind='vibration',
            unit='mm/s',
            normal_max=4.5,
            warn_max=6.0,
            critical_max=9.0,
        )
        return self.machine

    def set_signal(self, value, *, observed_at=None, quality=SignalQuality.GOOD):
        """Write a current reading for the environment's binding."""
        observed_at = observed_at or timezone.now()
        state, _created = MachineSignalState.objects.update_or_create(
            binding=self.binding,
            defaults={
                'value': {'value': value, 'unit': self.binding.unit},
                'observed_at': observed_at,
                'received_at': observed_at,
                'quality': quality,
            },
        )
        return state
