"""Fold child work orders into cards of the job they belong to.

Subtasks and procurement tasks were modelled as work orders with a ``parent``,
so sourcing a seal kit for a pump rebuild minted a second ``WO-`` number for
what is one maintenance job. That is the thing the split exists to stop: one
work order per job, and as many cards as the work needs.

Each child already has a card of its own from 0025. Converting it is therefore
mostly re-parenting that card - it keeps its kind, its column, its title and its
schedule, and simply belongs to the parent job now.

What travels with it:

* **Part requirements** move to the parent, because parts are required by the
  job. A line for a part the parent already requires is dropped rather than
  added: a procurement child's lines were copies of the parent's shortfall, and
  summing them would double the quantity.
* **Dependency edges** touching a child are removed. They exist to order two
  jobs, and once both ends are the same job the edge is a self-loop with nothing
  to say. Ordering *within* a job is what the cards' own schedules express.
* **Everything else** - the child's events, commands and closeout - goes with
  the row. That is a deliberate loss of audit history, accepted because these
  records are demo data with no real-world counterpart.

Irreversible by construction: the deleted rows cannot be reconstructed, so the
reverse is a documented refusal rather than a lie.
"""

from django.db import migrations
from django.db.models import Q


def convert_children(apps, schema_editor):
    """Re-parent each child's card, then delete the child work order."""
    WorkOrder = apps.get_model('tasks', 'WorkOrder')
    KanbanCard = apps.get_model('tasks', 'KanbanCard')
    WorkOrderPart = apps.get_model('tasks', 'WorkOrderPart')
    WorkOrderDependency = apps.get_model('tasks', 'WorkOrderDependency')

    children = list(WorkOrder.objects.filter(parent_id__isnull=False).order_by('pk'))

    for child in children:
        parent_id = child.parent_id

        # The card keeps everything that describes the piece of work; only the
        # job it hangs off changes.
        KanbanCard.objects.filter(work_order_id=child.pk).update(
            work_order_id=parent_id
        )

        parent_parts = set(
            WorkOrderPart.objects.filter(work_order_id=parent_id).values_list(
                'part_id', flat=True
            )
        )
        for line in WorkOrderPart.objects.filter(work_order_id=child.pk):
            if line.part_id in parent_parts:
                line.delete()
            else:
                WorkOrderPart.objects.filter(pk=line.pk).update(
                    work_order_id=parent_id
                )
                parent_parts.add(line.part_id)

        WorkOrderDependency.objects.filter(
            Q(predecessor_id=child.pk) | Q(successor_id=child.pk)
        ).delete()

    # Cascades the children's events, commands and closeouts with them.
    WorkOrder.objects.filter(pk__in=[child.pk for child in children]).delete()


def refuse_reverse(apps, schema_editor):
    """Reversing would have to invent the work orders this deleted."""
    raise RuntimeError(
        'Children were folded into cards and their work orders deleted; '
        'restore from a backup rather than reversing this migration.'
    )


class Migration(migrations.Migration):
    """One work order per maintenance job, at last."""

    dependencies = [('tasks', '0025_backfill_primary_cards')]

    operations = [migrations.RunPython(convert_children, refuse_reverse)]
