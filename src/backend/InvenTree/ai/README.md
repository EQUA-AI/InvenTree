# AIMMS Backend

**AI-Powered Intelligent Manufacturing Management System**

A production-ready multi-agent system built on Microsoft Agent Framework (MAF) for intelligent manufacturing operations.

## Features

- 🤖 **Multi-Agent Architecture**: 12 specialized agents for different manufacturing domains
- 🔄 **6 Workflow Patterns**: From simple lookups to complex collaborative diagnostics
- 🧠 **Semantic Memory**: Problem-solution caching with HITL-safe rules
- 📧 **Email Integration**: Gmail inbox processing for parts@equa.work
- 📄 **Document Intelligence**: Azure DI for invoice and technical document processing
- 🎯 **AG-UI Transparency**: Full event emission for DevUI integration
- ✅ **Human-in-the-Loop**: Approval workflows for critical operations

## Architecture

### Tiered Complexity Model (T1-T6)

| Tier | Complexity | Workflow | Example |
|------|------------|----------|---------|
| T1 | Single-agent fast-path | WF8 Lookup | "What is part IPN-12345?" |
| T2 | Sequential multi-agent | WF2 | "Find stock and check BOM" |
| T3 | Concurrent multi-agent | WF3 | "Parallel supplier queries" |
| T4 | HITL approval | WF4 Procurement | "Create purchase order" |
| T5 | Group chat | WF5 CPQ | "Configure and quote assembly" |
| T6 | Magentic orchestration | WF1 Diagnostics | "Why is line 3 down?" |

### Workflow Overview

- **WF1**: Diagnostics (T6) - Complex troubleshooting with MagenticBuilder
- **WF2**: Sequential (T2) - Step-by-step multi-agent processing
- **WF3**: Concurrent (T3) - Parallel agent execution
- **WF4**: Procurement (T4) - Purchase orders with HITL approval
- **WF5**: CPQ (T5) - Configure-Price-Quote with GroupChat
- **WF6**: Incoming Documents - Email attachment processing
- **WF8**: Lookup (T1) - Fast single-agent queries

## Quick Start

### Prerequisites

- Python 3.12+
- Docker (for InvenTree)
- Azure subscription (for Document Intelligence, Foundry)
- Google Cloud project (for Gmail API)

### Setup

1. **Clone and install**:
   ```bash
   git clone <repository>
   cd aimms-backend
   pip install -e ".[dev]"
   ```

2. **Configure environment**:
   ```bash
   cp .env.template .env
   # Edit .env with your credentials
   ```

3. **Start InvenTree** (with demo dataset):
   ```bash
   docker-compose up -d
   ```

4. **Run the server**:
   ```bash
   uvicorn aimms_backend.api:app --reload --port 8080
   ```

5. **Open DevUI** (optional):
   ```bash
   # DevUI runs on port 3000
   ```

### Development Container

This project includes a DevContainer configuration for VS Code:

1. Open in VS Code
2. Click "Reopen in Container" when prompted
3. Dependencies install automatically

## Project Structure

```
aimms_backend/
├── config.py                  # Typed settings (pydantic-settings)
├── agents/                    # 12 agent definitions
│   ├── router.py              # T1 routing agent
│   ├── diagnostics.py         # T6 diagnostics
│   └── ...
├── workflows/                 # WF1-WF6, WF8
│   ├── registry.py            # Workflow registry with .as_agent()
│   ├── wf1_diagnostics.py
│   └── ...
├── memory/
│   ├── providers/             # 4 ContextProviders
│   ├── foundry_store.py       # Foundry Memory Store
│   └── semantic_cache.py      # Problem-solution cache
├── middleware/
│   ├── observability.py       # Logging, metrics
│   ├── reflection.py          # LLM error recovery
│   └── hitl.py                # Human approval
├── integrations/
│   ├── inventree/             # InvenTree REST client
│   ├── email/                 # Gmail + EmailProvider
│   ├── doc_intelligence/      # Azure DI
│   └── foundry/               # Foundry services
├── infrastructure/
│   ├── message_store.py       # ChatMessageStoreProtocol
│   ├── checkpoints.py         # Workflow checkpoints
│   └── idempotency.py         # Idempotent operations
├── api/                       # FastAPI endpoints
└── events/                    # AG-UI event emission
```

## Configuration

All configuration is via environment variables. See `.env.template` for full list.

### Required Variables

| Variable | Description |
|----------|-------------|
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key |
| `INVENTREE_URL` | InvenTree API URL |
| `INVENTREE_TOKEN` | InvenTree API token |
| `GMAIL_EMAIL` | Gmail account (parts@equa.work) |
| `GOOGLE_SERVICE_ACCOUNT_PATH` | Path to service account JSON |

## Testing

```bash
# Run all tests
pytest

# Run unit tests only
pytest -m unit

# Run integration tests (requires InvenTree)
pytest -m integration

# Run with coverage
pytest --cov=aimms_backend --cov-report=html
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/chat` | POST | Main conversation with SSE streaming |
| `/threads/{id}` | GET | Get thread history |
| `/threads/{id}` | DELETE | Delete thread |
| `/workflows` | GET | List active workflows |
| `/health` | GET | Health check |

## License

MIT License
