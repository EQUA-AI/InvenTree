"""
AIMMS DevUI Integration

Provides integration with Microsoft Agent Framework DevUI for:
- Agent debugging and inspection
- Thread visualization
- Tool call tracing
- Real-time agent state monitoring

Uses agent-framework-devui package for local development.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

from ai.core.config import get_devui_settings

logger = logging.getLogger(__name__)


@dataclass
class DevUIConfig:
    """Configuration for DevUI server."""

    enabled: bool = True
    port: int = 3000
    host: str = "127.0.0.1"
    auto_open_browser: bool = True
    static_files_path: Path | None = None


class DevUIServer:
    """
    MAF DevUI server for agent debugging.

    The DevUI provides a web-based interface for:
    - Viewing agent threads and messages
    - Inspecting tool calls and results
    - Monitoring agent state changes
    - Debugging workflow execution

    Usage:
        # Start DevUI alongside the main app
        devui = DevUIServer()
        await devui.start()

        # Or use as context manager
        async with DevUIServer() as devui:
            # DevUI is running
            await app.run()
    """

    def __init__(self, config: DevUIConfig | None = None):
        """Initialize DevUI server."""
        settings = get_devui_settings()

        self.config = config or DevUIConfig(
            enabled=settings.enabled,
            port=settings.port,
        )

        self._server = None
        self._task = None
        self._running = False

    @property
    def is_running(self) -> bool:
        """Check if DevUI is running."""
        return self._running

    @property
    def url(self) -> str:
        """Get DevUI URL."""
        return f"http://{self.config.host}:{self.config.port}"

    async def start(self) -> None:
        """
        Start the DevUI server.

        Note: This requires the agent-framework-devui package.
        If not available, the server will not start but won't raise an error.
        """
        if not self.config.enabled:
            logger.info("DevUI is disabled")
            return

        try:
            # Try to import DevUI from agent-framework-devui
            # This is a placeholder - actual implementation depends on the MAF SDK
            from agent_framework_devui import DevServer

            self._server = DevServer(
                port=self.config.port,
                host=self.config.host,
            )

            self._task = asyncio.create_task(self._run_server())
            self._running = True

            logger.info(f"DevUI started at {self.url}")

            if self.config.auto_open_browser:
                await self._open_browser()

        except ImportError:
            logger.warning(
                "agent-framework-devui not installed. "
                "DevUI will not be available. "
                "Install with: pip install agent-framework-devui"
            )

        except Exception as e:
            logger.error(f"Failed to start DevUI: {e}")

    async def stop(self) -> None:
        """Stop the DevUI server."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        self._running = False
        logger.info("DevUI stopped")

    async def _run_server(self) -> None:
        """Run the DevUI server."""
        if self._server:
            await self._server.run()

    async def _open_browser(self) -> None:
        """Open DevUI in default browser."""
        try:
            import webbrowser

            webbrowser.open(self.url)
        except Exception as e:
            logger.warning(f"Could not open browser: {e}")

    async def __aenter__(self) -> DevUIServer:
        """Start server on context enter."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        """Stop server on context exit."""
        await self.stop()
        return False


class DevUIIntegration:
    """
    Integration helper for connecting agents to DevUI.

    Provides methods to:
    - Register agents for visualization
    - Push thread updates
    - Log tool calls
    - Track agent state

    Usage:
        integration = DevUIIntegration()

        # Register an agent
        integration.register_agent(router_agent, "Router")

        # Log a tool call
        integration.log_tool_call(
            agent_name="Router",
            tool_name="search_parts",
            arguments={"query": "motor"},
            result=parts_list,
        )
    """

    def __init__(self, devui: DevUIServer | None = None):
        """Initialize integration."""
        self.devui = devui or DevUIServer()
        self._agents: dict[str, Any] = {}
        self._threads: dict[str, list[dict[str, Any]]] = {}

    def register_agent(
        self,
        agent: Any,
        name: str,
        description: str = "",
    ) -> None:
        """
        Register an agent for DevUI visualization.

        Args:
            agent: The agent instance
            name: Display name for the agent
            description: Description of the agent's purpose
        """
        self._agents[name] = {
            "agent": agent,
            "name": name,
            "description": description,
            "registered_at": asyncio.get_event_loop().time(),
        }
        logger.debug(f"Registered agent for DevUI: {name}")

    def log_thread_message(
        self,
        thread_id: str,
        role: str,
        content: str,
        agent_name: str = "",
    ) -> None:
        """Log a message to a thread."""
        if thread_id not in self._threads:
            self._threads[thread_id] = []

        self._threads[thread_id].append(
            {
                "role": role,
                "content": content,
                "agent_name": agent_name,
                "timestamp": asyncio.get_event_loop().time(),
            }
        )

    def log_tool_call(
        self,
        agent_name: str,
        tool_name: str,
        arguments: dict[str, Any],
        result: Any = None,
        error: str | None = None,
        duration_ms: float = 0.0,
    ) -> None:
        """
        Log a tool call for visualization.

        Args:
            agent_name: Name of the agent that made the call
            tool_name: Name of the tool called
            arguments: Arguments passed to the tool
            result: Result from the tool (optional)
            error: Error message if failed (optional)
            duration_ms: Execution time in milliseconds
        """
        logger.debug(
            f"[DevUI] Tool call: {agent_name}.{tool_name}",
            extra={
                "arguments": arguments,
                "success": error is None,
                "duration_ms": duration_ms,
            },
        )

    def log_agent_state(
        self,
        agent_name: str,
        state: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """
        Log agent state change.

        Args:
            agent_name: Name of the agent
            state: New state (thinking, executing, waiting, etc.)
            details: Additional state details
        """
        logger.debug(
            f"[DevUI] Agent state: {agent_name} -> {state}",
            extra={"details": details or {}},
        )

    def get_thread_history(self, thread_id: str) -> list[dict[str, Any]]:
        """Get message history for a thread."""
        return self._threads.get(thread_id, [])

    def get_registered_agents(self) -> list[str]:
        """Get list of registered agent names."""
        return list(self._agents.keys())


# Global DevUI instance
_devui: DevUIServer | None = None
_integration: DevUIIntegration | None = None


def get_devui() -> DevUIServer:
    """Get or create the shared DevUI server."""
    global _devui
    if _devui is None:
        _devui = DevUIServer()
    return _devui


def get_devui_integration() -> DevUIIntegration:
    """Get or create the shared DevUI integration."""
    global _integration
    if _integration is None:
        _integration = DevUIIntegration(get_devui())
    return _integration


@asynccontextmanager
async def devui_context() -> AsyncIterator[DevUIServer]:
    """
    Context manager for DevUI server lifecycle.

    Usage:
        async with devui_context() as devui:
            # DevUI is running at devui.url
            await main_app()
    """
    devui = get_devui()
    await devui.start()
    try:
        yield devui
    finally:
        await devui.stop()
