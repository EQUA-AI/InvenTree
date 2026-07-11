"""
MAF DevUI Workflow Adapters v2

Simplified adapters to wrap AIMMS workflow classes for DevUI compatibility.
Uses the correct MAF API patterns for the agent-framework beta.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterable

from agent_framework import (
    AgentRunResponse,
    AgentRunResponseUpdate,
    ChatMessage,
    DataContent,
    Role,
    TextContent,
)

# Load environment at module level
from dotenv import load_dotenv
load_dotenv()

import os

logger = logging.getLogger(__name__)


class DevUICompatibleWorkflow:
    """
    Wrapper that makes any workflow class compatible with DevUI.
    
    This is a lightweight wrapper that delegates to the underlying
    workflow's execute() method while providing the interface DevUI expects.
    
    The underlying workflow classes already handle their own agent creation
    and Azure OpenAI configuration internally.
    """
    
    def __init__(
        self,
        workflow_instance: Any,
        name: str,
        description: str = "",
        execute_method: str = "execute",
    ):
        self._workflow = workflow_instance
        self.name = name
        self.description = description
        self._execute_method = execute_method
    
    @property
    def display_name(self) -> str:
        return self.name
    
    async def run(self, messages: list[ChatMessage] | str, **kwargs) -> AgentRunResponse:
        """
        Execute the workflow via the run interface.
        
        This delegates to the underlying workflow's execute() method.
        Returns an AgentRunResponse for proper DevUI display.
        """
        # Extract query and file data from messages
        query = self._extract_query(messages)
        file_data = self._extract_file_data(messages)
        
        # If we have file data, include it in the query context
        if file_data:
            logger.info(f"[{self.name}] Found {len(file_data)} file attachment(s)")
            # Append file content summary to query for document workflows
            file_context = self._build_file_context(file_data)
            if file_context:
                query = f"{query}\n\n--- ATTACHED FILE CONTENT ---\n{file_context}"
        
        logger.info(f"[{self.name}] Executing with query: {query[:100]}...")
        
        # Execute the underlying workflow
        execute_fn = getattr(self._workflow, self._execute_method)
        
        try:
            # Try with full context including file data
            thread_id = kwargs.get("thread_id", "devui_session")
            context = kwargs.get("context", {})
            # Add file data to context for workflows that support it
            if file_data:
                context["file_attachments"] = file_data
            result = await execute_fn(query, thread_id=thread_id, context=context)
        except TypeError:
            # Fallback to simpler signature
            try:
                result = await execute_fn(query)
            except Exception as e:
                logger.error(f"[{self.name}] Workflow execution failed: {e}")
                # Return error as AgentRunResponse with proper TextContent
                error_message = ChatMessage(
                    role=Role.ASSISTANT, 
                    contents=[TextContent(text=f"Error executing workflow: {e}")]
                )
                return AgentRunResponse(messages=[error_message])
        
        # Extract the text response
        if hasattr(result, "formatted_response"):
            response_text = result.formatted_response
        elif isinstance(result, dict) and "formatted_response" in result:
            response_text = result["formatted_response"]
        elif hasattr(result, "response"):
            response_text = result.response
        else:
            response_text = str(result)
        
        # Return as AgentRunResponse with proper TextContent for DevUI handling
        # CRITICAL: Must use contents=[TextContent(...)] not content=str
        # The DevUI mapper iterates over message.contents to extract text
        response_message = ChatMessage(
            role=Role.ASSISTANT, 
            contents=[TextContent(text=response_text)]
        )
        return AgentRunResponse(messages=[response_message])
    
    def _extract_file_data(self, messages: list[ChatMessage] | str | Any) -> list[dict]:
        """Extract file attachments (DataContent) from messages."""
        import base64
        
        file_data = []
        
        if isinstance(messages, str):
            return file_data
        
        # Handle single ChatMessage object
        if hasattr(messages, 'contents'):
            messages = [messages]
        
        if isinstance(messages, list):
            for msg in messages:
                if hasattr(msg, 'contents') and msg.contents:
                    logger.debug(f"Processing message with {len(msg.contents)} contents")
                    for content in msg.contents:
                        content_type = type(content).__name__
                        logger.debug(f"  Content type: {content_type}")
                        if content_type == 'DataContent':
                            # Extract base64 data from DataContent
                            uri = getattr(content, 'uri', '')
                            media_type = getattr(content, 'media_type', 'application/octet-stream')
                            
                            file_info = {
                                "media_type": media_type,
                                "uri": uri,
                            }
                            
                            # Try to decode base64 content from data URI
                            if uri and uri.startswith('data:'):
                                try:
                                    # Parse data URI: data:mediatype;base64,data
                                    if ';base64,' in uri:
                                        _, encoded = uri.split(';base64,', 1)
                                    elif ',' in uri:
                                        _, encoded = uri.split(',', 1)
                                    else:
                                        encoded = None
                                    
                                    if encoded:
                                        file_info["data"] = base64.b64decode(encoded)
                                        file_info["decoded"] = True
                                        logger.info(f"✅ Decoded file: {media_type}, {len(file_info['data'])} bytes")
                                except Exception as e:
                                    logger.warning(f"❌ Failed to decode file data: {e}")
                                    file_info["decoded"] = False
                            else:
                                logger.debug(f"  URI doesn't start with 'data:': {uri[:50] if uri else 'empty'}...")
                            
                            file_data.append(file_info)
        
        logger.info(f"📎 Extracted {len(file_data)} file attachment(s)")
        return file_data
    
    def _build_file_context(self, file_data: list[dict]) -> str:
        """Build a text context from file data for the workflow."""
        import base64
        
        context_parts = []
        
        for i, file_info in enumerate(file_data):
            media_type = file_info.get("media_type", "unknown")
            
            if file_info.get("decoded") and file_info.get("data"):
                data = file_info["data"]
                
                # For PDFs and documents, we can't easily extract text here
                # but we'll provide metadata and let the workflow handle it
                if "pdf" in media_type:
                    context_parts.append(f"[Attached PDF document - {len(data)} bytes]")
                    # Try to extract any readable text from PDF
                    try:
                        # Look for text patterns in PDF
                        text_content = data.decode('latin-1', errors='ignore')
                        # Extract readable ASCII portions
                        readable = ''.join(c if c.isprintable() or c in '\n\r\t' else ' ' for c in text_content)
                        # Clean up excessive whitespace
                        import re
                        readable = re.sub(r'\s+', ' ', readable)
                        if len(readable) > 100:
                            context_parts.append(f"PDF Text Extract (partial): {readable[:3000]}...")
                    except Exception as e:
                        logger.debug(f"Could not extract text from PDF: {e}")
                        
                elif "image" in media_type:
                    context_parts.append(f"[Attached image - {media_type} - {len(data)} bytes]")
                    
                elif "text" in media_type:
                    try:
                        text = data.decode('utf-8')
                        context_parts.append(f"File content:\n{text}")
                    except:
                        context_parts.append(f"[Attached text file - {len(data)} bytes]")
                else:
                    context_parts.append(f"[Attached file - {media_type} - {len(data)} bytes]")
            else:
                context_parts.append(f"[File attachment {i+1}: {media_type}]")
        
        return "\n".join(context_parts)
    
    def _extract_query(self, messages: list[ChatMessage] | str | Any) -> str:
        """Extract text query from various message formats."""
        if isinstance(messages, str):
            return messages
        
        if isinstance(messages, list):
            # Get the last user message
            for msg in reversed(messages):
                if hasattr(msg, 'role') and str(msg.role).lower() in ('user', 'role.user'):
                    return self._extract_text_from_message(msg)
            # Fallback to last message
            if messages:
                return self._extract_text_from_message(messages[-1])
            return ""
        
        # Single message object
        return self._extract_text_from_message(messages)
    
    def _extract_text_from_message(self, msg: Any) -> str:
        """Extract text from a single message object."""
        # Try direct text attribute
        if hasattr(msg, 'text') and msg.text:
            return str(msg.text)
        
        # Try contents array (TextContent objects)
        if hasattr(msg, 'contents') and msg.contents:
            text_parts = []
            for content in msg.contents:
                if hasattr(content, 'text') and content.text:
                    text_parts.append(str(content.text))
            if text_parts:
                return ' '.join(text_parts)
        
        # Try content attribute
        if hasattr(msg, 'content') and msg.content:
            if isinstance(msg.content, str):
                return msg.content
            # Content might be a list of content parts
            if isinstance(msg.content, list):
                text_parts = []
                for part in msg.content:
                    if isinstance(part, str):
                        text_parts.append(part)
                    elif hasattr(part, 'text'):
                        text_parts.append(str(part.text))
                if text_parts:
                    return ' '.join(text_parts)
        
        # Fallback to string representation
        return str(msg)
    
    async def run_stream(self, messages: list[ChatMessage] | str, **kwargs) -> AsyncIterable[AgentRunResponseUpdate]:
        """
        Streaming execution - yields AgentRunResponseUpdate for proper DevUI handling.
        
        For full streaming, the underlying workflow would need to support it.
        Currently executes fully and yields the complete result.
        """
        # Execute the workflow
        result = await self.run(messages, **kwargs)
        
        # Extract text from the AgentRunResponse
        if hasattr(result, 'messages'):
            msg = result.messages
            if isinstance(msg, list) and msg:
                msg = msg[0]
            if hasattr(msg, 'content'):
                response_text = msg.content
            elif hasattr(msg, 'contents') and msg.contents:
                text_parts = []
                for c in msg.contents:
                    if hasattr(c, 'text'):
                        text_parts.append(str(c.text))
                response_text = ' '.join(text_parts) if text_parts else str(msg)
            else:
                response_text = str(msg)
        else:
            response_text = str(result)
        
        # Yield as AgentRunResponseUpdate for DevUI streaming display
        yield AgentRunResponseUpdate(text=response_text, role=Role.ASSISTANT)


# =============================================================================
# Factory Functions for AIMMS Workflows
# =============================================================================

def create_diagnostics_workflow() -> DevUICompatibleWorkflow:
    """Create DevUI-compatible T6 Diagnostics workflow."""
    from ai.core.workflows.wf1_diagnostics import T6DiagnosticsWorkflow
    
    return DevUICompatibleWorkflow(
        T6DiagnosticsWorkflow(),
        name="T6 Diagnostics",
        description="Complex manufacturing diagnostics with root cause analysis. "
                   "Analyzes equipment problems, identifies root causes, and recommends solutions.",
    )


def create_parts_analysis_workflow() -> DevUICompatibleWorkflow:
    """Create DevUI-compatible T2 Parts Analysis workflow."""
    from ai.core.workflows.wf2_parts_analysis import T2PartsAnalysisWorkflow
    
    return DevUICompatibleWorkflow(
        T2PartsAnalysisWorkflow(),
        name="T2 Parts Analysis",
        description="Analyze parts and BOM structures. "
                   "Performs compatibility checks and finds alternative parts.",
    )


def create_research_workflow() -> DevUICompatibleWorkflow:
    """Create DevUI-compatible T3 Research workflow."""
    from ai.core.workflows.wf3_research import T3ResearchWorkflow
    
    return DevUICompatibleWorkflow(
        T3ResearchWorkflow(),
        name="T3 Research",
        description="Multi-source PARALLEL research and information gathering. "
                   "Searches suppliers, specifications, and pricing concurrently.",
    )


def create_procurement_workflow() -> DevUICompatibleWorkflow:
    """Create DevUI-compatible T4 Procurement workflow."""
    from ai.core.workflows.wf4_procurement import T4ProcurementWorkflow
    
    return DevUICompatibleWorkflow(
        T4ProcurementWorkflow(),
        name="T4 Procurement",
        description="Procurement with HITL (human-in-the-loop) approval. "
                   "Creates purchase orders with approval workflow.",
    )


def create_cpq_workflow() -> DevUICompatibleWorkflow:
    """Create DevUI-compatible T5 CPQ workflow."""
    from ai.core.workflows.wf5_cpq import T5CPQWorkflow
    
    return DevUICompatibleWorkflow(
        T5CPQWorkflow(),
        name="T5 CPQ",
        description="Configure-Price-Quote multi-agent workflow. "
                   "Configures products, calculates pricing, and generates quotes.",
    )


def create_documents_workflow() -> DevUICompatibleWorkflow:
    """Create DevUI-compatible T7 Documents workflow."""
    # Use the new conversational v2 workflow
    from ai.core.workflows.wf6_documents_v2 import WF6DocumentWorkflow
    
    return DevUICompatibleWorkflow(
        WF6DocumentWorkflow(),
        name="T7 Documents",
        description="Conversational document processing agent. "
                   "Reads documents, answers questions, searches inventory, and adds parts.",
    )


def create_lookup_workflow() -> DevUICompatibleWorkflow:
    """Create DevUI-compatible T1 Lookup workflow."""
    from ai.core.workflows.wf8_lookup import T1LookupWorkflow
    
    return DevUICompatibleWorkflow(
        T1LookupWorkflow(),
        name="T1 Lookup",
        description="Fast inventory and parts lookup. "
                   "Queries stock levels, part details, and BOM information.",
    )


def get_all_devui_workflows() -> list[DevUICompatibleWorkflow]:
    """Get all AIMMS workflows configured for DevUI."""
    workflows = []
    
    # Create each workflow with error handling
    factories = [
        ("T1 Lookup", create_lookup_workflow),
        ("T2 Parts Analysis", create_parts_analysis_workflow),
        ("T3 Research", create_research_workflow),
        ("T4 Procurement", create_procurement_workflow),
        ("T5 CPQ", create_cpq_workflow),
        ("T6 Diagnostics", create_diagnostics_workflow),
        ("T7 Documents", create_documents_workflow),
    ]
    
    for name, factory in factories:
        try:
            wf = factory()
            workflows.append(wf)
            logger.info(f"✅ Created {name} workflow")
        except Exception as e:
            logger.warning(f"❌ Failed to create {name}: {e}")
    
    return workflows


# =============================================================================
# DevUI Server Entry Point
# =============================================================================

def run_devui_with_workflows(port: int = 8080, auto_open: bool = True):
    """
    Launch DevUI server with all AIMMS workflows.
    
    Usage:
        python -m ai.core.workflows.devui_adapters_v2
    """
    from agent_framework_devui import serve
    
    workflows = get_all_devui_workflows()
    
    logger.info(f"Starting DevUI with {len(workflows)} workflows on port {port}")
    for wf in workflows:
        logger.info(f"  - {wf.name}: {wf.description[:50]}...")
    
    serve(entities=workflows, port=port, auto_open=auto_open)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run_devui_with_workflows()
