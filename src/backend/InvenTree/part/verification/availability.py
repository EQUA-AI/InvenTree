"""Advisory, timestamped stock and supplier availability projection.

Availability is a separate axis from compatibility (RPF-ADR-007): nothing in
this module can change eligibility or rank, and the owning inventory or
purchasing service always rechecks quantities before any effect.
"""

from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone


def availability_snapshot(part) -> dict:
    """Build the advisory availability projection for one candidate part.

    Quantities are serialized as strings (never binary floats) and stamped
    with an observation time. The projection carries an explicit caveat that
    it grants no reservation.
    """
    from company.models import SupplierPart
    from stock.models import StockItem

    items = StockItem.objects.filter(
        part=part, customer__isnull=True, consumed_by__isnull=True
    )

    in_stock = items.aggregate(total=Sum('quantity'))['total'] or Decimal(0)

    serialized_count = items.exclude(serial=None).exclude(serial='').count()

    supplier_rows = []
    for sp in SupplierPart.objects.filter(part=part, active=True).select_related(
        'supplier'
    ):
        supplier_rows.append({
            'supplier_id': sp.supplier_id,
            'supplier': sp.supplier.name,
            'sku': sp.SKU,
            'primary': sp.primary,
            'available': str(sp.available),
            'availability_updated': (
                sp.availability_updated.isoformat() if sp.availability_updated else None
            ),
        })

    return {
        'as_of': timezone.now().isoformat(),
        'in_stock': str(in_stock),
        'serialized_count': serialized_count,
        'supplier_parts': supplier_rows,
        'caveat': 'advisory-only; availability is rechecked by the owning service',
    }
