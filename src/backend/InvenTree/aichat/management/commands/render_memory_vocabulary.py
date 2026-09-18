"""Render the durable memory vocabulary without querying a database."""

from django.core.management.base import BaseCommand

from aichat import memory_choices


class Command(BaseCommand):
    """One schema vocabulary table for review and fixture maintenance."""

    help = 'Render durable memory choices; summary-only resolved is excluded.'

    def handle(self, *args, **options):
        """Print only the closed enum values, never stored facts."""
        self.stdout.write('| Enum | Values |\n| --- | --- |')
        for name in (
            'MemoryVerification',
            'MemoryLifecycle',
            'MemoryOrigin',
            'MemoryVisibility',
            'MemoryClassification',
            'DurableMemoryType',
            'MemoryTopic',
            'MemorySourceClass',
            'MemoryTrust',
            'MemoryShieldState',
        ):
            self.stdout.write(
                f'| {name} | {", ".join(getattr(memory_choices, name).values)} |'
            )
