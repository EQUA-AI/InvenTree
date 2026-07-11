"""
InvenTree Tools Package

Provides fine-grained AI tools for InvenTree API operations.
"""

from ai.core.tools.inventree.read import READ_TOOLS
from ai.core.tools.inventree.write import WRITE_TOOLS
from ai.core.tools.inventree.operations import OPERATION_TOOLS

# Combined list of all InvenTree tools
INVENTREE_TOOLS = [
    *READ_TOOLS,
    *WRITE_TOOLS,
    *OPERATION_TOOLS,
]

__all__ = [
    "INVENTREE_TOOLS",
    "READ_TOOLS",
    "WRITE_TOOLS",
    "OPERATION_TOOLS",
]
