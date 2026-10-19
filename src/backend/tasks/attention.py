"""R6 UI10 "Needs attention": one named, versioned rule over stored Work facts.

Open, active Work qualifies when it is high priority, past its due date, on
hold, or has no owner. Each criterion is a recorded fact the reader can check
on the Work itself; nothing is scored, weighted or predicted. The order is the
native priority, then the earliest due date, then the newest record, so the
list explains itself: every row carries the reasons it qualified.

This is a selection rule for the Work list, not an authorization: the rows are
the reader's scoped Work and nothing else. It does not decide that work is
unsafe, blocked or late beyond what its own fields say; "on hold" is the
recorded lifecycle, not a readiness evaluation, and "no owner" means no
assigned user and no recorded assignee text.
"""

from django.db.models import Case, F, IntegerField, Q, QuerySet, Value, When

from .dashboard_metrics import OPEN
from .models import WorkOrder

NEEDS_ATTENTION_RULE = 'needs_attention.v1'

#: Reason codes in the order the Work list shows them.
ATTENTION_REASONS = ('high_priority', 'overdue', 'on_hold', 'no_owner')

_PRIORITY_RANK = Case(
    When(priority=WorkOrder.PRIORITY_HIGH, then=Value(3)),
    When(priority=WorkOrder.PRIORITY_MEDIUM, then=Value(2)),
    When(priority=WorkOrder.PRIORITY_LOW, then=Value(1)),
    default=Value(0),
    output_field=IntegerField(),
)


def _no_owner() -> Q:
    return Q(assigned_to__isnull=True) & Q(assignee='')


def attention_filter(today) -> Q:
    """Open Work meeting at least one criterion, as of the site's ``today``."""
    return Q(lifecycle_status__in=OPEN) & (
        Q(priority=WorkOrder.PRIORITY_HIGH)
        | Q(due_date__lt=today)
        | Q(lifecycle_status='on_hold')
        | _no_owner()
    )


def order_by_attention(queryset: QuerySet) -> QuerySet:
    """Native priority first, then the earliest due date, then newest."""
    return queryset.annotate(attention_priority_rank=_PRIORITY_RANK).order_by(
        '-attention_priority_rank',
        F('due_date').asc(nulls_last=True),
        '-created_at',
        '-pk',
    )


def attention_reasons(work: WorkOrder, today) -> list[str]:
    """Why one row qualifies, from the same facts as ``attention_filter``."""
    if work.lifecycle_status not in OPEN:
        return []
    reasons = []
    if work.priority == WorkOrder.PRIORITY_HIGH:
        reasons.append('high_priority')
    if work.due_date is not None and work.due_date < today:
        reasons.append('overdue')
    if work.lifecycle_status == 'on_hold':
        reasons.append('on_hold')
    if work.assigned_to_id is None and not work.assignee:
        reasons.append('no_owner')
    return reasons
