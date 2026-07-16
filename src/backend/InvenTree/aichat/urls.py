"""Authenticated AI chat API routes (mounted at /api/aichat/)."""

from django.urls import path

from aichat.api import (
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
]
