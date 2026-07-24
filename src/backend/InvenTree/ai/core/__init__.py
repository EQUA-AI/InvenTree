"""
AIMMS Backend - AI-Powered Intelligent Manufacturing Management System

A multi-agent system built on Microsoft Agent Framework (MAF) for intelligent
manufacturing operations including diagnostics, inventory management, procurement,
and document processing.

Architecture:
- T1-T6 tiered complexity model
- 6 workflows (WF1-WF6 + WF8)
- AG-UI event emission for full transparency
- HITL approval for critical operations
- Semantic caching with safety rules

Components:
- OrchestratorAgent: Frontdoor agent for all user interactions
- RouterAgent: Intent classification and workflow dispatch (internal)
- Workflows: WF1-WF6 + WF8 for different complexity tiers
- Integrations: InvenTree, Gmail, Azure Document Intelligence
- Memory: ContextProviders, SemanticCache
- Events: AG-UI streaming events
- Middleware: Reflection, Logging, Metrics
"""

__version__ = "2.3.0"
__author__ = "AIMMS Team"

# Configuration
from .config import (
    get_azure_openai_settings,
    get_devui_settings,
    get_gmail_settings,
    get_inventree_settings,
    get_settings,
)

# Agents
# OrchestratorAgent and RouterAgent have been replaced by RootWorkflow and UnifiedRouter
# from .agents import (
#     OrchestratorAgent,
#     get_orchestrator_agent,
#     RouterAgent,
# )
# Events
from .events import (
    AGUIEvent,
    EventType,
    create_run_context,
    get_event_emitter,
)

# Memory
from .memory import (
    HITLSafetyRules,
    SemanticCache,
    get_semantic_cache,
)

# Middleware
from .middleware import (
    ErrorCategory,
    ReflectionFunctionMiddleware,
    get_reflection_middleware,
)

__all__ = [
    "AGUIEvent",
    # Middleware
    "ErrorCategory",
    # Agents
    # "OrchestratorAgent",
    # "get_shared_orchestrator",
    # "RouterAgent",
    # Events
    "EventType",
    "HITLSafetyRules",
    "ReflectionFunctionMiddleware",
    # Memory
    "SemanticCache",
    # Version
    "__version__",
    "create_run_context",
    "get_azure_openai_settings",
    "get_devui_settings",
    "get_event_emitter",
    "get_gmail_settings",
    "get_inventree_settings",
    "get_reflection_middleware",
    "get_semantic_cache",
    # Config
    "get_settings",
]
