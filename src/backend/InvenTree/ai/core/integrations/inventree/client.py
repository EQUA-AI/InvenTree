"""
InvenTree REST Client

Async HTTP client for InvenTree API with:
- Circuit breaker pattern for resilience
- Automatic retry with exponential backoff
- Request/response logging
- Error classification for reflection
"""

import json
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import httpx
import structlog
from ai.core.config import get_inventree_settings
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = structlog.get_logger(__name__)


class CircuitState(StrEnum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if service recovered


class CircuitBreaker:
    """
    Simple circuit breaker implementation.

    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Service is failing, reject requests immediately
    - HALF_OPEN: Testing if service has recovered

    Transitions:
    - CLOSED -> OPEN: After failure_threshold consecutive failures
    - OPEN -> HALF_OPEN: After recovery_timeout
    - HALF_OPEN -> CLOSED: On successful request
    - HALF_OPEN -> OPEN: On failed request
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 30,
        name: str = "default",
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.name = name

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time: datetime | None = None
        self.success_count = 0

    def can_execute(self) -> bool:
        """Check if request can be executed."""
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            # Check if recovery timeout has passed
            if self.last_failure_time:
                elapsed = datetime.now(UTC) - self.last_failure_time
                if elapsed > timedelta(seconds=self.recovery_timeout):
                    self.state = CircuitState.HALF_OPEN
                    self.success_count = 0
                    logger.info(
                        "Circuit breaker entering half-open state",
                        name=self.name,
                    )
                    return True
            return False

        # HALF_OPEN - allow one request to test
        return True

    def record_success(self) -> None:
        """Record a successful request."""
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= 2:  # Require 2 successes to close
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                logger.info("Circuit breaker closed", name=self.name)
        else:
            self.failure_count = 0

    def record_failure(self) -> None:
        """Record a failed request."""
        self.failure_count += 1
        self.last_failure_time = datetime.now(UTC)

        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            logger.warning(
                "Circuit breaker re-opened after half-open failure",
                name=self.name,
            )
        elif self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warning(
                "Circuit breaker opened",
                name=self.name,
                failure_count=self.failure_count,
            )


class InvenTreeError(Exception):
    """Base exception for InvenTree errors."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        error_type: str = "UNKNOWN",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type


class TransientError(InvenTreeError):
    """Transient infrastructure error (can be retried)."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message, status_code, "TRANSIENT_INFRA")


class ValidationError(InvenTreeError):
    """Validation error (needs LLM reflection)."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message, status_code, "VALIDATION")


class BusinessRuleError(InvenTreeError):
    """Business rule violation (surface to user)."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message, status_code, "BUSINESS_RULE")


#: Words that carry no selectivity in a part search but, because DRF's search
#: filter requires EVERY whitespace-separated token to match, are enough on
#: their own to return nothing. A spoken question ("what is the stock level for
#: the ceramic capacitor") arrives with several of them attached.
_SEARCH_STOPWORDS = frozenset(
    "a an the of for in on at to is are was were do does did what which who "  # noqa: SIM905
    "whats hand any all some need want looking there "  # codespell:ignore whats
    "how many much where when why we our us i you it its this that these those "
    "have has had get show list find check see tell me about please can could "
    "stock level levels quantity qty on-hand onhand available inventory part parts "
    "number no numbers item items".split()
)

#: Below this length a query is treated as a literal name, never as a spoken
#: question. "check valve" and "level switch" are real products whose words are
#: also scaffolding; stripping them would turn an exact hit into a wrong match.
_SEARCH_LITERAL_MAX_TOKENS = 3


def _search_terms(query: str) -> str:
    """Reduce a spoken question to the tokens worth searching on.

    DRF's SearchFilter ANDs every token, so one stray article or filler word
    zeroes an otherwise good query. Short queries are passed through untouched
    (they are names, not questions), and stripping never returns nothing.
    """
    tokens = [token for token in str(query or "").split() if token]
    if len(tokens) <= _SEARCH_LITERAL_MAX_TOKENS:
        return " ".join(tokens)
    kept = [
        token
        for token in tokens
        if token.strip(".,?!").replace("'", "").casefold() not in _SEARCH_STOPWORDS
    ]
    return " ".join(kept) if kept else " ".join(tokens)


class InvenTreeClient:
    """
    Async HTTP client for InvenTree API.

    Features:
    - Automatic authentication with API token
    - Circuit breaker for resilience
    - Retry with exponential backoff for transient errors
    - Request/response logging with structlog
    - Error classification for reflection middleware

    Example usage:
        ```python
        async with get_inventree_client() as client:
            parts = await client.search_parts(query="capacitor")
            stock = await client.get_stock(part_id=42)
        ```
    """

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: int | None = None,
    ) -> None:
        """
        Initialize the InvenTree client.

        Args:
            base_url: InvenTree API base URL.
            token: API authentication token.
            timeout: Request timeout in seconds.
        """
        config = get_inventree_settings()

        self.base_url = (base_url or config.url).rstrip("/")
        self.token = token or config.token.get_secret_value()
        self.timeout = timeout or config.timeout
        self.read_cache_ttl_s = config.read_cache_ttl_s

        self._client: httpx.AsyncClient | None = None
        self._circuit_breaker = CircuitBreaker(name="inventree")
        # Short-TTL GET cache: {key: (stored_monotonic, value)}. Opt-in via
        # read_cache_ttl_s; any write clears it.
        self._read_cache: dict[str, tuple[float, Any]] = {}

        logger.info("InvenTreeClient initialized", base_url=self.base_url)

    @asynccontextmanager
    async def _get_client(self) -> AsyncGenerator[httpx.AsyncClient, None]:
        """Get or create the HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Token {self.token}",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout,
                # Keep connections warm across turns. The httpx default
                # keepalive_expiry is 5s, so voice turns spaced further apart
                # pay a fresh TCP/TLS handshake; 60s reuses a warm connection.
                limits=httpx.Limits(
                    max_keepalive_connections=20,
                    max_connections=100,
                    keepalive_expiry=60.0,
                ),
            )

        yield self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def _read_cache_key(endpoint: str, params: dict[str, Any] | None) -> str:
        """Stable cache key for a GET (endpoint + normalized params)."""
        return f"{endpoint}?{json.dumps(params or {}, sort_keys=True, default=str)}"

    def _classify_error(self, status_code: int, response_body: str) -> InvenTreeError:
        """Classify an HTTP error into the error taxonomy."""
        if status_code in (500, 502, 503, 504):
            return TransientError(
                f"InvenTree server error: {status_code}",
                status_code,
            )

        if status_code == 429:
            return TransientError("Rate limited by InvenTree", status_code)

        if status_code == 400:
            return ValidationError(
                f"Invalid request: {response_body}",
                status_code,
            )

        if status_code == 404:
            return BusinessRuleError(
                f"Resource not found: {response_body}",
                status_code,
            )

        if status_code == 403:
            return BusinessRuleError(
                f"Permission denied: {response_body}",
                status_code,
            )

        if status_code == 401:
            return BusinessRuleError(
                "Authentication failed - invalid API token",
                status_code,
            )

        return InvenTreeError(
            f"Unexpected error: {status_code} - {response_body}",
            status_code,
        )

    @retry(
        retry=retry_if_exception_type(TransientError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
    )
    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """
        Make an HTTP request to InvenTree.

        Args:
            method: HTTP method (GET, POST, PUT, PATCH, DELETE).
            endpoint: API endpoint (e.g., "/part/").
            params: Query parameters.
            json_data: JSON body for POST/PUT/PATCH.

        Returns:
            Response JSON data.

        Raises:
            TransientError: For retryable errors.
            ValidationError: For validation errors (needs reflection).
            BusinessRuleError: For business rule violations.
        """
        method_upper = method.upper()

        # Voice turns run under a read-only fence (contract §0.2): speech may
        # never execute an effect, so mutating requests fail as tool errors.
        if method_upper != "GET":
            from ai.core.tools.read_only import READ_ONLY_MESSAGE, read_only_tools_active

            if read_only_tools_active():
                raise BusinessRuleError(READ_ONLY_MESSAGE)
            # A write may change any read; drop the short-TTL cache.
            self._read_cache.clear()

        endpoint = endpoint.lstrip("/")

        # Short-TTL read cache (opt-in): serve fresh GETs without a round-trip,
        # even when the circuit breaker is open.
        cache_key: str | None = None
        if method_upper == "GET" and self.read_cache_ttl_s > 0:
            cache_key = self._read_cache_key(endpoint, params)
            hit = self._read_cache.get(cache_key)
            if hit is not None and (time.monotonic() - hit[0]) < self.read_cache_ttl_s:
                return hit[1]

        # Check circuit breaker
        if not self._circuit_breaker.can_execute():
            raise TransientError("Circuit breaker is open - InvenTree unavailable")

        logger.debug(
            "InvenTree request",
            method=method,
            endpoint=endpoint,
            params=params,
        )

        try:
            async with self._get_client() as client:
                response = await client.request(
                    method=method,
                    url=endpoint,
                    params=params,
                    json=json_data,
                )

                if response.status_code >= 400:
                    error = self._classify_error(
                        response.status_code,
                        response.text,
                    )

                    if isinstance(error, TransientError):
                        self._circuit_breaker.record_failure()

                    raise error

                self._circuit_breaker.record_success()

                result = response.json()

                logger.debug(
                    "InvenTree response",
                    endpoint=endpoint,
                    status_code=response.status_code,
                    result_count=len(result) if isinstance(result, list) else 1,
                )

                if cache_key is not None:
                    self._read_cache[cache_key] = (time.monotonic(), result)

                return result

        except httpx.TimeoutException as e:
            self._circuit_breaker.record_failure()
            raise TransientError(f"Request timeout: {e}") from e

        except httpx.ConnectError as e:
            self._circuit_breaker.record_failure()
            raise TransientError(f"Connection error: {e}") from e

    # -------------------------------------------------------------------------
    # Part Operations
    # -------------------------------------------------------------------------

    async def search_parts(
        self,
        query: str | None = None,
        category: int | None = None,
        ipn: str | None = None,
        active: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Search for parts.

        Args:
            query: Search query string (searches name, description, IPN).
            category: Filter by category ID.
            ipn: Filter by Internal Part Number.
            active: Filter by active status.
            limit: Maximum results to return.
            offset: Pagination offset.

        Returns:
            List of matching parts.
        """
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            # Detail blocks are opt-in per request (the view-level output
            # options default them off regardless of serializer defaults)
            "category_detail": "true",
        }

        if query:
            params["search"] = _search_terms(query)
        if category:
            params["category"] = category
        if ipn:
            params["IPN"] = ipn
        if active is not None:
            params["active"] = str(active).lower()

        result = await self._request("GET", "/part/", params=params)

        # Handle paginated response
        if isinstance(result, dict) and "results" in result:
            return result["results"]

        return result if isinstance(result, list) else [result]

    async def get_part(self, part_id: int) -> dict[str, Any] | None:
        """
        Get a single part by ID.

        Args:
            part_id: The part ID.

        Returns:
            Part data or None if not found.
        """
        try:
            result = await self._request("GET", f"/part/{part_id}/")
            return result if isinstance(result, dict) else None
        except BusinessRuleError as e:
            if e.status_code == 404:
                return None
            raise

    async def get_part_by_ipn(self, ipn: str) -> dict[str, Any] | None:
        """
        Get a part by Internal Part Number.

        Args:
            ipn: The Internal Part Number.

        Returns:
            Part data or None if not found.
        """
        results = await self.search_parts(ipn=ipn, limit=1)
        return results[0] if results else None

    async def create_part(
        self,
        name: str,
        category: int,
        description: str | None = None,
        ipn: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Create a new part.

        Args:
            name: Part name.
            category: Category ID.
            description: Part description.
            ipn: Internal Part Number.
            **kwargs: Additional part fields.

        Returns:
            Created part data.
        """
        data = {
            "name": name,
            "category": category,
            **kwargs,
        }

        if description:
            data["description"] = description
        if ipn:
            data["IPN"] = ipn

        result = await self._request("POST", "/part/", json_data=data)
        return result if isinstance(result, dict) else {}

    async def update_part(
        self,
        part_id: int,
        **updates: Any,
    ) -> dict[str, Any]:
        """
        Update a part.

        Args:
            part_id: The part ID.
            **updates: Fields to update.

        Returns:
            Updated part data.
        """
        result = await self._request(
            "PATCH",
            f"/part/{part_id}/",
            json_data=updates,
        )
        return result if isinstance(result, dict) else {}

    # -------------------------------------------------------------------------
    # Stock Operations
    # -------------------------------------------------------------------------

    async def get_stock(
        self,
        part_id: int | None = None,
        location: int | None = None,
        in_stock: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Get stock items.

        Args:
            part_id: Filter by part ID.
            location: Filter by location ID.
            in_stock: Filter by in-stock status.
            limit: Maximum results.
            offset: Pagination offset.

        Returns:
            List of stock items.
        """
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            # Without this the serializer returns a bare location id and leaves
            # ``location_name`` null, so no caller can say where stock actually
            # is. The detail block carries name + pathstring in the same request.
            "location_detail": "true",
        }

        if part_id:
            params["part"] = part_id
        if location:
            params["location"] = location
        if in_stock is not None:
            params["in_stock"] = str(in_stock).lower()

        result = await self._request("GET", "/stock/", params=params)

        if isinstance(result, dict) and "results" in result:
            return result["results"]

        return result if isinstance(result, list) else [result]

    async def get_stock_item(self, stock_id: int) -> dict[str, Any] | None:
        """
        Get a single stock item by ID.

        Args:
            stock_id: The stock item ID.

        Returns:
            Stock item data or None if not found.
        """
        try:
            result = await self._request("GET", f"/stock/{stock_id}/")
            return result if isinstance(result, dict) else None
        except BusinessRuleError as e:
            if e.status_code == 404:
                return None
            raise

    async def adjust_stock(
        self,
        stock_id: int,
        quantity: float,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """
        Adjust stock quantity (add or remove).

        Args:
            stock_id: The stock item ID.
            quantity: Quantity to add (positive) or remove (negative).
            notes: Notes for the stock adjustment.

        Returns:
            Updated stock item data.
        """
        data: dict[str, Any] = {
            "items": [{"pk": stock_id, "quantity": abs(quantity)}],
        }

        if notes:
            data["notes"] = notes

        endpoint = "/stock/add/" if quantity > 0 else "/stock/remove/"
        result = await self._request("POST", endpoint, json_data=data)
        return result if isinstance(result, dict) else {}

    async def transfer_stock(
        self,
        stock_id: int,
        location: int,
        quantity: float | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """
        Transfer stock to a different location.

        Args:
            stock_id: The stock item ID.
            location: Target location ID.
            quantity: Quantity to transfer (defaults to all).
            notes: Notes for the transfer.

        Returns:
            Transfer result.
        """
        item: dict[str, Any] = {"pk": stock_id}
        if quantity:
            item["quantity"] = quantity

        data: dict[str, Any] = {
            "location": location,
            "items": [item],
        }

        if notes:
            data["notes"] = notes

        result = await self._request("POST", "/stock/transfer/", json_data=data)
        return result if isinstance(result, dict) else {}

    # -------------------------------------------------------------------------
    # BOM Operations
    # -------------------------------------------------------------------------

    async def get_bom(
        self,
        part_id: int,
        include_inherited: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Get Bill of Materials for a part.

        Args:
            part_id: The parent part ID.
            include_inherited: Include inherited BOM items.

        Returns:
            List of BOM items.
        """
        params: dict[str, Any] = {
            "part": part_id,
            "inherited": str(include_inherited).lower(),
            # Consumers read sub_part_detail/part_detail; both default off at
            # the view layer and must be requested explicitly
            "part_detail": "true",
            "sub_part_detail": "true",
        }

        result = await self._request("GET", "/bom/", params=params)

        if isinstance(result, dict) and "results" in result:
            return result["results"]

        return result if isinstance(result, list) else [result]

    async def get_bom_item(self, bom_id: int) -> dict[str, Any] | None:
        """
        Get a single BOM item.

        Args:
            bom_id: The BOM item ID.

        Returns:
            BOM item data or None if not found.
        """
        try:
            result = await self._request("GET", f"/bom/{bom_id}/")
            return result if isinstance(result, dict) else None
        except BusinessRuleError as e:
            if e.status_code == 404:
                return None
            raise

    # -------------------------------------------------------------------------
    # Category Operations
    # -------------------------------------------------------------------------

    async def list_categories(
        self,
        parent: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        List part categories.

        Args:
            parent: Filter by parent category ID.
            limit: Maximum results.

        Returns:
            List of categories.
        """
        params: dict[str, Any] = {"limit": limit}

        if parent is not None:
            params["parent"] = parent

        result = await self._request("GET", "/part/category/", params=params)

        if isinstance(result, dict) and "results" in result:
            return result["results"]

        return result if isinstance(result, list) else [result]

    # -------------------------------------------------------------------------
    # Location Operations
    # -------------------------------------------------------------------------

    async def list_locations(
        self,
        parent: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        List stock locations.

        Args:
            parent: Filter by parent location ID.
            limit: Maximum results.

        Returns:
            List of locations.
        """
        params: dict[str, Any] = {"limit": limit}

        if parent is not None:
            params["parent"] = parent

        result = await self._request("GET", "/stock/location/", params=params)

        if isinstance(result, dict) and "results" in result:
            return result["results"]

        return result if isinstance(result, list) else [result]

    # -------------------------------------------------------------------------
    # Supplier Operations
    # -------------------------------------------------------------------------

    async def list_suppliers(
        self,
        active: bool = True,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        List suppliers.

        Args:
            active: Filter by active status.
            limit: Maximum results.

        Returns:
            List of suppliers.
        """
        params: dict[str, Any] = {
            "limit": limit,
            "is_supplier": "true",
        }

        if active is not None:
            params["active"] = str(active).lower()

        result = await self._request("GET", "/company/", params=params)

        if isinstance(result, dict) and "results" in result:
            return result["results"]

        return result if isinstance(result, list) else [result]

    async def get_supplier_parts(
        self,
        supplier_id: int | None = None,
        part_id: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Get supplier parts (parts available from suppliers).

        Args:
            supplier_id: Filter by supplier ID.
            part_id: Filter by part ID.
            limit: Maximum results.

        Returns:
            List of supplier parts.
        """
        params: dict[str, Any] = {
            "limit": limit,
            # Consumers read these detail blocks and the price-break list;
            # all default off at the view layer and must be requested
            # explicitly
            "part_detail": "true",
            "supplier_detail": "true",
            "manufacturer_detail": "true",
            "price_breaks": "true",
        }

        if supplier_id:
            params["supplier"] = supplier_id
        if part_id:
            params["part"] = part_id

        result = await self._request("GET", "/company/part/", params=params)

        if isinstance(result, dict) and "results" in result:
            return result["results"]

        return result if isinstance(result, list) else [result]

    # -------------------------------------------------------------------------
    # Purchase Order Operations
    # -------------------------------------------------------------------------

    async def list_purchase_orders(
        self,
        supplier_id: int | None = None,
        status: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        List purchase orders.

        Args:
            supplier_id: Filter by supplier.
            status: Filter by status code.
            limit: Maximum results.

        Returns:
            List of purchase orders.
        """
        params: dict[str, Any] = {"limit": limit}

        if supplier_id:
            params["supplier"] = supplier_id
        if status is not None:
            params["status"] = status

        result = await self._request("GET", "/order/po/", params=params)

        if isinstance(result, dict) and "results" in result:
            return result["results"]

        return result if isinstance(result, list) else [result]

    async def create_purchase_order(
        self,
        supplier: int,
        description: str | None = None,
        reference: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Create a purchase order.

        Args:
            supplier: Supplier company ID.
            description: Order description.
            reference: Reference number.
            **kwargs: Additional order fields.

        Returns:
            Created purchase order data.
        """
        data: dict[str, Any] = {
            "supplier": supplier,
            **kwargs,
        }

        if description:
            data["description"] = description
        if reference:
            data["reference"] = reference

        result = await self._request("POST", "/order/po/", json_data=data)
        return result if isinstance(result, dict) else {}

    async def get_purchase_order(self, po_id: int) -> dict[str, Any] | None:
        """
        Get a single purchase order.

        Args:
            po_id: The purchase order ID.

        Returns:
            Purchase order data or None if not found.
        """
        try:
            result = await self._request("GET", f"/order/po/{po_id}/")
            return result if isinstance(result, dict) else None
        except BusinessRuleError as e:
            if e.status_code == 404:
                return None
            raise

    async def get_purchase_order_lines(self, po_id: int) -> list[dict[str, Any]]:
        """
        Get lines for a purchase order.

        Args:
            po_id: The purchase order ID.

        Returns:
            List of PO lines.
        """
        params = {"order": po_id, "limit": 250}
        result = await self._request("GET", "/order/po-line/", params=params)

        if isinstance(result, dict) and "results" in result:
            return result["results"]
        return result if isinstance(result, list) else []

    # -------------------------------------------------------------------------
    # Sales Order Operations
    # -------------------------------------------------------------------------

    async def list_sales_orders(
        self,
        customer_id: int | None = None,
        status: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        List sales orders.

        Args:
            customer_id: Filter by customer.
            status: Filter by status code.
            limit: Maximum results.

        Returns:
            List of sales orders.
        """
        params: dict[str, Any] = {"limit": limit}

        if customer_id:
            params["customer"] = customer_id
        if status is not None:
            params["status"] = status

        result = await self._request("GET", "/order/so/", params=params)

        if isinstance(result, dict) and "results" in result:
            return result["results"]

        return result if isinstance(result, list) else [result]

    async def get_sales_order(self, so_id: int) -> dict[str, Any] | None:
        """
        Get a single sales order.

        Args:
            so_id: The sales order ID.

        Returns:
            Sales order data or None if not found.
        """
        try:
            result = await self._request("GET", f"/order/so/{so_id}/")
            return result if isinstance(result, dict) else None
        except BusinessRuleError as e:
            if e.status_code == 404:
                return None
            raise

    async def get_sales_order_lines(self, so_id: int) -> list[dict[str, Any]]:
        """
        Get lines for a sales order.

        Args:
            so_id: The sales order ID.

        Returns:
            List of SO lines.
        """
        params = {"order": so_id, "limit": 250}
        result = await self._request("GET", "/order/so-line/", params=params)

        if isinstance(result, dict) and "results" in result:
            return result["results"]
        return result if isinstance(result, list) else []

    # -------------------------------------------------------------------------
    # Build Order Operations
    # -------------------------------------------------------------------------

    async def list_build_orders(
        self,
        part_id: int | None = None,
        status: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        List build orders.

        Args:
            part_id: Filter by part.
            status: Filter by status code.
            limit: Maximum results.

        Returns:
            List of build orders.
        """
        params: dict[str, Any] = {"limit": limit}

        if part_id:
            params["part"] = part_id
        if status is not None:
            params["status"] = status

        result = await self._request("GET", "/build/", params=params)

        if isinstance(result, dict) and "results" in result:
            return result["results"]

        return result if isinstance(result, list) else [result]

    async def get_build_order(self, bo_id: int) -> dict[str, Any] | None:
        """
        Get a single build order.

        Args:
            bo_id: The build order ID.

        Returns:
            Build order data or None if not found.
        """
        try:
            result = await self._request("GET", f"/build/{bo_id}/")
            return result if isinstance(result, dict) else None
        except BusinessRuleError as e:
            if e.status_code == 404:
                return None
            raise

    async def get_build_order_allocations(self, bo_id: int) -> list[dict[str, Any]]:
        """
        Get stock allocations for a build order.

        Args:
            bo_id: The build order ID.

        Returns:
            List of stock allocations.
        """
        # /build/item/ rows are stock allocations against build lines.
        # stock_detail=true embeds stock_item_detail (off by default).
        params = {"build": bo_id, "limit": 250, "stock_detail": "true"}
        result = await self._request("GET", "/build/item/", params=params)

        if isinstance(result, dict) and "results" in result:
            return result["results"]
        return result if isinstance(result, list) else []

    # -------------------------------------------------------------------------
    # Part Parameter Operations
    # -------------------------------------------------------------------------

    async def get_part_parameters(
        self,
        part_id: int,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Get parameters for a part.

        Args:
            part_id: The part ID.
            limit: Maximum results.

        Returns:
            List of part parameters.
        """
        # Generic parameter API (upstream v430 removed the part-scoped
        # parameter endpoints): parameters attach via (model_type, model_id).
        params: dict[str, Any] = {
            "model_type": "part",
            "model_id": part_id,
            "limit": limit,
            "template_detail": "true",
        }

        result = await self._request("GET", "/parameter/", params=params)

        if isinstance(result, dict) and "results" in result:
            return result["results"]

        return result if isinstance(result, list) else [result]

    # -------------------------------------------------------------------------
    # Part Attachment Operations
    # -------------------------------------------------------------------------

    async def get_part_attachments(
        self,
        part_id: int,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Get attachments for a part.

        Args:
            part_id: The part ID.
            limit: Maximum results.

        Returns:
            List of part attachments.
        """
        # InvenTree uses a unified attachment endpoint with model_type filter
        params: dict[str, Any] = {
            "model_type": "part",
            "model_id": part_id,
            "limit": limit,
        }

        result = await self._request("GET", "/attachment/", params=params)

        if isinstance(result, dict) and "results" in result:
            return result["results"]

        return result if isinstance(result, list) else [result]

    # -------------------------------------------------------------------------
    # Where Used (Reverse BOM)
    # -------------------------------------------------------------------------

    async def get_where_used(
        self,
        part_id: int,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Get assemblies where a part is used.

        Args:
            part_id: The sub-part ID.
            limit: Maximum results.

        Returns:
            List of BOM items where this part is used.
        """
        params: dict[str, Any] = {
            "sub_part": part_id,
            "limit": limit,
        }

        result = await self._request("GET", "/bom/", params=params)

        if isinstance(result, dict) and "results" in result:
            return result["results"]

        return result if isinstance(result, list) else [result]

    # -------------------------------------------------------------------------
    # Low Stock Check
    # -------------------------------------------------------------------------

    async def check_low_stock(
        self,
        threshold: float | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Get parts with stock below minimum threshold.

        Args:
            threshold: Optional override for minimum stock threshold.
            limit: Maximum results.

        Returns:
            List of parts with low stock.
        """
        params: dict[str, Any] = {
            "low_stock": "true",
            "limit": limit,
        }

        result = await self._request("GET", "/part/", params=params)

        if isinstance(result, dict) and "results" in result:
            parts = result["results"]
        else:
            parts = result if isinstance(result, list) else [result]

        # If threshold provided, filter further
        if threshold is not None:
            parts = [p for p in parts if p.get("in_stock", 0) < threshold]

        return parts


# Module-level singleton
_client: InvenTreeClient | None = None


def get_inventree_client() -> InvenTreeClient:
    """Get the singleton InvenTree client instance."""
    global _client
    if _client is None:
        _client = InvenTreeClient()
    return _client


@asynccontextmanager
async def inventree_client() -> AsyncGenerator[InvenTreeClient, None]:
    """Context manager for InvenTree client."""
    # Don't close singleton
    yield get_inventree_client()
