"""
Azure AI Search Integration Module

Provides semantic and vector search capabilities for AI conversation history.
"""

from ai.core.integrations.search.azure_ai_search import (
    INDEX_NAME,
    SearchResult,
    AzureAISearchService,
    get_search_service,
)

__all__ = [
    "INDEX_NAME",
    "SearchResult",
    "AzureAISearchService",
    "get_search_service",
]
