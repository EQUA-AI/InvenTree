"""Source call-site evidence never becomes authority or deployed completeness."""

import copy
import hashlib
import io
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from integration_state import writer_audit


class NativeWriterAuditTests(SimpleTestCase):
    """Audit actual source and reject hidden omissions without executing it."""

    def source(self, body, path='tasks/service.py'):
        """Parse an explicitly local code fixture, never a semantic policy oracle."""
        return writer_audit.inspect_source(path, body.encode())

    def test_model_aliases_hooks_raw_storage_and_delivery_are_review_leads(self):
        """Identify concrete mutation/storage/SQL surfaces with safe coordinates."""
        row = self.source("""from tasks.models import WorkOrder as Job
from django.http import FileResponse
class Writer:
    def perform_create(self, serializer):
        Job.objects.filter(pk=1).update(title="private payload")
        serializer.save()
        cursor.execute("DELETE FROM private_table WHERE secret='do-not-print'")
        storage.open("private/path", mode="wb")
        return FileResponse(stream)
""")
        findings = row['findings']
        update = next(x for x in findings if x['operation'] == 'update')
        self.assertEqual(update['model_hint'], 'tasks.workorder')
        self.assertEqual(update['target'], 'Job.objects.filter().update')
        self.assertEqual(update['symbol'], 'Writer.perform_create')
        self.assertEqual(update['line'], 5)
        self.assertEqual(
            {x['category'] for x in findings},
            {
                'writer_hook_definition',
                'orm_candidate',
                'orm_or_storage_candidate',
                'sql_candidate',
                'storage_open_candidate',
                'serialization_or_delivery_candidate',
            },
        )
        self.assertEqual(
            next(x for x in findings if x['operation'] == 'open')['open_access_hint'],
            'write',
        )
        encoded = writer_audit.encode_inventory(row)
        for secret in [
            'private payload',
            'private_table',
            'do-not-print',
            'private/path',
        ]:
            self.assertNotIn(secret, encoded)

    def test_source_is_never_executed_and_literals_are_not_reported(self):
        """Imports, exceptions, paths and credential-shaped literals stay inert."""
        body = 'import nonexistent_native_module\nraise RuntimeError("hidden-token")\nfile.write("hidden-original")\n'
        row = self.source(body)
        self.assertEqual(row['sha256'], hashlib.sha256(body.encode()).hexdigest())
        self.assertEqual(row['findings'][0]['operation'], 'write')
        encoded = writer_audit.encode_inventory(row)
        self.assertNotIn('hidden-token', encoded)
        self.assertNotIn('hidden-original', encoded)
        self.assertNotIn('nonexistent_native_module', encoded)

    def test_native_model_methods_and_dynamic_receivers_are_not_inferred(self):
        """Native class hints are local; dynamic values stay unproven."""
        row = self.source(
            """class WorkOrder:
    def save(self):
        self.save_record()
        super().save()
        self.delete()
        owners["private-owner"].update(value=1)
""",
            path='tasks/models.py',
        )
        hook = next(
            x for x in row['findings'] if x['category'] == 'writer_hook_definition'
        )
        self.assertEqual(hook['model_hint'], 'tasks.workorder')
        self.assertEqual(
            next(x for x in row['findings'] if x['operation'] == 'delete')[
                'model_hint'
            ],
            'tasks.workorder',
        )
        dynamic = next(x for x in row['findings'] if x['operation'] == 'update')
        self.assertIsNone(dynamic['model_hint'])
        self.assertIn('<dynamic>', dynamic['target'])
        self.assertNotIn('private-owner', writer_audit.encode_inventory(row))
        self.assertEqual(
            self.source('item.save()\n', path='InvenTree.py')['path'], 'InvenTree.py'
        )

    def test_open_mode_hints_never_emit_unrecognized_or_dynamic_values(self):
        """Read/open observations remain leads, not assumed write or copy proof."""
        row = self.source(
            'open("p", "rb")\nopen("p", "r+b")\nopen("p", unknown)\nopen("p", "hidden-mode")\nopen("p")\n'
        )
        self.assertEqual(
            [x['open_access_hint'] for x in row['findings']],
            [
                'read',
                'read_write',
                'dynamic',
                'unknown',
                'implicit_read_or_custom_default',
            ],
        )
        self.assertNotIn('hidden-mode', writer_audit.encode_inventory(row))
        methods = self.source(
            'path.open("wb")\nstorage.open("private", "rb")\npath.open()\n'
        )
        self.assertEqual(
            [x['open_access_hint'] for x in methods['findings']],
            [
                'method_positional_unresolved',
                'method_positional_unresolved',
                'method_default_unresolved',
            ],
        )

    def test_invalid_source_and_unsafe_or_oversized_inputs_refuse_sanitized(self):
        """Parse failures cannot silently return partial or empty success."""
        for path, body in [
            ('../outside.py', b'x=1'),
            ('/outside.py', b'x=1'),
            ('file.txt', b'x=1'),
            ('bad.py', b'private_secret = ('),
            ('bad.py', b'\xff'),
        ]:
            with self.subTest(path=path), self.assertRaises(ValueError) as caught:
                writer_audit.inspect_source(path, body)
            self.assertNotIn('private_secret', str(caught.exception))
        with (
            patch.object(writer_audit, 'MAX_FILE_BYTES', 4),
            self.assertRaises(ValueError),
        ):
            self.source('value = 1')

    def test_bounded_tree_scope_migrations_and_source_drift_are_exact(self):
        """Unexecuted source changes still invalidate an exact audit baseline."""
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as name:
            root = Path(name)
            (root / 'service.py').write_text('value.save()\n')
            (root / 'test_service.py').write_text('raise RuntimeError()\n')
            (root / 'conftest.py').write_text('raise RuntimeError()\n')
            (root / 'tests').mkdir()
            (root / 'tests/fixture.py').write_text('secret.write()\n')
            (root / 'migrations').mkdir()
            (root / 'migrations/0001_initial.py').write_text('cursor.execute(sql)\n')
            current = writer_audit.build_inventory(root)
            self.assertEqual(current['excluded_test_files'], 3)
            self.assertEqual(
                [x['path'] for x in current['files']],
                ['migrations/0001_initial.py', 'service.py'],
            )
            self.assertEqual(current['files'][0]['source_role'], 'migration')
            self.assertTrue(
                writer_audit.check_baseline(current, copy.deepcopy(current))
            )
            (root / 'service.py').write_text(
                'value.save()\n# changed unexecuted context\n'
            )
            changed = writer_audit.build_inventory(root)
            self.assertNotEqual(
                current['source_vector_sha256'], changed['source_vector_sha256']
            )
            with self.assertRaises(ValueError):
                writer_audit.check_baseline(changed, current)
            alias = copy.deepcopy(current)
            alias['writer_inventory_complete'] = 0
            with self.assertRaises(ValueError):
                writer_audit.check_baseline(current, alias)
            for key in [
                'writer_inventory_complete',
                'copy_instance_inventory_complete',
                'confers_authority',
                'capture_qualified',
                'erasure_authorized',
                'original_acceptance_complete',
                'queries_source_rows',
                'executes_source',
            ]:
                self.assertFalse(current[key], key)

    def test_empty_file_byte_and_finding_bounds_do_not_truncate_success(self):
        """Every resource-bound breach refuses the whole inventory."""
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as name:
            root = Path(name)
            with self.assertRaises(ValueError):
                writer_audit.build_inventory(root)
            (root / 'a.py').write_text('a.save()\na.delete()\n')
            (root / 'b.py').write_text('b.save()\n')
            for bound, value in [
                ('MAX_FILES', 1),
                ('MAX_FILE_BYTES', 2),
                ('MAX_TOTAL_BYTES', 5),
                ('MAX_FINDINGS', 1),
            ]:
                with (
                    self.subTest(bound=bound),
                    patch.object(writer_audit, bound, value),
                    self.assertRaises(ValueError),
                ):
                    writer_audit.build_inventory(root)

    def test_symlinked_files_directories_and_root_are_rejected(self):
        """A source inventory cannot silently follow or omit linked trees."""
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as name:
            parent = Path(name)
            root = parent / 'root'
            root.mkdir()
            (root / 'good.py').write_text('x.save()\n')
            outside = parent / 'outside.py'
            outside.write_text('secret.delete()\n')
            link = root / 'link.py'
            link.symlink_to(outside)
            with self.assertRaises(ValueError):
                writer_audit.build_inventory(root)
            link.unlink()
            link.symlink_to(parent, target_is_directory=True)
            with self.assertRaises(ValueError):
                writer_audit.build_inventory(root)
            link.unlink()
            alias = parent / 'alias'
            alias.symlink_to(root, target_is_directory=True)
            with self.assertRaises(ValueError):
                writer_audit.build_inventory(alias)

    def test_read_and_mid_read_change_faults_fail_without_details(self):
        """Unreadable/moving source is unavailable, not a partial clean report."""
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as name:
            root = Path(name)
            (root / 'source.py').write_text('x.save()\n')
            with (
                patch.object(
                    Path, 'open', side_effect=OSError('hidden-file-or-secret')
                ),
                self.assertRaises(ValueError) as caught,
            ):
                writer_audit.build_inventory(root)
            self.assertNotIn('hidden-file-or-secret', str(caught.exception))
            original_open = Path.open

            # Mutate via the original opener to avoid recursive patching.
            def mutated_open(path, *args, **kwargs):
                stream = original_open(path, *args, **kwargs)
                with original_open(path, 'wb') as writer:
                    writer.write(b'x.delete()\n')
                return stream

            with (
                patch.object(Path, 'open', mutated_open),
                self.assertRaises(ValueError),
            ):
                writer_audit.build_inventory(root)

    def test_directory_walk_errors_and_nonregular_source_refuse(self):
        """An unreadable subtree or pipe cannot become omitted or blocking source."""
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as name:
            root = Path(name)
            (root / 'good.py').write_text('x.save()\n')

            def unreadable(*args, **kwargs):
                kwargs['onerror'](PermissionError('hidden-directory'))
                return iter(())

            with (
                patch.object(writer_audit.os, 'walk', unreadable),
                self.assertRaises(ValueError) as caught,
            ):
                writer_audit.build_inventory(root)
            self.assertNotIn('hidden-directory', str(caught.exception))
            if hasattr(os, 'mkfifo'):
                os.mkfifo(root / 'pipe.py')
                with self.assertRaises(ValueError):
                    writer_audit.build_inventory(root)

    def test_actual_native_command_queries_no_database_and_records_partial_scope(self):
        """Actual source hashes/coordinates are reviewable; no row/API authority."""
        output = io.StringIO()
        call_command('audit_integration_writers', stdout=output)
        value = json.loads(output.getvalue())
        self.assertGreater(len(value['files']), 100)
        self.assertGreater(value['candidate_call_sites'], 100)
        self.assertFalse(value['writer_inventory_complete'])
        self.assertFalse(value['copy_instance_inventory_complete'])
        self.assertTrue(value['unresolved'])
        self.assertTrue(any(x['path'] == 'tasks/models.py' for x in value['files']))
        target = Path(os.environ['TMPDIR']) / 'native-writer-inventory.json'
        target.write_text(writer_audit.encode_inventory(value))
        checked = io.StringIO()
        call_command('audit_integration_writers', check_baseline=target, stdout=checked)
        self.assertEqual(json.loads(checked.getvalue()), value)
        altered = copy.deepcopy(value)
        altered['writer_inventory_complete'] = True
        target.write_text(json.dumps(altered))
        with self.assertRaises(CommandError):
            call_command(
                'audit_integration_writers', check_baseline=target, stdout=io.StringIO()
            )
        target.write_text(writer_audit.encode_inventory(value))
