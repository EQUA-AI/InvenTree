"""Seed the reserved video-evidence eval fixture set (decision #13, R4).

One frozen ~130 s recording with three baked scene overlays (COUPLING
INSPECTION / SEAL REPLACEMENT / TORQUE CHECK) that segments into exactly
three windows at the 60/5 defaults, so each midpoint keyframe carries one
deterministic OCR anchor. The binary is committed (generation varies across
encoder versions and would silently mutate the sha). Idempotent: entities
are ``get_or_create``d, attachment identity is the fixture comment + name
stem (the shared media share dedupe-suffixes filenames — 2026-08-21 lesson),
and ``run_ingest`` short-circuits on unchanged bytes.

Fixture-set version: ``aimms-video-fixtures-v2``. Changing the fixture means
a NEW version — never an in-place edit. The photo set
(``aimms-media-fixtures-v1``) and its HX-200 machine/WO-EVAL-HX200 are NEVER
touched: the video work order uses a dedicated machine so machine-scoped
retrieval cannot perturb the frozen photo items' result sets.
"""

from __future__ import annotations

from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError

_FIXTURE_FILE = 'eval-hx200-seal-video.mp4'

_FIXTURE_SET_VERSION = 'aimms-video-fixtures-v2'

_EVAL_WO_REFERENCE = 'WO-EVAL-HX200-VIDEO'

# R5: the name and model must not contain the literal 'HX-200' — the R2
# attachment machine already owns that string, and the machine resolver
# (assets.ai_read.machines_page) icontains-matches name/serial/model, so the
# old 'RAG Eval HX-200 Video Heat Exchanger' made every bare "HX-200" query
# AMBIGUOUS and short-circuited retrieval into a clarification turn (live
# golden, 2026-09-01). The serial keeps its hyphenated 'EVAL-HX200-VIDEO'
# form: 'HX-200' is not a substring of it, and the golden items cite the
# work-order reference, never the machine name.
_VIDEO_MACHINE_NAME = 'RAG Eval Video Heat Exchanger'

_VIDEO_MACHINE_MODEL = 'HX-V200'

_VIDEO_MACHINE_SERIAL = 'EVAL-HX200-VIDEO'


def _fixtures_dir() -> Path:
    """Locate the checked-in fixture media next to the golden set."""
    import ai.core.evals as evals_pkg

    return Path(evals_pkg.__file__).resolve().parent / 'golden' / 'fixtures' / 'media'


class Command(BaseCommand):
    """Seed (idempotently) the reserved eval video WO and its recording."""

    help = (
        'Seed the reserved video-evidence eval fixture set '
        f'({_FIXTURE_SET_VERSION}): a dedicated eval work order with the '
        'frozen recording, ingested through the real pipeline.'
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
        """Create eval entities, attach the recording, run the real ingest."""
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
                    'Seeding would create a recording that never ingests; '
                    f'enable first: {", ".join(missing_flags)}'
                )

        source = _fixtures_dir() / _FIXTURE_FILE
        if not source.is_file():
            raise CommandError(f'Fixture recording missing: {_FIXTURE_FILE}')

        from tasks.models import WorkOrder

        from aichat.services import eval_fixtures
        from assets.models import AssetMachine
        from common.models import Attachment

        if dry_run:
            self.stdout.write(f'DRY RUN — fixture set {_FIXTURE_SET_VERSION}')
            hx200 = (
                AssetMachine.objects.filter(serial=_VIDEO_MACHINE_SERIAL).first()
                or AssetMachine.objects.filter(name=_VIDEO_MACHINE_NAME).first()
            )
            work_order = WorkOrder.objects.filter(reference=_EVAL_WO_REFERENCE).first()
        else:
            eval_fixtures.refuse_production(
                break_glass=options.get('break_glass', False)
            )
            # S6: the video fixture machine belongs to eval-fixtures too.
            eval_client, _offlimits = eval_fixtures.ensure_eval_clients(dry_run=False)
            # Serial-first: the machine's name changed in R5 (collision
            # repair above), and a name-keyed get_or_create would duplicate
            # every pre-R5 seed. The repair branch renames in place.
            hx200 = AssetMachine.objects.filter(serial=_VIDEO_MACHINE_SERIAL).first()
            if hx200 is None:
                hx200, _ = AssetMachine.objects.get_or_create(
                    name=_VIDEO_MACHINE_NAME,
                    defaults={
                        'client': eval_client,
                        'serial': _VIDEO_MACHINE_SERIAL,
                        'manufacturer': 'Eval Fixtures',
                        'model': _VIDEO_MACHINE_MODEL,
                    },
                )
            elif (
                hx200.name != _VIDEO_MACHINE_NAME or hx200.model != _VIDEO_MACHINE_MODEL
            ):
                hx200.name = _VIDEO_MACHINE_NAME
                hx200.model = _VIDEO_MACHINE_MODEL
                hx200.save(update_fields=['name', 'model'])
            work_order, work_order_created = WorkOrder.objects.get_or_create(
                reference=_EVAL_WO_REFERENCE,
                defaults={
                    'title': 'RAG Eval HX-200 Seal Recording',
                    'status': WorkOrder.STATUS_BACKLOG,
                    'priority': WorkOrder.PRIORITY_MEDIUM,
                    'machine': hx200,
                },
            )
            # S6 repair branch: a pre-S6 video machine still owned by
            # 'internal' is explicitly re-pointed and restamped.
            manifest: list[dict] = []
            if eval_fixtures.repoint_machine(hx200, eval_client, manifest):
                for line in eval_fixtures.restamp_fixture_scope(
                    machine_pks=(hx200.pk,), work_order_pks=(work_order.pk,)
                ):
                    self.stdout.write(line)
            if manifest:
                self.stdout.write(eval_fixtures.render_manifest(manifest))
            moved_existing_work_order = (
                not work_order_created and work_order.machine_id != hx200.pk
            )
            if moved_existing_work_order:
                # Repair early R4 seeds that shared the frozen photo machine.
                # QuerySet.update avoids scheduling a redundant async re-stamp;
                # the synchronous call below guarantees index coordinates are
                # repaired before this command reports success.
                WorkOrder.objects.filter(pk=work_order.pk).update(machine=hx200)
                work_order.machine = hx200

        if work_order is None:
            self.stdout.write(f'{_FIXTURE_FILE}: owner absent (would create)')
            return

        stem = _FIXTURE_FILE.rsplit('.', 1)[0]
        existing = Attachment.objects.filter(
            model_type='workorder',
            model_id=work_order.pk,
            comment=f'RAG eval fixture ({_FIXTURE_SET_VERSION})',
            attachment__contains=stem,
        ).first()
        if dry_run:
            state = 'attached' if existing else 'would attach + ingest'
            self.stdout.write(f'{_FIXTURE_FILE}: {state} (workorder={work_order.pk})')
            return

        attachment = existing
        if attachment is None:
            attachment = Attachment.objects.create(
                model_type='workorder',
                model_id=work_order.pk,
                attachment=SimpleUploadedFile(_FIXTURE_FILE, source.read_bytes()),
                comment=f'RAG eval fixture ({_FIXTURE_SET_VERSION})',
            )

        from aichat.services.attachment_ingestion import run_ingest

        row = run_ingest(attachment.pk)
        state = str(getattr(row, 'state', 'not-ingested'))
        self.stdout.write(f'{_FIXTURE_FILE}: attachment={attachment.pk} state={state}')
        if state != 'indexed':
            raise CommandError(f'Fixture recording did not reach the index ({state})')
        if moved_existing_work_order:
            from aichat.services.attachment_ingestion import (
                restamp_work_order_media_client_codes,
            )

            restamp_work_order_media_client_codes(work_order.pk)
        self.stdout.write(
            self.style.SUCCESS(
                f'Fixture set {_FIXTURE_SET_VERSION}: recording ensured '
                f'(segments={getattr(row, "segment_count", "?")})'
            )
        )
