"""Django app configuration for the machine health module."""

from django.apps import AppConfig


class MachineHealthConfig(AppConfig):
    """App config for normalized machine health signals and anomalies."""

    name = 'machine_health'
    verbose_name = 'Machine Health'
