"""
Base Tool Classes and HITL Decorators

Provides the foundational classes for InvenTree tools:
- BaseTool: Abstract base for all tools
- ReadTool: Base for read-only tools
- WriteTool: Base for write tools with HITL support
- OperationTool: Base for complex multi-step operations
- requires_hitl: Decorator for Human-in-the-Loop confirmation
"""

from __future__ import annotations

import functools
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, TypeVar

from pydantic import BaseModel

from ai.core.maf_compat import ai_function  # re-export for submodules
from ai.core.integrations.inventree.client import InvenTreeClient

logger = logging.getLogger(__name__)


class HITLStatus(str, Enum):
    """Status of a HITL request."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMEOUT = "timeout"


@dataclass
class HITLContext:
    """Context for Human-in-the-Loop decisions."""
    
    approved: bool = False
    status: HITLStatus = HITLStatus.PENDING
    user_id: str | None = None
    reason: str | None = None
    display_data: dict[str, Any] = field(default_factory=dict)
    approval_timestamp: str | None = None


class HITLPendingError(Exception):
    """Raised when HITL approval is required but not yet given."""
    
    def __init__(
        self,
        message: str,
        tool_name: str,
        display_fields: dict[str, Any],
        reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.tool_name = tool_name
        self.display_fields = display_fields
        self.reason = reason


def requires_hitl(
    reason: str,
    display_fields: list[str] | None = None,
    condition: Callable[[Any], bool] | None = None,
) -> Callable:
    """
    Decorator to mark a tool method as requiring Human-in-the-Loop approval.
    
    Args:
        reason: Human-readable reason for requiring approval
        display_fields: List of input field names to show in approval UI
        condition: Optional function to determine if HITL is required
                  (receives the input data, returns bool)
    
    Example:
        @requires_hitl(
            reason="Creating a new part",
            display_fields=["name", "category_id", "ipn"]
        )
        async def execute(self, input: CreatePartInput) -> CreatePartOutput:
            ...
    """
    def decorator(func: Callable) -> Callable:
        # Detect whether this is decorating a standalone async function
        # (no 'self' first param) vs. a class method.  Standalone functions
        # are registered directly with @ai_function and their signature must
        # remain untouched so the framework can build a pydantic model from
        # the parameter names (name, category_id, …).
        import inspect
        sig = inspect.signature(func)
        params = list(sig.parameters.keys())
        is_standalone = not params or params[0] != "self"

        if is_standalone:
            # Lightweight marker — just tag metadata, don't wrap the sig
            func._requires_hitl = True          # type: ignore[attr-defined]
            func._hitl_reason = reason           # type: ignore[attr-defined]
            func._hitl_display_fields = display_fields  # type: ignore[attr-defined]
            return func

        # Class-method path (original behaviour)
        @functools.wraps(func)
        async def wrapper(self, input_data: Any, hitl_context: HITLContext | None = None, *args, **kwargs):
            # Check if HITL is conditionally required
            needs_hitl = True
            if condition is not None:
                needs_hitl = condition(input_data)
            
            if not needs_hitl:
                # HITL not required for this invocation
                return await func(self, input_data, HITLContext(approved=True), *args, **kwargs)
            
            # Check if we have approval
            if hitl_context is None or not hitl_context.approved:
                # Extract display fields from input
                display_data = {}
                if display_fields and hasattr(input_data, "model_dump"):
                    input_dict = input_data.model_dump()
                    display_data = {k: input_dict.get(k) for k in display_fields if k in input_dict}
                elif display_fields and isinstance(input_data, dict):
                    display_data = {k: input_data.get(k) for k in display_fields if k in input_data}
                
                raise HITLPendingError(
                    message=f"HITL approval required: {reason}",
                    tool_name=getattr(self, "name", func.__name__),
                    display_fields=display_data,
                    reason=reason,
                )
            
            # Approved - execute the function
            return await func(self, input_data, hitl_context, *args, **kwargs)
        
        # Mark the function as requiring HITL
        wrapper._requires_hitl = True
        wrapper._hitl_reason = reason
        wrapper._hitl_display_fields = display_fields
        
        return wrapper
    return decorator

# Alias for backward compatibility — most of the codebase uses require_hitl
require_hitl = requires_hitl


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
    
    Read tools do not require HITL approval and are safe to call
    without user confirmation.
    """
    
    requires_hitl: bool = False


class WriteTool(BaseTool):
    """
    Base class for write tools (create/update/delete).
    
    Write tools may require HITL approval depending on the operation.
    Use the @requires_hitl decorator on the execute method to enable
    human approval flow.
    """
    
    requires_hitl: bool = True


class OperationTool(BaseTool):
    """
    Base class for complex multi-step operation tools.
    
    Operation tools handle grouped actions and typically require
    HITL approval for critical operations.
    """
    
    requires_hitl: bool = True
    
    @property
    @abstractmethod
    def supported_actions(self) -> list[str]:
        """List of actions this operation tool supports."""
        pass


# Type variable for tool functions
T = TypeVar("T", bound=BaseTool)
