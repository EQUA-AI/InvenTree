"""Django app configuration for the tasks module."""

from django.apps import AppConfig


class TasksConfig(AppConfig):
    """App config for task management."""

    name = 'tasks'

    def ready(self):
        """Register approval executors supplied by the tasks application."""
        from approvals.executors import registry
        from approvals.models import ActionType
        from tasks.executors import ProcedurePublishExecutor

        if not registry.has(ActionType.PROCEDURE_PUBLISH):
            registry.register(ProcedurePublishExecutor())
