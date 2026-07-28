"""Every path that creates a work order produces the same reference.

Reference generation used to live in whichever caller remembered it, so a card
raised from the board or from packet generation came out with no reference while
one raised through the canonical API got one. These pin that it is now the
model's job, and therefore consistent.
"""

import uuid

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase

from tasks.models import WorkOrder
from tasks.services import scheduling

from assets.models import AssetMachine
from repair.work_packages import create_repair_work_package


class WorkOrderReferenceTest(TestCase):
    """A reference is assigned however the work order was created."""

    def setUp(self):
        """Create an actor and a machine."""
        suffix = uuid.uuid4().hex[:6]
        self.actor = get_user_model().objects.create_superuser(
            username=f'ref-{suffix}', email=f'{suffix}@example.com', password='pw'
        )
        self.machine = AssetMachine.objects.create(name=f'Machine {suffix}')

    def test_direct_creation_gets_a_reference(self):
        """Even a bare ORM create is identified."""
        work_order = WorkOrder.objects.create(
            title='Direct',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_LOW,
            machine=self.machine,
        )

        self.assertEqual(work_order.reference, f'WO-{work_order.pk:06d}')

    def test_the_command_service_gets_a_reference(self):
        """The board's own write path is no longer the odd one out."""
        result = scheduling.create_work_order(
            actor=self.actor,
            idempotency_key=uuid.uuid4().hex,
            title='Through the command',
            machine_id=self.machine.pk,
        )

        work_order = WorkOrder.objects.get(pk=result.work_order_id)
        self.assertEqual(work_order.reference, f'WO-{work_order.pk:06d}')

    def test_the_work_package_command_gets_a_reference(self):
        """Maintenance intake reports a reference it did not have to invent."""
        result = create_repair_work_package(
            actor=self.actor,
            draft={'machine_id': self.machine.pk, 'title': 'Work package'},
            idempotency_key=uuid.uuid4().hex,
        )

        self.assertEqual(
            result.work_order_reference, f'WO-{result.work_order_id:06d}'
        )

    def test_an_explicit_reference_is_never_overwritten(self):
        """Imported and demo records keep the identifier they were given."""
        work_order = WorkOrder.objects.create(
            reference='WO-DEMO-250912-001',
            title='Imported history',
            status=WorkOrder.STATUS_DONE,
            priority=WorkOrder.PRIORITY_LOW,
            machine=self.machine,
        )
        work_order.title = 'Renamed'
        work_order.save(update_fields=['title'])
        work_order.refresh_from_db()

        self.assertEqual(work_order.reference, 'WO-DEMO-250912-001')

    def test_references_are_unique_across_creation_paths(self):
        """Deriving from the pk makes collisions impossible by construction."""
        work_orders = [
            WorkOrder.objects.create(
                title=f'Card {index}',
                status=WorkOrder.STATUS_BACKLOG,
                priority=WorkOrder.PRIORITY_LOW,
                machine=self.machine,
            )
            for index in range(5)
        ]

        references = {work_order.reference for work_order in work_orders}
        self.assertEqual(len(references), 5)


class WorkOrderTableTest(TestCase):
    """Work orders live in a table Postgres names for what it holds."""

    def test_table_is_named_for_work_orders(self):
        """Model and table agree, now that the model is named for the job."""
        self.assertEqual(WorkOrder._meta.db_table, 'tasks_workorder')
        self.assertEqual(WorkOrder._meta.model_name, 'workorder')

    def test_the_table_exists_under_that_name(self):
        """The rename actually reached the database.

        Asked through Django's introspection rather than ``information_schema``,
        which only PostgreSQL has - the original spelling made this test an
        error on SQLite instead of an answer.
        """
        with connection.cursor() as cursor:
            tables = connection.introspection.table_names(cursor)

        self.assertIn('tasks_workorder', tables)
        self.assertNotIn('tasks_kanbancardpart', tables)

    def test_rbac_follows_the_renamed_model(self):
        """The ruleset keys on ``<app_label>_<model_name>``, so it moved too.

        Renaming a model renames the permissions Django derives from it. A
        ruleset still naming the old model would silently govern nothing.
        """
        from users.ruleset import get_ruleset_models

        work_order_models = get_ruleset_models()['work_order']
        table_name = (
            f'{WorkOrder._meta.app_label}_{WorkOrder._meta.model_name}'
        )

        self.assertEqual(table_name, 'tasks_workorder')
        self.assertIn(table_name, work_order_models)
