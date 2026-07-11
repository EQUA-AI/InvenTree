"""
WF6: Document Processing Workflow (v2 - Conversational)

DEPRECATED — This module is now a thin re-export shim.
All logic has been merged into ``wf6_documents.py``.

Imports here preserve backward compatibility for:
- ``devui_adapters_v2.py``
- Any other code that imported from ``wf6_documents_v2``
"""

from ai.core.workflows.wf6_documents import (  # noqa: F401
    DocumentExtractionResult,
    DocumentProcessingResult,
    DocumentType,
    ExtractionMode,
    ProcessingStatus,
    WF6DocumentBuilder,
    WF6DocumentWorkflow,
    WorkflowMode,
    create_wf6_document_workflow,
    wf6_document_builder,
)

__all__ = [
    "DocumentExtractionResult",
    "DocumentProcessingResult",
    "DocumentType",
    "ExtractionMode",
    "ProcessingStatus",
    "WF6DocumentBuilder",
    "WF6DocumentWorkflow",
    "WorkflowMode",
    "create_wf6_document_workflow",
    "wf6_document_builder",
]
