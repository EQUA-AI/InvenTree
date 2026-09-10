"""Load generalized pump-system component families, never installed equipment."""

import json
import re
from collections import Counter
from pathlib import Path

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from common.models import ParameterTemplate
from part.models import Part, PartCategory, PartCategoryParameterTemplate

CATALOGUE = Path(__file__).resolve().parents[2] / 'catalogues' / 'pump_systems.json'
NAMESPACE = 'pump_system_catalogue'
ROOT_CATEGORY = 'Pump System Master'
CLASSIFICATION_TAG = 'pumphouse-components'


def validate_catalogue(data):
    """Validate the manifest and expand only the explicitly supplied channels."""
    if (
        not isinstance(data, dict)
        or set(data) != {'schema_version', 'components'}
        or type(data['schema_version']) is not int
        or data['schema_version'] != 1
        or not isinstance(data['components'], list)
        or not data['components']
    ):
        raise CommandError('Expected schema_version=1 and a nonempty components list')

    codes, paths, names, tags = set(), set(), set(), set()
    records = []
    component_fields = {'code', 'name', 'group', 'description', 'virtual', 'parameters'}
    parameter_fields = {
        'name',
        'tag',
        'kind',
        'data_type',
        'units',
        'unit_status',
        'note',
    }

    def unique(value, seen, label):
        if value.casefold() in seen:
            raise CommandError(f'Duplicate {label}: {value}')
        seen.add(value.casefold())

    def text(record, fields, *, allow_empty=()):
        for field in fields:
            value = record.get(field)
            if (
                not isinstance(value, str)
                or value != value.strip()
                or (not value and field not in allow_empty)
            ):
                raise CommandError(f'Expected trimmed text for {field}')

    for component in data['components']:
        if not isinstance(component, dict) or set(component) != component_fields:
            raise CommandError(f'Component fields must be {sorted(component_fields)}')
        text(component, ['code', 'name', 'group', 'description'])
        if not re.fullmatch(r'[A-Z0-9]+(?:-[A-Z0-9]+)*', component['code']):
            raise CommandError(
                'Component code must contain uppercase letters/digits and hyphens'
            )
        if type(component['virtual']) is not bool or not isinstance(
            component['parameters'], list
        ):
            raise CommandError('Expected a boolean virtual flag and a parameters list')
        unique(component['code'], codes, 'component code')
        unique(f'{component["group"]}/{component["name"]}', paths, 'category path')
        expanded = []

        for parameter in component['parameters']:
            if (
                not isinstance(parameter, dict)
                or not parameter_fields <= set(parameter)
                or set(parameter) - parameter_fields - {'channels'}
            ):
                raise CommandError('Invalid parameter fields')
            text(parameter, parameter_fields, allow_empty=['units'])
            if parameter['data_type'] not in {'number', 'status', 'unknown'}:
                raise CommandError('Unsupported parameter data_type')
            status = parameter['unit_status']
            if status not in {'proposed', 'unresolved', 'unitless'}:
                raise CommandError('Unsupported unit_status')
            if bool(parameter['units']) != (status == 'proposed'):
                raise CommandError('Only proposed units may have a units value')
            if parameter['data_type'] in {'status', 'unknown'} and parameter['units']:
                raise CommandError(
                    'Status and unknown measurements must not have units'
                )

            channels = [None]
            if 'channels' in parameter:
                bounds = parameter['channels']
                if (
                    not isinstance(bounds, list)
                    or len(bounds) != 2
                    or any(type(n) is not int for n in bounds)
                    or not 1 <= bounds[0] <= bounds[1] <= 100
                ):
                    raise CommandError(
                        'channels must be an inclusive [first, last] range within 1..100'
                    )
                if any('{channel}' not in parameter[f] for f in ['name', 'tag']):
                    raise CommandError(
                        'Channel ranges require {channel} in both name and tag'
                    )
                channels = range(bounds[0], bounds[1] + 1)

            for channel in channels:
                display, tag = parameter['name'], parameter['tag']
                if channel is not None:
                    display = display.replace('{channel}', str(channel))
                    tag = tag.replace('{channel}', str(channel))
                if any(c in display + tag for c in '{}'):
                    raise CommandError('Unknown or unexpanded parameter placeholder')
                name = f'PUMP | {component["code"]} | {display}'
                unique(name, names, 'parameter name')
                unique(tag, tags, 'source tag (ambiguous component ownership)')
                expanded.append(
                    {
                        'name': name,
                        'display_name': display,
                        'source_tag': tag,
                        'source_pattern': parameter['tag'],
                        'channel': channel,
                        **{
                            k: parameter[k]
                            for k in [
                                'kind',
                                'data_type',
                                'units',
                                'unit_status',
                                'note',
                            ]
                        },
                    }
                )
        records.append({**component, 'expanded_parameters': expanded})
    return records


class Command(BaseCommand):
    """Install an isolated, reusable parts catalogue without site-specific data."""

    help = 'Load generic pump-system parts and blank parameter slots (no assets or readings)'

    def add_arguments(self, parser):
        """Expose a reviewable manifest and rollback-only preview."""
        parser.add_argument('--file', type=Path, default=CATALOGUE)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        """Validate and load all records atomically, rolling back a preview."""
        try:
            data = json.loads(Path(options['file']).read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise CommandError(f'Cannot read catalogue: {exc}') from exc
        records = validate_catalogue(data)
        self.counts = Counter()

        try:
            with transaction.atomic():
                self.load(records)
                if options['dry_run']:
                    transaction.set_rollback(True)
        except ValidationError as exc:
            raise CommandError(
                f'Catalogue validation failed; no records imported: {exc}'
            ) from exc

        prefix = 'Dry run (rolled back)' if options['dry_run'] else 'Catalogue loaded'
        counts = ', '.join(
            f'{key}={value}' for key, value in sorted(self.counts.items())
        )
        self.stdout.write(self.style.SUCCESS(f'{prefix}: {counts}'))
        self.stdout.write(
            'No sites, machines, stock, readings or alarm limits were created.'
        )

    def ensure(self, queryset, *, values, kind, key, definition=None):
        """Create owned records or reuse exact matches; never take over user data."""
        matches = list(queryset[:2])
        if len(matches) > 1:
            raise CommandError(
                f'Multiple {kind} records match {key!r}; resolve the collision first'
            )
        marker = {
            'schema_version': 1,
            'kind': kind,
            'key': key,
            'definition': definition or {},
        }
        if matches:
            obj = matches[0]
            owned = (obj.metadata or {}).get(NAMESPACE)
            if not isinstance(owned, dict) or any(
                owned.get(k) != v for k, v in marker.items()
            ):
                raise CommandError(
                    f'{kind} {key!r} is unowned or has a conflicting catalogue definition'
                )
            # Descriptions, external metadata and Part flags may be curated by people.
            identity_fields = {
                'category': ['name', 'parent', 'structural'],
                'part': ['IPN', 'name', 'category'],
                'template': [
                    'name',
                    'units',
                    'model_type',
                    'checkbox',
                    'choices',
                    'unique',
                    'enabled',
                    'selectionlist',
                ],
                'assignment': ['category', 'template', 'default_value'],
            }
            if any(getattr(obj, f) != values[f] for f in identity_fields[kind]):
                raise CommandError(
                    f'{kind} {key!r} differs from the catalogue; review instead of overwriting'
                )
            self.counts[f'{kind}_reused'] += 1
            return obj

        obj = queryset.model(**values, metadata={NAMESPACE: marker})
        obj.full_clean()
        obj.save()
        self.counts[f'{kind}_created'] += 1
        return obj

    def category(self, name, parent, *, structural):
        """Create a category without adopting unrelated namesakes."""
        key = f'{parent.pk if parent else "root"}:{name}'
        return self.ensure(
            PartCategory.objects.filter(parent=parent, name__iexact=name),
            values={'name': name, 'parent': parent, 'structural': structural},
            kind='category',
            key=key,
        )

    def load(self, records):
        """Create category definitions, component families and blank parameter slots."""
        root = self.category(ROOT_CATEGORY, None, structural=True)
        groups = {}
        content_type = ContentType.objects.get_for_model(Part)
        for record in records:
            group_name = record['group']
            if group_name not in groups:
                groups[group_name] = self.category(group_name, root, structural=True)
            category = self.category(
                record['name'], groups[group_name], structural=False
            )
            for parameter in record['expanded_parameters']:
                template = self.ensure(
                    ParameterTemplate.objects.filter(name__iexact=parameter['name']),
                    values={
                        'name': parameter['name'],
                        'description': f'Unverified catalogue definition. {parameter["note"]}',
                        'model_type': content_type,
                        'units': parameter['units'],
                        'checkbox': False,
                        'choices': '',
                        'unique': ParameterTemplate.UniqueOptions.NONE,
                        'enabled': True,
                        'selectionlist': None,
                    },
                    kind='template',
                    key=parameter['name'],
                    definition=parameter,
                )
                self.ensure(
                    PartCategoryParameterTemplate.objects.filter(
                        category=category, template=template
                    ),
                    values={
                        'category': category,
                        'template': template,
                        'default_value': '',
                    },
                    kind='assignment',
                    key=f'{record["code"]}:{parameter["name"]}',
                )

            ipn = f'PS-{record["code"]}'
            part = self.ensure(
                Part.objects.filter(IPN__iexact=ipn),
                values={
                    'IPN': ipn,
                    'name': record['name'],
                    'description': record['description'],
                    'category': category,
                    'active': True,
                    'virtual': record['virtual'],
                    'component': True,
                    'assembly': False,
                    'purchaseable': False,
                    'salable': False,
                    'trackable': False,
                    'is_template': False,
                    'units': '',
                },
                kind='part',
                key=ipn,
                definition={
                    'provenance': 'User-supplied pump hierarchy and tag patterns; not an OEM specification',
                    'review_status': 'unverified',
                    'catalogue_role': 'functional_group'
                    if record['virtual']
                    else 'component_family',
                    'installation_scope': 'unassigned',
                },
            )
            # Additive classification, not a site assignment or a SCADA tag.
            # This also backfills existing owned Parts without resetting their dates.
            part.tags.add(CLASSIFICATION_TAG)
            previous = part.parameters_list.count()
            # The existing category-copy workflow supports empty definitions.
            # Never save a blank checkbox: that would invent an OFF/False reading.
            part.copy_category_parameters(category)
            self.counts['parameter_slots_created'] += (
                part.parameters_list.count() - previous
            )
