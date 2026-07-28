"""Give every existing work order the card that represents it.

Until now a work order *was* its board card, so every job already has exactly
one board position, title and schedule. This lifts that into a real card so the
board has something to render once it stops reading work orders directly.

One card per work order, of kind ``work_order``: the piece that tracks the job
itself. Breaking a job into several cards is a later, deliberate act - this
migration invents no work that was not already there.

Child work orders (today's subtasks and procurement tasks) get their own card
here too, because they are still work orders at this point. Folding them into
their parent is the final stage of the split, not this one.

Idempotent: a work order that already has a card is skipped, so a re-run after
a partial apply does not double up.
"""

from django.db import migrations

#: Written in batches rather than one statement per row; a demo or production
#: database can hold thousands of work orders and this runs inside the deploy.
BATCH = 500


def create_primary_cards(apps, schema_editor):
    """Create the tracking card for every work order that lacks one."""
    WorkOrder = apps.get_model('tasks', 'WorkOrder')
    KanbanCard = apps.get_model('tasks', 'KanbanCard')

    existing = set(KanbanCard.objects.values_list('work_order_id', flat=True))

    # Explicit pk order, not the model's newest-first default: cards are ranked
    # within a column by creation time, and inheriting a reversed order would
    # silently flip every board.
    pending = []
    for work_order in WorkOrder.objects.order_by('pk').iterator(chunk_size=BATCH):
        if work_order.pk in existing:
            continue
        pending.append(
            KanbanCard(
                work_order_id=work_order.pk,
                # A child's kind describes what that piece of work is; a
                # standalone job's card tracks the job itself.
                card_kind=getattr(work_order, 'card_kind', None) or 'work_order',
                status=work_order.status,
                board_order=0,
                title=work_order.title,
                description=work_order.description,
                assigned_to_id=work_order.assigned_to_id,
                assignee=work_order.assignee,
                scheduled_start=work_order.scheduled_start,
                scheduled_end=work_order.scheduled_end,
                estimated_minutes=work_order.estimated_minutes,
                is_active=work_order.is_active,
                # created_at/updated_at are auto_now_add/auto_now, so they are
                # stamped now regardless of what is passed. Cards are ordered
                # by insertion, which pk order above makes match the jobs'.
            )
        )
        if len(pending) >= BATCH:
            KanbanCard.objects.bulk_create(pending)
            pending = []

    if pending:
        KanbanCard.objects.bulk_create(pending)


def drop_primary_cards(apps, schema_editor):
    """Remove the generated cards, leaving work orders as they were."""
    KanbanCard = apps.get_model('tasks', 'KanbanCard')
    KanbanCard.objects.all().delete()


class Migration(migrations.Migration):
    """Backfill one card per work order."""

    dependencies = [('tasks', '0024_kanbancard')]

    operations = [migrations.RunPython(create_primary_cards, drop_primary_cards)]
