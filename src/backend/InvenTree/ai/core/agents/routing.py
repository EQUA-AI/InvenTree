"""
AIMMS Routing Utilities

Core routing components for intent classification and fast-path routing.
This module provides the building blocks used by OrchestratorAgent.

Components:
- WorkflowType: Enum of available workflow types
- RoutingDecision: Dataclass representing a routing decision
- FastPathRouter: Pattern-based fast routing for simple queries
- IntentClassifier: LLM-based intent classification
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar

import numpy as np
from ai.core.config import get_settings
from ai.core.faults import log_fault
from ai.core.integrations.data_provider import get_data_provider
from openai import AsyncAzureOpenAI

if TYPE_CHECKING:
    from agent_framework import ChatAgent

logger = logging.getLogger(__name__)


class AzureOpenAIEmbeddingClient:
    """Wrapper for Azure OpenAI Embeddings using the official SDK."""

    def __init__(self, deployment_name: str, endpoint: str, api_key: str, api_version: str):
        self.client = AsyncAzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
        )
        self.deployment_name = deployment_name

    async def embed(self, inputs: list[str]) -> list[list[float]]:
        """Generate embeddings for a list of texts."""
        response = await self.client.embeddings.create(input=inputs, model=self.deployment_name)
        # S12 (WP-B2): routing embeddings are provider spend the turn ledger
        # was blind to (a no-op outside a bound turn).
        from ai.core.usage import record_usage

        usage = getattr(response, "usage", None)
        if usage is not None:
            record_usage(
                "embeddings",
                {
                    "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                    "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
                },
            )
        return [data.embedding for data in response.data]


class WorkflowType(Enum):
    """Workflow types for routing decisions."""

    # T1: Simple lookups (fast-path)
    T1_LOOKUP = "wf8_lookup"

    # T2: Parts/BOM analysis (sequential)
    T2_PARTS_ANALYSIS = "wf2_parts_analysis"

    # T3: Multi-source research (concurrent)
    T3_RESEARCH = "wf3_research"

    # T4: Procurement with HITL
    T4_PROCUREMENT = "wf4_procurement"

    # T5: Configure-Price-Quote (group chat)
    T5_CPQ = "wf5_cpq"

    # T6: Complex diagnostics (magentic)
    T6_DIAGNOSTICS = "wf1_diagnostics"

    # T7: Incoming documents
    T7_DOCUMENTS = "wf6_documents"

    # Fallback: general conversation
    GENERAL = "general_conversation"


# Mapping from WorkflowType to workflow registry ID
WORKFLOW_ID_MAP: dict[WorkflowType, str] = {
    WorkflowType.T1_LOOKUP: "wf8",
    WorkflowType.T2_PARTS_ANALYSIS: "wf2",
    WorkflowType.T3_RESEARCH: "wf3",
    WorkflowType.T4_PROCUREMENT: "wf4",
    # wf5 is retired (S13); a model that still emits the CPQ label falls
    # back to the read-only lookup rail instead of a dead registry id.
    WorkflowType.T5_CPQ: "wf8",
    WorkflowType.T6_DIAGNOSTICS: "wf1",
    WorkflowType.T7_DOCUMENTS: "wf6",
}


@dataclass
class RoutingDecision:
    """Result of routing analysis."""

    workflow_type: WorkflowType
    confidence: float
    reasoning: str
    fast_path_result: dict[str, Any] | None = None
    extracted_entities: dict[str, Any] = field(default_factory=dict)
    use_fast_path: bool = False

    def get_workflow_id(self) -> str | None:
        """Get the workflow registry ID for this decision."""
        return WORKFLOW_ID_MAP.get(self.workflow_type)


class FastPathRouter:
    """
    T1 Fast-path routing for simple database queries.

    Uses pattern matching to identify queries that can be answered
    directly from InvenTree without LLM involvement.

    Patterns supported:
    - Stock queries: "how much stock of X", "do we have X in stock"
    - Part info: "tell me about part X", "part details for X"
    - BOM queries: "what's the BOM for X", "components of X"
    - Location queries: "where is X", "location of X"
    """

    # Patterns for stock queries
    STOCK_GROUP_PATTERNS: ClassVar[list[str]] = [
        r"how many\s+(.+?)\s+do we have\s+(?:in stock|available|on hand)(?:\?|$)",
    ]

    STOCK_PATTERNS: ClassVar[list[str]] = [
        r"(?:how much|how many|what(?:'s| is) the)\s+(?:stock|inventory|quantity)\s+(?:of|for)\s+(.+?)(?:\?|$)",
        r"(?:do we have|is there|check)\s+(?:any\s+)?(.+?)\s+(?:in stock|available|on hand)",
        r"stock\s+(?:level|count|quantity)\s+(?:of|for)\s+(.+?)(?:\?|$)",
        r"(?:availability|available)\s+(?:of|for)\s+(.+?)(?:\?|$)",
        r"(?:what|which)\s+(?:types|kinds|sorts|varieties)\s+(?:of\s+)?(.+?)\s+(?:are|is)\s+(?:there\s+)?(?:in\s+)*stock(?:\?|$)",
        r"(?:what|which)\s+(?:types|kinds|sorts|varieties)\s+(?:of\s+)?(.+?)\s+(?:do we have|are available)(?:\?|$)",
    ]

    # Patterns for part info queries
    PART_PATTERNS: ClassVar[list[str]] = [
        r"(?:what is|tell me about|info(?:rmation)? (?:on|about))\s+(?:part\s+)?(.+?)(?:\?|$)",
        r"(?:part|component)\s+(?:details|info|information)\s+(?:for|of)\s+(.+?)(?:\?|$)",
        r"(?:get|show|find)\s+(?:part\s+)?(?:details|info)\s+(?:for|of|on)\s+(.+?)(?:\?|$)",
    ]

    # Patterns for BOM queries
    BOM_PATTERNS: ClassVar[list[str]] = [
        r"(?:what(?:'s| is) the|show|get)\s+bom\s+(?:for|of)\s+(.+?)(?:\?|$)",
        r"(?:bill of materials|components)\s+(?:for|of)\s+(.+?)(?:\?|$)",
        r"(?:what parts|which components)\s+(?:are in|make up|compose)\s+(.+?)(?:\?|$)",
    ]

    # Patterns for location queries
    LOCATION_PATTERNS: ClassVar[list[str]] = [
        r"(?:where is|locate|find)\s+(.+?)(?:\?|$)",
        r"(?:location|position)\s+(?:of|for)\s+(.+?)(?:\?|$)",
        r"(?:which (?:bin|shelf|location))\s+(?:has|contains)\s+(.+?)(?:\?|$)",
    ]

    @classmethod
    def compile_patterns(cls) -> dict[str, list[re.Pattern]]:
        """Compile all patterns for efficient matching."""
        return {
            "stock_group": [re.compile(p, re.IGNORECASE) for p in cls.STOCK_GROUP_PATTERNS],
            "stock": [re.compile(p, re.IGNORECASE) for p in cls.STOCK_PATTERNS],
            "part": [re.compile(p, re.IGNORECASE) for p in cls.PART_PATTERNS],
            "bom": [re.compile(p, re.IGNORECASE) for p in cls.BOM_PATTERNS],
            "location": [re.compile(p, re.IGNORECASE) for p in cls.LOCATION_PATTERNS],
        }

    def __init__(self):
        self.patterns = self.compile_patterns()
        self._data_provider = None

    async def _get_client(self):
        """Get data provider lazily (respects USE_DEMO_DATASET)."""
        if self._data_provider is None:
            self._data_provider = get_data_provider()
        return self._data_provider

    async def try_fast_path(
        self,
        query: str,
        thread_id: str,
    ) -> RoutingDecision | None:
        """
        Attempt to answer query via fast-path.

        Returns RoutingDecision with result if fast-path succeeds,
        None if query requires LLM processing.
        """
        query_lower = query.lower().strip()

        # Try each pattern category
        for category, patterns in self.patterns.items():
            for pattern in patterns:
                match = pattern.search(query_lower)
                if match:
                    entity = match.group(1).strip()
                    logger.info(
                        "Fast-path match",
                        extra={
                            "category": category,
                            "entity": entity,
                            "thread_id": thread_id,
                        },
                    )

                    result = await self._execute_fast_path(category, entity)
                    if result:
                        return RoutingDecision(
                            workflow_type=WorkflowType.T1_LOOKUP,
                            confidence=0.95,
                            reasoning=f"Fast-path {category} query for '{entity}'",
                            fast_path_result=result,
                            extracted_entities={"entity": entity, "category": category},
                            use_fast_path=True,
                        )

        return None

    async def _execute_fast_path(
        self,
        category: str,
        entity: str,
    ) -> dict[str, Any] | None:
        """Execute the appropriate InvenTree query."""
        try:
            client = await self._get_client()

            if category == "stock_group":
                # This wording asks for the total across every matching part,
                # not the stock of the first fuzzy-search hit. Search results
                # carry InvenTree's authoritative per-part `in_stock` figure.
                parts = await client.search_parts(entity, limit=100)
                if not parts or len(parts) >= 100:
                    # At the provider cap we cannot prove the set is complete;
                    # fall through to the normal aggregate-capable workflow.
                    return None
                quantities = [part.get("in_stock") for part in parts]
                if any(
                    not isinstance(quantity, (int, float)) or isinstance(quantity, bool)
                    for quantity in quantities
                ):
                    return None
                return {
                    "type": "stock_group",
                    "label": entity,
                    "part_count": len(parts),
                    "total_quantity": sum(float(quantity) for quantity in quantities),
                }

            if category == "stock":
                # Search for part and get stock levels
                parts = await client.search_parts(entity, limit=5)
                if parts:
                    part = parts[0]
                    stock = await client.get_stock_items(part_id=part["pk"])
                    return {
                        "type": "stock_check",
                        "part": part,
                        "stock_items": stock,
                        "total_quantity": sum(s.get("quantity", 0) for s in stock),
                    }

            elif category == "part":
                # Get part details
                parts = await client.search_parts(entity, limit=1)
                if parts:
                    part = parts[0]
                    details = await client.get_part(part["pk"])
                    return {
                        "type": "part_details",
                        "part": details,
                    }

            elif category == "bom":
                # Get BOM for part
                parts = await client.search_parts(entity, limit=1)
                if parts:
                    part = parts[0]
                    bom = await client.get_bom_items(part["pk"])
                    return {
                        "type": "bom",
                        "part": part,
                        "bom_items": bom,
                    }

            elif category == "location":
                # Find part location
                parts = await client.search_parts(entity, limit=1)
                if parts:
                    part = parts[0]
                    stock = await client.get_stock_items(part_id=part["pk"])
                    locations = [
                        {
                            "location": s.get("location_detail", {}).get("name", "Unknown"),
                            "quantity": s.get("quantity", 0),
                        }
                        for s in stock
                    ]
                    return {
                        "type": "location",
                        "part": part,
                        "locations": locations,
                    }

        except Exception as e:
            log_fault(logger, "Fast-path execution failed", e, stage="routing")

        return None


class IntentClassifier:
    """
    LLM-based intent classification for complex queries.

    Uses a lightweight prompt to classify user intent into workflow types.
    """

    CLASSIFICATION_PROMPT = """You are an intent classifier for a manufacturing intelligence system.
Analyze the user's query and classify it into one of these workflow types:

WORKFLOW TYPES:
1. T1_LOOKUP - Simple database queries: stock levels, part info, locations, basic BOM, kanban board, task management
2. T2_PARTS_ANALYSIS - Complex parts/BOM analysis, compatibility checks, alternatives
3. T3_RESEARCH - Multi-source research: specifications, supplier info, pricing
4. T4_PROCUREMENT - Purchasing, ordering, vendor quotes (requires approval)
6. T6_DIAGNOSTICS - Complex problem diagnosis, troubleshooting, root cause analysis
7. T7_DOCUMENTS - Processing incoming documents: RFQs, purchase orders, invoices
8. GENERAL - General conversation, greetings, unclear intent

IMPORTANT: Requests involving email, PDF generation, sending documents, generating
order/BOM PDFs, creating RFQs (requests for quote), sending work orders, kanban cards,
task board, task management, or searching manuals/datasheets/documentation for
troubleshooting, error codes, maintenance, or technical specs should be classified as
T1_LOOKUP (the lookup agent has email, PDF, kanban, and document search tools).
Available document types for PDF generation: sales_order, purchase_order, bom, quote, rfq, work_order.
Do NOT classify email, PDF, kanban, or document/manual lookup requests as GENERAL.

Consider the conversation context when classifying.

USER CONTEXT:
{user_context}

CONVERSATION SUMMARY:
{conversation_summary}

USER QUERY:
{query}

Respond with a JSON object:
{{
    "workflow_type": "<one of the types above>",
    "confidence": <0.0-1.0>,
    "reasoning": "<brief explanation>",
    "entities": {{
        "part_names": ["..."],
        "part_numbers": ["..."],
        "quantities": ["..."],
        "actions": ["..."]
    }}
}}

Only output the JSON object, no other text."""

    def __init__(self, model_client=None):
        """Initialize classifier with optional model client."""
        self._model_client = model_client
        self._agent = None

    async def _get_agent(self) -> ChatAgent:
        """Get or create the classification agent."""
        if self._agent is None:
            # Classification is a short JSON label, not reasoning. Running it on
            # the main deployment (a reasoning model in this deployment) put a
            # slow round trip in front of every turn the regex and semantic
            # routers missed; the fast deployment answers the same question.
            from ai.core.agents.factory import AgentSpec, build_agent
            from ai.core.model_policy import ModelPurpose, select_deployment

            self._agent = build_agent(
                AgentSpec(
                    deployment=select_deployment(ModelPurpose.FALLBACK_CLASSIFIER),
                    instructions="You are an intent classifier. Always respond with valid JSON only.",
                    name="Intent Classifier",
                    workflow="routing",
                )
            )

        return self._agent

    async def classify(
        self,
        query: str,
        user_context: str = "",
        conversation_summary: str = "",
    ) -> RoutingDecision:
        """Classify user intent using LLM."""
        prompt = self.CLASSIFICATION_PROMPT.format(
            query=query,
            user_context=user_context or "No user context available",
            conversation_summary=conversation_summary or "New conversation",
        )

        try:
            agent = await self._get_agent()
            response = await agent.run(prompt)

            # S37: the fallback classifier is a real provider call the turn
            # ledger was blind to. MAF response — usage lives on
            # usage_details, extracted by the shared helper.
            from ai.core.usage import maf_response_usage_metrics, record_usage

            record_usage("routing_classifier", maf_response_usage_metrics(response))

            # Extract response text
            response_text = ""
            if response.messages:
                last_msg = response.messages[-1]
                response_text = last_msg.text if hasattr(last_msg, "text") else str(last_msg)

            # Parse JSON response
            # Handle potential markdown code blocks
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0]
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0]

            result = json.loads(response_text.strip())

            workflow_type = WorkflowType[result.get("workflow_type", "GENERAL")]

            return RoutingDecision(
                workflow_type=workflow_type,
                confidence=float(result.get("confidence", 0.5)),
                reasoning=result.get("reasoning", "LLM classification"),
                extracted_entities=result.get("entities", {}),
                use_fast_path=False,
            )

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse classification response: {e}")
            return RoutingDecision(
                workflow_type=WorkflowType.GENERAL,
                confidence=0.3,
                reasoning="Failed to parse LLM response, defaulting to general",
            )

        except Exception as e:
            # The reasoning field travels into telemetry; a provider error's
            # text has no place there, only its type.
            log_fault(logger, "Intent classification failed", e, stage="routing")
            return RoutingDecision(
                workflow_type=WorkflowType.GENERAL,
                confidence=0.2,
                reasoning=f"Classification error ({type(e).__name__}), defaulting to general",
            )


def format_fast_path_response(decision: RoutingDecision) -> str:
    """
    Format fast-path result as natural language response.

    This is a utility function that can be used by any agent
    to format fast-path results consistently.
    """
    result = decision.fast_path_result
    if not result:
        return "Unable to retrieve information."

    result_type = result.get("type")

    if result_type == "stock_check":
        part = result.get("part", {})
        total = result.get("total_quantity", 0)
        part_name = part.get("name", "Unknown part")
        return f"**{part_name}** has **{total} units** in stock."

    elif result_type == "part_details":
        part = result.get("part", {})
        name = part.get("name", "Unknown")
        description = part.get("description", "No description")
        category = part.get("category_detail", {}).get("name", "Uncategorized")
        return f"**{name}**\n- Category: {category}\n- Description: {description}"

    elif result_type == "bom":
        part = result.get("part", {})
        bom_items = result.get("bom_items", [])
        part_name = part.get("name", "Unknown")
        if not bom_items:
            return f"**{part_name}** has no BOM defined."
        bom_list = "\n".join(
            f"- {item.get('sub_part_detail', {}).get('name', 'Unknown')}: "
            f"{item.get('quantity', 1)} units"
            for item in bom_items[:10]
        )
        return f"**BOM for {part_name}:**\n{bom_list}"

    elif result_type == "location":
        part = result.get("part", {})
        locations = result.get("locations", [])
        part_name = part.get("name", "Unknown")
        if not locations:
            return f"**{part_name}** has no recorded stock locations."
        loc_list = "\n".join(
            f"- {loc['location']}: {loc['quantity']} units" for loc in locations[:10]
        )
        return f"**Locations for {part_name}:**\n{loc_list}"

    return "Query completed successfully."


class SemanticRouter:
    """
    Embedding-based router for common intents.

    Uses vector similarity to route queries to workflows based on
    canonical examples, reducing the need for LLM calls.
    """

    # Canonical examples for each workflow
    ROUTES: ClassVar[dict[WorkflowType, list[str]]] = {
        WorkflowType.T1_LOOKUP: [
            "how much stock of X",
            "do we have X in stock",
            "inventory level for X",
            "which part has the highest stock",
            "which fastener has the most stock",
            "what item has the lowest inventory",
            "rank parts by stock quantity",
            "top five parts by stock level",
            "where is part X located",
            "what is the bin location for X",
            "show me the BOM for X",
            "what are the components of X",
            "tell me about part X",
            "specifications for X",
            "send an email to X",
            "email the purchase order to X",
            "generate a sales order PDF",
            "create a PDF for the BOM",
            "generate and email a quote",
            "send a purchase order PDF to X",
            "make a PDF of the order and send it",
            "create an RFQ and send it to the supplier",
            "request for quote for these parts",
            "send an RFQ to X",
            "generate an RFQ PDF",
            "ask the supplier for pricing",
            "send a work order to X",
            "generate a work order PDF",
            "email the work order",
            "show my kanban board",
            "list kanban cards",
            "create a kanban card for X",
            "move card X to in-progress",
            "what tasks are overdue",
            "update the kanban card",
            "archive the card",
            "show the board summary",
            "what does error code E04 mean",
            "troubleshoot the compressor overheating",
            "check the user manual for maintenance steps",
            "look up fault code F-217 in the manual",
            "what does the manual say about wiring",
            "how do I calibrate the sensor",
        ],
        WorkflowType.T2_PARTS_ANALYSIS: [
            "find alternatives for X",
            "what parts are compatible with X",
            "analyze the BOM for X",
            "suggest substitutes for X",
            "compare part X and Y",
            "check for obsolete parts in BOM X",
        ],
        WorkflowType.T3_RESEARCH: [
            "find suppliers for X",
            "who sells X",
            "get pricing for X",
            "research specs for X",
            "find datasheet for X",
            "what is the lead time for X",
        ],
        WorkflowType.T4_PROCUREMENT: [
            "order 10 units of X",
            "create a PO for X",
            "buy more X",
            "request quote for X",
            "purchase X",
            "restock X",
        ],
        WorkflowType.T6_DIAGNOSTICS: [
            "diagnose issue with X",
            "troubleshoot failure in X",
            "why is X not working",
            "analyze root cause of X",
            "debug X",
        ],
        WorkflowType.T7_DOCUMENTS: [
            "process this invoice",
            "read this PO",
            "extract data from this PDF",
            "analyze this datasheet",
        ],
    }

    def __init__(self, embedding_client=None):
        self._client = embedding_client
        self._index: dict[WorkflowType, list[list[float]]] = {}
        self._initialized = False

    async def _get_client(self):
        """Get embedding client lazily."""
        if self._client is None:
            settings = get_settings()
            self._client = AzureOpenAIEmbeddingClient(
                deployment_name=settings.azure_openai_embedding_deployment,
                endpoint=settings.azure_openai_endpoint,
                api_key=settings.azure_openai_api_key,
                api_version=settings.azure_openai_api_version,
            )
        return self._client

    async def initialize(self):
        """Build the embedding index. Never raises: this router is optional.

        Everything - including client construction - sits inside the guard.
        The 2026-07-28 outage came from the one line that did not: with Azure
        config missing, ``AzureOpenAIEmbeddingClient`` raised in its
        constructor, above the old try, and a router whose own comment
        promised "fallback to other routers" instead failed every turn.
        """
        if self._initialized:
            return

        try:
            client = await self._get_client()

            # Flatten routes for batch embedding
            all_texts = []
            mapping = []  # (workflow_type, index_in_list)

            for wf_type, examples in self.ROUTES.items():
                for example in examples:
                    all_texts.append(example)
                    mapping.append(wf_type)

            # Note: Agent Framework embedding client might return different
            # formats. We assume it returns a list of embeddings or similar.
            embeddings = await client.embed(all_texts)

            # Organize into index
            for i, embedding in enumerate(embeddings):
                wf_type = mapping[i]
                if wf_type not in self._index:
                    self._index[wf_type] = []
                self._index[wf_type].append(embedding)

            self._initialized = True
            logger.info("Semantic router initialized with %d examples", len(all_texts))

        except Exception as e:
            # Message-free on purpose: provider errors can carry credentials.
            log_fault(logger, "Semantic router initialization failed", e, stage="routing")

    async def route(self, query: str, threshold: float = 0.82) -> RoutingDecision | None:
        """
        Route query using vector similarity.

        Args:
            query: User query
            threshold: Similarity threshold (0.0-1.0)

        Returns:
            RoutingDecision if match found above threshold, else None
        """
        try:
            if not self._initialized:
                await self.initialize()
                if not self._initialized:
                    return None

            client = await self._get_client()
            query_embedding = (await client.embed([query]))[0]

            best_score = -1.0
            best_wf = None

            # Find best match
            # We use simple cosine similarity
            q_vec = np.array(query_embedding)
            q_norm = np.linalg.norm(q_vec)

            if q_norm == 0:
                return None

            for wf_type, embeddings in self._index.items():
                for emb in embeddings:
                    vec = np.array(emb)
                    norm = np.linalg.norm(vec)
                    if norm > 0:
                        score = np.dot(q_vec, vec) / (q_norm * norm)
                        if score > best_score:
                            best_score = score
                            best_wf = wf_type

            if best_wf and best_score >= threshold:
                return RoutingDecision(
                    workflow_type=best_wf,
                    confidence=float(best_score),
                    reasoning=f"Semantic match with score {best_score:.2f}",
                    use_fast_path=False,
                )

        except Exception as e:
            log_fault(logger, "Semantic routing failed", e, stage="routing")

        return None


_DOCUMENT_REFERENCE = re.compile(
    r"\b(?:manuals?|documentation|documents?|datasheets?|uploaded\s+(?:files?|pdfs?|documents?))\b",
    re.IGNORECASE,
)
_DOCUMENT_LOOKUP_CUE = re.compile(
    r"\b(?:according\s+to|based\s+on|per\s+(?:the\s+)?|what\s+(?:does|do)|how\s+often|where\s+in)\b",
    re.IGNORECASE,
)
_DOCUMENT_ACTION_REQUEST = re.compile(
    r"^\s*(?:(?:please|kindly)\s+|(?:(?:can|could|would|will)\s+you\s+))?"
    r"(?:process|extract|parse|ingest|upload|summari[sz]e|analy[sz]e|create|generate|send|email|order|buy|purchase|update|delete|approve|reject)\b",
    re.IGNORECASE,
)


def is_explicit_document_lookup(message: str) -> bool:
    """Return whether the user asks what an existing document says."""
    return bool(
        not _DOCUMENT_ACTION_REQUEST.search(message)
        and _DOCUMENT_REFERENCE.search(message)
        and _DOCUMENT_LOOKUP_CUE.search(message)
    )


def is_document_inventory_question(message: str) -> bool:
    """Return whether the user asks WHICH documents exist (S8a).

    "What manuals do you have for the HX-200?" names documents but carries
    no content cue, so it used to fall through to semantic search and get a
    similarity answer to a registry question. The shape itself is shared
    with the intent classifier (``TaskIntent.SOURCE_INVENTORY``) so routing
    and classification cannot drift.
    """
    from ai.core.analysis.intent import is_source_inventory_question

    return bool(
        not _DOCUMENT_ACTION_REQUEST.search(message) and is_source_inventory_question(message)
    )


class UnifiedRouter:
    """
    Unified router that combines FastPath, Semantic, and LLM routing.

    Strategy:
    1. Try FastPath (regex) - Very fast, high precision
    2. Try Semantic (embeddings) - Fast, good for common intents
    3. Try LLM (classifier) - Slower, handles complex/ambiguous queries
    """

    def __init__(self):
        self.fast_path = FastPathRouter()
        self.semantic = SemanticRouter()
        self.classifier = IntentClassifier()

    async def route(
        self, message: str, thread_id: str, context: dict[str, Any] | None = None
    ) -> RoutingDecision:
        """Route a message to a workflow.

        The first two strategies are optimisations, not dependencies: each is
        guarded so its failure degrades to the next rather than failing the
        turn. The routers guard themselves too, but this boundary is what makes
        the property structural instead of a habit every router must remember.
        """
        # Inventory first: a pure inventory shape ("what manuals do you
        # have") is registry work; content shapes keep their exact path.
        if is_document_inventory_question(message):
            return RoutingDecision(
                workflow_type=WorkflowType.T1_LOOKUP,
                confidence=1.0,
                reasoning="Document inventory question",
                use_fast_path=False,
            )
        if is_explicit_document_lookup(message):
            return RoutingDecision(
                workflow_type=WorkflowType.T1_LOOKUP,
                confidence=1.0,
                reasoning="Explicit existing-document lookup",
                use_fast_path=False,
            )

        # 1. Fast Path (regex)
        try:
            fast_result = await self.fast_path.try_fast_path(message, thread_id)
            if fast_result:
                return fast_result
        except Exception as e:
            log_fault(logger, "Fast-path routing failed", e, stage="routing")

        # 2. Semantic Routing (embeddings; high threshold to avoid false positives)
        try:
            semantic_result = await self.semantic.route(message, threshold=0.85)
            if semantic_result:
                return semantic_result
        except Exception as e:
            log_fault(logger, "Semantic routing failed", e, stage="routing")

        # 3. LLM Classification. classify() answers GENERAL on its own
        # failures; this guard exists so routing as a whole can never raise.
        try:
            return await self.classifier.classify(
                query=message,
                user_context=str(context) if context else "",
                conversation_summary=context.get("summary", "") if context else "",
            )
        except Exception as e:
            log_fault(logger, "Intent classification failed", e, stage="routing")
            return RoutingDecision(
                workflow_type=WorkflowType.GENERAL,
                confidence=0.0,
                reasoning="All routing strategies failed; defaulting to general",
            )
