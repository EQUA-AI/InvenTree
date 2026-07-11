"""
Read Tools Package

Read-only tools for retrieving data from InvenTree.
These tools are safe to call without HITL approval.
"""

from ai.core.tools.inventree.read.additional import (
    ADDITIONAL_READ_TOOLS,
    get_low_stock_report,
    get_part_test_templates,
    get_return_orders,
    get_stock_test_results,
    get_stock_tracking,
)
from ai.core.tools.inventree.read.documents import (
    DOCUMENT_READ_TOOLS,
    read_pdf_text,
)
from ai.core.tools.inventree.read.relationships import (
    RELATIONSHIP_READ_TOOLS,
    get_notifications,
    get_part_related,
    get_part_variants,
)
from ai.core.tools.inventree.read.parts import (
    PART_READ_TOOLS,
    get_part,
    get_part_attachments,
    get_part_parameters,
    get_part_pricing,
    search_parts,
)
from ai.core.tools.inventree.read.purchasing import (
    PURCHASING_READ_TOOLS,
    get_categories,
    get_purchase_order_lines,
    get_purchase_orders,
    get_supplier_parts,
    get_suppliers,
    get_where_used,
)
from ai.core.tools.inventree.read.sales import (
    SALES_READ_TOOLS,
    get_build_order_lines,
    get_build_orders,
    get_customers,
    get_sales_order_lines,
    get_sales_orders,
)
from ai.core.tools.inventree.read.shipments import (
    SHIPMENT_READ_TOOLS,
    get_company,
    get_manufacturer_parts,
    get_sales_order_shipments,
    search_stock,
)
from ai.core.tools.inventree.read.stock import (
    STOCK_READ_TOOLS,
    get_bom,
    get_stock_at_location,
    get_stock_item,
    get_stock_level,
    get_stock_locations,
)

# Combine all read tools (33 total)
READ_TOOLS = [
    *PART_READ_TOOLS,         # 5 tools
    *STOCK_READ_TOOLS,        # 5 tools
    *PURCHASING_READ_TOOLS,   # 5 tools
    *SALES_READ_TOOLS,        # 5 tools
    *ADDITIONAL_READ_TOOLS,   # 5 tools
    *DOCUMENT_READ_TOOLS,     # 1 tool
    *SHIPMENT_READ_TOOLS,     # 5 tools
    *RELATIONSHIP_READ_TOOLS, # 3 tools
]

__all__ = [
    "READ_TOOLS",
    # Tool collections
    "PART_READ_TOOLS",
    "STOCK_READ_TOOLS",
    "PURCHASING_READ_TOOLS",
    "SALES_READ_TOOLS",
    "ADDITIONAL_READ_TOOLS",
    "SHIPMENT_READ_TOOLS",
    "RELATIONSHIP_READ_TOOLS",
    "DOCUMENT_READ_TOOLS",
    "read_pdf_text",
    # Part tools (5)
    "get_part",
    "search_parts",
    "get_part_parameters",
    "get_part_attachments",
    "get_part_pricing",
    # Stock tools (5)
    "get_stock_level",
    "get_stock_item",
    "get_stock_locations",
    "get_stock_at_location",
    "get_bom",
    # Purchasing tools (5)
    "get_where_used",
    "get_categories",
    "get_suppliers",
    "get_supplier_parts",
    "get_purchase_orders",
    # Sales/Manufacturing tools (5)
    "get_sales_orders",
    "get_sales_order_lines",
    "get_customers",
    "get_build_orders",
    "get_build_order_lines",
    # Additional tools (5)
    "get_return_orders",
    "get_stock_tracking",
    "get_part_test_templates",
    "get_stock_test_results",
    "get_low_stock_report",
    # Document tools
    "read_pdf_text",
    # Shipment/Order detail tools (5)
    "get_sales_order_shipments",
    "get_purchase_order_lines",
    "get_company",
    "get_manufacturer_parts",
    "search_stock",
    # Relationship & notification tools (3)
    "get_part_related",
    "get_part_variants",
    "get_notifications",
]
