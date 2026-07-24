"""
Aggregate registry for InvenTree read tools.

Combines the per-module tool collections into the single READ_TOOLS list
re-exported by the package ``__init__``.
"""

from ai.core.tools.inventree.read.additional import ADDITIONAL_READ_TOOLS
from ai.core.tools.inventree.read.documents import DOCUMENT_READ_TOOLS
from ai.core.tools.inventree.read.parts import PART_READ_TOOLS
from ai.core.tools.inventree.read.purchasing import PURCHASING_READ_TOOLS
from ai.core.tools.inventree.read.relationships import RELATIONSHIP_READ_TOOLS
from ai.core.tools.inventree.read.sales import SALES_READ_TOOLS
from ai.core.tools.inventree.read.shipments import SHIPMENT_READ_TOOLS
from ai.core.tools.inventree.read.stock import STOCK_READ_TOOLS

# Combine all read tools (34 total)
READ_TOOLS = [
    *PART_READ_TOOLS,  # 5 tools
    *STOCK_READ_TOOLS,  # 5 tools
    *PURCHASING_READ_TOOLS,  # 5 tools
    *SALES_READ_TOOLS,  # 5 tools
    *ADDITIONAL_READ_TOOLS,  # 5 tools
    *DOCUMENT_READ_TOOLS,  # 1 tool
    *SHIPMENT_READ_TOOLS,  # 5 tools
    *RELATIONSHIP_READ_TOOLS,  # 3 tools
]
