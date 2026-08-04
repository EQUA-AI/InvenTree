#!/usr/bin/env python3
"""
AIMMS DevUI Runner

Starts the MAF DevUI server with FULL AIMMS workflows registered.
Uses the devui_adapters.py to wrap complete workflow classes for DevUI.

This provides access to the COMPLETE multi-agent workflows, including:
- Parallel execution (WF3 Research)
- Human-in-the-loop approval (WF4 Procurement)
- Semantic caching (WF1 Diagnostics)

Usage:
    python run_devui.py

The DevUI will be available at: http://localhost:8080
"""

import logging
import os
import socket
import sys
import webbrowser
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

# Load environment first
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def create_full_workflows():
    """
    Create FULL workflow instances wrapped for DevUI compatibility.

    Unlike the as_agent() pattern which creates simplified single-agent wrappers,
    this uses the DevUICompatibleWorkflow adapters to preserve:
    - Multi-agent orchestration
    - Parallel execution
    - HITL callbacks
    - Semantic caching
    - Structured results

    Returns:
        List of DevUICompatibleWorkflow instances ready for DevUI registration.
    """
    workflows = []

    # Check if Azure OpenAI is configured
    azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    azure_key = os.environ.get("AZURE_OPENAI_API_KEY", "")

    if not azure_endpoint or not azure_key or "your-" in azure_endpoint:
        logger.warning("⚠️  Azure OpenAI not configured!")
        logger.warning("   Set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY in .env")
        return workflows

    logger.info("🔧 Creating FULL workflow instances (not simplified agents)...")

    # Import the DevUI adapter factory functions (v2 - simplified and working)
    try:
        from ai.core.workflows.devui_adapters_v2 import get_all_devui_workflows

        workflows = get_all_devui_workflows()
        logger.info(f"✅ Created {len(workflows)} full workflows via devui_adapters_v2")
        for wf in workflows:
            logger.info(f"   • {wf.name}: {wf.description[:50]}...")
        return workflows
    except Exception as e:
        logger.warning(f"❌ Failed to load devui_adapters_v2: {e}")
        import traceback

        traceback.print_exc()

    # Fallback: Create workflows manually if adapters fail
    logger.info("📦 Falling back to manual workflow creation...")

    workflow_configs = [
        {
            "name": "T1 Lookup (WF8)",
            "description": "Fast inventory lookups: stock levels, part details, BOM queries",
            "factory": ("ai.core.workflows.wf8_lookup", "create_lookup_workflow"),
        },
        {
            "name": "T2 Parts Analysis (WF2)",
            "description": "BOM analysis, compatibility checks, alternative parts",
            "factory": ("ai.core.workflows.wf2_parts_analysis", "create_parts_analysis_workflow"),
        },
        {
            "name": "T3 Research (WF3)",
            "description": "Multi-source PARALLEL research: suppliers, specs, pricing",
            "factory": ("ai.core.workflows.wf3_research", "create_research_workflow"),
        },
        {
            "name": "T4 Procurement (WF4)",
            "description": "Procurement with HITL approval for purchase orders",
            "factory": ("ai.core.workflows.wf4_procurement", "create_procurement_workflow"),
        },
        {
            "name": "T6 Diagnostics (WF1)",
            "description": "Equipment diagnostics with semantic caching",
            "factory": ("ai.core.workflows.wf1_diagnostics", "create_diagnostics_workflow"),
        },
        {
            "name": "T7 Documents (WF6)",
            "description": "Document processing: classification, extraction, validation",
            "factory": ("ai.core.workflows.wf6_documents", "create_documents_workflow"),
        },
    ]

    for config in workflow_configs:
        try:
            module_name, func_name = config["factory"]
            module = __import__(module_name, fromlist=[func_name])
            factory_func = getattr(module, func_name)
            workflow = factory_func()
            workflows.append(workflow)
            logger.info(f"✅ Created: {config['name']}")
        except Exception as e:
            logger.warning(f"❌ Failed to create {config['name']}: {e}")
            import traceback

            traceback.print_exc()

    logger.info(f"📦 Created {len(workflows)} workflows successfully")
    return workflows


def main():
    """Start the DevUI server with FULL workflows properly configured."""
    try:
        from agent_framework_devui import DevServer
    except ImportError:
        print("❌ agent-framework-devui is not installed!")
        print("   Install with: pip install agent-framework-devui")
        sys.exit(1)

    # Get port from environment or use default
    port = int(os.environ.get("DEVUI_PORT", "8080"))

    # Check if port is available
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(("127.0.0.1", port))
    sock.close()

    if result == 0:
        print(f"⚠️  Port {port} is in use. Trying port {port + 1}...")
        port = port + 1

    print(f"""
╔══════════════════════════════════════════════════════════════════════╗
║                    AIMMS DevUI Server - FULL WORKFLOWS               ║
╠══════════════════════════════════════════════════════════════════════╣
║  Starting MAF Development UI with COMPLETE multi-agent workflows...  ║
║                                                                      ║
║  🌐 DevUI URL: http://127.0.0.1:{port:<5}                               ║
║                                                                      ║
║  Features enabled:                                                   ║
║    ✓ Multi-agent orchestration (WF3 Research)                       ║
║    ✓ Parallel execution (WF3 Research)                              ║
║    ✓ Human-in-the-loop approval (WF4 Procurement)                   ║
║    ✓ Semantic caching (WF1 Diagnostics)                             ║
║    ✓ Document Intelligence integration (WF6)                        ║
║                                                                      ║
║  Building FULL workflows...                                          ║
╚══════════════════════════════════════════════════════════════════════╝
""")

    # Create FULL workflows (not simplified agents)
    workflows = create_full_workflows()

    if not workflows:
        print("\n⚠️  No workflows could be created!")
        print("   Check your .env file for Azure OpenAI configuration:")
        print("   - AZURE_OPENAI_ENDPOINT")
        print("   - AZURE_OPENAI_API_KEY")
        print("   - AZURE_OPENAI_DEPLOYMENT")
        print("\n   Starting DevUI anyway for debugging...\n")

    # Create server WITHOUT entities_dir to avoid auto-scanning
    # This prevents middleware, memory, infrastructure from appearing in dropdown
    server = DevServer(
        entities_dir=None,  # Don't auto-scan directories
        port=port,
        host="127.0.0.1",
        ui_enabled=True,
        mode="developer",
    )

    # Register our FULL workflows
    if workflows:
        server.register_entities(workflows)
        print(f"\n✅ Registered {len(workflows)} FULL workflows:")
        for wf in workflows:
            name = getattr(wf, "name", getattr(wf, "display_name", "Unknown"))
            desc = getattr(wf, "description", "")
            print(f"   • {name}")
            if desc:
                print(f"     └─ {desc[:60]}...")

    # Open browser
    url = f"http://127.0.0.1:{port}"
    print(f"\n🚀 Opening browser at {url}...")
    webbrowser.open(url)

    print("\n📝 Press Ctrl+C to stop the server\n")

    # Run server
    import uvicorn

    uvicorn.run(
        server.get_app(),
        host="127.0.0.1",
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
