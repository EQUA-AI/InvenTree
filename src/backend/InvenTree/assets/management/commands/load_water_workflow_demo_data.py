"""Load maintenance history and scheduled repair scenarios for water assets."""

import datetime
import json
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from tasks.models import (
    KanbanCard,
    KanbanCardDependency,
    KanbanCardPart,
    KanbanColumn,
    WorkingCalendar,
    WorkOrderLifecycle,
    WorkOrderType,
)
from tasks.services.conflicts import detect_conflicts
from tasks.services.working_time import add_working_minutes, next_working_instant

from assets.demo_enrichment import CoverageReport, apply_profile, validate_profile
from assets.demo_history import normalize_completed_history_card
from assets.models import AssetMachine, AssetMaintenanceRecord
from part.models import Part
from repair.models import (
    GenerationStatus,
    LockoutPoint,
    PacketStatus,
    RepairPacket,
    SafetyGateTemplate,
)
from repair.services import resolve_safety_gates

DATA_FILE = Path(__file__).resolve().parents[2] / 'water_workflow_demo_data.json'
DATASET_TAG = 'water_workflow_demo'
DEMO_TAGS = {'demo', 'water_wastewater', DATASET_TAG}
CARD_STAGES = {
    KanbanCard.STATUS_BACKLOG,
    KanbanCard.STATUS_IN_PROGRESS,
    KanbanCard.STATUS_REVIEW,
}


class Command(BaseCommand):
    """Load deterministic maintenance and repair workflow demo records."""

    help = 'Load maintenance history and scheduled repair scenarios for water assets'

    def add_arguments(self, parser):
        """Register command-line arguments."""
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Validate and load the manifest, then roll back all changes',
        )
        parser.add_argument(
            '--schedule-anchor', help='Local schedule anchor date in YYYY-MM-DD form'
        )
        parser.add_argument(
            '--enrich-owned-work-orders',
            action='store_true',
            help=(
                'Apply detail profiles to every owned card, including records '
                'already present in the database'
            ),
        )
        parser.add_argument(
            '--require-complete-profiles',
            action='store_true',
            help=(
                'Fail the transaction if any owned work order lacks a detail '
                'profile; turn this on once every record is authored'
            ),
        )
        parser.add_argument(
            '--reset-owned-scenarios',
            action='store_true',
            help=(
                'Recreate only active cards and packets owned by this dataset; '
                'completed scenario records are never reset'
            ),
        )

    def handle(self, *args, **options):
        """Load the workflow manifest in one transaction."""
        data = self._read_data()
        anchor = self._schedule_anchor(
            options.get('schedule_anchor') or data['default_schedule_anchor']
        )

        with transaction.atomic():
            machines = self._load_machines(data['machines'])
            users = self._load_users(data)
            parts = self._load_parts(data)

            if options['reset_owned_scenarios']:
                self._reset_owned_scenarios(data)

            calendars = self._load_calendars(
                data['calendars'],
                machines=machines,
                reset=options['reset_owned_scenarios'],
            )
            self._load_safety_templates(
                data['safety_templates'], reset=options['reset_owned_scenarios']
            )

            history_count = self._load_history(
                data['machines'], machines=machines, users=users, calendars=calendars
            )
            scenario_cards, packet_count, part_line_count = self._load_scenarios(
                data['machines'],
                anchor=anchor,
                calendars=calendars,
                machines=machines,
                parts=parts,
                users=users,
                requested_by=users[data['requested_by']],
            )
            procurement_cards, dependency_count = self._load_procurement_children(
                data['procurement_children'],
                anchor=anchor,
                calendars=calendars,
                scenario_cards=scenario_cards,
                parts=parts,
                users=users,
                requested_by=users[data['requested_by']],
            )

            active_cards = [*scenario_cards.values(), *procurement_cards.values()]
            self._validate_schedule(active_cards)

            coverage = None
            if options['enrich_owned_work_orders']:
                coverage = self._enrich_owned_work_orders(
                    data, require_complete=options['require_complete_profiles']
                )

            if options['dry_run']:
                transaction.set_rollback(True)

        if coverage is not None:
            self._report_coverage(coverage)

        action = 'Validated' if options['dry_run'] else 'Loaded'
        self.stdout.write(
            self.style.SUCCESS(
                f'{action} {history_count} maintenance records, '
                f'{history_count} historical work orders, '
                f'{len(scenario_cards)} repair scenarios, '
                f'{len(procurement_cards)} procurement cards, '
                f'{packet_count} repair packets, {part_line_count} required-part '
                f'lines, {dependency_count} dependencies, and '
                f'{len(calendars["by_name"])} calendars; '
                f'schedule anchor {anchor.isoformat()}.'
            )
        )

    def _enrich_owned_work_orders(self, data, *, require_complete: bool):
        """Apply detail profiles to every card this dataset owns.

        Discovery is by ownership tag, not by a hardcoded list: a record added to
        the manifest later is picked up automatically rather than silently
        skipped.
        """
        report = CoverageReport()
        profiles = self._collect_profiles(data)

        # Every ownership-tagged card, not only the ones with a profile: that is
        # what makes the coverage number honest.
        discovered = [
            card
            for card in KanbanCard.objects.filter(
                reference__startswith='WO-WW-'
            ).select_related('repair_packet')
            if DEMO_TAGS.issubset(set(card.tags or []))
        ]
        report.discovered = len(discovered)

        by_reference = {card.reference: card for card in discovered}

        for reference, raw_profile in profiles.items():
            card = by_reference.get(reference)
            if card is None:
                # The manifest names a record the database does not have. That is
                # a load ordering problem, not something to paper over.
                raise CommandError(
                    f'Profile references unknown owned work order {reference!r}'
                )

            profile = validate_profile(
                raw_profile,
                reference=reference,
                card_kind=card.card_kind,
                is_terminal=card.lifecycle_status
                in {WorkOrderLifecycle.COMPLETED, WorkOrderLifecycle.CANCELED},
            )
            apply_profile(card, profile, dataset=DATASET_TAG, report=report)

        report.missing_profile = [
            card.reference for card in discovered if card.reference not in profiles
        ]

        if require_complete and not report.complete:
            raise CommandError(
                'Owned work orders without a detail profile: '
                + ', '.join(report.missing_profile)
            )

        return report

    @staticmethod
    def _collect_profiles(data) -> dict:
        """Return every declared detail profile keyed by work-order reference."""
        profiles = {}

        for machine in data['machines']:
            for event in machine.get('history', []):
                profile = event.get('detail_profile')
                if profile is not None:
                    profiles[event['work_order']['reference']] = profile

            scenario = machine.get('scenario') or {}
            if scenario.get('detail_profile') is not None:
                profiles[scenario['reference']] = scenario['detail_profile']

        for child in data.get('procurement_children', []):
            if child.get('detail_profile') is not None:
                profiles[child['reference']] = child['detail_profile']

        return profiles

    def _report_coverage(self, report):
        """Print the coverage report the plan asks for before commit."""
        summary = report.as_dict()
        self.stdout.write(
            'Enrichment coverage: '
            f'{summary["discovered"]} owned cards discovered, '
            f'{summary["enriched"]} enriched, '
            f'{summary["unchanged"]} already current, '
            f'{summary["findings_written"]} findings, '
            f'{summary["scopes_written"]} approved scopes.'
        )
        if summary['by_class']:
            self.stdout.write(f'  By class: {summary["by_class"]}')
        if summary['missing_profile']:
            self.stdout.write(
                self.style.WARNING(
                    f'  {len(summary["missing_profile"])} owned card(s) have no '
                    f'detail profile: {", ".join(summary["missing_profile"])}'
                )
            )

    def _read_data(self):
        """Read and minimally validate the bundled workflow manifest."""
        try:
            data = json.loads(DATA_FILE.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError(f'Could not read {DATA_FILE}: {exc}') from exc

        if data.get('schema_version') != 1:
            raise CommandError('Unsupported water workflow demo schema version')
        if data.get('dataset') != DATASET_TAG:
            raise CommandError('Unexpected water workflow dataset marker')
        for key in (
            'calendars',
            'safety_templates',
            'machines',
            'procurement_children',
        ):
            if not isinstance(data.get(key), list):
                raise CommandError(f'Water workflow manifest must contain a {key} list')
        if not isinstance(data.get('requested_by'), str):
            raise CommandError('Water workflow manifest must define requested_by')

        self._validate_history_declarations(data['machines'])

        return data

    @staticmethod
    def _validate_history_declarations(machines):
        """Require completed work-order metadata on every owned history row.

        Mirrors the rich-machine manifest rule: a dataset-owned maintenance row
        may never load as an unlinked legacy record.
        """
        references = {}

        for machine in machines:
            for event in machine.get('history', []):
                label = f'{machine["name"]!r} on {event.get("date")}'
                work_order = event.get('work_order')

                if not isinstance(work_order, dict):
                    raise CommandError(
                        f'Water workflow history for {label} must declare a '
                        'completed work order'
                    )

                missing = [
                    key
                    for key in ('reference', 'type', 'priority', 'assigned_to')
                    if not work_order.get(key)
                ]
                if missing:
                    raise CommandError(
                        f'Water workflow work order for {label} is missing: '
                        + ', '.join(missing)
                    )

                reference = work_order['reference']
                if reference in references:
                    raise CommandError(
                        f'Water workflow reference {reference!r} is declared '
                        f'twice ({references[reference]} and {label})'
                    )
                references[reference] = label

    @staticmethod
    def _schedule_anchor(value):
        """Parse the local schedule anchor date."""
        try:
            return datetime.date.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise CommandError(
                f'Invalid schedule anchor {value!r}; expected YYYY-MM-DD'
            ) from exc

    @staticmethod
    def _unique_match(queryset, description):
        """Return one exact logical match or raise on missing/ambiguous data."""
        matches = list(queryset.order_by('pk')[:2])
        if not matches:
            raise CommandError(f'Required {description} was not found')
        if len(matches) > 1:
            raise CommandError(f'Multiple records match {description}')
        return matches[0]

    def _load_machines(self, records):
        """Resolve all target machines by their unique names."""
        machines = {}
        for record in records:
            name = record['name']
            machines[name] = self._unique_match(
                AssetMachine.objects.filter(name__iexact=name),
                f'water machine {name!r}',
            )
        return machines

    def _load_users(self, data):
        """Resolve every user referenced by the workflow manifest."""
        usernames = {data['requested_by']}
        for machine_data in data['machines']:
            for event in machine_data['history']:
                usernames.add(event['work_order']['assigned_to'])
            usernames.add(machine_data['scenario']['assigned_to'])
        for child in data['procurement_children']:
            usernames.add(child['assigned_to'])

        user_model = get_user_model()
        return {
            username: self._unique_match(
                user_model.objects.filter(username__iexact=username, is_active=True),
                f'active user {username!r}',
            )
            for username in sorted(usernames)
        }

    def _load_parts(self, data):
        """Resolve every part referenced by repair and procurement records."""
        ipns = {
            line['ipn']
            for machine_data in data['machines']
            for line in machine_data['scenario']['required_parts']
        }
        ipns.update(child['part_ipn'] for child in data['procurement_children'])

        return {
            ipn: self._unique_match(
                Part.objects.filter(IPN__iexact=ipn), f'water part IPN {ipn!r}'
            )
            for ipn in sorted(ipns)
        }

    @staticmethod
    def _owned_tags(kind):
        """Return the complete ownership tag list for a card kind."""
        return [*sorted(DEMO_TAGS), kind]

    @staticmethod
    def _is_owned_card(card, kind):
        """Return whether a card is owned by this dataset and kind."""
        tags = set(card.tags or [])
        return DEMO_TAGS.issubset(tags) and kind in tags

    def _owned_card(self, reference, kind, machine, *, parent=None):
        """Return an existing owned card or raise on a reference collision."""
        matches = list(KanbanCard.objects.filter(reference=reference)[:2])
        if len(matches) > 1:
            raise CommandError(f'Multiple work orders match reference {reference!r}')
        if not matches:
            return None

        card = matches[0]
        if not self._is_owned_card(card, kind):
            raise CommandError(
                f'Work-order reference {reference!r} is already used by a record '
                'not owned by the water workflow dataset'
            )
        if card.machine_id != machine.pk:
            raise CommandError(
                f'Owned work order {reference!r} is linked to a different machine'
            )
        expected_parent_id = parent.pk if parent else None
        if card.parent_id != expected_parent_id:
            raise CommandError(
                f'Owned work order {reference!r} has an unexpected parent card'
            )
        return card

    def _reset_owned_scenarios(self, data):
        """Delete only non-terminal active scenario records owned by this dataset."""
        expected = {
            machine_data['scenario']['reference']: 'repair_scenario'
            for machine_data in data['machines']
        }
        expected.update({
            child['reference']: 'procurement' for child in data['procurement_children']
        })
        owned = list(KanbanCard.objects.filter(reference__in=expected))
        if not owned:
            return

        unowned = [
            card.reference
            for card in owned
            if not self._is_owned_card(card, expected[card.reference])
        ]
        if unowned:
            raise CommandError(
                'Cannot reset unowned water workflow references: '
                + ', '.join(sorted(unowned))
            )

        card_ids = [card.pk for card in owned]
        completed = [
            card.reference
            for card in owned
            if card.lifecycle_status
            in {WorkOrderLifecycle.COMPLETED, WorkOrderLifecycle.CANCELED}
            or AssetMaintenanceRecord.objects.filter(work_order=card).exists()
        ]
        if completed:
            raise CommandError(
                'Cannot reset completed water workflow scenarios: '
                + ', '.join(
                    sorted(reference or '<no reference>' for reference in completed)
                )
            )

        RepairPacket.objects.filter(work_order_id__in=card_ids).delete()
        KanbanCard.objects.filter(pk__in=card_ids, parent__isnull=False).delete()
        KanbanCard.objects.filter(pk__in=card_ids).delete()

    def _load_calendars(self, records, *, machines, reset):
        """Create demo calendars or adopt an existing calendar for the same scope."""
        by_name = {}
        by_machine = {}
        default_calendar = None

        for record in records:
            machine_name = record.get('machine')
            machine = machines[machine_name] if machine_name else None
            matches = list(WorkingCalendar.objects.filter(name=record['name'])[:2])
            if len(matches) > 1:
                raise CommandError(
                    f'Multiple working calendars match {record["name"]!r}'
                )

            values = {
                'timezone': record['timezone'],
                'windows': record['windows'],
                'holidays': record['holidays'],
                'is_default': record['is_default'],
                'machine': machine,
                'customer': None,
            }
            calendar = matches[0] if matches else None
            owned = calendar is not None
            if calendar is None:
                scoped = WorkingCalendar.objects.none()
                if record['is_default']:
                    scoped = WorkingCalendar.objects.filter(is_default=True)
                elif machine:
                    scoped = WorkingCalendar.objects.filter(machine=machine)
                scoped_matches = list(scoped.order_by('pk')[:2])
                if len(scoped_matches) > 1:
                    raise CommandError(
                        f'Multiple working calendars match the scope for '
                        f'{record["name"]!r}'
                    )
                calendar = scoped_matches[0] if scoped_matches else None

            if calendar is None:
                calendar = WorkingCalendar(name=record['name'], **values)
                calendar.full_clean()
                calendar.save()
                owned = True
            else:
                expected_machine_id = machine.pk if machine else None
                if calendar.machine_id != expected_machine_id:
                    raise CommandError(
                        f'Working calendar {calendar.name!r} has an unexpected scope'
                    )
                if bool(calendar.is_default) != bool(record['is_default']):
                    raise CommandError(
                        f'Working calendar {calendar.name!r} has an unexpected default flag'
                    )
                if reset and owned:
                    for field, value in values.items():
                        setattr(calendar, field, value)
                    calendar.full_clean()
                    calendar.save(update_fields=[*values, 'updated_at'])

            by_name[record['name']] = calendar
            if machine:
                by_machine[machine.name] = calendar
            if record['is_default']:
                default_calendar = calendar

        if default_calendar is None:
            raise CommandError('Workflow manifest must define one default calendar')
        return {
            'by_name': by_name,
            'by_machine': by_machine,
            'default': default_calendar,
        }

    def _load_safety_templates(self, records, *, reset):
        """Create managed templates and verify shared templates exactly."""
        for record in records:
            matches = list(SafetyGateTemplate.objects.filter(name=record['name'])[:2])
            if len(matches) > 1:
                raise CommandError(
                    f'Multiple safety templates match {record["name"]!r}'
                )

            values = {
                'gate_type': record['gate_type'],
                'instructions': record['instructions'],
                'applies_to': record['applies_to'],
                'required_permission': record.get('required_permission', ''),
                'requires_photo': record['requires_photo'],
                'requires_second_person': record['requires_second_person'],
                'is_blocking': record['is_blocking'],
                'is_mandatory': record['is_mandatory'],
                'risk_tier': record['risk_tier'],
                'default_sequence': record['default_sequence'],
                'active': True,
            }
            managed = bool(record.get('managed', True))
            template = matches[0] if matches else None
            if template is None:
                template = SafetyGateTemplate.objects.create(
                    name=record['name'], **values
                )
            elif managed and (template.applies_to or {}).get('dataset') != DATASET_TAG:
                raise CommandError(
                    f'Safety template {template.name!r} is not owned by the '
                    'water workflow dataset'
                )
            elif not managed and any(
                getattr(template, field) != value for field, value in values.items()
            ):
                raise CommandError(
                    f'Required safety template {template.name!r} does not match '
                    'the water workflow contract'
                )
            elif managed and reset:
                for field, value in values.items():
                    setattr(template, field, value)
                template.save(update_fields=[*values, 'updated_at'])

    @staticmethod
    def _user_label(user):
        """Return a human-readable assignee label."""
        return user.get_full_name().strip() or user.get_username()

    def _load_history(self, records, *, machines, users, calendars):
        """Upsert immutable completed maintenance history and linked work orders."""
        count = 0
        terminal_status = KanbanColumn.terminal_key() or KanbanCard.STATUS_DONE

        for machine_data in records:
            machine = machines[machine_data['name']]
            for event in machine_data['history']:
                work_order_data = event['work_order']
                reference = work_order_data['reference']
                assignee = users[work_order_data['assigned_to']]
                card = self._owned_card(
                    reference, 'maintenance_history', machine, parent=None
                )
                card_values = {
                    'title': f'{machine.name}: {event["summary"]}'[:200],
                    'description': event['details'],
                    'status': terminal_status,
                    'priority': work_order_data['priority'],
                    'due_date': datetime.date.fromisoformat(event['date']),
                    'assignee': event['performed_by'],
                    'tags': self._owned_tags('maintenance_history'),
                    'company': machine.customer.name if machine.customer else '',
                    'job_number': reference,
                    'is_active': False,
                    'lifecycle_status': WorkOrderLifecycle.COMPLETED,
                    'work_order_type': work_order_data['type'],
                    'machine': machine,
                    'customer': machine.customer,
                    'assigned_to': assignee,
                    'requested_by': assignee,
                    'card_kind': KanbanCard.KIND_WORK_ORDER,
                }
                if card is None:
                    card = KanbanCard.objects.create(reference=reference, **card_values)
                else:
                    for field, value in card_values.items():
                        setattr(card, field, value)
                    card.save(update_fields=[*card_values, 'updated_at'])

                record_date = datetime.date.fromisoformat(event['date'])
                calendar = calendars['by_machine'].get(
                    machine.name, calendars['default']
                )
                normalize_completed_history_card(
                    card,
                    record_date=record_date,
                    dataset=DATASET_TAG,
                    timezone_name=calendar.timezone,
                )
                maintenance = AssetMaintenanceRecord.objects.filter(
                    work_order=card
                ).first()
                if maintenance is None:
                    collision = AssetMaintenanceRecord.objects.filter(
                        machine=machine, date=record_date, summary=event['summary']
                    ).exists()
                    if collision:
                        raise CommandError(
                            f'Maintenance event {event["summary"]!r} on '
                            f'{event["date"]} collides with an unowned record'
                        )
                    maintenance = AssetMaintenanceRecord(work_order=card)

                maintenance.machine = machine
                maintenance.date = record_date
                maintenance.summary = event['summary']
                maintenance.details = event['details']
                maintenance.performed_by = event['performed_by']
                maintenance.save()
                count += 1

        return count

    @staticmethod
    def _stored_datetime(value, calendar):
        """Return a database-safe datetime for the current USE_TZ setting."""
        if settings.USE_TZ:
            return value
        return value.astimezone(ZoneInfo(calendar.timezone)).replace(tzinfo=None)

    def _schedule_window(self, machine, schedule, duration, anchor, calendars):
        """Return a calendar-aware scheduled start/end window."""
        calendar = calendars['by_machine'].get(machine.name, calendars['default'])
        try:
            start_time = datetime.time.fromisoformat(schedule['start'])
        except (KeyError, TypeError, ValueError) as exc:
            raise CommandError(f'Invalid schedule start {schedule!r}') from exc

        local_day = anchor + datetime.timedelta(days=int(schedule['day_offset']))
        local_start = datetime.datetime.combine(
            local_day, start_time, tzinfo=ZoneInfo(calendar.timezone)
        )
        spec = calendar.to_spec()
        start = next_working_instant(spec, local_start)
        end = add_working_minutes(spec, start, duration)
        return (
            self._stored_datetime(start, calendar),
            self._stored_datetime(end, calendar),
        )

    @staticmethod
    def _stage_status(record):
        """Return a valid non-terminal board stage for a seeded card."""
        stage = record.get('stage')
        if stage not in CARD_STAGES:
            raise CommandError(
                f'Invalid workflow stage {stage!r} for {record.get("reference")!r}'
            )
        return stage

    def _load_scenarios(
        self, records, *, anchor, calendars, machines, parts, users, requested_by
    ):
        """Create active repair cards, draft packets, gates, and required parts."""
        scenario_cards = {}
        packet_count = 0
        part_line_count = 0

        for machine_data in records:
            machine = machines[machine_data['name']]
            scenario = machine_data['scenario']
            reference = scenario['reference']
            assignee = users[scenario['assigned_to']]
            start, end = self._schedule_window(
                machine,
                scenario['schedule'],
                scenario['estimated_minutes'],
                anchor,
                calendars,
            )
            stage = self._stage_status(scenario)
            card = self._owned_card(reference, 'repair_scenario', machine, parent=None)
            created = card is None
            if created:
                card = KanbanCard.objects.create(
                    reference=reference,
                    title=scenario['title'],
                    description=scenario['description'],
                    status=stage,
                    priority=scenario['priority'],
                    due_date=end.date(),
                    assignee=self._user_label(assignee),
                    tags=self._owned_tags('repair_scenario'),
                    company=machine.customer.name if machine.customer else '',
                    job_number=reference,
                    is_active=True,
                    lifecycle_status=WorkOrderLifecycle.PLANNED,
                    work_order_type=WorkOrderType.CORRECTIVE,
                    machine=machine,
                    customer=machine.customer,
                    assigned_to=assignee,
                    requested_by=requested_by,
                    scheduled_start=start,
                    scheduled_end=end,
                    estimated_minutes=scenario['estimated_minutes'],
                    card_kind=KanbanCard.KIND_WORK_ORDER,
                )

            packet = RepairPacket.objects.filter(work_order=card).first()
            if packet is not None and packet.machine_id != machine.pk:
                raise CommandError(
                    f'Repair packet for {reference!r} is linked to a different machine'
                )
            if packet is None:
                packet = RepairPacket.objects.create(
                    status=PacketStatus.DRAFT,
                    machine=machine,
                    fault_summary=scenario['fault_summary'],
                    symptom=scenario['symptom'],
                    criticality=scenario['criticality'],
                    production_impact=scenario['production_impact'],
                    generation_status=GenerationStatus.IDLE,
                    work_order=card,
                    created_by=requested_by,
                )
                packet_count += 1
            else:
                packet_count += 1

            resolve_safety_gates(packet, actor=requested_by)
            for lockout in scenario.get('lockout_points', []):
                loto_gate = packet.gates.filter(gate_type='loto').first()
                if loto_gate is None:
                    raise CommandError(
                        f'Scenario {reference!r} defines a lockout point without '
                        'an applicable LOTO gate'
                    )
                LockoutPoint.objects.get_or_create(
                    gate=loto_gate,
                    energy_source=lockout['energy_source'],
                    isolation_device=lockout['isolation_device'],
                    defaults={
                        'status': LockoutPoint.PointStatus.IDENTIFIED,
                        'note': lockout.get('note', ''),
                    },
                )

            for line in scenario['required_parts']:
                part = parts[line['ipn']]
                card_part, _ = KanbanCardPart.objects.get_or_create(
                    card=card,
                    part=part,
                    defaults={'quantity': Decimal(str(line['quantity']))},
                )
                if card_part.card_id != card.pk:
                    raise CommandError(
                        f'Required part {line["ipn"]!r} is linked to another card'
                    )
                part_line_count += 1

            scenario_cards[reference] = card

        return scenario_cards, packet_count, part_line_count

    def _load_procurement_children(
        self, records, *, anchor, calendars, scenario_cards, parts, users, requested_by
    ):
        """Create scheduled procurement children and finish-to-start dependencies."""
        children = {}
        dependency_count = 0

        for record in records:
            parent = scenario_cards[record['parent_reference']]
            machine = parent.machine
            assignee = users[record['assigned_to']]
            start, end = self._schedule_window(
                machine,
                record['schedule'],
                record['estimated_minutes'],
                anchor,
                calendars,
            )
            stage = self._stage_status(record)
            child = self._owned_card(
                record['reference'], 'procurement', machine, parent=parent
            )
            if child is None:
                child = KanbanCard.objects.create(
                    reference=record['reference'],
                    title=record['title'],
                    description=record['description'],
                    status=stage,
                    priority=record['priority'],
                    due_date=end.date(),
                    assignee=self._user_label(assignee),
                    tags=self._owned_tags('procurement'),
                    company=machine.customer.name if machine.customer else '',
                    job_number=record['reference'],
                    is_active=True,
                    lifecycle_status=WorkOrderLifecycle.PLANNED,
                    work_order_type=WorkOrderType.OTHER,
                    machine=machine,
                    customer=machine.customer,
                    assigned_to=assignee,
                    requested_by=requested_by,
                    scheduled_start=start,
                    scheduled_end=end,
                    estimated_minutes=record['estimated_minutes'],
                    parent=parent,
                    card_kind=KanbanCard.KIND_PROCUREMENT,
                )

            part_line = parent.card_parts.filter(part=parts[record['part_ipn']]).first()
            if part_line is None:
                raise CommandError(
                    f'Procurement card {record["reference"]!r} references part '
                    f'{record["part_ipn"]!r} without a parent required-part line'
                )
            if part_line.allocation_status == KanbanCardPart.ALLOCATION_NONE:
                part_line.allocation_status = KanbanCardPart.ALLOCATION_INSUFFICIENT
                part_line.allocation_note = (
                    f'Long-lead demo shortage tracked by {record["reference"]}.'
                )
                part_line.save(
                    update_fields=['allocation_status', 'allocation_note', 'updated_at']
                )

            dependency, _ = KanbanCardDependency.objects.get_or_create(
                from_card=child,
                to_card=parent,
                dependency_type=record['dependency_type'],
                defaults={'lag_minutes': record['lag_minutes']},
            )
            if dependency.lag_minutes != record['lag_minutes']:
                raise CommandError(
                    f'Dependency for {record["reference"]!r} has unexpected lag'
                )
            if child.scheduled_end > parent.scheduled_start:
                raise CommandError(
                    f'Procurement card {record["reference"]!r} finishes after its '
                    'dependent repair starts'
                )

            dependency_count += 1
            children[record['reference']] = child

        return children, dependency_count

    @staticmethod
    def _validate_schedule(cards):
        """Reject machine or assignee overlaps in the seeded schedule."""
        warnings = detect_conflicts(cards)
        if warnings:
            messages = '; '.join(warning['message'] for warning in warnings)
            raise CommandError(
                f'Water workflow schedule contains conflicts: {messages}'
            )
