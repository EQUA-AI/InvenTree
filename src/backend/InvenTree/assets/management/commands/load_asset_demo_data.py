"""Load the equipment-machine extension for the InvenTree demo dataset."""

import datetime
import json
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import Q

from tasks.models import WorkOrder, WorkOrderLifecycle

from assets.demo_history import normalize_completed_history_card
from assets.models import AssetMachine, AssetMaintenanceRecord, MachinePart
from company.models import Company
from part.models import Part, PartCategory

DATA_FILE = Path(__file__).resolve().parents[2] / 'demo_machine_data.json'
DEMO_METADATA_KEY = 'asset_demo_data'


class Command(BaseCommand):
    """Load rich, idempotent equipment-machine demo records."""

    help = 'Load the equipment-machine extension for the InvenTree demo dataset'

    def add_arguments(self, parser):
        """Register command-line arguments."""
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Validate and load the manifest, then roll back all changes',
        )
        parser.add_argument(
            '--prune',
            action='store_true',
            help=(
                'Remove only the known placeholder links and maintenance '
                'records from the legacy machine sample data'
            ),
        )

    def handle(self, *args, **options):
        """Load and synchronize the machine demo manifest."""
        data = self._read_data()

        with transaction.atomic():
            customers = self._load_customers(data['customers'])
            categories = self._load_categories(data['parts'])
            parts = self._load_parts(data['parts'], categories=categories)

            machine_count = 0
            part_link_count = 0
            maintenance_count = 0
            work_order_count = 0

            for machine_data in data['machines']:
                machine, created_links, loaded_records, loaded_work_orders = (
                    self._load_machine(machine_data, customers=customers, parts=parts)
                )
                machine_count += 1
                part_link_count += created_links
                maintenance_count += loaded_records
                work_order_count += loaded_work_orders

                self.stdout.write(f'Loaded machine: {machine.name}')

            pruned_count = 0
            if options['prune']:
                pruned_count = self._prune_legacy_records(data['legacy_records'])

            if options['dry_run']:
                transaction.set_rollback(True)

        action = 'Validated' if options['dry_run'] else 'Loaded'
        self.stdout.write(
            self.style.SUCCESS(
                f'{action} '
                f'{machine_count} machines, {len(parts)} catalog parts, '
                f'{len(categories)} part categories, '
                f'{part_link_count} installed-part links, '
                f'{maintenance_count} maintenance records, and '
                f'{work_order_count} linked work orders; '
                f'removed {pruned_count} legacy placeholder records.'
            )
        )

    def _read_data(self):
        """Read and minimally validate the bundled data manifest."""
        try:
            data = json.loads(DATA_FILE.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError(f'Could not read {DATA_FILE}: {exc}') from exc

        if data.get('schema_version') != 1:
            raise CommandError('Unsupported machine demo data schema version')

        for key in ('customers', 'parts', 'machines'):
            if not isinstance(data.get(key), list):
                raise CommandError(f'Machine demo data must contain a {key} list')

        legacy_records = data.get('legacy_records')
        if not isinstance(legacy_records, dict):
            raise CommandError('Machine demo data must contain legacy_records')
        for key in ('machine_parts', 'maintenance'):
            if not isinstance(legacy_records.get(key), list):
                raise CommandError(
                    f'Machine demo legacy_records must contain a {key} list'
                )

        self._validate_maintenance_history(data['machines'])

        return data

    @staticmethod
    def _validate_maintenance_history(machines):
        """Require completed work-order metadata on every owned history row.

        A dataset-owned maintenance row without a work order would render in the
        machine Maintenance blade as an unlinked legacy record. That presentation
        exists only for genuinely unowned history, so a missing declaration here
        is an import error rather than a frontend empty state.
        """
        references = {}

        for machine in machines:
            for record in machine.get('maintenance', []):
                label = f'{machine["name"]!r} on {record.get("date")}'
                work_order = record.get('work_order')

                if not isinstance(work_order, dict):
                    raise CommandError(
                        f'Demo maintenance record for {label} must declare a '
                        'completed work order'
                    )

                missing = [
                    key
                    for key in ('reference', 'type', 'priority')
                    if not work_order.get(key)
                ]
                if missing:
                    raise CommandError(
                        f'Demo work order for {label} is missing: ' + ', '.join(missing)
                    )

                reference = work_order['reference']
                if reference in references:
                    raise CommandError(
                        f'Demo work-order reference {reference!r} is declared '
                        f'twice ({references[reference]} and {label})'
                    )
                references[reference] = label

    def _load_customers(self, records):
        """Upsert demo customers and return them by name."""
        customers = {}

        for record in records:
            values = record.copy()
            name = values.pop('name')
            legacy_machine = values.pop('legacy_machine', None)
            matches = Company.objects.filter(name__iexact=name).order_by('pk')

            if matches.count() > 1:
                raise CommandError(
                    f'Multiple companies match demo customer name {name!r}'
                )

            customer = matches.first()
            if customer is None:
                customer = Company.objects.create(
                    name=name,
                    metadata={
                        DEMO_METADATA_KEY: {'kind': 'customer', 'schema_version': 1}
                    },
                    **values,
                )
            else:
                marker = customer.get_metadata(DEMO_METADATA_KEY, {})
                managed = isinstance(marker, dict) and marker.get('kind') == 'customer'
                legacy_owned = bool(
                    legacy_machine
                    and AssetMachine.objects.filter(
                        name=legacy_machine, customer=customer
                    ).exists()
                )
                if not managed and not legacy_owned:
                    raise CommandError(
                        f'Demo customer name {name!r} is already used by '
                        'a record not owned by the machine demo dataset'
                    )

                for field, value in values.items():
                    setattr(customer, field, value)
                metadata = dict(customer.metadata or {})
                metadata[DEMO_METADATA_KEY] = {'kind': 'customer', 'schema_version': 1}
                customer.metadata = metadata
                customer.save(update_fields=[*values, 'metadata'])

            customers[name] = customer

        return customers

    @staticmethod
    def _normalize_category_path(value):
        """Return a normalized slash-separated category path."""
        if not isinstance(value, str):
            raise CommandError('Demo part category_path must be a string')

        components = [component.strip() for component in value.split('/')]
        if not components or any(not component for component in components):
            raise CommandError(f'Invalid demo part category path {value!r}')

        return '/'.join(components)

    @staticmethod
    def _metadata_marker(kind, record=None):
        """Return an ownership marker with optional dataset metadata."""
        extra = record.get('demo_metadata', {}) if record else {}
        if not isinstance(extra, dict):
            raise CommandError('Demo record demo_metadata must be an object')
        marker = dict(extra)
        marker.update({'kind': kind, 'schema_version': 1})
        return marker

    def _load_categories(self, part_records):
        """Create managed category paths requested by demo parts."""
        requested_paths = {
            self._normalize_category_path(record['category_path'])
            for record in part_records
            if record.get('category_path')
        }
        categories = {}

        for path in sorted(
            requested_paths, key=lambda value: (value.count('/'), value.casefold())
        ):
            parent = None
            components = path.split('/')

            for index, name in enumerate(components):
                current_path = '/'.join(components[: index + 1])
                if current_path in categories:
                    parent = categories[current_path]
                    continue

                matches = PartCategory.objects.filter(
                    parent=parent, name__iexact=name
                ).order_by('pk')
                if matches.count() > 1:
                    raise CommandError(
                        f'Multiple part categories match demo path {current_path!r}'
                    )

                category = matches.first()
                structural = index < len(components) - 1
                if category is None:
                    category = PartCategory.objects.create(
                        name=name,
                        parent=parent,
                        structural=structural,
                        metadata={
                            DEMO_METADATA_KEY: self._metadata_marker('part_category')
                        },
                    )
                else:
                    marker = category.get_metadata(DEMO_METADATA_KEY, {})
                    if (
                        not isinstance(marker, dict)
                        or marker.get('kind') != 'part_category'
                    ):
                        raise CommandError(
                            f'Demo category path {current_path!r} is already used by '
                            'a record not owned by the machine demo dataset'
                        )
                    if structural and not category.structural:
                        category.structural = True
                        category.save(update_fields=['structural'])

                categories[current_path] = category
                parent = category

        return categories

    def _load_parts(self, records, *, categories):
        """Upsert demo catalog parts and return them by IPN."""
        parts = {}

        for record in records:
            ipn = record['ipn']
            category_path = record.get('category_path')
            category = None
            if category_path:
                category_path = self._normalize_category_path(category_path)
                category = categories[category_path]

            values = {
                'IPN': ipn,
                'name': record['name'],
                'description': record['description'],
                'category': category,
                'link': record.get('link') or None,
            }
            try:
                Part._meta.get_field('link').clean(values['link'], None)
            except ValidationError as exc:
                raise CommandError(
                    f'Invalid demo link for part {ipn!r}: {exc}'
                ) from exc

            matches = Part.objects.filter(IPN__iexact=ipn).order_by('pk')

            if matches.count() > 1:
                raise CommandError(f'Multiple parts match demo IPN {ipn!r}')

            part = matches.first()

            if part is None:
                if record.get('reuse_existing'):
                    raise CommandError(
                        f'Required upstream demo part {ipn!r} was not found'
                    )
                part = Part.objects.create(
                    **values,
                    metadata={DEMO_METADATA_KEY: self._metadata_marker('part', record)},
                )
            elif record.get('reuse_existing'):
                if part.name.casefold() != record['name'].casefold():
                    raise CommandError(
                        f'Demo IPN {ipn!r} belongs to unexpected part {part.name!r}'
                    )
            elif part.name.casefold() != record['name'].casefold():
                raise CommandError(f'Demo IPN {ipn!r} is already used by {part.name!r}')
            else:
                marker = part.get_metadata(DEMO_METADATA_KEY, {})
                if not isinstance(marker, dict) or marker.get('kind') != 'part':
                    raise CommandError(
                        f'Demo IPN {ipn!r} is already used by a record not owned '
                        'by the machine demo dataset'
                    )
                for field, value in values.items():
                    setattr(part, field, value)
                metadata = dict(part.metadata or {})
                metadata[DEMO_METADATA_KEY] = self._metadata_marker('part', record)
                part.metadata = metadata
                part.save(update_fields=[*values, 'metadata'])

            parts[ipn] = part

        return parts

    def _load_machine(self, record, *, customers, parts):
        """Upsert one machine and its installed parts and history."""
        machine_values = {
            key: record[key]
            for key in (
                'description',
                'active',
                'location',
                'manufacturer',
                'model',
                'serial',
            )
        }

        customer_name = record.get('customer')
        if customer_name:
            try:
                machine_values['customer'] = customers[customer_name]
            except KeyError as exc:
                raise CommandError(
                    f'Unknown demo customer {customer_name!r} for {record["name"]!r}'
                ) from exc
        else:
            machine_values['customer'] = None

        matches = AssetMachine.objects.filter(name__iexact=record['name']).order_by(
            'pk'
        )
        if matches.count() > 1:
            raise CommandError(
                f'Multiple machines match demo machine name {record["name"]!r}'
            )

        machine = matches.first()
        if machine is None:
            machine = AssetMachine.objects.create(name=record['name'], **machine_values)
        else:
            actual_identity = self._machine_identity({
                'manufacturer': machine.manufacturer,
                'model': machine.model,
                'serial': machine.serial,
            })
            current_identity = self._machine_identity(record)
            legacy_identity = self._machine_identity(record['legacy_identity'])
            expected_identities = {current_identity, legacy_identity}
            if actual_identity not in expected_identities:
                raise CommandError(
                    f'Demo machine name {record["name"]!r} is already used by '
                    'a record with a different manufacturer, model, or serial'
                )

            legacy_owned = (
                actual_identity == legacy_identity
                and legacy_identity != current_identity
            )
            if not legacy_owned and not self._machine_has_managed_part(machine, record):
                raise CommandError(
                    f'Demo machine name {record["name"]!r} is already used by '
                    'a record not owned by the machine demo dataset'
                )

            machine.name = record['name']
            for field, value in machine_values.items():
                setattr(machine, field, value)
            machine.save(update_fields=['name', *machine_values])

        link_ids = []
        for link_data in record['installed_parts']:
            ipn = link_data['ipn']
            try:
                part = parts[ipn]
            except KeyError as exc:
                raise CommandError(
                    f'Unknown demo part IPN {ipn!r} for {machine.name!r}'
                ) from exc

            link, _ = MachinePart.objects.update_or_create(
                machine=machine,
                part=part,
                defaults={
                    'quantity': link_data['quantity'],
                    'notes': link_data.get('notes', ''),
                },
            )
            link_ids.append(link.pk)

        maintenance_ids = []
        work_order_count = 0

        for maintenance_data in record['maintenance']:
            work_order = self._load_work_order(machine, maintenance_data)
            maintenance = self._load_maintenance_record(
                machine, maintenance_data, work_order
            )
            maintenance_ids.append(maintenance.pk)
            work_order_count += int(work_order is not None)

        return machine, len(link_ids), len(maintenance_ids), work_order_count

    @staticmethod
    def _machine_identity(values):
        """Return a normalized manufacturer, model, and serial identity tuple."""
        return tuple(
            str(values.get(field) or '').strip().casefold()
            for field in ('manufacturer', 'model', 'serial')
        )

    @staticmethod
    def _machine_has_managed_part(machine, record):
        """Return whether the machine links to an expected managed demo part."""
        expected_ipns = {
            link_data['ipn'].casefold() for link_data in record['installed_parts']
        }

        for link in machine.machine_parts.select_related('part'):
            if (link.part.IPN or '').casefold() not in expected_ipns:
                continue

            marker = link.part.get_metadata(DEMO_METADATA_KEY, {})
            if isinstance(marker, dict) and marker.get('kind') == 'part':
                return True

        return False

    def _prune_legacy_records(self, records):
        """Remove only records that exactly match the old placeholder dataset."""
        deleted_count = 0

        for record in records['machine_parts']:
            # An empty revision in the record matches both '' and NULL - parts
            # created without an explicit revision store NULL
            revision = record['part_revision']
            revision_filter = (
                Q(part__revision=revision)
                if revision
                else (Q(part__revision='') | Q(part__revision__isnull=True))
            )
            candidates = (
                MachinePart.objects
                .select_related('machine', 'part')
                .filter(
                    revision_filter,
                    machine__name__iexact=record['machine'],
                    part__IPN__iexact=record['ipn'],
                    quantity=record['quantity'],
                )
                .order_by('pk')
            )
            matches = [
                link
                for link in candidates
                if link.machine.name == record['machine']
                and record['ipn'] == link.part.IPN
                and link.part.name == record['part_name']
                and (link.part.revision or '') == (record['part_revision'] or '')
                and link.notes == record['notes']
            ]
            if len(matches) > 1:
                raise CommandError(
                    'Multiple machine-part rows match one legacy placeholder '
                    f'record for {record["machine"]!r} and {record["ipn"]!r}'
                )
            if matches:
                matches[0].delete()
                deleted_count += 1

        for record in records['maintenance']:
            record_date = datetime.date.fromisoformat(record['date'])
            candidates = (
                AssetMaintenanceRecord.objects
                .select_related('machine')
                .filter(
                    machine__name__iexact=record['machine'],
                    date=record_date,
                    work_order__isnull=True,
                )
                .order_by('pk')
            )
            matches = [
                maintenance
                for maintenance in candidates
                if maintenance.machine.name == record['machine']
                and maintenance.summary == record['summary']
                and maintenance.details == record['details']
                and maintenance.performed_by == record['performed_by']
            ]
            if len(matches) > 1:
                raise CommandError(
                    'Multiple maintenance rows match one legacy placeholder '
                    f'record for {record["machine"]!r} on {record["date"]}'
                )
            if matches:
                matches[0].delete()
                deleted_count += 1

        return deleted_count

    def _load_work_order(self, machine, maintenance_data):
        """Create or update the optional completed work order for a history row."""
        if connection.vendor != 'postgresql':
            return None

        work_order_data = maintenance_data.get('work_order')
        if not work_order_data:
            return None

        reference = work_order_data['reference']
        title = f'{machine.name}: {maintenance_data["summary"]}'[:200]

        existing = WorkOrder.objects.filter(reference=reference).first()
        if existing and (
            existing.machine_id != machine.pk or 'demo' not in (existing.tags or [])
        ):
            raise CommandError(
                f'Work-order reference {reference!r} is already used by '
                'a record not owned by the machine demo dataset'
            )

        record_date = datetime.date.fromisoformat(maintenance_data['date'])

        work_order, _ = WorkOrder.objects.update_or_create(
            reference=reference,
            defaults={
                'title': title,
                'description': maintenance_data['details'],
                'status': WorkOrder.STATUS_DONE,
                'priority': work_order_data['priority'],
                'due_date': record_date,
                'assignee': maintenance_data['performed_by'],
                'tags': ['demo', 'maintenance'],
                'company': machine.customer.name if machine.customer else '',
                'job_number': reference,
                'is_active': False,
                'lifecycle_status': WorkOrderLifecycle.COMPLETED,
                'work_order_type': work_order_data['type'],
                'machine': machine,
                'customer': machine.customer,
            },
        )

        normalize_completed_history_card(
            work_order, record_date=record_date, dataset=DEMO_METADATA_KEY
        )

        return work_order

    def _load_maintenance_record(self, machine, record, work_order):
        """Create, update, or safely reattach one maintenance-history row."""
        record_date = datetime.date.fromisoformat(record['date'])

        maintenance = None
        if work_order is not None:
            maintenance = AssetMaintenanceRecord.objects.filter(
                work_order=work_order
            ).first()
            if maintenance and maintenance.machine_id != machine.pk:
                raise CommandError(
                    f'Work order {work_order.reference!r} is linked to maintenance '
                    f'for a different machine'
                )

        unlinked_candidates = list(
            AssetMaintenanceRecord.objects.filter(
                machine=machine, date=record_date, work_order__isnull=True
            ).order_by('pk')
        )
        exact_matches = [
            candidate
            for candidate in unlinked_candidates
            if candidate.summary == record['summary']
            and candidate.details == record['details']
            and candidate.performed_by == record['performed_by']
        ]

        if len(exact_matches) > 1:
            raise CommandError(
                f'Multiple maintenance rows match demo event {record["summary"]!r} '
                f'for {machine.name!r} on {record["date"]}'
            )

        if maintenance is not None and exact_matches:
            raise CommandError(
                f'Demo event {record["summary"]!r} for {machine.name!r} has '
                'both linked and unlinked maintenance records'
            )

        if maintenance is None and exact_matches:
            maintenance = exact_matches[0]

        if maintenance is None:
            title_collisions = [
                candidate
                for candidate in unlinked_candidates
                if candidate.summary == record['summary']
            ]
            if title_collisions:
                raise CommandError(
                    f'Demo maintenance event {record["summary"]!r} for '
                    f'{machine.name!r} on {record["date"]} conflicts with '
                    'an unowned maintenance record'
                )

        values = {
            'machine': machine,
            'date': record_date,
            'summary': record['summary'],
            'details': record['details'],
            'performed_by': record['performed_by'],
            'work_order': work_order,
        }

        if maintenance is None:
            maintenance = AssetMaintenanceRecord.objects.create(**values)
        else:
            for field, value in values.items():
                setattr(maintenance, field, value)
            maintenance.save(update_fields=list(values))

        return maintenance
