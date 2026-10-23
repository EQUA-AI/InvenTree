"""Validate and preserve the observed 2026-10-21 compatibility boundary.

The original 17 definitions were not recovered. This is an explicit structural
checkpoint of the complete recorded batch, not a reconstruction of historical
data transformations or merge parents. Existing rows, permissions and authority
records are authoritative and are never rewritten or replayed here.
"""

import hashlib
import json
from pathlib import Path

from django.db.migrations.recorder import MigrationRecorder

import sqlparse

LEGACY_MIGRATIONS = (
    ('aichat', '0054_investigation_proposal_action'),
    ('assets', '0023_registry_recorded_facts'),
    ('repair', '0011_repair_finding_revisions'),
    ('repair', '0012_operational_authority'),
    ('tasks', '0031_investigation_requests'),
    ('tasks', '0032_closeoutcapture_voice_unavailable_reason'),
    ('tasks', '0033_operational_authority'),
    ('tasks', '0034_work_time_downtime'),
    ('tasks', '0035_process_decisions'),
    ('tasks', '0036_capture_originals_accepted_reports'),
    ('tasks', '0037_calibration_traceability'),
    ('tasks', '0038_routine_corrective_origin'),
    ('tasks', '0039_merge_r6_parallel'),
    ('tasks', '0040_merge_r6_o21'),
    ('tasks', '0041_db_defaults_for_old_code'),
    ('users', '0025_operating_authority_role'),
    ('users', '0026_ruleset_operating_authority_db_default'),
)
BASELINE = ('legacy_boundary', '0001_observed_schema_checkpoint')
MANIFEST_SHA256 = '9ab4a13d0a7e0d85ad8f46a214dc1f51d16a23393608d2ea712b37f0a67f874f'
SQL_SHA256 = '13773ca9c753534b1a3adfea012c0a2e80488140bc0c411cbdb0623d9cd51868'
CHECK_RENDERINGS_SHA256 = (
    'a99180abb6a351b388b3291ec786bda599afb8e5825fc11d880fea3722069925'
)


class BoundaryMismatchError(RuntimeError):
    """The selected database does not match the reconstructed checkpoint."""


def artifact(name, expected_hash):
    """Reject altered checkpoint metadata or SQL before trusting it."""
    data = Path(__file__).with_name(name).read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_hash:
        raise BoundaryMismatchError(f'Checkpoint artifact checksum mismatch: {name}')
    return data.decode()


def manifest():
    """Load the immutable catalog footprint without application configuration."""
    return json.loads(artifact('snapshot.json', MANIFEST_SHA256))


def check_renderings():
    """Load four exact equivalent varchar/text-array CHECK renderings.

    PostgreSQL may deparse a binary-compatible varchar-to-text array coercion
    per element or around the complete array. These pairs preserve the exact
    field, comparison and enum values. No general SQL normalization is used.
    """
    return json.loads(artifact('check_renderings.json', CHECK_RENDERINGS_SHA256))


def rows(connection, query, params=None):
    """Read metadata through the selected Django database connection."""
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        names = [column[0] for column in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def verify_schema(connection, *, require_focus=False):
    """Require the exact archived structures, defaults and enforced constraints.

    This reads catalog metadata only. Extra or missing columns, constraints,
    indexes, triggers or row-security rules on the archived tables refuse;
    selected compatibility fields on active tables must also match exactly.
    """
    expected = manifest()
    tables = set(expected['tables'])
    selected = {tuple(key) for key in expected['selected_columns']}
    actual = {
        name: rows(connection, query) for name, query in expected['queries'].items()
    }
    columns = {
        (row['table_name'], row['column_name']): row for row in actual['columns']
    }
    observed = [
        row
        for row in actual['columns']
        if row['table_name'] in tables
        or (row['table_name'], row['column_name']) in selected
    ]
    column_key = lambda row: (row['table_name'], row['column_name'])
    if sorted(observed, key=column_key) != sorted(expected['columns'], key=column_key):
        raise BoundaryMismatchError(
            'Legacy checkpoint columns, types, identity or SQL defaults differ'
        )
    proposal = [
        row
        for row in actual['columns']
        if row['table_name'] == 'aichat_chatactionproposal'
    ]
    if proposal != expected['proposal_columns']:
        raise BoundaryMismatchError(
            'Proposal storage differs from the observed checkpoint'
        )
    renderings = check_renderings()
    for category, key in [
        ('constraints', 'constraint_name'),
        ('indexes', 'index_name'),
    ]:
        required = {(row['table_name'], row[key]) for row in expected[category]}
        observed = [
            row
            for row in actual[category]
            if row['table_name'] in tables or (row['table_name'], row[key]) in required
        ]
        if category == 'constraints':
            canonical = {
                (row['table_name'], row[key]): row for row in expected[category]
            }
            for row in observed:
                variants = renderings.get(f'{row["table_name"]}.{row[key]}', ())
                original = canonical.get((row['table_name'], row[key]))
                if (
                    original
                    and original['definition'] in variants
                    and row['definition'] in variants
                ):
                    row['definition'] = original['definition']
        if observed != expected[category]:
            raise BoundaryMismatchError(f'Legacy checkpoint {category} differ')
    security = rows(
        connection,
        'SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace '
        "WHERE n.nspname='public' AND c.relname=ANY(%s) AND "
        '(c.relrowsecurity OR c.relforcerowsecurity OR EXISTS '
        '(SELECT 1 FROM pg_trigger t WHERE t.tgrelid=c.oid AND NOT t.tgisinternal) '
        'OR EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid=c.oid))',
        [sorted(tables)],
    )
    if security:
        raise BoundaryMismatchError(
            'Legacy checkpoint has unexpected triggers or row-security rules'
        )
    focus = columns.get(('aichat_chatthread', 'conversation_focus'))
    version = columns.get(('aichat_chatthread', 'focus_version'))
    if require_focus or focus or version:
        if (
            not focus
            or not version
            or focus['data_type'] != 'jsonb'
            or version['data_type'] != 'bigint'
            or focus['is_nullable'] != 'NO'
            or version['is_nullable'] != 'NO'
            or focus['column_default'] != "'{}'::jsonb"
            or version['column_default'] != '0'
        ):
            raise BoundaryMismatchError(
                'Focus columns must have their complete persistent defaults'
            )


def validate_recorded_boundary(connection):
    """Accept the whole observed batch or a fresh database; reject partials.

    Called before lineage planning, so naming the 17 records in ``replaces``
    cannot turn a structurally incompatible database into an accepted one.
    """
    applied = MigrationRecorder(connection).applied_migrations()
    present = set(applied).intersection(LEGACY_MIGRATIONS)
    if present and len(present) != len(LEGACY_MIGRATIONS):
        raise BoundaryMismatchError(
            'Partial legacy migration batch; checkpoint adoption refused'
        )
    if connection.vendor != 'postgresql':
        if present and BASELINE not in applied:
            raise BoundaryMismatchError(
                'Legacy checkpoint adoption is verified only on PostgreSQL'
            )
        return
    if present:
        verify_schema(connection)
    else:
        expected = manifest()
        existing = rows(
            connection,
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename=ANY(%s)",
            [expected['tables']],
        )
        if existing:
            raise BoundaryMismatchError(
                'Unrecorded legacy compatibility tables; bootstrap refused'
            )


def install_checkpoint(apps, schema_editor):
    """Create inert compatibility storage on fresh PostgreSQL installations.

    Django skips this replacement when all 17 historical records are already
    applied. In that case the preflight and the subsequent verification
    migration validate the existing schema without changing customer data.
    On SQLite/MySQL, ``ReplayedState`` creates the storage from the replaced
    migrations' schema operations instead.
    """
    connection = schema_editor.connection
    if connection.vendor != 'postgresql':
        return
    expected = manifest()
    existing = rows(
        connection,
        "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename=ANY(%s)",
        [expected['tables']],
    )
    if existing:
        raise BoundaryMismatchError(
            'Checkpoint bootstrap requires absent compatibility tables'
        )
    for statement in sqlparse.split(artifact('snapshot.sql', SQL_SHA256)):
        schema_editor.execute(statement)
    verify_schema(connection)


def verify_checkpoint(apps, schema_editor):
    """Validate the completed schema after focus and checkpoint converge."""
    if schema_editor.connection.vendor == 'postgresql':
        verify_schema(schema_editor.connection, require_focus=True)
