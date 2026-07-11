"""
AIMMS Integrations Module

Contains external service integrations:
- InvenTree: Manufacturing inventory management
- Email: Gmail API with EmailProvider abstraction
- DemoDataset: Demo data provider for testing workflows
- DataProvider: Unified data provider (switches between demo/live)
- InventoryTools: AI-function tools that work with demo/live data
- DocIntelligence: Azure Document Intelligence for document processing (TODO)
- Foundry: Azure AI Foundry services (TODO)
"""

from ai.core.integrations.email import (
    EMAIL_TOOLS,
    EmailAttachment,
    EmailMessage,
    EmailProvider,
    EmailQuery,
    GmailClient,
    GmailError,
    get_gmail_client,
)
from ai.core.integrations.inventree import (
    INVENTREE_TOOLS,
    InvenTreeClient,
    InvenTreeError,
    TransientError,
    ValidationError,
    BusinessRuleError,
    get_inventree_client,
    inventree_client,
)
from ai.core.integrations.demo_dataset import (
    DemoDatasetProvider,
    get_demo_provider,
)
from ai.core.integrations.data_provider import (
    get_data_provider,
    data_provider,
    is_demo_mode,
    get_mode_status,
    reset_provider,
    DemoDataProviderAsync,
    LiveDataProviderAsync,
)
from ai.core.integrations.inventory_tools import (
    INVENTORY_TOOLS,
)

__all__ = [
    # InvenTree
    "InvenTreeClient",
    "InvenTreeError",
    "TransientError",
    "ValidationError",
    "BusinessRuleError",
    "get_inventree_client",
    "inventree_client",
    "INVENTREE_TOOLS",
    # Email
    "GmailClient",
    "GmailError",
    "get_gmail_client",
    "EmailProvider",
    "EmailMessage",
    "EmailAttachment",
    "EmailQuery",
    "EMAIL_TOOLS",
    # Demo Dataset
    "DemoDatasetProvider",
    "get_demo_provider",
    # Unified Data Provider (switches between demo/live)
    "get_data_provider",
    "data_provider",
    "is_demo_mode",
    "get_mode_status",
    "reset_provider",
    "DemoDataProviderAsync",
    "LiveDataProviderAsync",
    # Unified Inventory Tools (works with demo/live)
    "INVENTORY_TOOLS",
]
