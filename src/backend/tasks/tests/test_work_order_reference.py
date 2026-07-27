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

from tasks.models import KanbanCard
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
        card = KanbanCard.objects.create(
            title='Direct',
            status=KanbanCard.STATUS_BACKLOG,
            priority=KanbanCard.PRIORITY_LOW,
            machine=self.machine,
        )

        self.assertEqual(card.reference, f'WO-{card.pk:06d}')

    def test_the_command_service_gets_a_reference(self):
        """The board's own write path is no longer the odd one out."""
        result = scheduling.create_work_order(
            actor=self.actor,
            idempotency_key=uuid.uuid4().hex,
            title='Through the command',
            machine_id=self.machine.pk,
        )

        card = KanbanCard.objects.get(pk=result.work_order_id)
        self.assertEqual(card.reference, f'WO-{card.pk:06d}')

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
        card = KanbanCard.objects.create(
            reference='WO-DEMO-250912-001',
            title='Imported history',
            status=KanbanCard.STATUS_DONE,
            priority=KanbanCard.PRIORITY_LOW,
            machine=self.machine,
        )
        card.title = 'Renamed'
        card.save(update_fields=['title'])
        card.refresh_from_db()

        self.assertEqual(card.reference, 'WO-DEMO-250912-001')

    def test_references_are_unique_across_creation_paths(self):
        """Deriving from the pk makes collisions impossible by construction."""
        cards = [
            KanbanCard.objects.create(
                title=f'Card {index}',
                status=KanbanCard.STATUS_BACKLOG,
                priority=KanbanCard.PRIORITY_LOW,
                machine=self.machine,
            )
            for index in range(5)
        ]

        references = {card.reference for card in cards}
        self.assertEqual(len(references), 5)


class WorkOrderTableTest(TestCase):
    """Work orders live in a table Postgres names for what it holds."""

    def test_table_is_named_for_work_orders(self):
        """The model keeps its name; the table says what it stores."""
        self.assertEqual(KanbanCard._meta.db_table, 'tasks_workorder')

    def test_the_table_exists_under_that_name(self):
        """The rename actually reached the database."""
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT 1 FROM information_schema.tables WHERE table_name = %s',
                ['tasks_workorder'],
            )
            self.assertIsNotNone(cursor.fetchone())

    def test_rbac_still_maps_the_model_not_the_table(self):
        """The work_order ruleset keys off the model, so the rename is safe."""
        from users.ruleset import get_ruleset_models

        # The ruleset keys on ``<app_label>_<model_name>``, which the db_table
        # rename does not touch. That is why work-order RBAC is unaffected.
        self.assertIn('tasks_kanbancard', get_ruleset_models()['work_order'])
        self.assertEqual(KanbanCard._meta.model_name, 'kanbancard')
        self.assertNotEqual(
            KanbanCard._meta.db_table,
            f'{KanbanCard._meta.app_label}_{KanbanCard._meta.model_name}',
        )
