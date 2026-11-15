"""Django app configuration for the assets module."""

from django.apps import AppConfig


class AssetsConfig(AppConfig):
    """App config for equipment asset management."""

    name = 'assets'
    verbose_name = 'Equipment Assets'

    def ready(self):
        """Register default-off, future-only native source birth observation."""
        from django.db.models.signals import post_save

        from .attribution_evidence import observe_native_birth

        post_save.connect(
            observe_native_birth,
            dispatch_uid='aimms.native-source-birth.v1',
            weak=False,
        )
