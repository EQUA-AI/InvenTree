"""
Thread Summary Context Provider

MAF-compliant ContextProvider for conversation summarization.
Maintains and provides concise summaries of long conversations.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from ai.core.config import settings

logger = structlog.get_logger(__name__)


class ThreadSummaryProvider:
    """
    Context provider for thread summarization.
    
    Implements MAF ContextProvider interface:
    - invoking(messages, **kwargs) -> Context
    - invoked(request_messages, response_messages, invoke_exception, **kwargs)
    
    Provides:
    - Running summary of conversation
    - Key topics discussed
    - Decisions made
    - Open questions
    
    Summarization is triggered when conversation exceeds threshold.
    """
    
    # Configuration
    SUMMARY_THRESHOLD_MESSAGES = 10  # Summarize when exceeds this count
    SUMMARY_MAX_TOKENS = 500  # Max summary length
    
    def __init__(self, summaries_dir: Path | None = None) -> None:
        """
        Initialize the thread summary provider.
        
        Args:
            summaries_dir: Directory for summary files.
        """
        self.summaries_dir = summaries_dir or (settings.data_dir / "summaries")
        self.summaries_dir.mkdir(parents=True, exist_ok=True)
        logger.info("ThreadSummaryProvider initialized", summaries_dir=str(self.summaries_dir))
    
    def _get_summary_path(self, thread_id: str) -> Path:
        """Get the file path for a thread summary."""
        return self.summaries_dir / f"{thread_id}.json"
    
    async def get_summary(self, thread_id: str) -> dict[str, Any] | None:
        """
        Get the summary for a thread.
        
        Args:
            thread_id: The thread identifier.
            
        Returns:
            Summary data or None if not exists.
        """
        summary_path = self._get_summary_path(thread_id)
        
        if not summary_path.exists():
            return None
        
        try:
            with open(summary_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load thread summary", thread_id=thread_id, error=str(e))
            return None
    
    async def update_summary(
        self,
        thread_id: str,
        summary_data: dict[str, Any],
    ) -> None:
        """
        Update the summary for a thread.
        
        Args:
            thread_id: The thread identifier.
            summary_data: Summary data to save.
        """
        summary_path = self._get_summary_path(thread_id)
        
        with open(summary_path, "w") as f:
            json.dump(summary_data, f, indent=2, default=str)
        
        logger.debug("Thread summary updated", thread_id=thread_id)
    
    async def invoking(
        self,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Called before agent invocation.
        
        Provides conversation summary context.
        
        Args:
            messages: The conversation messages.
            **kwargs: Additional context including thread_id.
            
        Returns:
            Context dictionary with summary information.
        """
        thread_id = kwargs.get("thread_id")
        if not thread_id:
            return {}
        
        summary = await self.get_summary(thread_id)
        
        if summary is None:
            # No summary yet, provide message count info
            return {
                "thread_context": f"This is a {'new' if len(messages) <= 2 else 'continuing'} conversation with {len(messages)} messages.",
                "has_summary": False,
            }
        
        # Format summary for LLM consumption
        context = {
            "thread_summary": f"""
Conversation Summary (last updated: {summary.get('updated_at', 'unknown')}):

{summary.get('summary_text', 'No summary available.')}

Key Topics Discussed:
{self._format_list(summary.get('topics', []))}

Decisions Made:
{self._format_list(summary.get('decisions', []))}

Open Questions:
{self._format_list(summary.get('open_questions', []))}

Parts/Items Referenced:
{self._format_list(summary.get('parts_referenced', []))}
""".strip(),
            "has_summary": True,
            "raw_summary": summary,
        }
        
        logger.debug(
            "ThreadSummaryProvider.invoking",
            thread_id=thread_id,
            message_count=len(messages),
            has_summary=True,
        )
        
        return context
    
    async def invoked(
        self,
        request_messages: list[dict[str, Any]],
        response_messages: list[dict[str, Any]],
        invoke_exception: Exception | None,
        **kwargs: Any,
    ) -> None:
        """
        Called after agent invocation.
        
        Updates summary if conversation is getting long.
        
        Args:
            request_messages: The original request messages.
            response_messages: The response messages.
            invoke_exception: Exception if invocation failed.
            **kwargs: Additional context.
        """
        if invoke_exception:
            return
        
        thread_id = kwargs.get("thread_id")
        if not thread_id:
            return
        
        all_messages = request_messages + response_messages
        
        # Check if we should update summary
        if len(all_messages) < self.SUMMARY_THRESHOLD_MESSAGES:
            return
        
        # Check if enough new messages since last summary
        existing_summary = await self.get_summary(thread_id)
        if existing_summary:
            last_message_count = existing_summary.get("message_count", 0)
            if len(all_messages) - last_message_count < 5:
                return  # Not enough new messages
        
        # Create/update summary
        # Note: In production, this would use an LLM to generate the summary
        # For now, we create a basic summary from the last few messages
        summary_data = await self._create_summary(thread_id, all_messages)
        await self.update_summary(thread_id, summary_data)
        
        logger.debug(
            "ThreadSummaryProvider.invoked - summary updated",
            thread_id=thread_id,
            message_count=len(all_messages),
        )
    
    async def _create_summary(
        self,
        thread_id: str,
        messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        Create a summary from messages.
        
        In production, this would use an LLM. For now, creates basic summary.
        """
        # Extract user messages for summary
        user_messages = [m for m in messages if m.get("role") == "user"]
        assistant_messages = [m for m in messages if m.get("role") == "assistant"]
        
        # Basic topic extraction (would use LLM in production)
        topics = []
        parts_referenced = []
        
        for msg in user_messages[-10:]:  # Last 10 user messages
            content = msg.get("content", "")
            if isinstance(content, str):
                # Simple keyword extraction
                if any(word in content.lower() for word in ["part", "stock", "inventory"]):
                    if "parts" not in topics:
                        topics.append("Inventory/Parts inquiry")
                if any(word in content.lower() for word in ["order", "purchase", "buy"]):
                    if "procurement" not in topics:
                        topics.append("Procurement discussion")
                if any(word in content.lower() for word in ["problem", "issue", "fix", "broken"]):
                    if "troubleshooting" not in topics:
                        topics.append("Troubleshooting")
        
        # Create summary text
        recent_topics = ", ".join(topics[:5]) if topics else "General inquiry"
        summary_text = f"Conversation about {recent_topics}. User has made {len(user_messages)} requests with {len(assistant_messages)} responses."
        
        return {
            "thread_id": thread_id,
            "summary_text": summary_text,
            "topics": topics[:10],
            "decisions": [],  # Would be extracted by LLM
            "open_questions": [],  # Would be extracted by LLM
            "parts_referenced": parts_referenced[:20],
            "message_count": len(messages),
            "updated_at": datetime.now(UTC).isoformat(),
        }
    
    def _format_list(self, items: list[str]) -> str:
        """Format a list for display."""
        if not items:
            return "- None"
        return "\n".join(f"- {item}" for item in items[:10])


# Module-level singleton
_provider: ThreadSummaryProvider | None = None


def get_thread_summary_provider() -> ThreadSummaryProvider:
    """Get the singleton thread summary provider."""
    global _provider
    if _provider is None:
        _provider = ThreadSummaryProvider()
    return _provider
