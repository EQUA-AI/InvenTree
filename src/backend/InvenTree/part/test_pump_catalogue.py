"""Tests for generalized pump-component catalogue ingestion."""

import json
from copy import deepcopy
from datetime import date
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from rest_framework.test import APIClient

from assets.models import AssetMachine, HealthSource, MachinePart, MachineSignalBinding
from common.models import Parameter, ParameterTemplate
from part.management.commands.load_pump_catalogue import (
    CATALOGUE,
    CLASSIFICATION_TAG,
    NAMESPACE,
    ROOT_CATEGORY,
    validate_catalogue,
)
from part.models import Part, PartCategory, PartCategoryParameterTemplate
from stock.models import StockItem


class PumpCatalogueTests(TestCase):
    """Exercise the actual management command against an isolated test database."""

    def setUp(self):
        """Do not retain cached content-type IDs from rolled-back imports."""
        super().setUp()
        ContentType.objects.clear_cache()
        self.addCleanup(ContentType.objects.clear_cache)

    def load(self, data=None, **options):
        """Run the bundled catalogue or a temporary modified manifest."""
        output = StringIO()
        if data is None:
            call_command('load_pump_catalogue', stdout=output, **options)
        else:
            with TemporaryDirectory() as directory:
                path = Path(directory) / 'catalogue.json'
                path.write_text(json.dumps(data), encoding='utf-8')
                call_command('load_pump_catalogue', file=path, stdout=output, **options)
        return output.getvalue()

    @staticmethod
    def manifest():
        """Read a fresh copy of the bundled input."""
        return json.loads(CATALOGUE.read_text(encoding='utf-8'))

    @staticmethod
    def identities():
        """Snapshot primary keys for all catalogue-related models."""
        return {
            model.__name__: set(model.objects.values_list('pk', flat=True))
            for model in [
                Part,
                PartCategory,
                ParameterTemplate,
                PartCategoryParameterTemplate,
                Parameter,
            ]
        }

    def test_catalogue_and_empty_slots(self):
        """Load all supplied definitions without observations or equipment instances."""
        unrelated = [
            AssetMachine,
            MachinePart,
            HealthSource,
            MachineSignalBinding,
            StockItem,
        ]
        counts = {model: model.objects.count() for model in unrelated}
        output = self.load()
        self.assertIn('part_created=20', output)
        self.assertIn('template_created=63', output)
        self.assertIn('parameter_slots_created=63', output)
        self.assertEqual(Part.objects.filter(IPN__startswith='PS-').count(), 20)
        self.assertTrue(PartCategory.objects.get(name=ROOT_CATEGORY).structural)
        for parameter in Parameter.objects.filter(template__name__startswith='PUMP | '):
            self.assertEqual(parameter.data, '')
            self.assertIsNone(parameter.data_numeric)
            self.assertIsNone(parameter.updated)
            self.assertFalse(parameter.template.checkbox)
        for model in unrelated:
            self.assertEqual(model.objects.count(), counts[model])
        for part in Part.objects.filter(IPN__startswith='PS-'):
            self.assertFalse(part.purchaseable)
            self.assertFalse(part.salable)
            self.assertFalse(part.category.structural)
            self.assertEqual(
                part.metadata[NAMESPACE]['definition']['installation_scope'],
                'unassigned',
            )

    def test_idempotence_and_preservation(self):
        """Reruns retain IDs, human metadata, descriptions, parameter values and notes."""
        self.load()
        before = self.identities()
        part = Part.objects.get(IPN='PS-MOTOR')
        part.metadata['operator'] = {'note': 'Keep this'}
        part.description = 'Reviewed family description'
        part.save()
        parameter = part.parameters_list.get(
            template__name__endswith='Motor Core RTD 1'
        )
        parameter.data = '42'
        parameter.note = 'Existing manually entered value'
        parameter.save()
        template = parameter.template
        template.metadata['review_notes'] = 'Pending OEM confirmation'
        template.save()

        output = self.load()
        self.assertEqual(self.identities(), before)
        self.assertIn('parameter_slots_created=0', output)
        part.refresh_from_db()
        parameter.refresh_from_db()
        template.refresh_from_db()
        self.assertEqual(part.metadata['operator']['note'], 'Keep this')
        self.assertEqual(part.description, 'Reviewed family description')
        self.assertEqual(parameter.data, '42')
        self.assertEqual(parameter.note, 'Existing manually entered value')
        self.assertEqual(template.metadata['review_notes'], 'Pending OEM confirmation')

    def test_classification_backfill_preserves_data(self):
        """Backfill an existing family without changing its date or human tags."""
        self.load()
        part = Part.objects.get(IPN='PS-MOTOR')
        Part.objects.filter(pk=part.pk).update(creation_date=date(2025, 1, 10))
        part.tags.remove(CLASSIFICATION_TAG)
        part.tags.add('maintenance-reviewed')

        self.load(dry_run=True)
        self.assertFalse(part.tags.filter(name=CLASSIFICATION_TAG).exists())

        self.load()
        self.load()
        part.refresh_from_db()
        self.assertEqual(part.creation_date, date(2025, 1, 10))
        self.assertEqual(
            set(part.tags.values_list('name', flat=True)),
            {CLASSIFICATION_TAG, 'maintenance-reviewed'},
        )
        self.assertEqual(Part.objects.filter(tags__name=CLASSIFICATION_TAG).count(), 20)

    def test_classification_and_date_api_filters(self):
        """Combine tags, category descendants and exclusive creation-date bounds."""
        self.load()
        parts = Part.objects.filter(IPN__startswith='PS-')
        parts.update(creation_date=date(2026, 9, 10))
        parts.filter(IPN='PS-MOTOR').update(creation_date=date(2026, 9, 9))
        parts.filter(IPN='PS-VFD').update(creation_date=date(2026, 9, 11))
        parts.filter(IPN='PS-STATUS').update(creation_date=None)
        other = Part.objects.create(name='Unrelated component', IPN='UNRELATED')
        Part.objects.filter(pk=other.pk).update(creation_date=date(2026, 9, 10))
        user = get_user_model().objects.create_superuser(username='catalogue-admin')
        client = APIClient()
        client.force_authenticate(user)
        root = PartCategory.objects.get(name=ROOT_CATEGORY)

        def filtered_ids(**filters):
            response = client.get('/api/part/', {'limit': 100, **filters})
            self.assertEqual(response.status_code, 200)
            rows = response.data
            if isinstance(rows, dict):
                rows = rows['results']
            return {row['pk'] for row in rows}

        expected_all = set(parts.values_list('pk', flat=True))
        self.assertEqual(filtered_ids(tags=CLASSIFICATION_TAG), expected_all)
        self.assertEqual(filtered_ids(category=root.pk), expected_all)
        self.assertEqual(filtered_ids(category=root.pk, cascade='false'), set())
        expected_range = set(
            parts.exclude(IPN__in=['PS-MOTOR', 'PS-VFD', 'PS-STATUS']).values_list(
                'pk', flat=True
            )
        )
        self.assertEqual(
            filtered_ids(
                tags=CLASSIFICATION_TAG,
                category=root.pk,
                cascade='true',
                created_after='2026-09-09',
                created_before='2026-09-11',
            ),
            expected_range,
        )

    def test_dry_run_rolls_back(self):
        """Preview all validation and counts without persisting the catalogue."""
        before = self.identities()
        self.assertIn('Dry run (rolled back)', self.load(dry_run=True))
        self.assertEqual(self.identities(), before)

    def test_existing_category_is_not_adopted(self):
        """An unowned category with matching case-insensitive name is refused."""
        PartCategory.objects.create(name=ROOT_CATEGORY.lower())
        before = self.identities()
        with self.assertRaisesMessage(CommandError, 'unowned'):
            self.load()
        self.assertEqual(self.identities(), before)

    def test_existing_part_is_not_adopted(self):
        """An unowned IPN collision causes a full rollback."""
        Part.objects.create(name='Other forebay', IPN='ps-forebay')
        before = self.identities()
        with self.assertRaisesMessage(CommandError, 'unowned'):
            self.load()
        self.assertEqual(self.identities(), before)

    def test_existing_template_is_not_adopted(self):
        """Template names are global; unrelated definitions must never be reused."""
        ParameterTemplate.objects.create(name='pump | discharge | discharge pressure')
        before = self.identities()
        with self.assertRaisesMessage(CommandError, 'unowned'):
            self.load()
        self.assertEqual(self.identities(), before)

    def test_invalid_unit_rolls_back(self):
        """Model-level unit validation rejects an invalid late definition atomically."""
        data = self.manifest()
        data['components'][-2]['parameters'][0]['units'] = 'unrecognised_pump_unit'
        before = self.identities()
        with self.assertRaisesMessage(CommandError, 'validation failed'):
            self.load(data)
        self.assertEqual(self.identities(), before)

    def test_additive_definition(self):
        """New channels add only missing definitions and slots to existing families."""
        self.load()
        before = self.identities()
        data = self.manifest()
        motor = next(c for c in data['components'] if c['code'] == 'MOTOR')
        motor['parameters'][0]['channels'][1] = 7
        self.load(data)
        after = self.identities()
        self.assertEqual(after['Part'], before['Part'])
        self.assertEqual(after['PartCategory'], before['PartCategory'])
        self.assertEqual(len(after['Parameter'] - before['Parameter']), 1)
        self.assertEqual(
            len(after['ParameterTemplate'] - before['ParameterTemplate']), 1
        )

    def test_conflicting_definition_is_not_overwritten(self):
        """A changed engineering unit requires explicit review, not silent conversion."""
        self.load()
        data = self.manifest()
        data['components'][2]['parameters'][0]['units'] = 'kPa'
        before = self.identities()
        with self.assertRaisesMessage(CommandError, 'conflicting catalogue definition'):
            self.load(data)
        self.assertEqual(self.identities(), before)
        self.assertEqual(
            ParameterTemplate.objects.get(
                name='PUMP | DISCHARGE | Discharge Pressure'
            ).units,
            'bar',
        )

    def test_bad_manifest(self):
        """Reject malformed ranges, duplicate identities and guessed fields."""
        base = self.manifest()
        variants = [None, {}, {**base, 'schema_version': True}]
        duplicate = deepcopy(base)
        duplicate['components'].append(deepcopy(duplicate['components'][0]))
        variants.append(duplicate)
        for field, value in [
            ('channels', [1, 10000]),
            ('units', 42),
            ('warn_max', 90),
            ('tag', '{pump}_TAG'),
        ]:
            invalid = deepcopy(base)
            invalid['components'][1]['parameters'][0][field] = value
            variants.append(invalid)
        for variant in variants:
            with self.subTest(data=variant), self.assertRaises(CommandError):
                validate_catalogue(variant)

    def test_unresolved_units_and_source_spellings(self):
        """Retain uncertain semantics and original spellings without pump prefixes."""
        self.load()
        definitions = {
            template.metadata[NAMESPACE]['definition']['source_tag']: template
            for template in ParameterTemplate.objects.filter(name__startswith='PUMP | ')
        }
        for tag in [
            'SPIRAL_CASE1',
            'PUMP_THRUST_AXIAL_PAD_1D5',
            'MOTOR_DE_VIBRATION1',
            'REACTIVE_POWER',
        ]:
            self.assertEqual(definitions[tag].units, '')
            self.assertEqual(
                definitions[tag].metadata[NAMESPACE]['definition']['unit_status'],
                'unresolved',
            )
        for tag in [
            'PUMP_POWERFATCOR',
            'COMMAN_FORBAY_LEVEL',
            'PUMP_MOTOR_COLD_AIR TEMP1',
            'st',
        ]:
            self.assertIn(tag, definitions)
        self.assertNotIn('PUMP01', definitions)
        for template in definitions.values():
            template.full_clean()
            self.assertNotIn('warn_max', template.metadata[NAMESPACE]['definition'])
