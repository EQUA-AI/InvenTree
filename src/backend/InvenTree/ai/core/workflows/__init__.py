"""
AIMMS Workflows Module

Contains all workflow definitions for the AIMMS system:
- WF1: Diagnostics Workflow (T6 - MagenticBuilder)
- WF2: Sequential Workflow (T2 - SequentialBuilder)
- WF3: Concurrent Workflow (T3 - ConcurrentBuilder)
- WF4: Procurement Workflow (T4 - HITL approval)
- WF5: CPQ Workflow (T5 - GroupChatBuilder)
- WF6: Incoming Documents Workflow (Email + Doc Intelligence)
- WF8: Lookup Workflow (T1 - Single agent fast-path)

Each workflow implements the .as_agent() pattern for registry integration.
"""

from ai.core.workflows.registry import WorkflowRegistry, get_workflow_registry

# WF1: T6 Diagnostics
from ai.core.workflows.wf1_diagnostics import (
    DiagnosisConfidence,
    DiagnosticsResult,
    ProblemCategory,
    RootCause,
    Solution,
    T6DiagnosticsBuilder,
    T6DiagnosticsWorkflow,
    create_t6_diagnostics_workflow,
    t6_diagnostics_builder,
)

# WF2: T2 Parts Analysis
from ai.core.workflows.wf2_parts_analysis import (
    AnalysisResult,
    AnalysisType,
    T2PartsAnalysisBuilder,
    T2PartsAnalysisWorkflow,
    create_t2_parts_workflow,
    t2_parts_builder,
)

# WF3: T3 Research
from ai.core.workflows.wf3_research import (
    ResearchResult,
    ResearchSource,
    ResearchType,
    T3ResearchBuilder,
    T3ResearchWorkflow,
    create_t3_research_workflow,
    t3_research_builder,
)

# WF4: T4 Procurement
from ai.core.workflows.wf4_procurement import (
    ApprovalType,
    LineItem,
    ProcurementResult,
    ProcurementStatus,
    PurchaseOrder,
    T4ProcurementBuilder,
    T4ProcurementWorkflow,
    create_t4_procurement_workflow,
    t4_procurement_builder,
)

# WF5: T5 CPQ
from ai.core.workflows.wf5_cpq import (
    ConfigurationStatus,
    CPQResult,
    ProductConfiguration,
    Quote,
    T5CPQBuilder,
    T5CPQWorkflow,
    create_t5_cpq_workflow,
    t5_cpq_builder,
)

# WF6: Documents
from ai.core.workflows.wf6_documents import (
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

# WF8: T1 Lookup
from ai.core.workflows.wf8_lookup import (
    LookupResult,
    LookupType,
    T1LookupWorkflow,
    T1LookupWorkflowBuilder,
    create_t1_lookup_workflow,
    t1_lookup_builder,
)

__all__ = [  # noqa: RUF022
    # Registry
    "WorkflowRegistry",
    "get_workflow_registry",
    # WF1: Diagnostics
    "T6DiagnosticsWorkflow",
    "T6DiagnosticsBuilder",
    "DiagnosticsResult",
    "ProblemCategory",
    "DiagnosisConfidence",
    "RootCause",
    "Solution",
    "create_t6_diagnostics_workflow",
    "t6_diagnostics_builder",
    # WF2: Parts Analysis
    "T2PartsAnalysisWorkflow",
    "T2PartsAnalysisBuilder",
    "AnalysisType",
    "AnalysisResult",
    "create_t2_parts_workflow",
    "t2_parts_builder",
    # WF3: Research
    "T3ResearchWorkflow",
    "T3ResearchBuilder",
    "ResearchType",
    "ResearchResult",
    "ResearchSource",
    "create_t3_research_workflow",
    "t3_research_builder",
    # WF4: Procurement
    "T4ProcurementWorkflow",
    "T4ProcurementBuilder",
    "ProcurementResult",
    "ProcurementStatus",
    "ApprovalType",
    "PurchaseOrder",
    "LineItem",
    "create_t4_procurement_workflow",
    "t4_procurement_builder",
    # WF5: CPQ
    "T5CPQWorkflow",
    "T5CPQBuilder",
    "CPQResult",
    "ProductConfiguration",
    "Quote",
    "ConfigurationStatus",
    "create_t5_cpq_workflow",
    "t5_cpq_builder",
    # WF6: Documents
    "WF6DocumentWorkflow",
    "WF6DocumentBuilder",
    "DocumentProcessingResult",
    "DocumentExtractionResult",
    "DocumentType",
    "ExtractionMode",
    "ProcessingStatus",
    "WorkflowMode",
    "create_wf6_document_workflow",
    "wf6_document_builder",
    # WF8: Lookup
    "T1LookupWorkflow",
    "T1LookupWorkflowBuilder",
    "LookupType",
    "LookupResult",
    "create_t1_lookup_workflow",
    "t1_lookup_builder",
]
