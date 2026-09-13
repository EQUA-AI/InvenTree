"""Apply a reviewed dictionary mapping from a file.

Reviewing 31 tags by hand through a modal is slow and, worse, leaves no record
of *why* each decision was made - the review note is the only trace, and it is
written once and never diffed. A review file is version-controlled, diffable and
can be argued with in a pull request before it touches anything.

The validation here is deliberately the same as the HTTP review endpoint's:
component and parameter present, unit settled and convertible to the catalogue
unit, data type known, a note written, and no second approved path competing for
the same component parameter. A bulk tool that applied a weaker standard than
the interactive one would be a way to launder unreviewed mappings past the gate.

Withheld points are recorded too. A tag nobody could settle is a finding, and
writing the reason onto the point keeps it attached to the thing it is about
rather than living only in someone's notes.
"""

import json
from pathlib import Path
from uuid import UUID

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from assets.models import AssetMachine
from assets.registry_models import DictionaryPoint


class Command(BaseCommand):
    """Operator-only local review; the HTTP registry applies actor scopes."""

    help = 'Apply a reviewed dictionary mapping file to a registered station'

    def add_arguments(self, parser):
        """Take the review file; the station is named inside it."""
        parser.add_argument('--review', type=Path, required=True)
        parser.add_argument('--dry-run', action='store_true')

    def station_for(self, review):
        """Resolve the station by source identity, never by display name."""
        try:
            source_uuid = UUID(review['station_source_uuid'])
        except (KeyError, TypeError, ValueError) as exc:
            raise CommandError(f'Review file needs a station_source_uuid: {exc}')

        station = AssetMachine.objects.filter(
            asset_type='pumphouse', source_entity_uuid=source_uuid
        ).first()
        if station is None:
            raise CommandError(f'No registered station for source {source_uuid}.')
        return station

    def check_approval(self, point, entry):
        """Re-run the interactive endpoint's approval rules for one point."""
        from InvenTree.conversion import convert_physical_value
        from InvenTree.validators import validate_physical_units

        unit = entry.get('unit', '')
        unit_status = entry['unit_status']

        if not point.component_id or not point.template_id:
            raise ValidationError(
                f'{point.path}: needs both a component and a parameter to be approved.'
            )
        if entry['data_type'] == 'unknown':
            raise ValidationError(f'{point.path}: data type is unknown.')
        if not entry.get('note', '').strip():
            raise ValidationError(f'{point.path}: approval requires a review note.')
        if unit_status not in {'verified', 'unitless'}:
            raise ValidationError(f'{point.path}: unit is not settled.')
        if unit_status == 'unitless' and unit:
            raise ValidationError(f'{point.path}: unitless points cannot carry a unit.')
        if unit_status == 'verified' and not unit:
            raise ValidationError(f'{point.path}: verified units require a unit.')

        if unit:
            validate_physical_units(unit)

        template_units = getattr(point.template, 'units', '') or ''
        if template_units:
            if not unit:
                raise ValidationError(f'{point.path}: this parameter requires units.')
            # Catches a point recorded in units the catalogue cannot reconcile -
            # millimetres against a parameter defined in degrees, say.
            convert_physical_value(f'1 {unit}', template_units)

        clash = (
            DictionaryPoint.objects
            .filter(
                station=point.station,
                component=point.component,
                template=point.template,
                status='approved',
            )
            .exclude(pk=point.pk)
            .first()
        )
        if clash is not None:
            raise ValidationError(
                f'{point.path}: {clash.path} is already approved for the same '
                'component parameter.'
            )

    def handle(self, *args, **options):
        """Apply every decision atomically, or none of them."""
        try:
            review = json.loads(options['review'].read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise CommandError(f'Could not read the review file: {exc}') from exc

        approved = withheld = 0

        try:
            with transaction.atomic():
                station = self.station_for(review)
                points = {
                    point.path: point
                    for point in DictionaryPoint.objects.select_related(
                        'template', 'component'
                    ).filter(station=station)
                }

                for entry in review.get('approve', []):
                    for path in entry['paths']:
                        point = points.get(path)
                        if point is None:
                            raise CommandError(f'No dictionary point at {path!r}.')

                        self.check_approval(point, entry)
                        point.data_type = entry['data_type']
                        point.unit = entry.get('unit', '')
                        point.unit_status = entry['unit_status']
                        point.review_note = entry['note']
                        point.status = 'approved'
                        point.issue = ''
                        point.reviewed_at = timezone.now()
                        point.save()
                        approved += 1

                for entry in review.get('withhold', []):
                    for path in entry['paths']:
                        point = points.get(path)
                        if point is None:
                            raise CommandError(f'No dictionary point at {path!r}.')

                        note = entry['reason']
                        if entry.get('recommendation'):
                            note = f'{note} Recommended: {entry["recommendation"]}'
                        point.review_note = note
                        point.reviewed_at = timezone.now()
                        point.save(update_fields=['review_note', 'reviewed_at'])
                        withheld += 1

                self.stdout.write(f'station   : {station.name}')
                self.stdout.write(f'approved  : {approved}')
                self.stdout.write(
                    f'withheld  : {withheld} (reason recorded on the point)'
                )
                remaining = (
                    DictionaryPoint.objects
                    .filter(station=station)
                    .exclude(status='approved')
                    .count()
                )
                self.stdout.write(f'unapproved: {remaining}')

                if options['dry_run']:
                    transaction.set_rollback(True)

            self.stdout.write(
                'Rolled back preview.' if options['dry_run'] else 'Review applied.'
            )
        except ValidationError as exc:
            raise CommandError(f'Refused: {"; ".join(exc.messages)}') from exc
