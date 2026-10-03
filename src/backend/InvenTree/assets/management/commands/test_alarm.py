"""Raise a real threshold alarm on a chosen machine, to see the chain work.

The alarm chain - limit, vote, anomaly, page, acknowledgement - had never been
seen end to end on this estate, because nothing in the recorded data breaches a
limit. This writes a reading that does, through the same ingestion every source
uses, and evaluates it with the same detector. What follows is therefore real:
a real anomaly, on a real binding, that the mimic and the Health tab show as
they would show any other.

It is a test tool, and it says so in every place it leaves a mark:

- The readings it writes are **not measurements**. Each is the detector's
  critical limit plus ten per cent, stamped five seconds after the machine's
  newest reading on the plant's clock, on as many detectors as the vote needs.
- The anomaly it raises carries ``test_alarm`` in its metrics with the reading
  it displaced. ``--clear`` writes that reading back, five minutes later on the
  plant's clock, which is what lets the condition close by the same rule a real
  recovery would - and then puts the displaced row back exactly as it was, so
  the channel reads what it read before, at the time it read it. A station
  presented on a shifted clock would otherwise show the recovery reading as
  five minutes in the future for ever. The anomaly, resolved, is the one trace
  a test leaves, and it is marked as one.

Run it only on a development database.
"""

from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from assets.health_models import (
    MachineAnomaly,
    MachineSignalBinding,
    MachineSignalState,
    SignalQuality,
)
from assets.models import AssetMachine
from machine_health.services.anomalies import (
    RESOLVE_AFTER,
    STANDING_STATUSES,
    THRESHOLD_DETECTOR,
    evaluate_thresholds,
)
from machine_health.services.ingestion import ingest_readings

MARK = 'test_alarm'


class Command(BaseCommand):
    """Raise, or clear, a test alarm on one machine."""

    help = __doc__

    def add_arguments(self, parser):
        """One machine; raise by default, or clear what a raise left."""
        parser.add_argument('--machine', type=int, required=True)
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Write back the readings a raise displaced and let it close.',
        )
        parser.add_argument(
            '--yes',
            action='store_true',
            help='Confirm that this is a development database.',
        )

    def handle(self, *args, **options):
        """Refuse without confirmation; this writes readings."""
        if not options['yes']:
            raise CommandError(
                'This writes readings that are not measurements. Pass --yes on a '
                'development database.'
            )
        machine = AssetMachine.objects.filter(pk=options['machine']).first()
        if machine is None:
            raise CommandError('No such machine.')
        if options['clear']:
            self.clear(machine)
        else:
            self.raise_alarm(machine)

    @staticmethod
    def _station(binding):
        """The station a bay's readings are ingested under, if it has one."""
        if binding.machine.asset_type == 'pumphouse':
            return binding.machine
        if binding.machine.parent_id:
            return binding.machine.parent
        return None

    @transaction.atomic
    def raise_alarm(self, machine):
        """Breach a confirmed critical on the machine's best-covered limit."""
        states = list(
            MachineSignalState.objects.filter(
                binding__machine=machine,
                binding__active=True,
                binding__critical_max__isnull=False,
                quality=SignalQuality.GOOD,
            ).select_related('binding', 'binding__source', 'binding__machine__parent')
        )
        if not states:
            raise CommandError(
                'This machine has no good reading with a critical limit to breach.'
            )
        newest = max(state.observed_at for state in states)
        # The vote group with the most live detectors on one source, and as
        # many of them as the vote needs - so the result is a confirmed
        # critical, not a lone breach de-escalated to a warning. One source,
        # because a key is only a key within its source: two sources naming
        # the same channel are two bindings, and the ingestion is one source's.
        by_group = {}
        for state in states:
            key = (state.binding.source_id, state.binding.vote_group or '')
            by_group.setdefault(key, []).append(state)
        (_, group), members = max(by_group.items(), key=lambda item: len(item[1]))
        needed = max((state.binding.vote_minimum or 1 for state in members), default=1)
        chosen = sorted(members, key=lambda state: state.binding.display_name)[:needed]
        if len(chosen) < needed:
            raise CommandError(
                f'Group {group!r} has {len(chosen)} live detectors; the vote needs '
                f'{needed}.'
            )

        at = newest + timedelta(seconds=5)
        readings = []
        displaced = {}
        for state in chosen:
            binding = state.binding
            readings.append({
                'external_key': binding.external_key,
                'value': round(binding.critical_max * 1.1, 3),
                'observed_at': at,
                'quality': SignalQuality.GOOD,
            })
            displaced[str(binding.pk)] = snapshot(state)
        source = chosen[0].binding.source
        result = ingest_readings(
            source, readings, now=at, station=self._station(chosen[0].binding)
        )
        if result.accepted != len(readings):
            raise CommandError(
                f'Ingestion accepted {result.accepted} of {len(readings)}: '
                f'{result.warnings}'
            )

        raised = evaluate_thresholds(machine)
        if not raised:
            raise CommandError('The readings were written but no anomaly was raised.')
        for anomaly in raised:
            anomaly.metrics = {**(anomaly.metrics or {}), MARK: displaced}
            anomaly.save(update_fields=['metrics'])
            self.stdout.write(
                f'raised : #{anomaly.pk} {anomaly.severity} - {anomaly.title}'
            )
            self.stdout.write(f'         {anomaly.evidence_summary}')
        keys = ', '.join(sorted(entry['external_key'] for entry in displaced.values()))
        self.stdout.write(
            f'wrote  : {len(readings)} reading(s) at {at} (plant clock) on {keys}'
        )
        self.stdout.write(
            f'clear  : manage.py test_alarm --machine {machine.pk} --clear --yes'
        )

    @transaction.atomic
    def clear(self, machine):
        """Let the detector close it, then put the readings back as they were."""
        anomalies = list(
            MachineAnomaly.objects.filter(
                machine=machine,
                detector=THRESHOLD_DETECTOR,
                # Dismissed during the test counts too: it is still standing.
                status__in=STANDING_STATUSES,
                metrics__has_key=MARK,
            )
        )
        if not anomalies:
            raise CommandError('No test alarm is open on this machine.')
        displaced = {}
        for anomaly in anomalies:
            displaced.update(anomaly.metrics[MARK])
        bindings = {
            str(binding.pk): binding
            for binding in MachineSignalBinding.objects.filter(
                machine=machine, pk__in=[int(pk) for pk in displaced]
            ).select_related('source', 'machine__parent')
        }
        newest = max(
            MachineSignalState.objects.filter(binding__machine=machine).values_list(
                'observed_at', flat=True
            )
        )
        # The recovery goes through ingestion and the detector, so the
        # condition closes the way a real one would: a reading inside its
        # limits, RESOLVE_AFTER of plant time after the breach.
        at = newest + RESOLVE_AFTER + timedelta(seconds=5)
        readings = [
            {
                'external_key': bindings[pk].external_key,
                'value': entry['value'],
                'observed_at': at,
                'quality': SignalQuality.GOOD,
            }
            for pk, entry in displaced.items()
            if pk in bindings
        ]
        first = next(iter(bindings.values()))
        ingest_readings(first.source, readings, now=at, station=self._station(first))
        evaluate_thresholds(machine)
        for anomaly in anomalies:
            anomaly.refresh_from_db()
            self.stdout.write(
                f'#{anomaly.pk}: {anomaly.status} - {anomaly.resolution_note}'
            )
        # Then undo the writes. This is the one place a state row is written
        # without going through ingestion, because it is not a reading: it is
        # the row that was there before the test, put back.
        for pk, entry in displaced.items():
            if pk in bindings:
                MachineSignalState.objects.filter(binding_id=int(pk)).update(
                    **restored(entry)
                )
        self.stdout.write(
            f'closed : recovery read at {at} (plant clock); '
            f'{len(readings)} reading(s) restored as found'
        )


def snapshot(state):
    """Everything needed to put a state row back as it was."""
    return {
        'external_key': state.binding.external_key,
        'value': (state.value or {}).get('value'),
        'row': {
            'value': state.value,
            'observed_at': state.observed_at.isoformat(),
            'received_at': state.received_at.isoformat(),
            'value_changed_at': state.value_changed_at.isoformat()
            if state.value_changed_at
            else None,
            'quality': state.quality,
            'source_sequence': state.source_sequence,
            'payload_hash': state.payload_hash,
        },
    }


def restored(entry):
    """The snapshot's row, with its instants parsed back."""
    row = dict(entry['row'])
    for name in ('observed_at', 'received_at', 'value_changed_at'):
        row[name] = datetime.fromisoformat(row[name]) if row[name] else None
    return row
