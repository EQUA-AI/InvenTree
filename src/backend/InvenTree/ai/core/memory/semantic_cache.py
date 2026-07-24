"""
AIMMS Semantic Cache

Provides intelligent caching of query-response pairs using semantic similarity.
Uses embeddings to find similar past queries and return cached responses
when appropriate, significantly reducing LLM calls and latency.

Key features:
- Embedding-based similarity matching
- HITL-safe rules (never cache HITL-required decisions)
- TTL-based expiration
- Confidence thresholds
- Cache invalidation strategies
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Protocol, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CachePolicy(Enum):
    """Cache storage and retrieval policies."""

    ALWAYS_CACHE = "always_cache"  # Always cache responses
    NEVER_CACHE = "never_cache"  # Never cache (HITL, sensitive)
    CACHE_IF_CONFIDENT = "cache_if_confident"  # Cache only high-confidence results
    CACHE_READ_ONLY = "cache_read_only"  # Only read from cache, don't write


@dataclass
class CachedEntry:
    """A cached query-response pair with metadata."""

    query: str
    query_hash: str
    embedding: list[float]
    response: Any
    confidence: float
    created_at: datetime
    expires_at: datetime
    access_count: int = 0
    last_accessed: datetime | None = None
    workflow_id: str = ""
    user_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        """Check if entry has expired."""
        return datetime.now(UTC) > self.expires_at

    def touch(self) -> None:
        """Update access tracking."""
        self.access_count += 1
        self.last_accessed = datetime.now(UTC)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "query": self.query,
            "query_hash": self.query_hash,
            "embedding": self.embedding,
            "response": self.response,
            "confidence": self.confidence,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "access_count": self.access_count,
            "last_accessed": self.last_accessed.isoformat() if self.last_accessed else None,
            "workflow_id": self.workflow_id,
            "user_id": self.user_id,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CachedEntry:
        """Deserialize from dictionary."""
        return cls(
            query=data["query"],
            query_hash=data["query_hash"],
            embedding=data["embedding"],
            response=data["response"],
            confidence=data["confidence"],
            created_at=datetime.fromisoformat(data["created_at"]),
            expires_at=datetime.fromisoformat(data["expires_at"]),
            access_count=data.get("access_count", 0),
            last_accessed=datetime.fromisoformat(data["last_accessed"])
            if data.get("last_accessed")
            else None,
            workflow_id=data.get("workflow_id", ""),
            user_id=data.get("user_id", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass
class CacheResult:
    """Result of a cache lookup."""

    hit: bool
    entry: CachedEntry | None = None
    similarity: float = 0.0
    from_exact_match: bool = False


class EmbeddingProvider(Protocol):
    """Protocol for embedding providers."""

    async def embed(self, text: str) -> list[float]:
        """Generate embedding for text."""
        ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts."""
        ...


class AzureOpenAIEmbeddingProvider:
    """Azure OpenAI embedding provider."""

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        deployment: str = "text-embedding-ada-002",
    ):
        self.endpoint = endpoint
        self.api_key = api_key
        self.deployment = deployment
        self._client = None

    async def _get_client(self):
        """Lazy initialization of OpenAI client."""
        if self._client is None:
            try:
                from openai import AsyncAzureOpenAI

                self._client = AsyncAzureOpenAI(
                    azure_endpoint=self.endpoint,
                    api_key=self.api_key,
                    api_version="2024-02-01",
                )
            except ImportError:
                raise RuntimeError("openai package required for AzureOpenAIEmbeddingProvider")
        return self._client

    async def embed(self, text: str) -> list[float]:
        """Generate embedding for text."""
        client = await self._get_client()
        response = await client.embeddings.create(
            input=text,
            model=self.deployment,
        )
        return response.data[0].embedding

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts."""
        client = await self._get_client()
        response = await client.embeddings.create(
            input=texts,
            model=self.deployment,
        )
        return [item.embedding for item in response.data]


class LocalEmbeddingProvider:
    """
    Local embedding provider using sentence-transformers.
    Useful for development/testing without Azure calls.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self._model = None

    def _get_model(self):
        """Lazy initialization of sentence transformer model."""
        if self._model is None:
            try:
                # Optional dev/test dependency; guarded by the ImportError handler
                from sentence_transformers import (  # ty: ignore[unresolved-import]
                    SentenceTransformer,
                )

                self._model = SentenceTransformer(self.model_name)
            except ImportError:
                raise RuntimeError(
                    "sentence-transformers package required for LocalEmbeddingProvider"
                )
        return self._model

    async def embed(self, text: str) -> list[float]:
        """Generate embedding for text."""
        model = self._get_model()
        # Run in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        embedding = await loop.run_in_executor(
            None, lambda: model.encode(text, convert_to_numpy=True).tolist()
        )
        return embedding

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts."""
        model = self._get_model()
        loop = asyncio.get_event_loop()
        embeddings = await loop.run_in_executor(
            None, lambda: model.encode(texts, convert_to_numpy=True).tolist()
        )
        return embeddings


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Calculate cosine similarity between two vectors."""
    if len(a) != len(b):
        return 0.0

    dot_product = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot_product / (norm_a * norm_b)


class HITLSafetyRules:
    """
    Rules for determining what can be cached safely.

    CRITICAL: Never cache:
    - HITL-required decisions
    - User-specific authorizations
    - Time-sensitive data
    - Financial transactions
    - Security-related queries
    """

    # Patterns that indicate HITL-required queries
    HITL_PATTERNS: ClassVar[list[str]] = [
        "approve",
        "authorization",
        "permission",
        "purchase order",
        "po approval",
        "budget",
        "delete",
        "remove",
        "cancel",
        "override",
        "bypass",
        "force",
        "sensitive",
        "confidential",
        "secret",
    ]

    # Patterns that indicate time-sensitive queries
    TIME_SENSITIVE_PATTERNS: ClassVar[list[str]] = [
        "current stock",
        "available now",
        "real-time",
        "live",
        "latest",
        "today",
        "right now",
        "this moment",
        "immediate",
    ]

    # Workflow IDs that should never be cached
    NEVER_CACHE_WORKFLOWS: ClassVar[list[str]] = [
        "wf4_procurement",  # Has HITL approval
        "wf6_documents",  # Document processing may vary
    ]

    @classmethod
    def can_cache(
        cls,
        query: str,
        workflow_id: str = "",
        _response: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        """
        Determine if a query-response can be safely cached.

        Returns:
            Tuple of (can_cache, reason)
        """
        query_lower = query.lower()

        # Check HITL patterns
        for pattern in cls.HITL_PATTERNS:
            if pattern in query_lower:
                return False, f"Contains HITL-required pattern: {pattern}"

        # Check time-sensitive patterns
        for pattern in cls.TIME_SENSITIVE_PATTERNS:
            if pattern in query_lower:
                return False, f"Contains time-sensitive pattern: {pattern}"

        # Check workflow restrictions
        if workflow_id in cls.NEVER_CACHE_WORKFLOWS:
            return False, f"Workflow {workflow_id} is marked as never-cache"

        # Check metadata flags
        if metadata:
            if metadata.get("requires_hitl"):
                return False, "Response marked as requiring HITL"
            if metadata.get("no_cache"):
                return False, "Response marked as no-cache"

        return True, "Safe to cache"


@dataclass
class CacheConfig:
    """Configuration for semantic cache."""

    # Similarity threshold for cache hits
    similarity_threshold: float = 0.85

    # Default TTL for cached entries
    default_ttl_hours: int = 24

    # Maximum cache entries
    max_entries: int = 10000

    # Whether to use exact hash matching first
    use_exact_matching: bool = True

    # Minimum confidence for caching
    min_cache_confidence: float = 0.7

    # Whether to apply HITL safety rules
    enforce_hitl_safety: bool = True

    # Storage path for persistent cache
    storage_path: Path | None = None


class SemanticCache:
    """
    Semantic cache for query-response pairs.

    Uses embeddings to find similar past queries and return
    cached responses when similarity exceeds threshold.

    Example usage:
        cache = SemanticCache(
            embedding_provider=AzureOpenAIEmbeddingProvider(...),
            config=CacheConfig(similarity_threshold=0.9),
        )

        # Check cache before calling LLM
        result = await cache.get("What parts do we have in stock?")
        if result.hit:
            return result.entry.response

        # Call LLM and cache result
        response = await call_llm(query)
        await cache.put(query, response, workflow_id="wf8_lookup")
    """

    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        config: CacheConfig | None = None,
    ):
        self.embedding_provider = embedding_provider
        self.config = config or CacheConfig()

        # In-memory cache
        self._cache: dict[str, CachedEntry] = {}

        # Exact match index (hash -> query_hash)
        self._hash_index: dict[str, str] = {}

        # Load from storage if configured
        if self.config.storage_path:
            self._load_from_storage()

    def _query_hash(self, query: str) -> str:
        """Generate hash for exact matching."""
        normalized = query.lower().strip()
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    async def get(
        self,
        query: str,
        workflow_id: str = "",
        user_id: str = "",
    ) -> CacheResult:
        """
        Look up a query in the cache.

        First tries exact hash matching, then falls back
        to semantic similarity search.

        Args:
            query: The query to look up
            workflow_id: Current workflow context
            user_id: User making the query

        Returns:
            CacheResult with hit status and entry if found
        """

        def _visible(entry: CachedEntry) -> bool:
            """Cache entries are scoped: responses may embed permission-dependent
            data, so a hit is only valid for the same user and workflow context
            that stored it (unscoped legacy entries stay shared)."""
            if entry.user_id and entry.user_id != user_id:
                return False
            return not (entry.workflow_id and entry.workflow_id != workflow_id)

        # Try exact match first
        if self.config.use_exact_matching:
            query_hash = self._query_hash(query)
            if query_hash in self._hash_index:
                entry_key = self._hash_index[query_hash]
                if entry_key in self._cache:
                    entry = self._cache[entry_key]
                    if not entry.is_expired and _visible(entry):
                        entry.touch()
                        logger.debug(f"Cache exact hit: {query[:50]}...")
                        return CacheResult(
                            hit=True,
                            entry=entry,
                            similarity=1.0,
                            from_exact_match=True,
                        )
                    elif entry.is_expired:
                        # Remove expired entry
                        self._remove_entry(entry_key)

        # Fall back to semantic similarity
        query_embedding = await self.embedding_provider.embed(query)

        best_match: CachedEntry | None = None
        best_similarity = 0.0

        for entry in self._cache.values():
            if entry.is_expired or not _visible(entry):
                continue

            similarity = cosine_similarity(query_embedding, entry.embedding)

            if similarity > best_similarity and similarity >= self.config.similarity_threshold:
                best_similarity = similarity
                best_match = entry

        if best_match:
            best_match.touch()
            logger.debug(f"Cache semantic hit: {query[:50]}... (similarity: {best_similarity:.3f})")
            return CacheResult(
                hit=True,
                entry=best_match,
                similarity=best_similarity,
                from_exact_match=False,
            )

        logger.debug(f"Cache miss: {query[:50]}...")
        return CacheResult(hit=False)

    async def put(
        self,
        query: str,
        response: Any,
        confidence: float = 1.0,
        workflow_id: str = "",
        user_id: str = "",
        ttl_hours: int | None = None,
        metadata: dict[str, Any] | None = None,
        policy: CachePolicy = CachePolicy.CACHE_IF_CONFIDENT,
    ) -> bool:
        """
        Store a query-response pair in the cache.

        Args:
            query: The query
            response: The response to cache
            confidence: Confidence level of the response (0-1)
            workflow_id: Workflow that generated the response
            user_id: User who made the query
            ttl_hours: TTL override
            metadata: Additional metadata
            policy: Caching policy to apply

        Returns:
            True if cached successfully, False otherwise
        """
        # Check policy
        if policy == CachePolicy.NEVER_CACHE or policy == CachePolicy.CACHE_READ_ONLY:
            logger.debug(f"Skip caching due to policy: {policy.value}")
            return False

        # Check confidence threshold
        if (
            policy == CachePolicy.CACHE_IF_CONFIDENT
            and confidence < self.config.min_cache_confidence
        ):
            logger.debug(f"Skip caching due to low confidence: {confidence:.2f}")
            return False

        # Check HITL safety rules
        if self.config.enforce_hitl_safety:
            can_cache, reason = HITLSafetyRules.can_cache(
                query=query,
                workflow_id=workflow_id,
                _response=response,
                metadata=metadata,
            )
            if not can_cache:
                logger.debug(f"Skip caching due to HITL safety: {reason}")
                return False

        # Generate embedding
        embedding = await self.embedding_provider.embed(query)

        # Create entry
        query_hash = self._query_hash(query)
        now = datetime.now(UTC)
        ttl = ttl_hours or self.config.default_ttl_hours

        entry = CachedEntry(
            query=query,
            query_hash=query_hash,
            embedding=embedding,
            response=response,
            confidence=confidence,
            created_at=now,
            expires_at=now + timedelta(hours=ttl),
            workflow_id=workflow_id,
            user_id=user_id,
            metadata=metadata or {},
        )

        # Check capacity
        if len(self._cache) >= self.config.max_entries:
            self._evict_lru()

        # Store entry
        self._cache[query_hash] = entry
        self._hash_index[query_hash] = query_hash

        # Persist if configured
        if self.config.storage_path:
            self._save_to_storage()

        logger.debug(f"Cached: {query[:50]}... (ttl: {ttl}h)")
        return True

    def invalidate(
        self,
        query: str | None = None,
        workflow_id: str | None = None,
        older_than: datetime | None = None,
    ) -> int:
        """
        Invalidate cache entries matching criteria.

        Args:
            query: Specific query to invalidate (exact match)
            workflow_id: Invalidate all entries for workflow
            older_than: Invalidate entries created before this time

        Returns:
            Number of entries invalidated
        """
        to_remove = []

        for key, entry in self._cache.items():
            should_remove = False

            if query and entry.query_hash == self._query_hash(query):
                should_remove = True

            if workflow_id and entry.workflow_id == workflow_id:
                should_remove = True

            if older_than and entry.created_at < older_than:
                should_remove = True

            if should_remove:
                to_remove.append(key)

        for key in to_remove:
            self._remove_entry(key)

        if to_remove and self.config.storage_path:
            self._save_to_storage()

        logger.info(f"Invalidated {len(to_remove)} cache entries")
        return len(to_remove)

    def clear(self) -> None:
        """Clear all cache entries."""
        self._cache.clear()
        self._hash_index.clear()

        if self.config.storage_path:
            self._save_to_storage()

        logger.info("Cache cleared")

    def get_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        entries = list(self._cache.values())
        expired = sum(1 for e in entries if e.is_expired)

        total_accesses = sum(e.access_count for e in entries)

        entries_by_workflow: dict[str, int] = {}
        for entry in entries:
            wf = entry.workflow_id or "unknown"
            entries_by_workflow[wf] = entries_by_workflow.get(wf, 0) + 1

        return {
            "total_entries": len(entries),
            "expired_entries": expired,
            "active_entries": len(entries) - expired,
            "total_accesses": total_accesses,
            "max_entries": self.config.max_entries,
            "utilization": len(entries) / self.config.max_entries,
            "entries_by_workflow": entries_by_workflow,
        }

    def _remove_entry(self, key: str) -> None:
        """Remove an entry from the cache."""
        if key in self._cache:
            entry = self._cache[key]
            del self._cache[key]
            if entry.query_hash in self._hash_index:
                del self._hash_index[entry.query_hash]

    def _evict_lru(self) -> None:
        """Evict least recently used entries."""
        # Sort by last access time
        entries = sorted(
            self._cache.items(),
            key=lambda x: x[1].last_accessed or x[1].created_at,
        )

        # Remove oldest 10%
        to_remove = max(1, len(entries) // 10)
        for key, _ in entries[:to_remove]:
            self._remove_entry(key)

        logger.debug(f"Evicted {to_remove} LRU cache entries")

    def _load_from_storage(self) -> None:
        """Load cache from persistent storage."""
        if not self.config.storage_path:
            return

        cache_file = self.config.storage_path / "semantic_cache.json"
        if not cache_file.exists():
            return

        try:
            with Path(cache_file).open() as f:
                data = json.load(f)

            for entry_data in data.get("entries", []):
                entry = CachedEntry.from_dict(entry_data)
                if not entry.is_expired:
                    self._cache[entry.query_hash] = entry
                    self._hash_index[entry.query_hash] = entry.query_hash

            logger.info(f"Loaded {len(self._cache)} cache entries from storage")

        except Exception as e:
            logger.warning(f"Failed to load cache from storage: {e}")

    def _save_to_storage(self) -> None:
        """Save cache to persistent storage."""
        if not self.config.storage_path:
            return

        self.config.storage_path.mkdir(parents=True, exist_ok=True)
        cache_file = self.config.storage_path / "semantic_cache.json"

        try:
            # Only save non-expired entries
            entries = [entry.to_dict() for entry in self._cache.values() if not entry.is_expired]

            with Path(cache_file).open("w") as f:
                json.dump({"entries": entries}, f, indent=2)

        except Exception as e:
            logger.warning(f"Failed to save cache to storage: {e}")


# Factory function for creating cache with settings
def create_semantic_cache(
    use_local_embeddings: bool = False,
    storage_path: Path | None = None,
) -> SemanticCache:
    """
    Create a semantic cache instance with configuration from settings.

    Args:
        use_local_embeddings: Use local sentence-transformers instead of Azure
        storage_path: Path for persistent cache storage

    Returns:
        Configured SemanticCache instance
    """
    from ai.core.config import get_azure_openai_settings, get_settings

    settings = get_settings()
    azure_settings = get_azure_openai_settings()

    if use_local_embeddings:
        provider = LocalEmbeddingProvider()
    else:
        provider = AzureOpenAIEmbeddingProvider(
            endpoint=azure_settings.endpoint,
            api_key=azure_settings.api_key.get_secret_value(),
            deployment=azure_settings.embedding_deployment,
        )

    config = CacheConfig(
        similarity_threshold=settings.semantic_cache_similarity_threshold,
        default_ttl_hours=settings.semantic_cache_ttl_hours,
        max_entries=10000,
        use_exact_matching=True,
        min_cache_confidence=0.7,
        enforce_hitl_safety=True,
        storage_path=storage_path or settings.cache_dir,
    )

    return SemanticCache(
        embedding_provider=provider,
        config=config,
    )


# Shared cache instance
_semantic_cache: SemanticCache | None = None


def get_semantic_cache() -> SemanticCache:
    """Get or create the shared semantic cache instance."""
    global _semantic_cache
    if _semantic_cache is None:
        _semantic_cache = create_semantic_cache()
    return _semantic_cache
