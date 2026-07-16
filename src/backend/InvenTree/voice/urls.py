"""Authenticated Voice capture API routes (mounted at /api/voice/)."""

from django.urls import path

from voice.api import (
    CaptureAcceptView,
    CaptureCancelView,
    CaptureCommitView,
    CaptureDetailView,
    CaptureListCreateView,
    CaptureReviseView,
)

app_name = 'voice'

urlpatterns = [
    path('captures/', CaptureListCreateView.as_view(), name='capture-list'),
    path(
        'captures/<uuid:capture_id>/',
        CaptureDetailView.as_view(),
        name='capture-detail',
    ),
    path(
        'captures/<uuid:capture_id>/revise/',
        CaptureReviseView.as_view(),
        name='capture-revise',
    ),
    path(
        'captures/<uuid:capture_id>/accept/',
        CaptureAcceptView.as_view(),
        name='capture-accept',
    ),
    path(
        'captures/<uuid:capture_id>/cancel/',
        CaptureCancelView.as_view(),
        name='capture-cancel',
    ),
    path(
        'captures/<uuid:capture_id>/commit/',
        CaptureCommitView.as_view(),
        name='capture-commit',
    ),
]
