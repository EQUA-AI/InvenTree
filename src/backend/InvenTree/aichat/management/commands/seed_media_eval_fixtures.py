"""Seed the reserved evidence-media eval fixture set (decision #13, R3).

The live media corpus mutates on every upload, so golden-eval items pin a
dedicated, never-mutated fixture set: a reserved eval work order carrying a
synthetic nameplate photo, and an off-limits machine photo, checked into
``ai/core/evals/golden/fixtures/media/``. The images are committed binaries
by design — Pillow text rendering varies across versions, and a generated
image would silently change sha, mutating the fixture set unversioned.
Idempotent by construction — entities are ``get_or_create``d and re-running
``run_ingest`` on unchanged bytes short-circuits on the sha.

Fixture-set version: ``aimms-media-fixtures-v1``. Changing any fixture's
content requires a NEW version (new files, new pin in items.yaml, new value
in ``AIMMS_GOLDEN_CORPUS``) — never an in-place edit. The doc fixture set
(``aimms-attachment-fixtures-v1``) stays frozen; this command reuses its
eval entities by name but never touches its documents.

The ZR-9 photo lands on the machine owned by the dedicated ``eval-offlimits``
client that no operator scope grants, powering the media cross-client denial
item: retrieval must treat that photo as nonexistent (decision #16's direct
``machine.client.code`` derivation is exactly what it exercises).
"""

from __future__ import annotations

from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError

#: Fixture images, relative to the fixtures directory:
#: (file name, owner kind). ``workorder`` -> the eval WO on the HX-200,
#: ``offlimits`` -> the off-scope ZR-9 machine (assetmachine-owned).
_FIXTURES = (
    ('eval-hx200-nameplate.png', 'workorder'),
    ('eval-zr9-offlimits-nameplate.png', 'offlimits'),
)

_FIXTURE_SET_VERSION = 'aimms-media-fixtures-v1'

_EVAL_WO_REFERENCE = 'WO-EVAL-HX200'


def _fixtures_dir() -> Path:
    """Locate the checked-in fixture images next to the golden set."""
    import ai.core.evals as evals_pkg

    return Path(evals_pkg.__file__).resolve().parent / 'golden' / 'fixtures' / 'media'


class Command(BaseCommand):
    """Seed (idempotently) the reserved eval WO/machines and their photos."""

    help = (
        'Seed the reserved evidence-media eval fixture set '
        f'({_FIXTURE_SET_VERSION}): eval work order + off-limits machine '
        'with their fixture photos, ingested through the real pipeline.'
    )

    def add_arguments(self, parser):
        """Register preview mode."""
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would be created/ingested without writing',
        )

    def handle(self, *args, **options):
        """Create eval entities, attach fixture photos, run the real ingest."""
        from django.conf import settings as django_settings

        dry_run = options['dry_run']
        if not dry_run:
            missing_flags = [
                flag
                for flag in ('AIMMS_ATTACHMENT_RAG_ENABLED', 'AIMMS_MEDIA_RAG_ENABLED')
                if not getattr(django_settings, flag, False)
            ]
            try:
                from ai.core.config import get_settings

                if not get_settings().feature_media_rag_ingest:
                    missing_flags.append('FEATURE_MEDIA_RAG_INGEST')
            except Exception as exc:
                raise CommandError(
                    'AI configuration failed to load; fix it before seeding'
                ) from exc
            if missing_flags:
                raise CommandError(
                    'Seeding would create photos that never ingest; enable '
                    f'first: {", ".join(missing_flags)}'
                )

        fixtures_dir = _fixtures_dir()
        missing = [
            name for name, _owner in _FIXTURES if not (fixtures_dir / name).is_file()
        ]
        if missing:
            raise CommandError(f'Fixture images missing: {", ".join(missing)}')

        from tasks.models import WorkOrder

        from assets.models import AssetMachine, Client, get_default_client
        from common.models import Attachment

        if dry_run:
            self.stdout.write(f'DRY RUN — fixture set {_FIXTURE_SET_VERSION}')
        internal = get_default_client()
        if dry_run:
            offlimits = Client.objects.filter(code='eval-offlimits').first()
        else:
            offlimits, _ = Client.objects.get_or_create(
                code='eval-offlimits',
                defaults={'name': 'RAG Eval Off-Limits Client', 'active': True},
            )

        # Same eval entities as seed_attachment_eval_fixtures, by name.
        if dry_run:
            hx200 = AssetMachine.objects.filter(
                name='RAG Eval HX-200 Heat Exchanger'
            ).first()
            zr9 = AssetMachine.objects.filter(name='RAG Eval ZR-9 Compressor').first()
            work_order = WorkOrder.objects.filter(reference=_EVAL_WO_REFERENCE).first()
        else:
            hx200, _ = AssetMachine.objects.get_or_create(
                name='RAG Eval HX-200 Heat Exchanger',
                defaults={
                    'client': internal,
                    'serial': 'EVAL-HX200',
                    'manufacturer': 'Eval Fixtures',
                    'model': 'HX-200',
                },
            )
            zr9, _ = AssetMachine.objects.get_or_create(
                name='RAG Eval ZR-9 Compressor',
                defaults={
                    'client': offlimits,
                    'serial': 'EVAL-ZR9',
                    'manufacturer': 'Eval Fixtures',
                    'model': 'ZR-9',
                },
            )
            work_order, _ = WorkOrder.objects.get_or_create(
                reference=_EVAL_WO_REFERENCE,
                defaults={
                    'title': 'RAG Eval HX-200 Evidence',
                    'status': WorkOrder.STATUS_BACKLOG,
                    'priority': WorkOrder.PRIORITY_MEDIUM,
                    'machine': hx200,
                },
            )

        owners = {'workorder': work_order, 'offlimits': zr9}

        from aichat.services.attachment_ingestion import run_ingest

        seeded = 0
        failures: list[str] = []
        for file_name, owner_kind in _FIXTURES:
            owner = owners[owner_kind]
            model_type = 'workorder' if owner_kind == 'workorder' else 'assetmachine'
            if owner is None:
                self.stdout.write(f'{file_name}: owner absent (would create)')
                continue
            # The storage layer may dedupe-suffix file names (the two envs
            # share one media file share), so an endswith match on the
            # original name can never be idempotent there — the fixture
            # comment is the stable identity (live finding, 2026-08-21:
            # a re-run double-seeded the HX-200 photo on dev).
            stem = file_name.rsplit('.', 1)[0]
            existing = Attachment.objects.filter(
                model_type=model_type,
                model_id=owner.pk,
                comment=f'RAG eval fixture ({_FIXTURE_SET_VERSION})',
                attachment__contains=stem,
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
            state = str(getattr(row, 'state', 'not-ingested'))
            self.stdout.write(f'{file_name}: attachment={attachment.pk} state={state}')
            if state != 'indexed':
                failures.append(f'{file_name} ({state})')
                continue
            seeded += 1

        if not dry_run:
            if failures:
                raise CommandError(
                    'Fixture photos did not reach the index: ' + ', '.join(failures)
                )
            self.stdout.write(
                self.style.SUCCESS(
                    f'Fixture set {_FIXTURE_SET_VERSION}: {seeded} photos ensured'
                )
            )
