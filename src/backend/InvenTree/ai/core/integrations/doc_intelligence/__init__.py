"""
Azure Document Intelligence Integration Module

Provides document processing capabilities:
- DocIntelligenceClient: Azure DI API client
- Document extraction tools for various formats (PDF, images, etc.)
- Invoice, purchase order, and technical drawing analysis
"""

from ai.core.integrations.doc_intelligence.client import (
    DocIntelligenceClient,
    get_doc_intelligence_client,
)

__all__ = [
    "DocIntelligenceClient",
    "get_doc_intelligence_client",
]
