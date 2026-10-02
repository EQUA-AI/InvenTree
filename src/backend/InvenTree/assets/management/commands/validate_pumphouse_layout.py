"""Validate editable mimic assets and approved station coverage."""

import json
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from assets.models import AssetMachine
from machine_health.mimic_layout import layout_coverage, load_layout, validate_assets


class Command(BaseCommand):
    """Fail release validation on missing points or provisional layout review."""

    def add_arguments(self, parser):
        """Accept one station or validate all registered stations."""
        parser.add_argument('--station', type=int)
        parser.add_argument('--layout', type=Path)
        parser.add_argument(
            '--assets',
            type=Path,
            default=Path(__file__).resolve().parents[6]
            / 'src/frontend/src/assets/mimic',
        )
        parser.add_argument('--allow-incomplete', action='store_true')

    def handle(self, *args, **options):
        """Emit machine-readable coverage even when readiness fails."""
        try:
            self.validate(options)
        except (OSError, ValueError, KeyError, TypeError, ValidationError) as exc:
            raise CommandError(str(exc)) from exc

    def validate(self, options):
        """Inspect geometry and each registered station's point coverage."""
        layout = load_layout(options['layout'])
        validate_assets(layout, options['assets'])
        stations = AssetMachine.objects.filter(asset_type='pumphouse')
        if options['station'] is not None:
            stations = stations.filter(pk=options['station'])
        if not stations.exists():
            raise CommandError('No matching registered stations.')
        reports = [layout_coverage(station, layout) for station in stations]
        self.stdout.write(json.dumps(reports, indent=2))
        if not options['allow_incomplete'] and any(
            not report['ready'] for report in reports
        ):
            raise CommandError(
                'Layout needs plant review or approved point coverage; see report.'
            )
