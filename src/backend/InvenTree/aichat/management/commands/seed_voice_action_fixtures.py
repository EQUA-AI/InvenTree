"""Seed the designated VOICE-TEST records for voice action tests (plan P0-10 / OD-6).

Creates, idempotently, the records the voice worksheets and decision tests
name explicitly, so no action test ever touches a real record:

- customer company ``VOICE-TEST Customer`` and its work orders, one per
  lifecycle state the first vertical slice needs (in progress → hold,
  on hold → resume, planned → assign), one with an applied procedure
  (``VT-PM`` revision 1, three steps) and one closeout-eligible in-progress
  order assigned to the actor;
- stock location ``VOICE-TEST`` with two parts and two stock items;
- tier-1 and tier-2 pending approvals for the Phase C representative action
  types (purchase_order, email, stock_update) with sandbox recipients.

Safety: refuses to run against the estate unless the database is
``inventree_dev`` (never experimental/production), and always requires
``--confirm-db <name>`` matching the configured database. ``--reset``
deletes the seeded work orders (cascade), approvals and stock items first so
the reset contract restores versions and lifecycle states.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

TAG = 'VOICE-TEST'
ESTATE_DB_ALLOWED = 'inventree_dev'
ESTATE_HOST_MARKER = 'postgres.database.azure.com'

WORK_ORDERS = (
    # (title suffix, lifecycle_status, kanban status)
    ('hold candidate', 'in_progress', 'in-progress'),
    ('resume candidate', 'on_hold', 'in-progress'),
    ('assign candidate', 'planned', 'backlog'),
    ('procedure candidate', 'in_progress', 'in-progress'),
    ('closeout candidate', 'in_progress', 'in-progress'),
)

PARTS = (
    ('VOICE-TEST Bearing 6205', 'VT-6205', 'pcs', 'Deep-groove ball bearing 6205'),
    ('VOICE-TEST Drive belt B42', 'VT-B42', 'pcs', 'V-belt B42'),
)

STOCK = (('VT-6205', 10), ('VT-B42', 25))

APPROVALS = (
    # (action_type, risk_tier, summary suffix, payload)
    (
        'purchase_order',
        1,
        'PO for two VT-6205 bearings',
        {
            'intent_summary': 'Create PO to restock VT-6205',
            'entity_refs': {'supplier_id': 0, 'part_ipn': 'VT-6205'},
            'proposed_changes': {'type': 'purchase_order_create'},
            'line_items': [{'part_ipn': 'VT-6205', 'quantity': 2, 'unit_price': 12.5}],
            'currency': 'USD',
        },
    ),
    (
        'purchase_order',
        2,
        'PO for forty VT-B42 belts',
        {
            'intent_summary': 'Create PO to restock VT-B42',
            'entity_refs': {'supplier_id': 0, 'part_ipn': 'VT-B42'},
            'proposed_changes': {'type': 'purchase_order_create'},
            'line_items': [{'part_ipn': 'VT-B42', 'quantity': 40, 'unit_price': 6.0}],
            'currency': 'USD',
        },
    ),
    (
        'email',
        1,
        'email the sandbox mailbox a status note',
        {
            'intent_summary': 'Status note to the sandbox mailbox',
            'entity_refs': {},
            'proposed_changes': {'type': 'email_send'},
            'to': ['voice-test@sandbox.test'],
            'subject': 'VOICE-TEST status note',
            'body': 'Sandbox-only message body.',
        },
    ),
    (
        'email',
        2,
        'email the sandbox supplier a quote request',
        {
            'intent_summary': 'Quote request to the sandbox supplier',
            'entity_refs': {},
            'proposed_changes': {'type': 'email_send'},
            'to': ['supplier@sandbox.test'],
            'cc': ['voice-test@sandbox.test'],
            'subject': 'VOICE-TEST quote request',
            'body': 'Sandbox-only quote request body.',
        },
    ),
    (
        'stock_update',
        1,
        'count VT-6205 at VOICE-TEST',
        {
            'intent_summary': 'Stock count',
            'entity_refs': {'part_ipn': 'VT-6205', 'location': TAG},
            'proposed_changes': {'type': 'stock_count', 'quantity': 10},
        },
    ),
    (
        'stock_update',
        2,
        'remove five VT-B42 from VOICE-TEST',
        {
            'intent_summary': 'Stock removal',
            'entity_refs': {'part_ipn': 'VT-B42', 'location': TAG},
            'proposed_changes': {'type': 'stock_remove', 'quantity': 5},
        },
    ),
)


class Command(BaseCommand):
    """Seed (or reset) the VOICE-TEST designated records."""

    help = 'Seed the VOICE-TEST designated records for voice action tests (dev only).'

    def add_arguments(self, parser):
        """Register arguments."""
        parser.add_argument(
            '--actor',
            required=True,
            help='Username used to apply the procedure and as the closeout assignee '
            '(needs work-order execution rights; a superuser works).',
        )
        parser.add_argument(
            '--confirm-db',
            required=True,
            help='Must equal the configured database name; an explicit acknowledgement '
            'of which database is being seeded.',
        )
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Delete previously seeded VOICE-TEST work orders, approvals and stock '
            'items before seeding (restores versions and lifecycle states).',
        )

    def handle(self, *args, **options):
        """Run the seeder."""
        self._guard_database(options['confirm_db'])
        actor = self._actor(options['actor'])

        if options['reset']:
            self._reset()

        with transaction.atomic():
            customer = self._customer()
            work_orders = self._work_orders(customer, actor)
            self._apply_procedure(customer, actor, work_orders['procedure candidate'])
            stock = self._stock()
            approvals = self._approvals()

        self.stdout.write(self.style.SUCCESS(f'{TAG} fixtures ready'))
        for name, work_order in work_orders.items():
            self.stdout.write(
                f'  work order {work_order.reference} ({work_order.pk}) '
                f'{name}: {work_order.lifecycle_status} v{work_order.lifecycle_version}'
            )
        for item in stock:
            self.stdout.write(
                f'  stock item {item.pk}: {item.part.IPN} x {item.quantity} @ {TAG}'
            )
        for approval in approvals:
            self.stdout.write(
                f'  approval {approval.pk} tier {approval.risk_tier} '
                f'{approval.action_type}: {approval.summary}'
            )

    # -- guards --------------------------------------------------------------

    def _guard_database(self, confirm_db: str) -> None:
        db = settings.DATABASES['default']
        name = str(db.get('NAME', ''))
        host = str(db.get('HOST', '') or '')
        if confirm_db != name:
            raise CommandError(
                f'--confirm-db {confirm_db!r} does not match the configured database '
                f'{name!r}; refusing to seed.'
            )
        if ESTATE_HOST_MARKER in host and name != ESTATE_DB_ALLOWED:
            raise CommandError(
                f'Refusing to seed {name!r} on {host!r}: designated records may only '
                f'be seeded into {ESTATE_DB_ALLOWED!r} on the estate (plan OD-6).'
            )

    @staticmethod
    def _actor(username: str):
        user_model = get_user_model()
        try:
            return user_model.objects.get(username=username)
        except user_model.DoesNotExist as exc:
            raise CommandError(f'No user named {username!r}') from exc

    # -- reset ---------------------------------------------------------------

    def _reset(self) -> None:
        from tasks.models import WorkOrder

        from approvals.models import Approval
        from stock.models import StockItem, StockLocation

        deleted_wo, _ = WorkOrder.objects.filter(title__startswith=TAG).delete()
        deleted_ap, _ = Approval.objects.filter(summary__startswith=f'{TAG}:').delete()
        location = StockLocation.objects.filter(name=TAG).first()
        deleted_si = 0
        if location is not None:
            deleted_si, _ = StockItem.objects.filter(location=location).delete()
        self.stdout.write(
            f'reset: removed {deleted_wo} work-order rows, {deleted_ap} approval rows, '
            f'{deleted_si} stock rows'
        )

    # -- builders ------------------------------------------------------------

    @staticmethod
    def _customer():
        from company.models import Company

        customer, _ = Company.objects.get_or_create(
            name=f'{TAG} Customer', defaults={'is_customer': True}
        )
        return customer

    @staticmethod
    def _work_orders(customer, actor) -> dict:
        from tasks.models import WorkOrder, WorkOrderType

        created = {}
        for suffix, lifecycle, status in WORK_ORDERS:
            title = f'{TAG} {suffix}'
            work_order = WorkOrder.objects.filter(
                title=title, customer=customer
            ).first()
            if work_order is None:
                work_order = WorkOrder.objects.create(
                    title=title,
                    status=status,
                    priority=WorkOrder.PRIORITY_MEDIUM,
                    customer=customer,
                    work_order_type=WorkOrderType.PREVENTIVE,
                    lifecycle_status=lifecycle,
                    assigned_to=actor if suffix == 'closeout candidate' else None,
                )
            created[suffix] = work_order
        return created

    @staticmethod
    def _apply_procedure(customer, actor, work_order) -> None:
        from tasks.models import (
            Procedure,
            ProcedureRevision,
            ProcedureRevisionStatus,
            ProcedureStep,
            ProcedureStepType,
            WorkOrderType,
        )
        from tasks.procedure_models import WorkOrderStepExecution
        from tasks.scope import MaintenanceScope
        from tasks.services.procedure_execution import apply_procedure_revision

        if WorkOrderStepExecution.objects.filter(
            application__work_order=work_order
        ).exists():
            return
        procedure, _ = Procedure.objects.get_or_create(
            code='VT-PM',
            defaults={
                'name': f'{TAG} preventive procedure',
                'customer': customer,
                'created_by': actor,
            },
        )
        revision = ProcedureRevision.objects.filter(
            procedure=procedure, revision=1
        ).first()
        if revision is None:
            revision = ProcedureRevision.objects.create(
                procedure=procedure,
                revision=1,
                status=ProcedureRevisionStatus.PUBLISHED,
                work_order_type=WorkOrderType.PREVENTIVE,
                created_by=actor,
                published_by=actor,
                published_at=timezone.now(),
            )
            for index, (title, instruction) in enumerate(
                (
                    (
                        'Isolate power',
                        'Lock out the main breaker and verify zero energy.',
                    ),
                    (
                        'Inspect belt',
                        'Check the drive belt for wear and correct tension.',
                    ),
                    ('Restore power', 'Remove the lock and restore power.'),
                ),
                start=1,
            ):
                ProcedureStep.objects.create(
                    revision=revision,
                    sequence=index,
                    step_type=ProcedureStepType.INSTRUCTION,
                    title=title,
                    instruction=instruction,
                    required=False,
                )
            procedure.current_revision = revision
            procedure.save(update_fields=['current_revision'])

        # The service checks scope on the in-memory actor exactly like the
        # fork tests do; nothing durable is granted here.
        actor.maintenance_scopes = {
            MaintenanceScope(customer_id=customer.pk, site_key=None)
        }
        apply_procedure_revision(
            work_order_id=work_order.pk,
            revision_id=revision.pk,
            actor=actor,
            expected_version=work_order.lifecycle_version,
            idempotency_key=f'voice-test-apply:{work_order.pk}:{uuid.uuid4().hex[:8]}',
        )
        work_order.refresh_from_db()

    @staticmethod
    def _stock() -> list:
        from part.models import Part
        from stock.models import StockItem, StockLocation

        location, _ = StockLocation.objects.get_or_create(name=TAG)
        parts = {}
        for name, ipn, units, description in PARTS:
            # IPN alone is not unique in InvenTree (name, IPN, revision is), so
            # look up first and create only when nothing carries the marker IPN.
            part = Part.objects.filter(IPN=ipn).first()
            if part is None:
                part = Part.objects.create(
                    name=name, IPN=ipn, units=units, description=description
                )
            parts[ipn] = part
        items = []
        for ipn, quantity in STOCK:
            item = StockItem.objects.filter(part=parts[ipn], location=location).first()
            if item is None:
                item = StockItem.objects.create(
                    part=parts[ipn], location=location, quantity=quantity
                )
            items.append(item)
        return items

    @staticmethod
    def _approvals() -> list:
        from approvals.models import (
            Approval,
            ApprovalEvent,
            ApprovalRevision,
            EventType,
            compute_idempotency_key,
        )

        created = []
        for action_type, tier, suffix, payload in APPROVALS:
            summary = f'{TAG}: {suffix}'
            approval = Approval.objects.filter(summary=summary).first()
            if approval is None:
                run_id = f'voice-test-{action_type}-t{tier}'
                tool_call_id = f'tc-{uuid.uuid4().hex[:12]}'
                approval = Approval.objects.create(
                    tool_call_id=tool_call_id,
                    agent_run_id=run_id,
                    agent_checkpoint_id=f'cp-{uuid.uuid4().hex[:12]}',
                    action_type=action_type,
                    summary=summary,
                    payload=payload,
                    risk_tier=tier,
                    expires_at=timezone.now() + timedelta(days=30),
                    baseline_context={'seeded_by': TAG},
                    preconditions={},
                    card_context={'version': 1, 'seeded_by': TAG},
                    idempotency_key=compute_idempotency_key(run_id, tool_call_id),
                    current_revision_number=0,
                )
                ApprovalRevision.objects.create(
                    approval=approval,
                    revision_number=0,
                    payload_snapshot=payload,
                    diff_summary=None,
                    created_by_user=None,
                )
                ApprovalEvent.objects.create(
                    approval=approval,
                    event_type=EventType.CREATED,
                    actor_user=None,
                    event_payload={
                        'action_type': action_type,
                        'risk_tier': tier,
                        'agent_run_id': run_id,
                        'tool_call_id': tool_call_id,
                        'seeded_by': TAG,
                    },
                )
            created.append(approval)
        return created
