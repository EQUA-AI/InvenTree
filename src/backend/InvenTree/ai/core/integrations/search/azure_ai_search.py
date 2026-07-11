"""
Azure AI Search Integration for Conversation Persistence

This module provides:
1. Index management (create, update, delete)
2. Document indexing for conversation messages
3. Semantic/vector search across conversation history

The index schema is designed to support:
- Full-text search on message content
- Semantic ranking for contextual relevance
- Vector search for similarity matching
- Filtering by user, thread, role, and time
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncIterator

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.aio import SearchClient as AsyncSearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex,
    SearchField,
    SearchFieldDataType,
    SimpleField,
    SearchableField,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    VectorSearch,
    VectorSearchProfile,
    HnswAlgorithmConfiguration,
    AzureOpenAIVectorizer,
    AzureOpenAIVectorizerParameters,
)
from azure.search.documents.models import VectorizedQuery

from ai.core.config import get_settings, get_azure_openai_settings

logger = logging.getLogger(__name__)


# ===== Index Configuration =====

INDEX_NAME = "inventree-ai-conversations"

# Index schema fields
INDEX_FIELDS = [
    SimpleField(
        name="id",
        type=SearchFieldDataType.String,
        key=True,
        filterable=True,
    ),
    SimpleField(
        name="thread_id",
        type=SearchFieldDataType.String,
        filterable=True,
        facetable=True,
    ),
    SimpleField(
        name="user_id",
        type=SearchFieldDataType.String,
        filterable=True,
        facetable=True,
    ),
    SimpleField(
        name="role",
        type=SearchFieldDataType.String,
        filterable=True,
        facetable=True,
    ),
    SearchableField(
        name="content",
        type=SearchFieldDataType.String,
        searchable=True,
        analyzer_name="en.microsoft",
    ),
    SimpleField(
        name="workflow_id",
        type=SearchFieldDataType.String,
        filterable=True,
        facetable=True,
    ),
    SearchableField(
        name="thread_title",
        type=SearchFieldDataType.String,
        searchable=True,
    ),
    SimpleField(
        name="created_at",
        type=SearchFieldDataType.DateTimeOffset,
        filterable=True,
        sortable=True,
    ),
    SimpleField(
        name="token_count",
        type=SearchFieldDataType.Int32,
        filterable=True,
    ),
    # Vector field for semantic search
    SearchField(
        name="content_vector",
        type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
        searchable=True,
        vector_search_dimensions=1536,  # text-embedding-ada-002
        vector_search_profile_name="conversation-vector-profile",
    ),
]


@dataclass
class SearchResult:
    """Result from a search query."""
    
    message_id: str
    thread_id: str
    user_id: str
    role: str
    content: str
    workflow_id: str | None
    thread_title: str
    created_at: datetime
    score: float
    reranker_score: float | None = None
    
    @classmethod
    def from_document(cls, doc: dict[str, Any], score: float = 0.0) -> "SearchResult":
        """Create from search document."""
        return cls(
            message_id=doc.get("id", ""),
            thread_id=doc.get("thread_id", ""),
            user_id=doc.get("user_id", ""),
            role=doc.get("role", ""),
            content=doc.get("content", ""),
            workflow_id=doc.get("workflow_id"),
            thread_title=doc.get("thread_title", ""),
            created_at=doc.get("created_at"),
            score=score,
            reranker_score=doc.get("@search.reranker_score"),
        )


class AzureAISearchService:
    """
    Service for managing Azure AI Search index and documents.
    
    Provides:
    - Index lifecycle management
    - Document CRUD operations
    - Semantic and vector search
    - Batch operations for efficiency
    """
    
    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
        index_name: str = INDEX_NAME,
    ):
        """
        Initialize the Azure AI Search service.
        
        Args:
            endpoint: Azure AI Search endpoint (defaults to env var)
            api_key: API key (defaults to env var)
            index_name: Name of the search index
        """
        settings = get_settings()
        
        # Get credentials from env if not provided
        self._endpoint = endpoint or settings.azure_search_endpoint
        self._api_key = api_key or settings.azure_search_api_key
        self._index_name = index_name
        
        if not self._endpoint or not self._api_key:
            raise ValueError(
                "Azure AI Search credentials not configured. "
                "Set AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_API_KEY."
            )
        
        self._credential = AzureKeyCredential(self._api_key)
        
        # Clients are created lazily
        self._index_client: SearchIndexClient | None = None
        self._search_client: SearchClient | None = None
        self._async_search_client: AsyncSearchClient | None = None
    
    @property
    def index_client(self) -> SearchIndexClient:
        """Get or create the index management client."""
        if self._index_client is None:
            self._index_client = SearchIndexClient(
                endpoint=self._endpoint,
                credential=self._credential,
            )
        return self._index_client
    
    @property
    def search_client(self) -> SearchClient:
        """Get or create the search client."""
        if self._search_client is None:
            self._search_client = SearchClient(
                endpoint=self._endpoint,
                index_name=self._index_name,
                credential=self._credential,
            )
        return self._search_client
    
    @property
    def async_search_client(self) -> AsyncSearchClient:
        """Get or create the async search client."""
        if self._async_search_client is None:
            self._async_search_client = AsyncSearchClient(
                endpoint=self._endpoint,
                index_name=self._index_name,
                credential=self._credential,
            )
        return self._async_search_client
    
    # ===== Index Management =====
    
    def create_or_update_index(self) -> SearchIndex:
        """
        Create or update the conversation search index.
        
        Sets up:
        - Full-text search fields
        - Semantic search configuration
        - Vector search for embeddings
        """
        openai_settings = get_azure_openai_settings()
        
        # Vector search configuration
        vector_search = VectorSearch(
            algorithms=[
                HnswAlgorithmConfiguration(
                    name="conversation-hnsw",
                    parameters={
                        "m": 4,
                        "efConstruction": 400,
                        "efSearch": 500,
                        "metric": "cosine",
                    },
                ),
            ],
            profiles=[
                VectorSearchProfile(
                    name="conversation-vector-profile",
                    algorithm_configuration_name="conversation-hnsw",
                    vectorizer_name="conversation-vectorizer",
                ),
            ],
            vectorizers=[
                AzureOpenAIVectorizer(
                    vectorizer_name="conversation-vectorizer",
                    parameters=AzureOpenAIVectorizerParameters(
                        resource_url=openai_settings.endpoint,
                        deployment_name=openai_settings.embedding_deployment,
                        api_key=openai_settings.api_key.get_secret_value(),
                        model_name="text-embedding-ada-002",
                    ),
                ),
            ],
        )
        
        # Semantic search configuration
        semantic_config = SemanticConfiguration(
            name="conversation-semantic-config",
            prioritized_fields=SemanticPrioritizedFields(
                content_fields=[SemanticField(field_name="content")],
                title_field=SemanticField(field_name="thread_title"),
            ),
        )
        
        semantic_search = SemanticSearch(
            configurations=[semantic_config],
            default_configuration_name="conversation-semantic-config",
        )
        
        # Create index
        index = SearchIndex(
            name=self._index_name,
            fields=INDEX_FIELDS,
            vector_search=vector_search,
            semantic_search=semantic_search,
        )
        
        result = self.index_client.create_or_update_index(index)
        logger.info(f"Index '{result.name}' created/updated successfully")
        return result
    
    def delete_index(self) -> None:
        """Delete the search index."""
        self.index_client.delete_index(self._index_name)
        logger.info(f"Index '{self._index_name}' deleted")
    
    def index_exists(self) -> bool:
        """Check if the index exists."""
        try:
            self.index_client.get_index(self._index_name)
            return True
        except Exception:
            return False
    
    # ===== Document Operations =====
    
    def index_document(self, document: dict[str, Any]) -> dict[str, Any]:
        """
        Index a single document.
        
        Args:
            document: Document to index (must have 'id' field)
            
        Returns:
            Indexing result
        """
        result = self.search_client.upload_documents(documents=[document])
        return result[0].as_dict()
    
    def index_documents(
        self,
        documents: list[dict[str, Any]],
        batch_size: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Index multiple documents in batches.
        
        Args:
            documents: Documents to index
            batch_size: Number of documents per batch
            
        Returns:
            List of indexing results
        """
        results = []
        for i in range(0, len(documents), batch_size):
            batch = documents[i:i + batch_size]
            batch_results = self.search_client.upload_documents(documents=batch)
            results.extend([r.as_dict() for r in batch_results])
        return results
    
    async def index_document_async(self, document: dict[str, Any]) -> dict[str, Any]:
        """Async version of index_document."""
        result = await self.async_search_client.upload_documents(documents=[document])
        return result[0].as_dict()
    
    def delete_document(self, document_id: str) -> dict[str, Any]:
        """Delete a document by ID."""
        result = self.search_client.delete_documents(
            documents=[{"id": document_id}]
        )
        return result[0].as_dict()
    
    def delete_documents_by_thread(self, thread_id: str) -> int:
        """
        Delete all documents in a thread.
        
        Returns the number of documents deleted.
        """
        # First, find all documents in the thread
        results = self.search_client.search(
            search_text="*",
            filter=f"thread_id eq '{thread_id}'",
            select=["id"],
            top=1000,
        )
        
        doc_ids = [{"id": doc["id"]} for doc in results]
        
        if doc_ids:
            self.search_client.delete_documents(documents=doc_ids)
        
        return len(doc_ids)
    
    # ===== Search Operations =====
    
    def search(
        self,
        query: str,
        user_id: str | None = None,
        thread_id: str | None = None,
        role: str | None = None,
        top: int = 10,
        use_semantic: bool = True,
    ) -> list[SearchResult]:
        """
        Search conversation history.
        
        Args:
            query: Search query text
            user_id: Filter by user ID
            thread_id: Filter by thread ID
            role: Filter by message role
            top: Maximum number of results
            use_semantic: Use semantic ranking
            
        Returns:
            List of search results
        """
        # Build filter
        filters = []
        if user_id:
            filters.append(f"user_id eq '{user_id}'")
        if thread_id:
            filters.append(f"thread_id eq '{thread_id}'")
        if role:
            filters.append(f"role eq '{role}'")
        
        filter_expr = " and ".join(filters) if filters else None
        
        # Execute search
        results = self.search_client.search(
            search_text=query,
            filter=filter_expr,
            top=top,
            query_type="semantic" if use_semantic else "simple",
            semantic_configuration_name="conversation-semantic-config" if use_semantic else None,
            select=["id", "thread_id", "user_id", "role", "content", "workflow_id", "thread_title", "created_at"],
        )
        
        return [
            SearchResult.from_document(doc, doc.get("@search.score", 0.0))
            for doc in results
        ]
    
    async def search_async(
        self,
        query: str,
        user_id: str | None = None,
        thread_id: str | None = None,
        role: str | None = None,
        top: int = 10,
        use_semantic: bool = True,
    ) -> list[SearchResult]:
        """Async version of search."""
        filters = []
        if user_id:
            filters.append(f"user_id eq '{user_id}'")
        if thread_id:
            filters.append(f"thread_id eq '{thread_id}'")
        if role:
            filters.append(f"role eq '{role}'")
        
        filter_expr = " and ".join(filters) if filters else None
        
        results = await self.async_search_client.search(
            search_text=query,
            filter=filter_expr,
            top=top,
            query_type="semantic" if use_semantic else "simple",
            semantic_configuration_name="conversation-semantic-config" if use_semantic else None,
            select=["id", "thread_id", "user_id", "role", "content", "workflow_id", "thread_title", "created_at"],
        )
        
        return [
            SearchResult.from_document(doc, doc.get("@search.score", 0.0))
            async for doc in results
        ]
    
    def vector_search(
        self,
        query_vector: list[float],
        user_id: str | None = None,
        thread_id: str | None = None,
        top: int = 10,
    ) -> list[SearchResult]:
        """
        Perform vector similarity search.
        
        Args:
            query_vector: Embedding vector for the query
            user_id: Filter by user ID
            thread_id: Filter by thread ID
            top: Maximum number of results
            
        Returns:
            List of search results ordered by similarity
        """
        filters = []
        if user_id:
            filters.append(f"user_id eq '{user_id}'")
        if thread_id:
            filters.append(f"thread_id eq '{thread_id}'")
        
        filter_expr = " and ".join(filters) if filters else None
        
        vector_query = VectorizedQuery(
            vector=query_vector,
            k_nearest_neighbors=top,
            fields="content_vector",
        )
        
        results = self.search_client.search(
            search_text=None,
            vector_queries=[vector_query],
            filter=filter_expr,
            top=top,
            select=["id", "thread_id", "user_id", "role", "content", "workflow_id", "thread_title", "created_at"],
        )
        
        return [
            SearchResult.from_document(doc, doc.get("@search.score", 0.0))
            for doc in results
        ]
    
    def hybrid_search(
        self,
        query: str,
        query_vector: list[float] | None = None,
        user_id: str | None = None,
        thread_id: str | None = None,
        top: int = 10,
    ) -> list[SearchResult]:
        """
        Perform hybrid search (text + vector + semantic).
        
        Combines:
        - Full-text search on content
        - Vector similarity (if vector provided)
        - Semantic reranking
        
        Args:
            query: Search query text
            query_vector: Optional embedding vector
            user_id: Filter by user ID
            thread_id: Filter by thread ID
            top: Maximum number of results
            
        Returns:
            List of search results
        """
        filters = []
        if user_id:
            filters.append(f"user_id eq '{user_id}'")
        if thread_id:
            filters.append(f"thread_id eq '{thread_id}'")
        
        filter_expr = " and ".join(filters) if filters else None
        
        vector_queries = None
        if query_vector:
            vector_queries = [
                VectorizedQuery(
                    vector=query_vector,
                    k_nearest_neighbors=top * 2,  # Fetch more for reranking
                    fields="content_vector",
                )
            ]
        
        results = self.search_client.search(
            search_text=query,
            vector_queries=vector_queries,
            filter=filter_expr,
            top=top,
            query_type="semantic",
            semantic_configuration_name="conversation-semantic-config",
            select=["id", "thread_id", "user_id", "role", "content", "workflow_id", "thread_title", "created_at"],
        )
        
        return [
            SearchResult.from_document(doc, doc.get("@search.score", 0.0))
            for doc in results
        ]
    
    # ===== Utility Methods =====
    
    def get_document_count(self) -> int:
        """Get the total number of documents in the index."""
        results = self.search_client.search(
            search_text="*",
            top=0,
            include_total_count=True,
        )
        return results.get_count() or 0
    
    def get_stats(self) -> dict[str, Any]:
        """Get index statistics."""
        index = self.index_client.get_index(self._index_name)
        return {
            "name": index.name,
            "fields": len(index.fields),
            "document_count": self.get_document_count(),
        }


# ===== Global Instance =====

_search_service: AzureAISearchService | None = None


def get_search_service() -> AzureAISearchService:
    """Get or create the global search service instance."""
    global _search_service
    if _search_service is None:
        _search_service = AzureAISearchService()
    return _search_service


# Export all symbols
__all__ = [
    "INDEX_NAME",
    "SearchResult",
    "AzureAISearchService",
    "get_search_service",
]
