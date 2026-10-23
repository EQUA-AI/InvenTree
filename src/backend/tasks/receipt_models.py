"""Durable native receipt outcomes and separately retryable kit continuation."""

from django.conf import settings
from django.db import models


class MaintenanceReceipt(models.Model):
    """One committed native receive operation, independent of allocation success."""

    operation_key = models.CharField(max_length=128, unique=True)
    request_hash = models.CharField(max_length=64)
    work_order = models.ForeignKey('tasks.WorkOrder', on_delete=models.PROTECT)
    kit_line = models.ForeignKey('tasks.JobKitLine', on_delete=models.PROTECT)
    purchase_order_line = models.ForeignKey(
        'order.PurchaseOrderLineItem', on_delete=models.PROTECT,
        related_name='maintenance_receipts',
    )
    shortage = models.ForeignKey('tasks.JobKitShortage', on_delete=models.PROTECT)
    event = models.OneToOneField('tasks.WorkOrderEvent', on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    requirement_snapshot = models.JSONField()
    received_before = models.DecimalField(max_digits=15, decimal_places=5)
    received_after = models.DecimalField(max_digits=15, decimal_places=5)
    response_snapshot = models.JSONField()
    recorded_at = models.DateTimeField(auto_now_add=True)
    reservation_state = models.CharField(max_length=16, default='pending')
    reservation_code = models.CharField(max_length=64, blank=True)
    allocation_ids = models.JSONField(default=list)
    continued_at = models.DateTimeField(null=True)


class MaintenanceReceiptStock(models.Model):
    """Protected exact line-to-stock association with immutable receipt facts."""

    receipt = models.ForeignKey(
        MaintenanceReceipt, on_delete=models.PROTECT, related_name='stocks'
    )
    stock_item = models.OneToOneField('stock.StockItem', on_delete=models.PROTECT)
    snapshot = models.JSONField()
