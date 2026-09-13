"""
Base Tool Classes and human review Decorators

Provides the foundational classes for InvenTree tools:
- BaseTool: Abstract base for all tools
- ReadTool: Base for read-only tools
- WriteTool: Base for write tools with human review support
- OperationTool: Base for complex multi-step operations
- requires_confirmation: Decorator for Human-in-the-Loop confirmation
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, TypeVar

from ai.core.maf_compat import ai_function as ai_function  # re-export for submodules

if TYPE_CHECKING:
    from collections.abc import Callable

    from ai.core.integrations.inventree.client import InvenTreeClient

logger = logging.getLogger(__name__)


def requires_confirmation(reason: str, display_fields: list[str] | None = None) -> Callable:
    """Declare preview metadata only; authority belongs to the canonical command.

    This marker never accepts an in-memory approval context or grants permission.
    The shared write fence and action adapter authorize every actual invocation.
    """

    def decorate(function: Callable) -> Callable:
        function._requires_confirmation = True
        function._confirmation_reason = reason
        function._confirmation_display_fields = display_fields
        return function

    return decorate


require_confirmation = requires_confirmation


class BaseTool(ABC):
    """
    Abstract base class for all InvenTree tools.

    Subclasses must implement:
    - name: Tool name for registration
    - description: Tool description for LLM context
    - execute(): The tool's main logic
    """

    name: str
    description: str

    def __init__(self, client: InvenTreeClient | None = None) -> None:
        """
        Initialize the tool.

        Args:
            client: Optional InvenTree client instance.
                   If not provided, will create one when needed.
        """
        self._client = client

    def get_inventree_client(self) -> InvenTreeClient:
        """Get or create the InvenTree client."""
        if self._client is None:
            # Imported lazily: importing the client at module level would pull in
            # ai.core.integrations while this module is still initialising
            # (circular import via ai.core.integrations.inventory_tools).
            from ai.core.integrations.inventree.client import InvenTreeClient

            self._client = InvenTreeClient()
        return self._client

    @abstractmethod
    async def execute(self, *args, **kwargs) -> Any:
        """Execute the tool's main logic."""
        pass

    async def __call__(self, *args, **kwargs) -> Any:
        """Make the tool callable."""
        return await self.execute(*args, **kwargs)


class ReadTool(BaseTool):
    """
    Base class for read-only tools.

    Read tools do not require human review approval and are safe to call
    without user confirmation.
    """

    requires_confirmation: bool = False


class WriteTool(BaseTool):
    """
    Base class for write tools (create/update/delete).

    Write tools may require human review approval depending on the operation.
    Use the @requires_confirmation decorator on the execute method to enable
    human approval flow.
    """

    requires_confirmation: bool = True


class OperationTool(BaseTool):
    """
    Base class for complex multi-step operation tools.

    Operation tools handle grouped actions and typically require
    human review approval for critical operations.
    """

    requires_confirmation: bool = True

    @property
    @abstractmethod
    def supported_actions(self) -> list[str]:
        """List of actions this operation tool supports."""
        pass


# Type variable for tool functions
T = TypeVar("T", bound=BaseTool)
