"""Read-only, complete dashboard counts and matching paginated drill-downs."""

from django.conf import settings
from django.db.models import F, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

from rest_framework.response import Response
from rest_framework.views import APIView

from InvenTree.helpers import current_date
from InvenTree.permissions import IsAuthenticatedOrReadScope


class ManagementMetricsView(APIView):
    """Expose only definitions backed by canonical fields and current grants."""

    permission_classes = [IsAuthenticatedOrReadScope]

    def get(self, request):
        """Count full authorized populations, never the displayed page alone."""
        from tasks.models import WorkOrder, WorkOrderLifecycle, WorkOrderType
        from tasks.scope import ScopeError, work_order_scope_filter

        from ai.core.integrations.retrieval_authority import fresh_actor, fresh_role
        from order.models import PurchaseOrder, PurchaseOrderLineItem

        actor = fresh_actor(request.user)
        if actor is None:
            return Response(status=403)
        try:
            offset = int(request.query_params.get('offset', 0))
            if offset < 0 or offset > 1000000:
                raise ValueError
        except ValueError:
            return Response({'detail': 'Invalid offset'}, status=400)
        selected = request.query_params.get('metric')
        today = current_date()
        metrics = []

        def metric(key, rows, missing, model):
            count = rows.count()
            result = {
                'id': key,
                'version': 1,
                'state': 'ready',
                'count': count,
                'missing_dates': missing,
                'complete': True,
            }
            if selected == key:
                records = list(rows[offset : offset + 25])
                result['records'] = [
                    {
                        'model': model,
                        'pk': row['pk'],
                        'label': row['label'],
                        'due_date': row['due_date'],
                    }
                    for row in records
                ]
                result['next_offset'] = (
                    offset + len(records) if offset + len(records) < count else None
                )
            metrics.append(result)

        if fresh_role(actor, 'purchase_order'):
            # InvenTree inventory permissions are role-wide. Use the line date,
            # falling back explicitly to the order's expected receipt date.
            outstanding = PurchaseOrderLineItem.objects.filter(
                order__status__in=PurchaseOrder.get_status_class().OPEN,
                received__lt=F('quantity'),
            ).annotate(effective_due=Coalesce('target_date', 'order__target_date'))
            late_ids = outstanding.filter(effective_due__lt=today).values('order_id')
            rows = (
                PurchaseOrder.objects
                .filter(pk__in=late_ids)
                .annotate(
                    label=F('reference'),
                    due_date=Subquery(
                        outstanding
                        .filter(order_id=OuterRef('pk'), effective_due__lt=today)
                        .order_by('effective_due')
                        .values('effective_due')[:1]
                    ),
                )
                .values('pk', 'label', 'due_date')
                .order_by('pk')
            )
            metric(
                'overdue-purchase',
                rows,
                outstanding
                .filter(effective_due__isnull=True)
                .values('order_id')
                .distinct()
                .count(),
                'purchaseorder',
            )
        if fresh_role(actor, 'work_order'):
            try:
                population = WorkOrder.objects.filter(
                    work_order_scope_filter(actor),
                    is_active=True,
                    work_order_type=WorkOrderType.PREVENTIVE,
                    lifecycle_status__in=[
                        WorkOrderLifecycle.PLANNED,
                        WorkOrderLifecycle.READY,
                        WorkOrderLifecycle.IN_PROGRESS,
                        WorkOrderLifecycle.ON_HOLD,
                        WorkOrderLifecycle.VERIFYING,
                    ],
                )
                rows = (
                    population
                    .filter(due_date__lt=today)
                    .annotate(label=Coalesce('reference', 'title'))
                    .values('pk', 'label', 'due_date')
                    .order_by('due_date', 'pk')
                )
                metric(
                    'maintenance-due',
                    rows,
                    population.filter(due_date__isnull=True).count(),
                    'workorder',
                )
            except ScopeError:
                metrics.append({
                    'id': 'maintenance-due',
                    'version': 1,
                    'state': 'unavailable',
                })
        response = Response({
            'metrics': metrics,
            'observed_at': timezone.now().isoformat(),
            'reporting_date': today.isoformat(),
            'timezone': settings.TIME_ZONE,
            'snapshot': 'live',
        })
        response['Cache-Control'] = 'private, no-store'
        return response
