"""
AIMMS InvenTree Tools Package

This package provides AI-callable tools for interacting with the InvenTree API.
Tools are organized into:
- read/ - Read-only data retrieval tools
- write/ - Create/update/delete tools with HITL support
- operations/ - Complex multi-step operations
"""

from ai.core.tools.inventree import INVENTREE_TOOLS

__all__ = [
    "INVENTREE_TOOLS",
]
