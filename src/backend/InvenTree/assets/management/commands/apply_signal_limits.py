"""Apply reviewed alarm limits to signal bindings from a file.

Limits decide when somebody is woken up, so they are kept where a change to one
can be argued with in a diff rather than discovered in a database.

There is a second, harder reason this cannot be a one-off script. Activation
clears all six bounds on any binding whose dictionary point changes meaning
(``assets.activation._refresh_binding``) - correctly, because a limit derived
for one measurement must not survive onto another. That makes limits something
a deployment has to be able to *re-apply*, after every re-review, which a file
and a command can do and a hand-edit cannot.

A family may also name channels to exclude, each with its reason. That is not
a convenience: a limit armed on a channel whose own history breaches it raises
a permanent alarm about a machine that is standing still, which is the fastest
way to teach an operator to ignore the system. An exclusion without a reason is
refused, because a channel nobody can explain is not excluded, it is forgotten.

The validation mirrors the dictionary review's: a bound may be set only with a
citation and a reason, the same way a point may be approved only with a note.
The unit check is the one that matters most - a figure derived in degrees
Celsius landing on a signal measured in metres of water would be silently
wrong in the direction that raises alarms nobody can explain.

Arming a limit also *evaluates* it, against every machine the family matched.
Without that the command is inert on a station whose history is a recorded
window: the poller evaluates the machines a poll wrote to, and a fully consumed
window has no further documents to write, so a limit armed after the readings
landed would never be judged at all. Evaluation covers every matched binding,
not only the ones whose bounds moved - re-running the command on an
already-armed estate must still answer "does anything breach this", which is the
question an operator is actually asking when they run it again.

It runs inside the same transaction, so ``--dry-run`` reports how many alarms
the file would raise and then throws them away along with the limits. That
number is the point of the preview: a limit is a decision about waking somebody
up, and the count of people woken is the thing worth seeing before committing.
"""

import json
import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from assets.health_models import MachineSignalBinding
from assets.models import AssetMachine
from machine_health.services import anomalies as anomaly_services

#: The six bounds a family entry may carry.
BOUNDS = (
    'normal_min',
    'normal_max',
    'warn_min',
    'warn_max',
    'critical_min',
    'critical_max',
)


class Command(BaseCommand):
    """Operator-only; the limits themselves are reviewed in the file."""

    help = 'Apply reviewed alarm limits to matching signal bindings'

    def add_arguments(self, parser):
        """Take the limits file; every family is named inside it."""
        parser.add_argument('--limits', type=Path, required=True)
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument(
            '--station',
            type=int,
            default=None,
            help='Restrict to one registered station, by primary key.',
        )

    def load(self, path):
        """Read the file, refusing one that cannot mean what it says."""
        try:
            document = json.loads(Path(path).read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise CommandError(f'Cannot read limits file: {exc}') from exc
        if not isinstance(document, dict) or document.get('schema_version') != 1:
            raise CommandError('Limits file requires schema_version 1.')
        families = document.get('limits')
        if not isinstance(families, list) or not families:
            raise CommandError('Limits file contains no families.')
        return families

    def validated(self, entry):
        """Re-state the review's own rules, so a file cannot weaken them."""
        for field in ('family', 'match', 'unit', 'citation', 'reasoning'):
            if not str(entry.get(field, '')).strip():
                raise CommandError(
                    f'{entry.get("family", "<unnamed>")}: {field} is required.'
                )
        bounds = {name: entry[name] for name in BOUNDS if entry.get(name) is not None}
        if not bounds:
            raise CommandError(f'{entry["family"]}: no bound is set.')
        for name, value in bounds.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise CommandError(f'{entry["family"]}: {name} must be a number.')
        if (
            bounds.get('warn_max') is not None
            and bounds.get('critical_max') is not None
            and bounds['warn_max'] > bounds['critical_max']
        ):
            raise CommandError(
                f'{entry["family"]}: warn_max is above critical_max, so the '
                'warning could never be reached before the critical.'
            )
        if (
            bounds.get('warn_min') is not None
            and bounds.get('critical_min') is not None
            and bounds['warn_min'] < bounds['critical_min']
        ):
            raise CommandError(
                f'{entry["family"]}: warn_min is below critical_min, so the '
                'warning could never be reached before the critical.'
            )
        try:
            pattern = re.compile(entry['match'])
        except re.error as exc:
            raise CommandError(f'{entry["family"]}: match is not a regex: {exc}')
        excluded = {}
        for item in entry.get('exclude', []):
            if not isinstance(item, dict) or not str(item.get('key', '')).strip():
                raise CommandError(f'{entry["family"]}: an exclusion needs a key.')
            # Bay numbering repeats across stations, so PUMP1's first winding
            # exists at all three. An exclusion naming only the tag would
            # silently disarm the same channel on every other station.
            if not str(item.get('station', '')).strip():
                raise CommandError(
                    f'{entry["family"]}: {item["key"]} is excluded without a '
                    'station, and the same tag exists on every station.'
                )
            if not str(item.get('reason', '')).strip():
                raise CommandError(
                    f'{entry["family"]}: {item["key"]} is excluded without a '
                    'reason, and a channel nobody can explain is not excluded, '
                    'it is forgotten.'
                )
            excluded[item['station'], item['key']] = item['reason']
        return pattern, bounds, excluded

    def handle(self, *args, **options):
        """Apply every family, or preview and roll the whole thing back."""
        families = self.load(options['limits'])
        applied = skipped = unchanged = 0
        report = []
        armed_machine_ids = set()

        with transaction.atomic():
            for entry in families:
                pattern, bounds, excluded = self.validated(entry)
                bindings = MachineSignalBinding.objects.filter(
                    active=True
                ).select_related('dictionary_point__station')
                if options['station'] is not None:
                    bindings = bindings.filter(
                        dictionary_point__station_id=options['station']
                    )
                matched = [b for b in bindings if pattern.match(b.external_key)]
                wrong_unit = [b for b in matched if (b.unit or '') != entry['unit']]
                for binding in wrong_unit:
                    report.append(
                        f'  skipped {binding.external_key}: reviewed unit '
                        f'{binding.unit!r} is not {entry["unit"]!r}'
                    )
                skipped += len(wrong_unit)

                def station_of(binding):
                    point = binding.dictionary_point
                    return point.station.source_key if point else ''

                hit = [
                    b for b in matched if (station_of(b), b.external_key) in excluded
                ]
                skipped += len(hit)

                for binding in matched:
                    if binding in wrong_unit or binding in hit:
                        continue
                    # Collected before the unchanged short-circuit below, so a
                    # re-run on an already-armed estate still evaluates.
                    armed_machine_ids.add(binding.machine_id)
                    current = {name: getattr(binding, name) for name in BOUNDS}
                    wanted = {name: bounds.get(name) for name in BOUNDS}
                    if current == wanted:
                        unchanged += 1
                        continue
                    for name, value in wanted.items():
                        setattr(binding, name, value)
                    # Validate the bounds, and only the bounds: a binding
                    # carrying some unrelated invalid field is not a reason to
                    # refuse a limit the review settled.
                    binding.full_clean(
                        exclude=[
                            field.name
                            for field in binding._meta.fields
                            if field.name not in BOUNDS
                        ]
                    )
                    binding.save(update_fields=list(BOUNDS))
                    applied += 1

                report.append(
                    f'{entry["family"]}: {len(matched)} matched, '
                    f'{len(wrong_unit)} skipped on unit, '
                    f'{len(hit)} excluded by name'
                )

            # Inside the transaction, so a preview reports the one number an
            # operator actually needs before arming anything - how many alarms
            # this file raises on the estate as it stands - and then discards
            # them with the limits that produced them.
            raised = 0
            for machine in AssetMachine.objects.filter(pk__in=armed_machine_ids):
                raised += len(anomaly_services.evaluate_thresholds(machine))

            for line in report:
                self.stdout.write(line)
            self.stdout.write(f'applied   : {applied}')
            self.stdout.write(f'unchanged : {unchanged}')
            self.stdout.write(f'skipped   : {skipped}')
            self.stdout.write(
                f'evaluated : {len(armed_machine_ids)} machines, {raised} breaching'
            )

            if options['dry_run']:
                transaction.set_rollback(True)
                self.stdout.write('Rolled back preview.')
                return
        self.stdout.write('Limits applied.')
