"""Seed the reserved attachment-RAG eval fixture set (decision #13, R2).

The live attachment corpus mutates on every upload, so golden-eval items pin
a dedicated, never-mutated fixture set instead: a reserved eval machine and
part carrying small fictional documents checked into
``ai/core/evals/golden/fixtures/attachments/``. Idempotent by construction —
entities are ``get_or_create``d and re-running ``run_ingest`` on an unchanged
document short-circuits on its sha.

Fixture-set version: ``aimms-attachment-fixtures-v2``. Changing any fixture's
content requires a NEW version (new file revisions, new pin in items.yaml,
new value in ``AIMMS_GOLDEN_CORPUS``) — never an in-place edit. v1 -> v2 is
the S6 OWNERSHIP change: the HX-200 machine and gasket part moved from the
default ``internal`` client to the dedicated ``eval-fixtures`` client, so
the pinned answers are only reproducible under the new scope configuration
(deployments still on v1 SKIP rather than fail).

The ZR-9 fixture lands on a machine owned by a dedicated ``eval-offlimits``
client that no operator scope grants, powering the cross-client denial item:
retrieval must treat that document as nonexistent. Since S6 the HX-200
fixtures are likewise invisible to ordinary users — they belong to
``eval-fixtures``, granted only to the designated evaluation user — while
``eval-offlimits`` stays granted to NOBODY (the adversarial control).
"""

from __future__ import annotations

from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError

#: Fixture documents, relative to the fixtures directory:
#: (file name, owner kind). ``machine`` -> the in-scope eval machine,
#: ``part`` -> the eval part, ``offlimits`` -> the off-scope machine.
_FIXTURES = (
    ('eval-hx200-manual.md', 'machine'),
    ('eval-hx200-gasket-datasheet.md', 'part'),
    ('eval-zr9-offlimits-manual.md', 'offlimits'),
)

_FIXTURE_SET_VERSION = 'aimms-attachment-fixtures-v2'


def _fixtures_dir() -> Path:
    """Locate the checked-in fixture documents next to the golden set."""
    import ai.core.evals as evals_pkg

    return (
        Path(evals_pkg.__file__).resolve().parent
        / 'golden'
        / 'fixtures'
        / 'attachments'
    )


class Command(BaseCommand):
    """Seed (idempotently) the reserved eval part/machines and their docs."""

    help = (
        'Seed the reserved attachment-RAG eval fixture set '
        f'({_FIXTURE_SET_VERSION}): eval machine + part + off-limits machine '
        'with their fixture documents, ingested through the real pipeline.'
    )

    def add_arguments(self, parser):
        """Register preview mode."""
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would be created/ingested without writing',
        )
        parser.add_argument(
            '--break-glass',
            action='store_true',
            help='Explicitly allow seeding on a non-DEBUG deployment',
        )

    def handle(self, *args, **options):
        """Create eval entities, attach fixture docs, run the real ingest."""
        from django.conf import settings as django_settings

        from aichat.services import eval_fixtures

        dry_run = options['dry_run']
        if not dry_run:
            eval_fixtures.refuse_production(break_glass=options['break_glass'])
        if not dry_run and not getattr(
            django_settings, 'AIMMS_ATTACHMENT_RAG_ENABLED', False
        ):
            raise CommandError(
                'AIMMS_ATTACHMENT_RAG_ENABLED is off; seeding would create '
                'attachments that never ingest. Enable the flag first.'
            )
        if not dry_run:
            # R5 posture C: the Django flag defaults on, so also require an
            # EFFECTIVE ingest arm — seeding against a degraded plane would
            # create attachments whose only registry record is a
            # PIPELINE_DISABLED skip.
            from aichat.receivers import _any_ingest_effective

            if not _any_ingest_effective():
                raise CommandError(
                    'No ingest pipeline is effective (providers incomplete '
                    'or flags degraded); fix the AI-plane configuration first.'
                )

        fixtures_dir = _fixtures_dir()
        missing = [
            name for name, _owner in _FIXTURES if not (fixtures_dir / name).is_file()
        ]
        if missing:
            raise CommandError(f'Fixture documents missing: {", ".join(missing)}')

        from assets.models import AssetMachine
        from common.models import Attachment
        from part.models import Part

        if dry_run:
            self.stdout.write(f'DRY RUN — fixture set {_FIXTURE_SET_VERSION}')
        # S6: evaluation entities belong to the dedicated eval-fixtures
        # client, never the default internal tenant.
        eval_client, offlimits = eval_fixtures.ensure_eval_clients(dry_run=dry_run)

        owners = {}
        machine_defaults = {
            'client': eval_client,
            'serial': 'EVAL-HX200',
            'manufacturer': 'Eval Fixtures',
            'model': 'HX-200',
        }
        offlimits_defaults = {
            'client': offlimits,
            'serial': 'EVAL-ZR9',
            'manufacturer': 'Eval Fixtures',
            'model': 'ZR-9',
        }
        if dry_run:
            owners['machine'] = AssetMachine.objects.filter(
                name='RAG Eval HX-200 Heat Exchanger'
            ).first()
            owners['offlimits'] = AssetMachine.objects.filter(
                name='RAG Eval ZR-9 Compressor'
            ).first()
            owners['part'] = Part.objects.filter(
                name='RAG Eval HX-200 Gasket Set'
            ).first()
        else:
            owners['machine'], _ = AssetMachine.objects.get_or_create(
                name='RAG Eval HX-200 Heat Exchanger', defaults=machine_defaults
            )
            owners['offlimits'], _ = AssetMachine.objects.get_or_create(
                name='RAG Eval ZR-9 Compressor', defaults=offlimits_defaults
            )
            owners['part'], _ = Part.objects.get_or_create(
                name='RAG Eval HX-200 Gasket Set',
                defaults={'description': 'Reserved attachment-RAG eval fixture part'},
            )
            # S6 repair branch: get_or_create defaults are CREATE-only, so a
            # pre-S6 HX-200 row still owned by 'internal' is explicitly
            # re-pointed (manifest + save(), never QuerySet.update()), and
            # the gasket part is installed on the machine so its documents
            # follow the eval client instead of the 'internal' fallback.
            manifest: list[dict] = []
            moved = eval_fixtures.repoint_machine(
                owners['machine'], eval_client, manifest
            )
            eval_fixtures.ensure_gasket_link(
                owners['part'], owners['machine'], manifest
            )
            if moved:
                for line in eval_fixtures.restamp_fixture_scope(
                    machine_pks=(owners['machine'].pk,)
                ):
                    self.stdout.write(line)
            if manifest:
                self.stdout.write(eval_fixtures.render_manifest(manifest))

        from aichat.services.attachment_ingestion import run_ingest

        seeded = 0
        for file_name, owner_kind in _FIXTURES:
            owner = owners[owner_kind]
            model_type = 'part' if owner_kind == 'part' else 'assetmachine'
            if owner is None:
                self.stdout.write(f'{file_name}: owner absent (would create)')
                continue
            existing = Attachment.objects.filter(
                model_type=model_type, model_id=owner.pk, attachment__endswith=file_name
            ).first()
            if dry_run:
                state = 'attached' if existing else 'would attach + ingest'
                self.stdout.write(f'{file_name}: {state} ({model_type}={owner.pk})')
                continue
            attachment = existing
            if attachment is None:
                content = (fixtures_dir / file_name).read_bytes()
                attachment = Attachment.objects.create(
                    model_type=model_type,
                    model_id=owner.pk,
                    attachment=SimpleUploadedFile(file_name, content),
                    comment=f'RAG eval fixture ({_FIXTURE_SET_VERSION})',
                )
            # The real pipeline; a re-run on unchanged content short-circuits
            # on the sha, so the fixture set is never re-embedded by accident.
            row = run_ingest(attachment.pk)
            state = getattr(row, 'state', 'skipped-by-receiver')
            self.stdout.write(f'{file_name}: attachment={attachment.pk} state={state}')
            seeded += 1

        if not dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f'Fixture set {_FIXTURE_SET_VERSION}: {seeded} documents ensured'
                )
            )
