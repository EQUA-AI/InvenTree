"""Operator wrapper for the attachment-RAG reconciliation sweep.

The scheduled task (``aichat.tasks.sweep_attachment_rag``) runs the same
service functions every ten minutes; this command exists for on-demand runs
and for inspecting the backlog without acting on it.
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Resume stalled ingests and purge orphaned registry rows."""

    help = 'Reconcile the attachment-RAG registry (stalled ingests, orphans).'

    def add_arguments(self, parser):
        """Register the dry-run flag."""
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report orphan count only; schedule and purge nothing.',
        )

    def handle(self, *args, **options):
        """Run (or preview) the sweep and report counts."""
        from aichat.services.attachment_ingestion import (
            reconcile_orphaned_ingests,
            resume_stalled_ingests,
        )

        if options['dry_run']:
            orphans = reconcile_orphaned_ingests(dry_run=True)
            self.stdout.write(f'orphaned attachment ingests: {orphans}')
            return
        counts = resume_stalled_ingests()
        self.stdout.write(
            'done: resumed={resumed} stalled={stalled} orphans={orphans}'.format(
                **counts
            )
        )
