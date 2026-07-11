"""
AI Conversation Database Models

Django models for persisting AI chat conversations in PostgreSQL.
These models integrate with Azure AI Search for semantic/vector search.

Tables:
- AIConversationThread: Stores conversation threads
- AIConversationMessage: Stores individual messages
- AIConversationIndex: Metadata about Azure AI Search indexing
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models
from django.db.models import Q
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _

import structlog
import uuid

import InvenTree.models

logger = structlog.get_logger('inventree')


class AIConversationThread(InvenTree.models.InvenTreeMetadataModel):
    """
    Represents a conversation thread between a user and the AI system.
    
    Each thread maintains:
    - User association (for access control)
    - Conversation summary (updated periodically)
    - Turn count for tracking length
    - Workflow history for context
    """
    
    class Meta:
        """Metaclass options."""
        verbose_name = _('AI Conversation Thread')
        verbose_name_plural = _('AI Conversation Threads')
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['user', '-updated_at']),
            models.Index(fields=['thread_id']),
            models.Index(fields=['is_active', '-updated_at']),
        ]
    
    # Unique thread identifier (used by frontend)
    thread_id = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        verbose_name=_('Thread ID'),
        help_text=_('Unique identifier for this conversation thread'),
    )
    
    # User who owns this thread
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='ai_conversation_threads',
        verbose_name=_('User'),
        help_text=_('User who owns this conversation'),
        null=True,
        blank=True,
    )
    
    # Thread title (auto-generated from first message or manual)
    title = models.CharField(
        max_length=255,
        blank=True,
        default='',
        verbose_name=_('Title'),
        help_text=_('Title of the conversation thread'),
    )
    
    # Conversation summary (updated periodically for long conversations)
    summary = models.TextField(
        blank=True,
        default='',
        verbose_name=_('Summary'),
        help_text=_('AI-generated summary of the conversation'),
    )
    
    # Turn count (number of user-assistant exchanges)
    turn_count = models.PositiveIntegerField(
        default=0,
        verbose_name=_('Turn Count'),
        help_text=_('Number of conversation turns'),
    )
    
    # Turn at which last summary was generated
    summary_turn = models.PositiveIntegerField(
        default=0,
        verbose_name=_('Summary Turn'),
        help_text=_('Turn count when summary was last updated'),
    )
    
    # Last workflow used in this thread
    last_workflow = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name=_('Last Workflow'),
        help_text=_('ID of the last workflow used'),
    )
    
    # Whether there's a pending handoff
    pending_handoff = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name=_('Pending Handoff'),
        help_text=_('Workflow ID for pending handoff'),
    )
    
    # Whether thread is active
    is_active = models.BooleanField(
        default=True,
        verbose_name=_('Active'),
        help_text=_('Whether this thread is active'),
    )
    
    # Timestamps
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_('Created'),
    )
    
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name=_('Updated'),
    )
    
    def __str__(self):
        """String representation."""
        return f"Thread {self.thread_id}: {self.title or 'Untitled'}"
    
    def get_recent_messages(self, limit: int = 20):
        """Get the most recent messages in this thread."""
        return self.messages.order_by('-created_at')[:limit][::-1]
    
    def needs_summarization(self, threshold: int = 10) -> bool:
        """Check if this thread needs a new summary."""
        return (self.turn_count - self.summary_turn) >= threshold
    
    @classmethod
    def get_or_create_for_user(cls, thread_id: str, user=None):
        """Get existing thread or create a new one."""
        thread, created = cls.objects.get_or_create(
            thread_id=thread_id,
            defaults={'user': user},
        )
        if not created and user and not thread.user:
            thread.user = user
            thread.save(update_fields=['user'])
        return thread, created


class AIConversationMessage(InvenTree.models.InvenTreeModel):
    """
    Represents a single message in an AI conversation.
    
    Stores:
    - Message content and role (user/assistant/system/tool)
    - Workflow association
    - Metadata for context
    - Embedding vector reference (stored in Azure AI Search)
    """
    
    class Meta:
        """Metaclass options."""
        verbose_name = _('AI Conversation Message')
        verbose_name_plural = _('AI Conversation Messages')
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['thread', 'created_at']),
            models.Index(fields=['role', 'created_at']),
            models.Index(fields=['message_id']),
        ]
    
    # Message role choices
    class Role(models.TextChoices):
        USER = 'user', _('User')
        ASSISTANT = 'assistant', _('Assistant')
        SYSTEM = 'system', _('System')
        TOOL = 'tool', _('Tool')
    
    # Unique message identifier
    message_id = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        default=uuid.uuid4,
        verbose_name=_('Message ID'),
    )
    
    # Parent thread
    thread = models.ForeignKey(
        AIConversationThread,
        on_delete=models.CASCADE,
        related_name='messages',
        verbose_name=_('Thread'),
    )
    
    # Message role
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        verbose_name=_('Role'),
    )
    
    # Message content
    content = models.TextField(
        verbose_name=_('Content'),
        help_text=_('Message content'),
    )
    
    # Workflow that generated/processed this message
    workflow_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name=_('Workflow ID'),
    )
    
    # Tool call information (for tool messages)
    tool_call_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name=_('Tool Call ID'),
    )
    
    tool_name = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name=_('Tool Name'),
    )
    
    # Metadata (JSON field for flexible storage)
    message_metadata = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_('Metadata'),
        help_text=_('Additional message metadata'),
    )
    
    # Token count (for cost tracking)
    token_count = models.PositiveIntegerField(
        default=0,
        verbose_name=_('Token Count'),
        help_text=_('Estimated token count for this message'),
    )
    
    # Whether this message has been indexed in Azure AI Search
    is_indexed = models.BooleanField(
        default=False,
        verbose_name=_('Indexed'),
        help_text=_('Whether this message is indexed in Azure AI Search'),
    )
    
    # Azure AI Search document ID (for updating/deleting)
    search_document_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name=_('Search Document ID'),
        help_text=_('Document ID in Azure AI Search index'),
    )
    
    # Timestamp
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_('Created'),
    )
    
    def __str__(self):
        """String representation."""
        preview = self.content[:50] + '...' if len(self.content) > 50 else self.content
        return f"[{self.role}] {preview}"
    
    def to_dict(self):
        """Convert to dictionary for serialization."""
        return {
            'message_id': str(self.message_id),
            'role': self.role,
            'content': self.content,
            'workflow_id': self.workflow_id,
            'tool_call_id': self.tool_call_id,
            'tool_name': self.tool_name,
            'metadata': self.message_metadata,
            'token_count': self.token_count,
            'created_at': self.created_at.isoformat(),
        }
    
    def to_search_document(self):
        """
        Convert to Azure AI Search document format.
        
        Returns a dict ready for indexing in Azure AI Search.
        """
        return {
            'id': str(self.message_id),
            'thread_id': self.thread.thread_id,
            'user_id': str(self.thread.user_id) if self.thread.user_id else 'anonymous',
            'role': self.role,
            'content': self.content,
            'workflow_id': self.workflow_id or '',
            'thread_title': self.thread.title,
            'created_at': self.created_at.isoformat(),
            'token_count': self.token_count,
            # Content will be vectorized by Azure AI Search
        }


class AISearchIndexStatus(InvenTree.models.InvenTreeModel):
    """
    Tracks the status of Azure AI Search indexing for conversation data.
    
    Used to:
    - Track last sync time
    - Monitor indexing errors
    - Enable incremental updates
    """
    
    class Meta:
        """Metaclass options."""
        verbose_name = _('AI Search Index Status')
        verbose_name_plural = _('AI Search Index Statuses')
    
    # Index name in Azure AI Search
    index_name = models.CharField(
        max_length=100,
        unique=True,
        verbose_name=_('Index Name'),
    )
    
    # Last successful sync timestamp
    last_sync_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_('Last Sync'),
    )
    
    # Number of documents indexed
    document_count = models.PositiveIntegerField(
        default=0,
        verbose_name=_('Document Count'),
    )
    
    # Last error message (if any)
    last_error = models.TextField(
        blank=True,
        default='',
        verbose_name=_('Last Error'),
    )
    
    # Status
    class Status(models.TextChoices):
        IDLE = 'idle', _('Idle')
        SYNCING = 'syncing', _('Syncing')
        ERROR = 'error', _('Error')
    
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.IDLE,
        verbose_name=_('Status'),
    )
    
    def __str__(self):
        """String representation."""
        return f"Index: {self.index_name} ({self.status})"


# ===== Signals for Azure AI Search Integration =====

@receiver(post_save, sender=AIConversationMessage)
def queue_message_for_indexing(sender, instance, created, **kwargs):
    """Queue new messages for Azure AI Search indexing."""
    if created and not instance.is_indexed:
        # In production, this would trigger an async task
        # For now, we just log it
        logger.info(
            'Message queued for indexing',
            message_id=str(instance.message_id),
            thread_id=instance.thread.thread_id,
        )


@receiver(post_delete, sender=AIConversationMessage)
def remove_message_from_index(sender, instance, **kwargs):
    """Remove deleted messages from Azure AI Search index."""
    if instance.search_document_id:
        logger.info(
            'Message queued for removal from index',
            message_id=str(instance.message_id),
            search_doc_id=instance.search_document_id,
        )
