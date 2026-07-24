"""
InvenTree Integration Module

Provides REST client and AI-function tools for InvenTree API:
- InvenTreeClient: Async HTTP client with circuit breaker
- Part tools: search, get, create, update
- Stock tools: query, transfer, adjust
- BOM tools: get BOM, analyze dependencies
"""

from ai.core.integrations.inventree.client import (
    BusinessRuleError,
    CircuitState,
    InvenTreeClient,
    InvenTreeError,
    TransientError,
    ValidationError,
    get_inventree_client,
    inventree_client,
)
from ai.core.integrations.inventree.tools import (
    INVENTREE_TOOLS,
    check_low_stock,
    get_bom,
    get_part_details,
    get_stock_levels,
    get_supplier_parts,
    list_categories,
    list_suppliers,
    search_parts,
)

__all__ = [
    # Tools
    "INVENTREE_TOOLS",
    "BusinessRuleError",
    "CircuitState",
    # Client
    "InvenTreeClient",
    "InvenTreeError",
    "TransientError",
    "ValidationError",
    "check_low_stock",
    "get_bom",
    "get_inventree_client",
    "get_part_details",
    "get_stock_levels",
    "get_supplier_parts",
    "inventree_client",
    "list_categories",
    "list_suppliers",
    "search_parts",
]
