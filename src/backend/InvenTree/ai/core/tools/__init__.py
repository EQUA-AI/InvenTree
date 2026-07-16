"""
AIMMS InvenTree Tools Package

This package provides AI-callable tools for interacting with the InvenTree API.
Tools are organized into:
- read/ - Read-only data retrieval tools
- write/ - Create/update/delete tools with HITL support
- operations/ - Complex multi-step operations
"""

from ai.core.tools.diagnostics import (
    DIAGNOSTIC_TOOL_NAMES,
    DIAGNOSTIC_TOOL_REGISTRY,
    DiagnosticToolRegistry,
    build_diagnostic_context,
    get_diagnostic_tool_registry,
)

__all__ = [
    "DIAGNOSTIC_TOOL_NAMES",
    "DIAGNOSTIC_TOOL_REGISTRY",
    "DiagnosticToolRegistry",
    "build_diagnostic_context",
    "get_diagnostic_tool_registry",
]
