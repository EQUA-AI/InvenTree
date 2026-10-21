"""O15 endpoints kept separate from registration/activation commands."""

from django.urls import path

from .record_api import CriticalDesignations, Fits, SharedViews

urlpatterns = [
    path(
        '<int:pk>/critical-signals/',
        CriticalDesignations.as_view(),
        name='registry-critical-signals',
    ),
    path(
        '<int:pk>/slots/<int:component_pk>/fits/',
        Fits.as_view(),
        name='registry-slot-fits',
    ),
    path(
        '<int:pk>/performance-views/',
        SharedViews.as_view(),
        name='machine-shared-views',
    ),
]
