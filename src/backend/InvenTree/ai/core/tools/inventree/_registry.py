"""
Aggregate registry for all InvenTree AI tools.

Combines the read, write and operation tool collections into the single
INVENTREE_TOOLS list re-exported by the package ``__init__``.
"""

from ai.core.tools.inventree.operations import OPERATION_TOOLS
from ai.core.tools.inventree.read import READ_TOOLS
from ai.core.tools.inventree.write import WRITE_TOOLS

# Combined list of all InvenTree tools
INVENTREE_TOOLS = [
    *READ_TOOLS,
    *WRITE_TOOLS,
    *OPERATION_TOOLS,
]
