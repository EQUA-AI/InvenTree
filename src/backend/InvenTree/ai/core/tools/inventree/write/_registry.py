"""
Aggregate registry for InvenTree write tools.

Combines the per-module tool collections into the single WRITE_TOOLS list
re-exported by the package ``__init__``.
"""

from ai.core.tools.inventree.write.addresses import ADDRESS_WRITE_TOOLS
from ai.core.tools.inventree.write.attachments import ATTACHMENT_WRITE_TOOLS
from ai.core.tools.inventree.write.bom import BOM_WRITE_TOOLS
from ai.core.tools.inventree.write.build_orders import BUILD_ORDER_WRITE_TOOLS
from ai.core.tools.inventree.write.build_orders_advanced import (
    BUILD_ORDER_ADVANCED_WRITE_TOOLS,
)
from ai.core.tools.inventree.write.categories import CATEGORY_WRITE_TOOLS
from ai.core.tools.inventree.write.companies import COMPANY_WRITE_TOOLS
from ai.core.tools.inventree.write.notifications import NOTIFICATION_WRITE_TOOLS
from ai.core.tools.inventree.write.parameters import PARAMETER_WRITE_TOOLS
from ai.core.tools.inventree.write.parts import PART_WRITE_TOOLS
from ai.core.tools.inventree.write.project_codes import PROJECT_CODE_WRITE_TOOLS
from ai.core.tools.inventree.write.purchase_orders import PURCHASE_ORDER_WRITE_TOOLS
from ai.core.tools.inventree.write.return_orders import RETURN_ORDER_WRITE_TOOLS
from ai.core.tools.inventree.write.sales_orders import SALES_ORDER_WRITE_TOOLS
from ai.core.tools.inventree.write.sales_orders_advanced import (
    SALES_ORDER_ADVANCED_WRITE_TOOLS,
)
from ai.core.tools.inventree.write.settings import SETTINGS_WRITE_TOOLS
from ai.core.tools.inventree.write.stock import STOCK_WRITE_TOOLS
from ai.core.tools.inventree.write.stock_advanced import STOCK_ADVANCED_WRITE_TOOLS
from ai.core.tools.inventree.write.stock_operations import (
    STOCK_OPERATIONS_WRITE_TOOLS,
)
from ai.core.tools.inventree.write.supplier_parts import SUPPLIER_PART_WRITE_TOOLS
from ai.core.tools.inventree.write.test_templates import TEST_TEMPLATE_WRITE_TOOLS

# Combine all write tools
WRITE_TOOLS = [
    *PART_WRITE_TOOLS,
    *STOCK_WRITE_TOOLS,
    *STOCK_ADVANCED_WRITE_TOOLS,
    *STOCK_OPERATIONS_WRITE_TOOLS,
    *BOM_WRITE_TOOLS,
    *PURCHASE_ORDER_WRITE_TOOLS,
    *SALES_ORDER_WRITE_TOOLS,
    *SALES_ORDER_ADVANCED_WRITE_TOOLS,
    *BUILD_ORDER_WRITE_TOOLS,
    *BUILD_ORDER_ADVANCED_WRITE_TOOLS,
    *RETURN_ORDER_WRITE_TOOLS,
    *COMPANY_WRITE_TOOLS,
    *CATEGORY_WRITE_TOOLS,
    *ATTACHMENT_WRITE_TOOLS,
    *PARAMETER_WRITE_TOOLS,
    *TEST_TEMPLATE_WRITE_TOOLS,
    *NOTIFICATION_WRITE_TOOLS,
    *ADDRESS_WRITE_TOOLS,
    *PROJECT_CODE_WRITE_TOOLS,
    *SETTINGS_WRITE_TOOLS,
    *SUPPLIER_PART_WRITE_TOOLS,
]
