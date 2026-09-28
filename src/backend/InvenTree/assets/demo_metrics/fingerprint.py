"""Runtime target fingerprinting for the demo metrics commands.

A fingerprint combines the configured server/database identity (host, port and
configured database name from the Django connection settings — never
credentials) with ``current_database()``, the connection backend/vendor, the
effective role, the server version and the schema/migration identity. A
hostname alone is not proof of schema compatibility or of the identity of a
restored database, and credentials are never read or hashed here.

Code/image identity is tracked separately from the database fingerprint: the
running image attests the commit and image digest it was built from through
``AIMMS_APPROVED_COMMIT_SHA`` / ``AIMMS_APPROVED_IMAGE_DIGEST`` (set by the
reviewed Job/web environment), and the apply preflight *compares* that
attestation with the approved mapping — an absent or different attestation is
refused, never merely stored.
"""

from __future__ import annotations

import hashlib
import json
import os

from django.db import connection


class RuntimeGateError(Exception):
    """The runtime does not satisfy a hard prerequisite for the operation."""

    code = 'RUNTIME_GATE'

    def __init__(self, message: str, code: str = 'RUNTIME_GATE'):
        """Carry a stable machine-readable code plus a human message."""
        self.code = code
        super().__init__(message)


def require_postgresql() -> None:
    """Fail closed unless the default connection is PostgreSQL.

    The apply/replay/stop paths use PostgreSQL transaction-level advisory locks
    and partial unique constraints as concurrency backstops; there is no
    bypass flag for tests or local runs.
    """
    if connection.vendor != 'postgresql':
        raise RuntimeGateError(
            'PostgreSQL is required for governed demo metrics writes; '
            f'connected backend is {connection.vendor!r}'
        )


def _scalar(sql: str, default: str = '') -> str:
    with connection.cursor() as cursor:
        cursor.execute(sql)
        row = cursor.fetchone()
    return str(row[0]) if row and row[0] is not None else default


def migration_state_hash() -> str:
    """Hash of the applied migration set (schema identity)."""
    with connection.cursor() as cursor:
        cursor.execute('SELECT app, name FROM django_migrations ORDER BY app, name')
        rows = cursor.fetchall()
    body = json.dumps(rows, separators=(',', ':'))
    return hashlib.sha256(body.encode()).hexdigest()


def configured_database_identity() -> dict:
    """Non-secret configured connection identity (host/port/database name)."""
    settings_dict = connection.settings_dict
    return {
        'host': str(settings_dict.get('HOST') or ''),
        'port': str(settings_dict.get('PORT') or ''),
        'configured_database': str(settings_dict.get('NAME') or ''),
    }


def runtime_fingerprint() -> dict:
    """Return the non-secret runtime identity of the connected database."""
    configured = configured_database_identity()
    if connection.vendor == 'postgresql':
        database = _scalar('SELECT current_database()')
        role = _scalar('SELECT current_user')
        server_version = _scalar('SELECT version()')
    else:
        database = configured['configured_database']
        role = ''
        server_version = _scalar('SELECT sqlite_version()', 'unknown')
    return {
        'vendor': connection.vendor,
        'host': configured['host'],
        'port': configured['port'],
        'configured_database': configured['configured_database'],
        'database': database,
        'role': role,
        'server_version': server_version.split(' on ')[0][:120],
        'migrations': migration_state_hash(),
    }


def fingerprint_id() -> str:
    """Stable hash of the runtime fingerprint (never contains credentials)."""
    body = json.dumps(runtime_fingerprint(), sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(body.encode()).hexdigest()


#: Environment attestations of the running code/image identity. Set by the
#: reviewed deployment (Job/web environment), never derived from user input.
CODE_IDENTITY_ENV = 'AIMMS_APPROVED_COMMIT_SHA'
IMAGE_IDENTITY_ENV = 'AIMMS_APPROVED_IMAGE_DIGEST'


def runtime_code_identity() -> dict:
    """Attested code/image identity of the running process, or None values.

    The deployment declares what immutable code and image this process runs.
    ``None`` means *unverified* — the apply preflight refuses it rather than
    storing an approved value it cannot compare against the runtime.
    """
    return {
        'commit_sha': os.environ.get(CODE_IDENTITY_ENV) or None,
        'image_digest': os.environ.get(IMAGE_IDENTITY_ENV) or None,
    }


def require_runtime_identity_approved(
    approved_commit: str, approved_image: str
) -> None:
    """Compare the runtime attestation with the approved code/image identity.

    Fail closed on an unverified runtime and on any difference: the approved
    values are compared here, never merely stored on the session row.
    """
    attested = runtime_code_identity()
    if not attested['commit_sha'] or not attested['image_digest']:
        raise RuntimeGateError(
            'Runtime code/image identity is not attested; set '
            f'{CODE_IDENTITY_ENV} and {IMAGE_IDENTITY_ENV} to the reviewed '
            'image identity before applying',
            'CODE_IDENTITY_UNVERIFIED',
        )
    if attested['commit_sha'] != approved_commit:
        raise RuntimeGateError(
            'Running code commit does not match the approved commit identity',
            'CODE_IDENTITY_MISMATCH',
        )
    if attested['image_digest'] != approved_image:
        raise RuntimeGateError(
            'Running image digest does not match the approved image identity',
            'CODE_IDENTITY_MISMATCH',
        )


def assert_schema_unchanged(planned_fingerprint: dict) -> None:
    """Fail closed when the schema/migration identity drifted since planning.

    A plan records the schema identity observed at planning time; writes are
    refused when the applied migration set no longer matches it (replan
    instead of writing against a drifted schema).
    """
    current = runtime_fingerprint()
    for key in ('vendor', 'migrations'):
        if current.get(key) != planned_fingerprint.get(key):
            raise RuntimeGateError(
                f'Runtime {key} identity changed since the plan was created; replan',
                'SCHEMA_CHANGED',
            )
