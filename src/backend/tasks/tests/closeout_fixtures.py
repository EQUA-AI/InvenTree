"""Shared fixtures and fake extractors for the closeout test suites."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission

from assets.models import AssetMachine
from company.models import Company
from part.models import Part
from stock.models import StockItem
from tasks.models import (
    FulfillmentMode,
    JobKitLine,
    KanbanCard,
    ProcedureResourceKind,
    WorkOrderLifecycle,
    WorkOrderType,
)
from tasks.scope import MaintenanceScope

CLOSEOUT_FLAGS = {
    'AIMMS_WORK_ORDERS_ENABLED': True,
    'AIMMS_CLOSEOUT_WIZARD_ENABLED': True,
}

VALID_CLOSEOUT = {
    'action': 'Replaced filter',
    'result': 'Restored flow',
    'verification_summary': 'Flow verified at 20 GPM',
    'cause': 'Clogged filter',
}


class CloseoutEnvMixin:
    """Builds one scoped customer/actor/machine/work-order environment."""

    def build_env(
        self,
        *,
        username='closeout-user',
        lifecycle=WorkOrderLifecycle.VERIFYING,
        superuser=True,
    ):
        """Create the standard scoped closeout test environment."""
        self.customer = Company.objects.create(
            name=f'Closeout {username}', is_customer=True
        )
        factory = (
            get_user_model().objects.create_superuser
            if superuser
            else get_user_model().objects.create_user
        )
        self.actor = factory(
            username=username, email=f'{username}@example.com', password='pw'
        )
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        self.machine = AssetMachine.objects.create(
            name=f'Machine {username}', customer=self.customer
        )
        self.work_order = KanbanCard.objects.create(
            title='Closeout work',
            status=KanbanCard.STATUS_REVIEW,
            priority=KanbanCard.PRIORITY_MEDIUM,
            customer=self.customer,
            machine=self.machine,
            assigned_to=self.actor,
            work_order_type=WorkOrderType.PREVENTIVE,
            lifecycle_status=lifecycle,
        )

    def make_scoped_user(self, username, *, permissions=()):
        """A non-superuser inside the environment scope with named perms."""
        user = get_user_model().objects.create_user(
            username=username, email=f'{username}@example.com', password='pw'
        )
        for codename in permissions:
            user.user_permissions.add(
                Permission.objects.get(
                    codename=codename, content_type__app_label='tasks'
                )
            )
        user = get_user_model().objects.get(pk=user.pk)
        user.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        return user

    def build_kit_line(self, *, kind=ProcedureResourceKind.PART, quantity='3'):
        """Create a kit, one line, and backing stock; returns the line."""
        from tasks.models import JobKit

        kit = JobKit.objects.filter(work_order=self.work_order).first()
        if kit is None:
            kit = JobKit.objects.create(
                work_order=self.work_order, created_by=self.actor
            )
        sequence = kit.lines.count() + 1
        part = Part.objects.create(
            name=f'KitPart {self.work_order.pk}-{sequence}',
            description='closeout kit part',
            component=True,
        )
        StockItem.objects.create(part=part, quantity=Decimal('50'))
        return JobKitLine.objects.create(
            kit=kit,
            sequence=sequence,
            kind=kind,
            requested_part=part,
            selected_part=part,
            required_quantity=Decimal(quantity),
            fulfillment_mode=FulfillmentMode.RESERVE_CONSUME,
            source='manual',
        )

    def reserve_kit(self):
        """Reserve the built kit with the real reservation service."""
        from tasks.services.job_kits import reserve_job_kit

        reserve_job_kit(
            work_order_id=self.work_order.pk,
            actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key=f'reserve-{self.work_order.pk}',
        )


def _span(narrative, length=6):
    return [0, max(1, min(length, len(narrative)))]


def extractor_ok(narrative, shape):
    """A well-formed schema-v1 document anchored to the narrative."""
    span = _span(narrative)
    return {
        'schema_version': 1,
        'fields': {
            'cause': {
                'value': 'Clogged filter',
                'spans': [span],
                'confidence': 0.9,
                'warnings': [],
            },
            'action': {
                'value': 'Replaced filter',
                'spans': [span],
                'confidence': 0.92,
                'warnings': [],
            },
            'result': {
                'value': 'Restored flow',
                'spans': [span],
                'confidence': 0.88,
                'warnings': [],
            },
            'verification_summary': {
                'value': '',
                'spans': [],
                'confidence': 0.0,
                'warnings': ['not_stated'],
            },
        },
        'part_candidates': [
            {'text': 'the 30A contactor', 'spans': [span], 'quantity_text': 'one'}
        ],
        'reading_candidates': [
            {
                'text': 'fifteen-fifty on the output',
                'spans': [span],
                'value_text': '',
                'unit_text': '',
                'warnings': ['numeric_ambiguity'],
            }
        ],
        'warnings': [],
    }


def extractor_echo(narrative, shape):
    """Echoes (possibly hostile) narrative text as inert field values."""
    span = [0, len(narrative)]
    return {
        'schema_version': 1,
        'fields': {
            'action': {
                'value': narrative[:200],
                'spans': [span],
                'confidence': 0.5,
                'warnings': [],
            }
        },
        'part_candidates': [],
        'reading_candidates': [],
        'warnings': [],
    }


def extractor_identity_leak(narrative, shape):
    """Violates FR-CO-003 by resolving a part candidate to an id."""
    span = _span(narrative)
    return {
        'schema_version': 1,
        'fields': {},
        'part_candidates': [
            {'text': 'contactor', 'spans': [span], 'part_id': 42}
        ],
        'reading_candidates': [],
        'warnings': [],
    }


def extractor_unknown_schema(narrative, shape):
    """Returns a schema version this deployment does not understand."""
    return {'schema_version': 99, 'fields': {}}


def extractor_unanchored(narrative, shape):
    """Returns a populated value with no source span."""
    return {
        'schema_version': 1,
        'fields': {
            'action': {'value': 'did things', 'spans': [], 'confidence': 0.9}
        },
    }


def extractor_extra_keys(narrative, shape):
    """Smuggles a non-schema top-level key."""
    return {
        'schema_version': 1,
        'fields': {},
        'tool_calls': [{'name': 'consume_all_parts'}],
    }


def extractor_boom(narrative, shape):
    """Simulates a provider outage."""
    raise RuntimeError('provider down')
