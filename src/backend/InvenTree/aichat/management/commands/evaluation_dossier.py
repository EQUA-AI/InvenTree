"""Frozen-config dossier emitter (S15, §13.5 evaluation-version pins).

One JSON document naming every §13.5 pin: code revision, provider and
deployment coordinates, model pins, per-purpose deployment routing, judge
identity, corpus manifests (registry-row hashes), search indexes, the
full flag registry with effective values, redacted non-secret config, and
the gold revision. ``evaluation_version`` is derived — sha256 over the
canonical JSON of the pins (volatile fields excluded) — so ANY material
change yields a new version by construction (§13.5).

Content-free: manifests are hashes over (id, revision, sha) rows; no
document text, prompt, or customer identifier appears. Battery runners
consume the file via ``run_battery --dossier`` (keeping eval principals
non-staff).
"""

import hashlib
import json
import os
from pathlib import Path

from django.core.management.base import BaseCommand


def _sha(data: str) -> str:
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


def _manifest_hash(rows) -> dict:
    ordered = sorted(str(row) for row in rows)
    return {
        'count': len(ordered),
        'manifest_sha256': _sha('\n'.join(ordered)) if ordered else '',
    }


class Command(BaseCommand):
    """Emit the frozen-configuration dossier."""

    help = 'Emit the §13.5 frozen-config dossier (read-only, content-free).'

    def add_arguments(self, parser):
        """Register output options."""
        parser.add_argument(
            '--out', default='', help='Write to a file (default stdout)'
        )
        parser.add_argument('--pretty', action='store_true')

    def handle(self, *args, **options):
        """Assemble the pins and derive the evaluation version."""
        pins = {
            'code_revision': self._code_revision(),
            'prompt_template_revisions': {'covered_by': 'code_revision'},
            'provider': self._provider(),
            'model_pins': self._model_pins(),
            'model_purposes': self._model_purposes(),
            'judge': self._judge(),
            'corpus': self._corpus(),
            'search_indexes': self._search_indexes(),
            'flags': self._flags(),
            'config_non_secret': self._config(),
            'gold': self._gold(),
        }
        canonical = json.dumps(pins, sort_keys=True, separators=(',', ':'), default=str)
        document = {
            'dossier_version': 1,
            'evaluation_version': _sha(canonical)[:16],
            'pins': pins,
        }
        rendered = json.dumps(
            document, indent=2 if options['pretty'] else None, default=str
        )
        if options['out']:
            Path(options['out']).write_text(rendered + '\n', encoding='utf-8')
            self.stdout.write(f'wrote {options["out"]}')
        else:
            self.stdout.write(rendered)

    # ------------------------------------------------------------------ #
    def _code_revision(self):
        try:
            import InvenTree.version as version

            return {
                'commit': version.inventreeCommitHash(),
                'date': version.inventreeCommitDate(),
                'version': version.inventreeVersion(),
            }
        except Exception:
            return {'commit': '', 'date': '', 'version': ''}

    def _settings(self):
        from ai.core.config import get_settings

        return get_settings()

    def _provider(self):
        settings = self._settings()
        endpoint = str(getattr(settings, 'azure_openai_endpoint', '') or '')
        host = endpoint.split('//')[-1].split('/')[0] if endpoint else ''
        return {
            'endpoint_host': host,
            'api_version_chat': getattr(settings, 'azure_openai_api_version', ''),
            'api_version_responses': getattr(settings, 'azure_luna_api_version', ''),
            'deployment_standard': getattr(settings, 'azure_openai_deployment', ''),
            'deployment_fast': getattr(settings, 'azure_openai_fast_deployment', ''),
            'deployment_luna': getattr(settings, 'azure_luna_deployment', ''),
            'embedding_deployment': getattr(
                settings, 'azure_openai_embedding_deployment', ''
            ),
        }

    def _model_pins(self):
        settings = self._settings()
        return {
            'boot_probe_enabled': bool(
                getattr(settings, 'model_version_boot_probe_enabled', False)
            ),
            'expected_model': getattr(settings, 'azure_openai_expected_model', ''),
            'expected_fast_model': getattr(
                settings, 'azure_openai_expected_fast_model', ''
            ),
            'expected_embedding_model': getattr(
                settings, 'azure_openai_expected_embedding_model', ''
            ),
            'resolved_note': (
                'live resolution is stamped per-turn in metadata.model_versions'
            ),
        }

    def _model_purposes(self):
        try:
            from ai.core.model_policy import ModelPurpose, select_deployment

            return {
                purpose.value: select_deployment(purpose) for purpose in ModelPurpose
            }
        except Exception:
            return {}

    def _judge(self):
        try:
            from ai.core.evals.battery_judge import battery_judge_fingerprint
            from ai.core.evals.judge import _judge_client_config, judge_fingerprint

            _, _, _, deployment = _judge_client_config()
            return {
                'deployment': deployment,
                'golden_fingerprint': judge_fingerprint(),
                'battery_fingerprint': battery_judge_fingerprint(),
            }
        except Exception:
            return {}

    def _corpus(self):
        from aichat.models import AttachmentIngest, ControlledDocument

        controlled = _manifest_hash(
            f'{row["scope_key"]}|{row["document_id"]}|{row["revision"]}|{row["source_sha256"]}'
            for row in ControlledDocument.objects.values(
                'scope_key', 'document_id', 'revision', 'source_sha256'
            )
        )
        ingests = _manifest_hash(
            f'{row["model_type"]}|{row["model_id"]}|{row["source_sha256"]}'
            for row in AttachmentIngest.objects.values(
                'model_type', 'model_id', 'source_sha256'
            )
        )
        fixture_sets = []
        for module_name in (
            'seed_attachment_eval_fixtures',
            'seed_media_eval_fixtures',
            'seed_video_eval_fixtures',
            'seed_analysis_eval_fixtures',
        ):
            try:
                module = __import__(
                    f'aichat.management.commands.{module_name}', fromlist=['x']
                )
                fixture_sets.append(module._FIXTURE_SET_VERSION)
            except Exception:
                continue
        return {
            'controlled_documents': controlled,
            'attachment_ingests': ingests,
            'fixture_sets': fixture_sets,
            'golden_corpus_env': os.environ.get('AIMMS_GOLDEN_CORPUS', ''),
        }

    def _search_indexes(self):
        settings = self._settings()
        return {
            'controlled_documents': getattr(
                settings, 'azure_search_controlled_documents_index', ''
            ),
            'attachments': getattr(settings, 'azure_search_attachment_docs_index', ''),
            'media': getattr(settings, 'azure_search_media_index', ''),
            'documents': getattr(settings, 'azure_search_documents_index', ''),
            'embedding_dimensions': getattr(
                settings, 'controlled_document_embedding_dimensions', 0
            ),
        }

    def _flags(self):
        from django.conf import settings as django_settings

        from aimms_flags import REGISTRY

        settings = self._settings()
        rows = []
        for entry in REGISTRY:
            effective = None
            if entry.ai_field and hasattr(settings, entry.ai_field):
                effective = getattr(settings, entry.ai_field)
            elif hasattr(django_settings, entry.env_name):
                effective = getattr(django_settings, entry.env_name)
            rows.append({
                'env_name': entry.env_name,
                'planes': entry.planes,
                'default': entry.default,
                'effective': effective,
            })
        return rows

    def _config(self):
        from ai.core.config import redact_config

        return redact_config(self._settings().model_dump(mode='json'))

    def _gold(self):
        gold_dir = os.environ.get('AIMMS_GOLD_DIR', '')
        entry = {'path_env': 'AIMMS_GOLD_DIR', 'available': False, 'sha256': ''}
        if gold_dir:
            questions = Path(gold_dir) / 'questions.yaml'
            if questions.is_file():
                entry['available'] = True
                entry['sha256'] = _sha(questions.read_text(encoding='utf-8'))
        return entry
