"""Read-only source call-site leads; never a deployed writer/copy inventory."""

import ast
import hashlib
import json
import os
import stat
from pathlib import Path

SCHEMA = 'aimms.native-writer-audit.v1'
MAX_FILES = 4096
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_FINDINGS = 20000
MODEL_NAMES = {
    'assets.models': (
        'Client',
        'AssetLocation',
        'AssetMachine',
        'AssetMaintenanceRecord',
    ),
    'common.models': ('Attachment',),
    'tasks.models': (
        'WorkOrder',
        'WorkOrderProcedureApplication',
        'WorkOrderStepExecution',
        'WorkOrderCloseout',
        'AcceptedCloseoutReport',
    ),
    'repair.models': ('RepairPacket', 'RepairPacketGate', 'RepairPacketEvidence'),
}
ORM_CALLS = frozenset({
    'create',
    'get_or_create',
    'update_or_create',
    'bulk_create',
    'bulk_update',
    'update',
    'delete',
    '_raw_delete',
    'save',
})
SQL_CALLS = frozenset({'execute', 'executemany', 'executescript', 'raw', 'RunSQL'})
STORAGE_CALLS = frozenset({
    'write',
    'writelines',
    'write_bytes',
    'write_text',
    'upload',
    'upload_file',
    'upload_fileobj',
    'put_object',
    'delete_object',
    'remove',
    'unlink',
    'rmdir',
    'rename',
    'replace',
    'copy',
    'copyfile',
    'copy2',
    'copytree',
    'move',
})
EXPORT_CALLS = frozenset({
    'FileResponse',
    'StreamingHttpResponse',
    'HttpResponse',
    'sendfile',
    'dump',
    'dumps',
    'serialize',
})
WRITER_HOOKS = frozenset({
    'save',
    'delete',
    'create',
    'update',
    'save_model',
    'save_related',
    'delete_model',
    'delete_queryset',
    'perform_create',
    'perform_update',
    'perform_destroy',
    'before_import',
    'after_import',
    'import_data',
})


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _encoded(value):
    return json.dumps(
        value, sort_keys=True, separators=(',', ':'), allow_nan=False
    ).encode()


def _module(path):
    parts = list(Path(path).with_suffix('').parts)
    if len(parts) > 1 and parts[0] == 'InvenTree':
        parts.pop(0)
    if parts[-1] == '__init__':
        parts.pop()
    return '.'.join(parts)


def _target(node, depth=0):
    """Describe identifiers only; never emit literal arguments or expressions."""
    if depth > 12:
        return '<dynamic>'
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _target(node.value, depth + 1) + '.' + node.attr
    if isinstance(node, ast.Call):
        return _target(node.func, depth + 1) + '()'
    return '<dynamic>'


def _open_access(node):
    mode = next((k.value for k in node.keywords if k.arg == 'mode'), None)
    if mode is None and isinstance(node.func, ast.Attribute):
        return (
            'method_positional_unresolved' if node.args else 'method_default_unresolved'
        )
    if mode is None and len(node.args) > 1:
        mode = node.args[1]
    if mode is None:
        return 'implicit_read_or_custom_default'
    if not isinstance(mode, ast.Constant) or type(mode.value) is not str:
        return 'dynamic'
    if mode.value not in (
        'r',
        'rb',
        'rt',
        'w',
        'wb',
        'wt',
        'a',
        'ab',
        'at',
        'x',
        'xb',
        'xt',
        'r+',
        'rb+',
        'r+b',
        'w+',
        'wb+',
        'w+b',
        'a+',
        'ab+',
        'a+b',
    ):
        return 'unknown'
    if '+' in mode.value:
        return 'read_write'
    return 'read' if mode.value.startswith('r') else 'write'


def inspect_source(path, data):
    """Parse source without imports/execution; hints require human reconciliation."""
    if type(path) is not str or type(data) is not bytes:
        raise ValueError('A bounded relative Python source is required')
    relative = Path(path)
    if (
        type(path) is not str
        or relative.is_absolute()
        or '..' in relative.parts
        or relative.suffix != '.py'
        or type(data) is not bytes
        or len(data) > MAX_FILE_BYTES
    ):
        raise ValueError('A bounded relative Python source is required')
    try:
        tree = ast.parse(data, filename='<native-source>')
    except (SyntaxError, ValueError, RecursionError):
        raise ValueError('Native Python source cannot be parsed') from None
    module = _module(path)
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0:
            for name in node.names:
                if name.name in MODEL_NAMES.get(node.module, ()):
                    aliases[name.asname or name.name] = (
                        node.module.split('.')[0] + '.' + name.name.lower()
                    )
    local_models = {
        name: module.split('.')[0] + '.' + name.lower()
        for name in MODEL_NAMES.get(module, ())
    }
    findings = []
    stack = [(tree, (), None)]
    while stack:
        node, symbols, native_class = stack.pop()
        if isinstance(node, ast.ClassDef):
            symbols = (*symbols, node.name)
            native_class = local_models.get(node.name)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols = (*symbols, node.name)
            if node.name in WRITER_HOOKS:
                findings.append({
                    'line': node.lineno,
                    'column': node.col_offset,
                    'symbol': '.'.join(symbols),
                    'operation': node.name,
                    'category': 'writer_hook_definition',
                    'target': node.name,
                    'model_hint': native_class,
                })
        if isinstance(node, ast.Call):
            target = _target(node.func)
            operation = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else None
            )
            category = (
                'orm_or_storage_candidate'
                if operation in ('save', 'delete')
                else 'orm_candidate'
                if operation in ORM_CALLS
                else 'sql_candidate'
                if operation in SQL_CALLS
                else 'storage_candidate'
                if operation in STORAGE_CALLS
                else 'serialization_or_delivery_candidate'
                if operation in EXPORT_CALLS
                else 'storage_open_candidate'
                if operation == 'open'
                else None
            )
            if category:
                root = target.split('.', 1)[0].removesuffix('()')
                hint = (
                    native_class
                    if root in ('self', 'cls')
                    else aliases.get(root) or local_models.get(root)
                )
                row = {
                    'line': node.lineno,
                    'column': node.col_offset,
                    'symbol': '.'.join(symbols) or '<module>',
                    'operation': operation,
                    'category': category,
                    'target': target,
                    'model_hint': hint,
                }
                if operation == 'open':
                    row['open_access_hint'] = _open_access(node)
                findings.append(row)
        if len(findings) > MAX_FINDINGS:
            raise ValueError('Native call-site inventory exceeds the bound')
        stack.extend(
            (child, symbols, native_class) for child in ast.iter_child_nodes(node)
        )
    findings.sort(key=lambda row: (row['line'], row['column'], row['category']))
    return {
        'path': path,
        'sha256': _hash(data),
        'size': len(data),
        'source_role': 'migration' if 'migrations' in relative.parts else 'runtime',
        'module_model_hints': sorted(
            set(aliases.values()) | set(local_models.values())
        ),
        'findings': findings,
    }


def build_inventory(source_root=None):
    """Read bounded native Python only; fail on source faults instead of truncation."""
    root = Path(source_root) if source_root is not None else Path(__file__).parents[2]
    if root.is_symlink() or not root.is_dir():
        raise ValueError('An ordinary native source directory is required')
    root = root.resolve(strict=True)
    files, total, finding_count, excluded_tests = [], 0, 0, 0

    def walk_error(error):
        raise error

    try:
        for directory, dirs, names in os.walk(
            root, followlinks=False, onerror=walk_error
        ):
            parent = Path(directory)
            for name in dirs:
                if (parent / name).is_symlink():
                    raise ValueError('Native source symlink is unsupported')
            dirs[:] = sorted(name for name in dirs if name != '__pycache__')
            for name in sorted(names):
                if not name.endswith('.py'):
                    continue
                path = parent / name
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    raise ValueError('Native source symlink is unsupported')
                relative = path.relative_to(root)
                if (
                    'tests' in relative.parts
                    or name.startswith(('test_', 'tests.'))
                    or name in ('tests.py', 'test.py', 'conftest.py')
                ):
                    excluded_tests += 1
                    continue
                if len(files) >= MAX_FILES:
                    raise ValueError('Native source file inventory exceeds the bound')
                before = path.stat()
                if not stat.S_ISREG(before.st_mode):
                    raise ValueError('Native source must be a regular file')
                with path.open('rb') as stream:
                    data = stream.read(MAX_FILE_BYTES + 1)
                after = path.stat()
                if (
                    before.st_dev,
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                    raise ValueError('Native source changed during read')
                total += len(data)
                if total > MAX_TOTAL_BYTES:
                    raise ValueError('Native source byte inventory exceeds the bound')
                row = inspect_source(relative.as_posix(), data)
                finding_count += len(row['findings'])
                if finding_count > MAX_FINDINGS:
                    raise ValueError('Native call-site inventory exceeds the bound')
                files.append(row)
    except OSError:
        raise ValueError('Native source inventory is unavailable') from None
    if not files:
        raise ValueError('Native source inventory is empty')
    files.sort(key=lambda row: row['path'])
    return {
        'schema': SCHEMA,
        'kind': 'source_call_site_leads',
        'source_scan_complete_for_declared_scope': True,
        'scope': 'native backend Python including migrations; tests/ paths, test_*.py, tests.*.py, tests.py, test.py and conftest.py excluded',
        'excluded_test_files': excluded_tests,
        'source_bytes': total,
        'source_vector_sha256': _hash(
            _encoded([{k: row[k] for k in ('path', 'sha256', 'size')} for row in files])
        ),
        'files': files,
        'candidate_call_sites': finding_count,
        'queries_source_rows': False,
        'executes_source': False,
        'confers_authority': False,
        'writer_inventory_complete': False,
        'copy_instance_inventory_complete': False,
        'capture_qualified': False,
        'erasure_authorized': False,
        'original_acceptance_complete': False,
        'unresolved': [
            'candidate_calls_may_be_reads_or_unrelated_operations',
            'model_hints_are_syntactic_not_proven_dispatch',
            'dynamic_aliases_reflection_and_indirect_calls',
            'inherited_orm_admin_serializer_importer_and_plugin_writers',
            'external_database_and_storage_writers',
            'shell_sql_templates_other_languages_and_runtime_generated_code',
            'deployed_configuration_and_actual_copy_instances',
            'transaction_capture_restore_retention_and_backup_qualification',
        ],
    }


def check_baseline(current, baseline):
    """Require exact source/finding/scope reconciliation; no automatic approval."""
    if (
        type(baseline) is not dict
        or baseline.get('schema') != SCHEMA
        or _encoded(current) != _encoded(baseline)
    ):
        raise ValueError('Native writer source inventory changed')
    return True


def encode_inventory(value):
    """Stable review metadata contains no code excerpts or literal payloads."""
    return json.dumps(value, sort_keys=True, indent=2) + '\n'
