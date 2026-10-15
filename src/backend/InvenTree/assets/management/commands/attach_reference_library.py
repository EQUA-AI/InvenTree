"""Link reference-library documents from a population workbook to family parts.

Modes, as for ``populate_from_workbook``:

``--plan``     read the workbook, the pack and the database; print the plan.
``--dry-run``  create the attachments inside a transaction and roll back.
``--apply``    keep them. Requires ``--backup-ref`` and, for a non-local
               database host, ``--allow-remote``.

Only link-only attachments are created; no document bytes are uploaded.
See ``assets/reference_library.py`` for the hold rules.
"""

import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from assets.management.commands.populate_from_workbook import (
    database_host,
    is_local_database,
)
from assets.reference_library import (
    ReferenceLibraryError,
    apply_reference_links,
    load_attachment_plan,
    load_pack_index,
    load_review,
    plan_reference_links,
)


class Command(BaseCommand):
    """Operator-only; writes gated behind an explicit backup reference."""

    help = 'Plan, preview or apply reference-library links from a workbook Attachment Plan.'

    def add_arguments(self, parser):
        """Inputs, mode switches and the write gate."""
        parser.add_argument('workbook', type=Path)
        parser.add_argument(
            '--pack',
            type=Path,
            required=True,
            help='Root of the consolidated reference pack (contains 00_Start_Here/)',
        )
        parser.add_argument(
            '--review', type=Path, help='JSON hold list {"holds": {key: reason}}'
        )
        parser.add_argument(
            '--ipn-prefix', default='CC-REF-', help='Family part IPN prefix'
        )
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument('--plan', action='store_true', help='Plan only (default)')
        mode.add_argument('--dry-run', action='store_true', help='Write and roll back')
        mode.add_argument('--apply', action='store_true', help='Keep the writes')
        parser.add_argument('--actor', help='Username recorded as the uploader')
        parser.add_argument(
            '--backup-ref', help='Identifier of the database backup taken first'
        )
        parser.add_argument(
            '--allow-remote',
            action='store_true',
            help='Permit --apply against a non-local database host',
        )
        parser.add_argument(
            '--json', action='store_true', help='Machine-readable output'
        )

    def handle(self, *args, **options):
        """Validate the gate, plan, then optionally write."""
        apply = options['apply']
        dry_run = options['dry_run']
        if apply:
            if not options['backup_ref']:
                raise CommandError(
                    '--apply requires --backup-ref naming the backup taken first.'
                )
            if not is_local_database() and not options['allow_remote']:
                raise CommandError(
                    f'Database host {database_host()!r} is not local; re-run with '
                    '--allow-remote once the owner has approved the write.'
                )
        try:
            sha, rows, manual = load_attachment_plan(options['workbook'])
            manifest, catalog = load_pack_index(options['pack'])
            holds = load_review(options['review'])
            plan = plan_reference_links(
                rows,
                manifest,
                catalog,
                holds,
                pack_dir=options['pack'],
                ipn_prefix=options['ipn_prefix'],
                workbook_sha256=sha,
                manual=manual,
            )
        except ReferenceLibraryError as exc:
            raise CommandError(str(exc)) from exc

        payload = {'mode': 'plan', **plan.as_dict()}
        if apply or dry_run:
            if plan.counts().get('conflict'):
                raise CommandError('Plan has conflicts; nothing written.')
            actor = self._actor(options['actor'])
            result = apply_reference_links(plan, actor, dry_run=dry_run)
            payload.update({
                'mode': 'dry-run' if dry_run else 'apply',
                'database_host': database_host(),
                'backup_ref': options['backup_ref'],
                'created': result['created'],
            })

        if options['json']:
            self.stdout.write(json.dumps(payload, indent=2, default=str))
            return
        self._render(payload)

    def _actor(self, username):
        if not username:
            return None
        user = get_user_model().objects.filter(username=username).first()
        if user is None:
            raise CommandError(f'Unknown actor: {username}')
        return user

    def _render(self, payload):
        self.stdout.write(
            f'Workbook {payload["workbook_sha256"][:12]}: '
            f'{len(payload["rows"])} reference-library rows'
        )
        for row in payload['rows']:
            where = (
                f'{row["target_type"]}:{row["target_label"]}'
                if row['target_type']
                else row['ipn']
            )
            line = f'  {row["action"]:<9} {row["key"]:<10} {where[:48]:<48} {row["title"][:50]}'
            if row['reason']:
                line += f' -- {row["reason"]}'
            self.stdout.write(line)
        counts = ', '.join(f'{k}={v}' for k, v in sorted(payload['counts'].items()))
        self.stdout.write(f'  totals    {counts}')
        for target, n in sorted(payload['manual'].items()):
            self.stdout.write(f'  manual    {target}: {n} rows (no file in the pack)')
        if payload['mode'] != 'plan':
            label = (
                'DRY RUN (rolled back)' if payload['mode'] == 'dry-run' else 'APPLIED'
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f'{label} against {payload["database_host"]!r}: '
                    f'{len(payload["created"])} link attachments'
                )
            )
