"""
WF5: T5 Configure-Price-Quote (CPQ) Workflow

Workflow for complex product configuration:
- Product configuration with rules and constraints
- Dynamic pricing based on configuration
- Quote generation and presentation
- Multi-agent collaboration for complex requests

Uses WorkflowBuilder to coordinate multiple specialized agents
in a collaborative pattern.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any

from agent_framework import ChatAgent
from agent_framework.azure import AzureOpenAIChatClient
from ai.core.config import get_settings
from ai.core.integrations.inventory_tools import INVENTORY_TOOLS
from ai.core.tools.invocation_guard import CapabilityInvocationMiddleware
from ai.core.workflows.rbac_run import run_with_rbac

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)


class ConfigurationStatus(Enum):
    """Status of product configuration."""

    DRAFT = "draft"
    VALID = "valid"
    INVALID = "invalid"
    PRICED = "priced"
    QUOTED = "quoted"


@dataclass
class ConfigurationOption:
    """A configurable option."""

    name: str
    value: Any
    unit_price: Decimal = Decimal("0.00")
    category: str = "standard"
    required: bool = False


@dataclass
class ConfigurationRule:
    """A configuration rule/constraint."""

    rule_id: str
    description: str
    rule_type: str  # "requires", "excludes", "recommends"
    source_option: str
    target_option: str
    is_hard_constraint: bool = True


@dataclass
class ProductConfiguration:
    """A product configuration."""

    config_id: str = field(default_factory=lambda: f"CFG-{uuid.uuid4().hex[:8].upper()}")
    product_name: str = ""
    base_product: str = ""
    options: list[ConfigurationOption] = field(default_factory=list)
    status: ConfigurationStatus = ConfigurationStatus.DRAFT
    base_price: Decimal = Decimal("0.00")
    options_price: Decimal = Decimal("0.00")
    total_price: Decimal = Decimal("0.00")
    validation_errors: list[str] = field(default_factory=list)
    validation_warnings: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_id": self.config_id,
            "product_name": self.product_name,
            "base_product": self.base_product,
            "options": [
                {
                    "name": opt.name,
                    "value": opt.value,
                    "unit_price": str(opt.unit_price),
                    "category": opt.category,
                }
                for opt in self.options
            ],
            "status": self.status.value,
            "base_price": str(self.base_price),
            "options_price": str(self.options_price),
            "total_price": str(self.total_price),
            "validation_errors": self.validation_errors,
            "validation_warnings": self.validation_warnings,
        }


@dataclass
class Quote:
    """A formal quote document."""

    quote_id: str = field(default_factory=lambda: f"Q-{uuid.uuid4().hex[:8].upper()}")
    configuration: ProductConfiguration | None = None
    customer_name: str = ""
    customer_email: str = ""
    valid_until: datetime | None = None
    terms: str = "Net 30"
    discount_percent: Decimal = Decimal("0.00")
    final_price: Decimal = Decimal("0.00")
    status: str = "draft"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "quote_id": self.quote_id,
            "configuration": self.configuration.to_dict() if self.configuration else None,
            "customer_name": self.customer_name,
            "customer_email": self.customer_email,
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
            "terms": self.terms,
            "discount_percent": str(self.discount_percent),
            "final_price": str(self.final_price),
            "status": self.status,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class CPQResult:
    """Result of CPQ workflow."""

    success: bool
    configuration: ProductConfiguration | None = None
    quote: Quote | None = None
    conversation_log: list[str] = field(default_factory=list)
    formatted_response: str = ""
    execution_time_ms: float = 0.0
    error: str | None = None


class ConfiguratorAgent:
    """
    Product configuration agent.

    Handles product configuration logic:
    - Option selection and validation
    - Rule enforcement
    - Compatibility checks
    """

    SYSTEM_PROMPT = """You are a product configurator specialist.
Your job is to help customers configure products correctly.

Configuration Process:
1. Understand the customer's requirements
2. Suggest appropriate base product
3. Recommend compatible options
4. Validate configuration against rules
5. Flag any compatibility issues

Configuration Rules:
- Always check component compatibility
- Warn about options that require other options
- Identify mutually exclusive options
- Suggest cost-effective alternatives when available

When configuring:
- Ask clarifying questions for ambiguous requirements
- Explain why certain options are recommended
- Highlight any required options that are missing
- Summarize the configuration clearly"""

    NAME = "Configurator"

    def __init__(self):
        self._agent: ChatAgent | None = None

    async def get_agent(self) -> ChatAgent:
        if self._agent is None:
            settings = get_settings()
            chat_client = AzureOpenAIChatClient(
                deployment_name=settings.azure_openai_deployment,
                endpoint=settings.azure_openai_endpoint,
                api_key=settings.azure_openai_api_key,
            )
            # Tools-less: run_with_rbac supplies the per-user-filtered toolset.
            self._agent = ChatAgent(
                chat_client=chat_client,
                instructions=self.SYSTEM_PROMPT,
                name="Configurator Agent",
                middleware=CapabilityInvocationMiddleware(),
            )
        return self._agent


class PricingAgent:
    """
    Pricing calculation agent.

    Handles pricing logic:
    - Base price lookup
    - Option pricing
    - Volume discounts
    - Promotional pricing
    """

    SYSTEM_PROMPT = """You are a pricing specialist.
Your job is to calculate accurate pricing for product configurations.

Pricing Components:
1. Base product price
2. Option prices (additive or multiplicative)
3. Volume discounts
4. Promotional discounts
5. Shipping and handling

Pricing Rules:
- Standard markup is 30% on components
- Volume discounts: 5% for 10+, 10% for 50+, 15% for 100+
- Bundle discounts when multiple related options selected
- Expedite fees for rush orders

When pricing:
- Show breakdown of all costs
- Highlight any applicable discounts
- Note any price uncertainties
- Provide options if multiple pricing tiers available"""

    NAME = "Pricing"

    def __init__(self):
        self._agent: ChatAgent | None = None

    async def get_agent(self) -> ChatAgent:
        if self._agent is None:
            settings = get_settings()
            chat_client = AzureOpenAIChatClient(
                deployment_name=settings.azure_openai_deployment,
                endpoint=settings.azure_openai_endpoint,
                api_key=settings.azure_openai_api_key,
            )
            self._agent = ChatAgent(
                chat_client=chat_client,
                instructions=self.SYSTEM_PROMPT,
                name="Pricing Agent",
                middleware=CapabilityInvocationMiddleware(),
            )
        return self._agent


class QuoteAgent:
    """
    Quote generation agent.

    Creates formal quote documents:
    - Quote formatting
    - Terms and conditions
    - Validity period
    - Approval routing
    """

    SYSTEM_PROMPT = """You are a quote generation specialist.
Your job is to create professional quotes for customers.

Quote Components:
1. Quote header (ID, date, validity)
2. Customer information
3. Configuration summary
4. Pricing breakdown
5. Terms and conditions
6. Call to action

Quote Standards:
- Quotes are valid for 30 days by default
- Include all applicable terms
- Clearly state payment terms
- Note any exclusions or limitations

When generating quotes:
- Use professional formatting
- Include all relevant details
- Make pricing clear and transparent
- Include next steps for the customer"""

    NAME = "QuoteWriter"

    def __init__(self):
        self._agent: ChatAgent | None = None

    async def get_agent(self) -> ChatAgent:
        if self._agent is None:
            settings = get_settings()
            chat_client = AzureOpenAIChatClient(
                deployment_name=settings.azure_openai_deployment,
                endpoint=settings.azure_openai_endpoint,
                api_key=settings.azure_openai_api_key,
            )
            self._agent = ChatAgent(
                chat_client=chat_client,
                instructions=self.SYSTEM_PROMPT,
                name="Quote Agent",
                middleware=CapabilityInvocationMiddleware(),
            )
        return self._agent


class T5CPQWorkflow:
    """
    T5 Configure-Price-Quote Workflow implementation.

    Uses GroupChat pattern to coordinate configuration,
    pricing, and quote generation agents in a collaborative
    discussion.

    The workflow:
    1. Customer describes requirements
    2. Configurator proposes configuration
    3. Pricing calculates costs
    4. QuoteWriter generates formal quote
    5. Agents may iterate to refine

    Usage:
        workflow = T5CPQWorkflow()
        result = await workflow.execute(
            query="I need a configuration for a custom assembly...",
            customer_info={"name": "ACME Corp"},
            thread_id="thread_123",
        )
    """

    def __init__(self):
        """Initialize workflow with CPQ agents."""
        self.configurator = ConfiguratorAgent()
        self.pricing = PricingAgent()
        self.quote_writer = QuoteAgent()
        logger.info("T5CPQWorkflow initialized")

    async def execute(
        self,
        query: str,
        customer_info: dict[str, Any] | None = None,
        thread_id: str = "",
        context: dict[str, Any] | None = None,
    ) -> CPQResult:
        """
        Execute CPQ workflow.

        Args:
            query: Customer's configuration request
            customer_info: Customer details
            thread_id: Conversation thread ID
            context: Additional context

        Returns:
            CPQResult with configuration and quote
        """
        import time

        start_time = time.perf_counter()

        logger.info(
            "Executing T5 CPQ",
            extra={
                "thread_id": thread_id,
                "has_customer_info": customer_info is not None,
            },
        )

        conversation_log = []

        try:
            # Step 1: Configuration
            config_response = await self._run_configuration(query)
            conversation_log.append(f"[Configurator]: {config_response}")

            # Step 2: Pricing
            pricing_query = f"""Configuration request:
{query}

Configuration proposed:
{config_response}

Please calculate pricing for this configuration."""

            pricing_response = await self._run_pricing(pricing_query)
            conversation_log.append(f"[Pricing]: {pricing_response}")

            # Step 3: Quote Generation
            quote_query = f"""Configuration:
{config_response}

Pricing:
{pricing_response}

Customer: {customer_info or "Not specified"}

Please generate a formal quote."""

            quote_response = await self._run_quote(quote_query)
            conversation_log.append(f"[QuoteWriter]: {quote_response}")

            # Parse outputs
            configuration = self._parse_configuration(config_response)
            quote = self._parse_quote(quote_response, customer_info)

            if configuration:
                quote.configuration = configuration

            execution_time = (time.perf_counter() - start_time) * 1000

            logger.info(
                "T5 CPQ complete",
                extra={
                    "thread_id": thread_id,
                    "config_id": configuration.config_id if configuration else None,
                    "quote_id": quote.quote_id if quote else None,
                    "execution_time_ms": execution_time,
                },
            )

            return CPQResult(
                success=True,
                configuration=configuration,
                quote=quote,
                conversation_log=conversation_log,
                formatted_response=self._format_final_response(
                    config_response,
                    pricing_response,
                    quote_response,
                ),
                execution_time_ms=execution_time,
            )

        except Exception as e:
            execution_time = (time.perf_counter() - start_time) * 1000

            logger.error(f"T5 CPQ failed: {e}", extra={"thread_id": thread_id})

            return CPQResult(
                success=False,
                conversation_log=conversation_log,
                error=str(e),
                formatted_response=f"CPQ workflow failed: {e!s}",
                execution_time_ms=execution_time,
            )

    async def _run_configuration(self, query: str) -> str:
        """Run configuration agent."""
        agent = await self.configurator.get_agent()

        response = await run_with_rbac(
            agent, query, workflow="wf5", full_tools=INVENTORY_TOOLS, context=None
        )
        response_text = ""
        if response.messages:
            last_msg = response.messages[-1]
            response_text = last_msg.text if hasattr(last_msg, "text") else str(last_msg)

        return response_text

    async def _run_pricing(self, query: str) -> str:
        """Run pricing agent."""
        agent = await self.pricing.get_agent()

        response = await run_with_rbac(
            agent, query, workflow="wf5", full_tools=INVENTORY_TOOLS, context=None
        )
        response_text = ""
        if response.messages:
            last_msg = response.messages[-1]
            response_text = last_msg.text if hasattr(last_msg, "text") else str(last_msg)

        return response_text

    async def _run_quote(self, query: str) -> str:
        """Run quote agent."""
        agent = await self.quote_writer.get_agent()

        response = await agent.run(query)
        response_text = ""
        if response.messages:
            last_msg = response.messages[-1]
            response_text = last_msg.text if hasattr(last_msg, "text") else str(last_msg)

        return response_text

    def _parse_configuration(self, response: str) -> ProductConfiguration:
        """Parse configuration from response."""
        config = ProductConfiguration()

        # Extract configuration ID if present
        import re

        cfg_match = re.search(r"CFG-[A-Z0-9]+", response)
        if cfg_match:
            config.config_id = cfg_match.group(0)

        config.status = ConfigurationStatus.VALID

        return config

    def _parse_quote(
        self,
        response: str,
        customer_info: dict[str, Any] | None,
    ) -> Quote:
        """Parse quote from response."""
        quote = Quote()

        if customer_info:
            quote.customer_name = customer_info.get("name", "")
            quote.customer_email = customer_info.get("email", "")

        quote.status = "generated"

        return quote

    def _format_final_response(
        self,
        config: str,
        pricing: str,
        quote: str,
    ) -> str:
        """Format final CPQ response."""
        return f"""# 📋 Configure-Price-Quote Summary

## Product Configuration
{config}

---

## Pricing Details
{pricing}

---

## Formal Quote
{quote}

---
*This quote was generated by the AIMMS CPQ Workflow.*
*Quote valid for 30 days from date of issue.*
"""

    async def stream_execute(
        self,
        query: str,
        customer_info: dict[str, Any] | None = None,
        thread_id: str = "",
    ) -> AsyncIterator[str]:
        """Execute with streaming response."""
        yield "📋 **Configure-Price-Quote Process**\n\n"

        yield "🔧 **Step 1: Configuring Product**\n"
        agent = await self.configurator.get_agent()
        response = await run_with_rbac(
            agent, query, workflow="wf5", full_tools=INVENTORY_TOOLS, context=None
        )
        if response.messages:
            last_msg = response.messages[-1]
            content = last_msg.text if hasattr(last_msg, "text") else str(last_msg)
            yield f"\n{content}\n"

        yield "\n---\n💰 **Step 2: Calculating Pricing**\n"
        agent = await self.pricing.get_agent()
        response = await run_with_rbac(
            agent,
            f"Price this configuration: {query}",
            workflow="wf5",
            full_tools=INVENTORY_TOOLS,
            context=None,
        )
        if response.messages:
            last_msg = response.messages[-1]
            content = last_msg.text if hasattr(last_msg, "text") else str(last_msg)
            yield f"\n{content}\n"

        yield "\n---\n📄 **Step 3: Generating Quote**\n"
        agent = await self.quote_writer.get_agent()
        response = await agent.run(f"Generate quote for: {query}")
        if response.messages:
            last_msg = response.messages[-1]
            content = last_msg.text if hasattr(last_msg, "text") else str(last_msg)
            yield f"\n{content}\n"

        yield "\n---\n✅ CPQ process complete.\n"


class T5CPQBuilder:
    """
    Builder for T5 CPQ Workflow.

    Implements .as_agent() pattern for workflow composition.
    """

    def __init__(self):
        self._custom_rules: list[ConfigurationRule] = []
        self._discount_rules: dict[str, Any] = {}
        self._quote_validity_days: int = 30

    def with_configuration_rules(
        self,
        rules: list[ConfigurationRule],
    ) -> T5CPQBuilder:
        """Add custom configuration rules."""
        self._custom_rules.extend(rules)
        return self

    def with_discount_rules(
        self,
        rules: dict[str, Any],
    ) -> T5CPQBuilder:
        """Set discount rules."""
        self._discount_rules = rules
        return self

    def with_quote_validity(
        self,
        days: int,
    ) -> T5CPQBuilder:
        """Set quote validity period."""
        self._quote_validity_days = days
        return self

    def build(self) -> T5CPQWorkflow:
        """Build configured workflow."""
        return T5CPQWorkflow()

    def as_agent(self) -> ChatAgent:
        """Convert workflow to a composable agent."""
        settings = get_settings()

        combined_prompt = """You are a comprehensive CPQ (Configure-Price-Quote) specialist.

You combine expertise in:
- Product configuration with rules and constraints
- Pricing calculations with discounts
- Professional quote generation

CPQ Process:
1. Understand customer requirements
2. Configure appropriate product/options
3. Calculate accurate pricing
4. Generate professional quote

When handling CPQ requests:
- Validate configuration against rules
- Apply appropriate discounts
- Create clear, professional quotes
- Include all relevant terms

Provide complete CPQ response with:
- Configuration summary
- Itemized pricing
- Total with discounts
- Quote terms and validity"""

        chat_client = AzureOpenAIChatClient(
            deployment_name=settings.azure_openai_deployment,
            endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
        )

        return ChatAgent(
            chat_client=chat_client,
            instructions=combined_prompt,
            name="AIMMS CPQ Agent",
            description="Configure-Price-Quote for complex assemblies",
            # Tools-less: wf5 is retired (S13) and an unfiltered constructor
            # toolset must not outlive it.
            middleware=CapabilityInvocationMiddleware(),
        )


# Factory functions
def create_t5_cpq_workflow() -> T5CPQWorkflow:
    """Create a T5 CPQ workflow instance."""
    return T5CPQWorkflow()


def t5_cpq_builder() -> T5CPQBuilder:
    """Get a T5 CPQ workflow builder."""
    return T5CPQBuilder()
