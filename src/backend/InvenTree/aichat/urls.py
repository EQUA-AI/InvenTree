"""Authenticated AI chat API routes (mounted at /api/aichat/)."""

from django.urls import path

from aichat.api import (
    ContextResolveView,
    ConversationCitationsView,
    ConversationDetailView,
    ConversationListCreateView,
    ConversationToolInvokeView,
    ConversationToolTraceView,
    ProposalConfirmView,
    ProposalDetailView,
    ProposalListCreateView,
    ProposalRejectView,
)

app_name = 'aichat'

urlpatterns = [
    path('proposals/', ProposalListCreateView.as_view(), name='proposal-list'),
    path(
        'proposals/<uuid:proposal_id>/',
        ProposalDetailView.as_view(),
        name='proposal-detail',
    ),
    path(
        'proposals/<uuid:proposal_id>/confirm/',
        ProposalConfirmView.as_view(),
        name='proposal-confirm',
    ),
    path(
        'proposals/<uuid:proposal_id>/reject/',
        ProposalRejectView.as_view(),
        name='proposal-reject',
    ),
    path('context/resolve/', ContextResolveView.as_view(), name='context-resolve'),
    path(
        'conversations/', ConversationListCreateView.as_view(), name='conversation-list'
    ),
    path(
        'conversations/<uuid:conversation_id>/',
        ConversationDetailView.as_view(),
        name='conversation-detail',
    ),
    path(
        'conversations/<uuid:conversation_id>/citations/',
        ConversationCitationsView.as_view(),
        name='conversation-citations',
    ),
    path(
        'conversations/<uuid:conversation_id>/tools/',
        ConversationToolTraceView.as_view(),
        name='conversation-tools',
    ),
    path(
        'conversations/<uuid:conversation_id>/tools/invoke/',
        ConversationToolInvokeView.as_view(),
        name='conversation-tool-invoke',
    ),
]
