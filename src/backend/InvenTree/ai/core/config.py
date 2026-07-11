"""
AIMMS Configuration Module

Centralized typed configuration using pydantic-settings.
Loads from environment variables with validation.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    AIMMS Backend Configuration.
    
    All settings are loaded from environment variables.
    Use .env file for local development.
    """
    
    model_config = SettingsConfigDict(
        env_prefix="AIMMS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )
    
    # -------------------------------------------------------------------------
    # Application Settings
    # -------------------------------------------------------------------------
    env: Literal["development", "staging", "production"] = "development"
    debug: bool = True
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "DEBUG"
    
    # -------------------------------------------------------------------------
    # Server Configuration
    # -------------------------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = 8080
    
    # -------------------------------------------------------------------------
    # Storage Paths
    # -------------------------------------------------------------------------
    data_dir: Path = Field(default=Path("./data"))
    threads_dir: Path = Field(default=Path("./data/threads"))
    checkpoints_dir: Path = Field(default=Path("./data/checkpoints"))
    cache_dir: Path = Field(default=Path("./data/cache"))
    
    @field_validator("data_dir", "threads_dir", "checkpoints_dir", "cache_dir", mode="after")
    @classmethod
    def ensure_directories(cls, v: Path) -> Path:
        """Ensure storage directories exist."""
        v.mkdir(parents=True, exist_ok=True)
        return v
    
    # -------------------------------------------------------------------------
    # Semantic Cache Configuration
    # -------------------------------------------------------------------------
    semantic_cache_enabled: bool = Field(default=True, alias="SEMANTIC_CACHE_ENABLED")
    semantic_cache_similarity_threshold: float = Field(
        default=0.92, 
        alias="SEMANTIC_CACHE_SIMILARITY_THRESHOLD",
        ge=0.0,
        le=1.0,
    )
    semantic_cache_ttl_hours: int = Field(default=24, alias="SEMANTIC_CACHE_TTL_HOURS")
    
    # -------------------------------------------------------------------------
    # HITL Configuration
    # -------------------------------------------------------------------------
    hitl_timeout_seconds: int = Field(default=300, alias="HITL_TIMEOUT_SECONDS")
    hitl_auto_reject_on_timeout: bool = Field(default=False, alias="HITL_AUTO_REJECT_ON_TIMEOUT")
    
    # -------------------------------------------------------------------------
    # Feature Flags
    # -------------------------------------------------------------------------
    feature_wf1_diagnostics: bool = Field(default=True, alias="FEATURE_WF1_DIAGNOSTICS")
    feature_wf2_sequential: bool = Field(default=True, alias="FEATURE_WF2_SEQUENTIAL")
    feature_wf3_concurrent: bool = Field(default=True, alias="FEATURE_WF3_CONCURRENT")
    feature_wf4_procurement: bool = Field(default=True, alias="FEATURE_WF4_PROCUREMENT")
    feature_wf5_cpq: bool = Field(default=True, alias="FEATURE_WF5_CPQ")
    feature_wf6_incoming_docs: bool = Field(default=True, alias="FEATURE_WF6_INCOMING_DOCS")
    feature_wf8_lookup: bool = Field(default=True, alias="FEATURE_WF8_LOOKUP")
    feature_reflection_middleware: bool = Field(default=True, alias="FEATURE_REFLECTION_MIDDLEWARE")
    
    # -------------------------------------------------------------------------
    # CORS Configuration
    # -------------------------------------------------------------------------
    cors_allowed_origins: list[str] = Field(
        default=[
            "http://localhost:5173",      # Vite dev server
            "http://localhost:3000",      # Alternative dev server
            "http://localhost:8000",      # InvenTree backend (same-origin proxy)
            "http://127.0.0.1:5173",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:8000",
        ],
        alias="CORS_ALLOWED_ORIGINS",
    )
    cors_allow_credentials: bool = Field(default=True, alias="CORS_ALLOW_CREDENTIALS")
    
    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: str | list[str]) -> list[str]:
        """Parse comma-separated origins from env var."""
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v
    
    # -------------------------------------------------------------------------
    # Demo Dataset Configuration (for testing)
    # -------------------------------------------------------------------------
    demo_dataset_path: Path = Field(default=Path("./inventree-demo-dataset"), alias="DEMO_DATASET_PATH")
    demo_dataset_json: Path = Field(default=Path("./inventree-demo-dataset/inventree_data.json"), alias="DEMO_DATASET_JSON")
    use_demo_dataset: bool = Field(default=False, alias="USE_DEMO_DATASET")
    
    # -------------------------------------------------------------------------
    # Azure OpenAI (convenience accessors - reads from AZURE_OPENAI_* env vars)
    # -------------------------------------------------------------------------
    azure_openai_endpoint: str = Field(default="", alias="AZURE_OPENAI_ENDPOINT")
    azure_openai_api_key: str = Field(default="", alias="AZURE_OPENAI_API_KEY")
    azure_openai_deployment: str = Field(default="gpt-4o", alias="AZURE_OPENAI_DEPLOYMENT")
    azure_openai_fast_deployment: str = Field(default="gpt-4o-mini", alias="AZURE_OPENAI_FAST_DEPLOYMENT")
    azure_openai_embedding_deployment: str = Field(default="text-embedding-ada-002", alias="AZURE_OPENAI_EMBEDDING_DEPLOYMENT")
    azure_openai_api_version: str = Field(default="2024-10-21", alias="AZURE_OPENAI_API_VERSION")
    
    # -------------------------------------------------------------------------
    # Azure AI Search Configuration
    # -------------------------------------------------------------------------
    azure_search_endpoint: str = Field(default="", alias="AZURE_SEARCH_ENDPOINT")
    azure_search_api_key: str = Field(default="", alias="AZURE_SEARCH_API_KEY")
    azure_search_index_name: str = Field(default="inventree-ai-conversations", alias="AZURE_SEARCH_INDEX_NAME")
    azure_search_documents_index: str = Field(default="", alias="AZURE_SEARCH_DOCUMENTS_INDEX")
    
    # -------------------------------------------------------------------------
    # Conversation Persistence Configuration
    # -------------------------------------------------------------------------
    conversation_persistence_enabled: bool = Field(default=True, alias="CONVERSATION_PERSISTENCE_ENABLED")
    conversation_search_enabled: bool = Field(default=True, alias="CONVERSATION_SEARCH_ENABLED")
    conversation_sync_batch_size: int = Field(default=50, alias="CONVERSATION_SYNC_BATCH_SIZE")

class AzureOpenAISettings(BaseSettings):
    """Azure OpenAI Configuration."""
    
    model_config = SettingsConfigDict(
        env_prefix="AZURE_OPENAI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    endpoint: str = Field(description="Azure OpenAI endpoint URL")
    api_key: SecretStr = Field(description="Azure OpenAI API key")
    deployment: str = Field(default="gpt-4o", description="Primary model deployment name")
    fast_deployment: str = Field(default="gpt-4o-mini", description="Fast model for T1 routing")
    embedding_deployment: str = Field(default="text-embedding-ada-002", description="Embedding model deployment")
    api_version: str = Field(default="2024-10-21", description="API version")


class AzureDocIntelligenceSettings(BaseSettings):
    """Azure Document Intelligence Configuration."""
    
    model_config = SettingsConfigDict(
        env_prefix="AZURE_DOC_INTELLIGENCE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    endpoint: str = Field(description="Document Intelligence endpoint URL")
    key: SecretStr = Field(description="Document Intelligence API key")


class AzureFoundryMemorySettings(BaseSettings):
    """Azure Foundry Memory Store Configuration."""
    
    model_config = SettingsConfigDict(
        env_prefix="AZURE_FOUNDRY_MEMORY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    endpoint: str = Field(description="Foundry Memory Store endpoint")
    key: SecretStr = Field(description="Foundry Memory Store API key")


class InvenTreeSettings(BaseSettings):
    """InvenTree Configuration."""
    
    model_config = SettingsConfigDict(
        env_prefix="INVENTREE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    url: str = Field(default="http://localhost:8000/api/", description="InvenTree API URL")
    token: SecretStr = Field(description="InvenTree API token")
    timeout: int = Field(default=30, description="Request timeout in seconds")


class GmailSettings(BaseSettings):
    """Gmail Integration Configuration."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    email: str = Field(default="parts@equa.work", alias="GMAIL_EMAIL")
    service_account_path: Path = Field(
        default=Path("./secrets/google-service-account.json"),
        alias="GOOGLE_SERVICE_ACCOUNT_PATH",
    )
    # Raw JSON content of the service account key — preferred in containers
    # where mounting a file is impractical.  When set, the file path is ignored.
    service_account_json: str | None = Field(
        default=None,
        alias="GOOGLE_SERVICE_ACCOUNT_JSON",
    )
    # Stored as a raw string so pydantic-settings doesn't try JSON-decoding
    # a comma-separated env var as a list field.  Access via the ``scopes``
    # property which splits it into ``list[str]``.
    scopes_raw: str = Field(
        default="https://www.googleapis.com/auth/gmail.readonly,https://www.googleapis.com/auth/gmail.modify",
        alias="GMAIL_SCOPES",
    )

    @property
    def scopes(self) -> list[str]:
        """Return Gmail scopes as a list, parsed from the comma-separated env var."""
        raw = self.scopes_raw
        if isinstance(raw, list):
            return raw
        return [s.strip() for s in raw.split(",") if s.strip()]


class DevUISettings(BaseSettings):
    """MAF DevUI Configuration."""
    
    model_config = SettingsConfigDict(
        env_prefix="DEVUI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    enabled: bool = True
    port: int = 3000


@lru_cache
def get_settings() -> Settings:
    """Get cached application settings."""
    return Settings()


@lru_cache
def get_azure_openai_settings() -> AzureOpenAISettings:
    """Get cached Azure OpenAI settings."""
    return AzureOpenAISettings()


@lru_cache
def get_azure_doc_intelligence_settings() -> AzureDocIntelligenceSettings:
    """Get cached Document Intelligence settings."""
    return AzureDocIntelligenceSettings()


@lru_cache
def get_azure_foundry_memory_settings() -> AzureFoundryMemorySettings:
    """Get cached Foundry Memory Store settings."""
    return AzureFoundryMemorySettings()


@lru_cache
def get_inventree_settings() -> InvenTreeSettings:
    """Get cached InvenTree settings."""
    return InvenTreeSettings()


@lru_cache
def get_gmail_settings() -> GmailSettings:
    """Get cached Gmail settings."""
    return GmailSettings()


@lru_cache
def get_devui_settings() -> DevUISettings:
    """Get cached DevUI settings."""
    return DevUISettings()


# Default settings instance
settings = get_settings()
