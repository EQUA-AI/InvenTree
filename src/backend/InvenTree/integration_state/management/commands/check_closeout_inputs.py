"""Inspect candidate-bound source-owner review input without granting authority."""

import hashlib
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from integration_state.closeout_inputs import input_template, validate_inputs
from integration_state.contract_audit import build_inventory, check_baseline

MAX_INPUT_BYTES = 2 * 1024 * 1024


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate review object key')
        value[key] = item
    return value


def _read(path):
    with Path(path).open('rb') as stream:
        data = stream.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError('Bounded review inputs are required')
    return data, json.loads(data, object_pairs_hook=_unique_object)


class Command(BaseCommand):
    """Validate exact source/metadata coverage; preserve unknown and unreviewed facts."""

    help = 'Check closeout review input shape/binding/coverage; never approve source access or acceptance'
    requires_system_checks = []

    def add_arguments(self, parser):
        """Require explicit local manifest/inventory and an input or template path."""
        parser.add_argument('--manifest', required=True)
        parser.add_argument('--inventory', required=True)
        selection = parser.add_mutually_exclusive_group(required=True)
        selection.add_argument('--input')
        selection.add_argument('--write-template')
        parser.add_argument('--require-all-field-decisions', action='store_true')

    def handle(self, *args, **options):
        """Perform metadata/file checks only; no source/client rows or remote I/O."""
        try:
            manifest_bytes, manifest = _read(options['manifest'])
            inventory_bytes, inventory = _read(options['inventory'])
            if (
                type(manifest) is not dict
                or type(manifest.get('files')) is not list
                or not 1 <= len(manifest['files']) <= 20000
            ):
                raise ValueError('A selected candidate manifest is required')
            names = set()
            for item in manifest['files']:
                if (
                    type(item) is not dict
                    or set(item) != {'path', 'sha256', 'size'}
                    or type(item['path']) is not str
                    or not item['path']
                    or Path(item['path']).is_absolute()
                    or '..' in Path(item['path']).parts
                    or item['path'] in names
                    or type(item['size']) is not int
                    or item['size'] < 0
                ):
                    raise ValueError(
                        'Exact unique selected manifest paths are required'
                    )
                if (
                    type(item['sha256']) is not str
                    or len(item['sha256']) != 64
                    or any(c not in '0123456789abcdef' for c in item['sha256'])
                ):
                    raise ValueError('Exact selected file digests are required')
                names.add(item['path'])
            check_baseline(build_inventory(), inventory)
            bindings = {
                'manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
                'inventory_sha256': hashlib.sha256(inventory_bytes).hexdigest(),
            }
            if options['write_template']:
                value = input_template(**bindings)
                with Path(options['write_template']).open(
                    'x', encoding='utf-8'
                ) as stream:
                    json.dump(value, stream, indent=2)
                    stream.write('\n')
            else:
                _, value = _read(options['input'])
            result = validate_inputs(value, inventory=inventory, **bindings)
            if (
                options['require_all_field_decisions']
                and result['missing_field_decisions']
            ):
                raise ValueError('Required field decisions remain undescribed')
        except (OSError, KeyError, TypeError, ValueError):
            raise CommandError(
                'Invalid, stale or incomplete closeout review inputs; no authority or acceptance conferred'
            ) from None
        self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
