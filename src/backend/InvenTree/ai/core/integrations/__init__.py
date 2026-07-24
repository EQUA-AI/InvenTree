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

from ai.core.integrations.data_provider import (
    DemoDataProviderAsync,
    LiveDataProviderAsync,
    data_provider,
    get_data_provider,
    get_mode_status,
    is_demo_mode,
    reset_provider,
)
from ai.core.integrations.demo_dataset import (
    DemoDatasetProvider,
    get_demo_provider,
)
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
from ai.core.integrations.inventory_tools import (
    INVENTORY_TOOLS,
)
from ai.core.integrations.inventree import (
    INVENTREE_TOOLS,
    BusinessRuleError,
    InvenTreeClient,
    InvenTreeError,
    TransientError,
    ValidationError,
    get_inventree_client,
    inventree_client,
)

__all__ = [
    "EMAIL_TOOLS",
    # Unified Inventory Tools (works with demo/live)
    "INVENTORY_TOOLS",
    "INVENTREE_TOOLS",
    "BusinessRuleError",
    "DemoDataProviderAsync",
    # Demo Dataset
    "DemoDatasetProvider",
    "EmailAttachment",
    "EmailMessage",
    "EmailProvider",
    "EmailQuery",
    # Email
    "GmailClient",
    "GmailError",
    # InvenTree
    "InvenTreeClient",
    "InvenTreeError",
    "LiveDataProviderAsync",
    "TransientError",
    "ValidationError",
    "data_provider",
    # Unified Data Provider (switches between demo/live)
    "get_data_provider",
    "get_demo_provider",
    "get_gmail_client",
    "get_inventree_client",
    "get_mode_status",
    "inventree_client",
    "is_demo_mode",
    "reset_provider",
]
