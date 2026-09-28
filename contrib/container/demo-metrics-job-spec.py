#!/usr/bin/env python3
"""Render and validate the EQUA demo metrics one-shot Job execution spec.

Offline only. This script reads an explicit non-secret input file, validates it
fail-closed, and writes an Azure Container Apps Job configuration document that
an operator may review. It performs no network calls, never provisions
anything, and never starts a Job. Provisioning and execution require separate,
explicit human approvals (see demo-metrics-runbook.md).

Allowed inputs are non-secret identity, immutable image references, approved
SECRET REFERENCES (names only), and the single reviewed one-shot command.
Inline secrets, mutable image tags, unresolved placeholders, digest
mismatches, migration commands, non-approved commands, unknown or duplicated
switches and non-allowlisted environment names are refused.

The rendered Job environment always carries the two runtime attestation names
the backend apply preflight requires (``AIMMS_APPROVED_COMMIT_SHA`` /
``AIMMS_APPROVED_IMAGE_DIGEST``, exact ``fingerprint.py`` contract). Their
values are derived from the approved input identities (``git_commit`` and the
single digest shared by the Job, web and worker images); an explicit
declaration must match the derived values exactly — conflicting, blank or
malformed attestations are refused rather than attesting arbitrary code.
These are operator declarations of the running identity, not cryptographic
proof of the image contents.

The rendered envelope is a review artifact, NOT an Azure ARM/CLI document:
the approved deployment step translates it field by field (see
demo-metrics-runbook.md). Credential-shaped values are refused by pattern as
defense in depth, but pattern matching is NOT proof that no secret is present;
the real controls are the explicit environment/secret allowlists and the
rule that no secret value ever belongs in this input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys

ARTIFACT = 'aimms-demo-metrics-job-spec'
VERSION = 3

#: Tokens that mean an operator still has to fill in a value. Anything
#: carrying one of these is refused, never silently rendered.
PLACEHOLDERS = ('<', '>', 'CHANGEME', 'REPLACE_WITH', 'PLACEHOLDER', 'TODO', 'XXXX')

#: The only six commands the Job may ever run (plan section 13.2/13.3).
ALLOWED_COMMANDS = (
    'plan_demo_metrics',
    'apply_demo_metrics',
    'verify_demo_metrics',
    'replay_demo_metrics',
    'stop_demo_metrics',
    'cleanup_demo_metrics',
)

#: Strict per-command option grammar, mirroring the add_arguments() of the
#: management commands in src/backend/InvenTree/assets/management/commands/
#: exactly. Nothing outside these tables may appear in command_args.
#: Kinds:
#:   'value'  - takes one non-empty string value
#:   'hash'   - takes one value that must be 64 lowercase hex characters
#:   'actor'  - takes one non-empty operator username
#:   'number' - takes one finite numeric value (bounds checked below)
#:   'bool'   - explicit flag (store_true): takes NO value
#: Note: --max-duration-seconds is required explicitly for replay even
#: though the backend has a default — the renderer demands explicit
#: boundedness. --include-history is only allowlisted on apply, matching the
#: backend declaration; if the backend moves/adds it, this table must be
#: re-reviewed (renderer policy, not a backend edit).
OPTION_SPECS = {
    'plan_demo_metrics': {
        'required': {
            '--fixture': 'value',
            '--mapping': 'value',
            '--out': 'value',
            '--actor': 'actor',
        },
        'optional': {},
    },
    'apply_demo_metrics': {
        'required': {
            '--fixture': 'value',
            '--mapping': 'value',
            '--plan': 'value',
            '--approved-plan-sha256': 'hash',
            '--actor': 'actor',
        },
        'optional': {'--include-history': 'bool'},
    },
    'verify_demo_metrics': {
        'required': {'--session': 'value', '--actor': 'actor'},
        'optional': {},
    },
    'replay_demo_metrics': {
        'required': {
            '--session': 'value',
            '--actor': 'actor',
            '--max-duration-seconds': 'number',
        },
        'optional': {'--interval-seconds': 'number'},
    },
    'stop_demo_metrics': {
        'required': {'--session': 'value', '--actor': 'actor'},
        'optional': {},
    },
    'cleanup_demo_metrics': {
        'required': {'--session': 'value', '--actor': 'actor'},
        'optional': {
            '--out': 'value',
            '--apply': 'bool',
            '--approved-cleanup-sha256': 'hash',
        },
    },
}

#: Django/global switches that must never reach the command line: they can
#: point the process at unreviewed settings or code paths.
FORBIDDEN_SWITCHES = {
    '--settings',
    '--pythonpath',
    '--skip-checks',
    '--traceback',
    '--no-color',
    '--force-color',
    '--version',
    '--verbosity',
    '-v',
    '--help',
    '-h',
}

#: Django project layout inside the reviewed image (plan section 13.2).
MANAGE_PY = '/home/inventree/src/backend/InvenTree/manage.py'
BACKEND_DIR = '/home/inventree/src/backend/InvenTree'
#: The image WORKDIR is fixed and NOT changed at runtime: Container Apps has
#: no working-directory override. The absolute manage.py path is what
#: bootstraps sys.path for the Django project.
IMAGE_WORKDIR = '/home/inventree'

SHA256_RE = re.compile(r'^sha256:[0-9a-f]{64}$')
HEX64_RE = re.compile(r'^[0-9a-f]{64}$')
COMMIT_RE = re.compile(r'^[0-9a-f]{40}$')
ENV_NAME_RE = re.compile(r'^[A-Z][A-Z0-9_]*$')
SECRET_REF_RE = re.compile(r'^[a-z0-9][a-z0-9\-]{1,62}$')
JOB_NAME_RE = re.compile(r'^[a-z][a-z0-9\-]{2,40}$')
RESOURCE_GROUP_RE = re.compile(r'^[A-Za-z0-9_\-.()]{1,90}$')
LOCATION_RE = re.compile(r'^[a-z0-9]{2,40}$')
UUID_RE = (
    r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}'
    r'-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
)
RESOURCE_ID_RE = re.compile(
    r'^/subscriptions/' + UUID_RE + r'/resourceGroups/[A-Za-z0-9_\-.()]{1,90}'
    r'/providers/Microsoft\.App/managedEnvironments/[A-Za-z0-9\-]{1,60}$'
)
PY_RE = re.compile(r'^/(.*/)?python(3(\.\d+)?)?$')

#: Values that are secrets even when the variable name looks harmless.
#: Best-effort defense in depth only — a pass is NOT proof of no secret.
CREDENTIAL_VALUE_RE = (
    re.compile(r'://[^/\s:@]+:[^/\s@]+@'),  # scheme://user:password@host
    re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----'),
    re.compile(
        r'(?i)(accountkey|sharedaccesskey|sig|password|passwd|secret|token)\s*=\s*\S'
    ),
    re.compile(r'(?i)bearer\s+[A-Za-z0-9._\-]{16,}'),
)

#: Variable names that must never carry an inline value.
SECRETISH_NAME_RE = re.compile(
    r'(?i)(PASSWORD|PASSWD|SECRET|TOKEN|KEY|CREDENTIAL|CONNECTION_?STRING|DSN|AUTH)'
)

#: Runtime code/image attestation environment names — the EXACT names the
#: backend apply preflight requires (assets/demo_metrics/fingerprint.py:
#: CODE_IDENTITY_ENV / IMAGE_IDENTITY_ENV). Their values are DERIVED from the
#: approved input identities (git_commit and expected_image_digest) and
#: validated against any explicit declaration; conflicting, blank or
#: malformed attestations are refused so the Job never attests code or an
#: image other than the reviewed one. These are operator declarations of the
#: running identity, not cryptographic proof of the image contents.
COMMIT_ATTESTATION_ENV = 'AIMMS_APPROVED_COMMIT_SHA'
IMAGE_ATTESTATION_ENV = 'AIMMS_APPROVED_IMAGE_DIGEST'
ATTESTATION_ENV_NAMES = (COMMIT_ATTESTATION_ENV, IMAGE_ATTESTATION_ENV)

#: The ONLY environment names allowed to carry an explicit inline value:
#: non-secret database topology (host/name/user/port/engine + TLS options),
#: the two runtime attestation declarations above (strictly validated), code
#: identity, and the minimal app configuration the Job needs. Explicit
#: allowlist, not pattern matching: anything else (plugin/email/AI/unknown
#: switches) is refused. The DB user is a non-secret role name; its password
#: must always come via secret_references.
INLINE_ENV_ALLOWLIST = (
    'INVENTREE_AUTO_UPDATE',
    'INVENTREE_LOG_LEVEL',
    'INVENTREE_DB_ENGINE',
    'INVENTREE_DB_NAME',
    'INVENTREE_DB_USER',
    'INVENTREE_DB_HOST',
    'INVENTREE_DB_PORT',
    'INVENTREE_DB_OPTIONS',
    'INVENTREE_COMMIT_HASH',
    'INVENTREE_COMMIT_DATE',
    *ATTESTATION_ENV_NAMES,
)

#: The ONLY names allowed in secret_references (names only, never values).
SECRET_ENV_ALLOWLIST = ('INVENTREE_DB_PASSWORD', 'INVENTREE_SECRET_KEY')

#: argv tokens that would run schema changes. The web app is the single
#: migration owner; this Job must never migrate.
MIGRATION_TOKENS = {'migrate', 'makemigrations', 'migrate_schemas', 'update', 'invoke'}

#: Numeric bounds for the bounded replay feed (mirrors the backend bounds in
#: replay_demo_metrics: 0 <= interval, 0 < duration <= 3600).
MAX_INTERVAL_SECONDS = 3600.0
MAX_DURATION_SECONDS = 3600.0


class SpecError(Exception):
    """A sanitized refusal reason safe for deployment logs."""

    def __init__(self, code: str, message: str):
        """Carry a stable refusal code without exposing the rejected input."""
        super().__init__(f'{code}: {message}')
        self.code = code


def require(condition: bool, code: str, message: str) -> None:
    """Refuse inputs which do not satisfy the named contract condition."""
    if not condition:
        raise SpecError(code, message)


def _walk_strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _walk_strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_strings(item)


def reject_placeholders(data) -> None:
    """Refuse any unresolved placeholder anywhere in the input document."""
    for text in _walk_strings(data):
        for token in PLACEHOLDERS:
            require(
                token not in text,
                'UNRESOLVED_PLACEHOLDER',
                f'Unresolved placeholder token in input: {token}',
            )


def _image_digest(reference: str, code: str) -> str:
    """Return the sha256 digest of a digest-pinned image reference."""
    require('@' in reference, code, 'Image reference must be pinned by digest')
    require(reference.count('@') == 1, code, 'Malformed image reference')
    name, digest = reference.rsplit('@', 1)
    require(bool(SHA256_RE.match(digest)), code, 'Image digest must be sha256:<64 hex>')
    segments = name.split('/')
    require(bool(segments[-1]) and ' ' not in name, code, 'Malformed image name')
    require(
        ':' not in segments[-1], code, 'Mutable image tags are refused; pin a digest'
    )
    return digest


def _require_no_credential_values(node, where: str) -> None:
    for text in _walk_strings(node):
        for pattern in CREDENTIAL_VALUE_RE:
            require(
                not pattern.search(text),
                'INLINE_SECRET',
                f'Secret-looking value refused in {where}',
            )


def _derive_attestations(data) -> dict:
    """Derive the runtime attestation values from the approved input.

    The Job must attest exactly the reviewed commit and the single immutable
    image digest shared by the Job, web and worker identities. Values are
    derived from the approved input wherever it supplies them; a missing or
    malformed source is refused outright rather than attesting arbitrary code.
    """
    commit = data.get('git_commit')
    require(
        isinstance(commit, str) and bool(commit.strip()),
        'ATTESTATION_MISSING',
        'A reviewed git_commit is required to attest the runtime commit identity',
    )
    require(
        bool(COMMIT_RE.match(commit)),
        'BAD_ATTESTATION',
        'The attested commit identity must be 40 lowercase hex characters',
    )
    image_digest = data.get('expected_image_digest')
    require(
        isinstance(image_digest, str) and bool(image_digest.strip()),
        'ATTESTATION_MISSING',
        'A reviewed expected_image_digest is required to attest the runtime '
        'image identity',
    )
    require(
        bool(SHA256_RE.match(image_digest)),
        'BAD_ATTESTATION',
        'The attested image identity must be sha256:<64 lowercase hex>',
    )
    return {COMMIT_ATTESTATION_ENV: commit, IMAGE_ATTESTATION_ENV: image_digest}


def _validate_env(environment, secret_references, attestations) -> list:
    """Validate explicit settings and secret references; return spec env.

    ``attestations`` carries the values derived from the approved input; an
    explicit attestation declaration must match them exactly (conflicting,
    blank or malformed attestations are refused) and is rendered once.
    """
    require(isinstance(environment, list), 'BAD_ENV', 'environment must be a list')
    require(
        isinstance(secret_references, list),
        'BAD_SECRET_REFS',
        'secret_references must be a list',
    )
    spec_env = []
    seen_names = set()
    secret_names = set()
    for entry in secret_references:
        require(
            isinstance(entry, dict) and set(entry) == {'name', 'secret_ref'},
            'BAD_SECRET_REFS',
            'Each secret reference needs exactly name and secret_ref',
        )
        name, ref = entry['name'], entry['secret_ref']
        require(
            isinstance(name, str) and bool(ENV_NAME_RE.match(name)),
            'BAD_SECRET_REFS',
            'Bad secret env name',
        )
        require(
            isinstance(ref, str) and bool(SECRET_REF_RE.match(ref)),
            'BAD_SECRET_REFS',
            'Bad secret reference name',
        )
        require(
            name in SECRET_ENV_ALLOWLIST,
            'BAD_SECRET_REFS',
            f'{name} is not an approved secret reference name',
        )
        require(
            name not in secret_names, 'BAD_SECRET_REFS', 'Duplicate secret reference'
        )
        secret_names.add(name)
        spec_env.append({'name': name, 'secretRef': ref})
    for entry in environment:
        require(
            isinstance(entry, dict) and set(entry) == {'name', 'value'},
            'BAD_ENV',
            'Each environment entry needs exactly name and value',
        )
        name, value = entry['name'], entry['value']
        require(
            isinstance(name, str) and bool(ENV_NAME_RE.match(name)),
            'BAD_ENV',
            'Bad environment name',
        )
        require(isinstance(value, str), 'BAD_ENV', 'Environment values must be strings')
        require(
            name not in seen_names, 'BAD_ENV', f'Duplicate environment name: {name}'
        )
        require(
            name not in secret_names,
            'INLINE_SECRET',
            'A secret-referenced name must not also carry an inline value',
        )
        _require_no_credential_values([value], 'environment')
        require(
            not SECRETISH_NAME_RE.search(name),
            'INLINE_SECRET',
            'Secret-named settings must use secret_references, never inline values',
        )
        if name in ATTESTATION_ENV_NAMES:
            # Runtime attestation declarations are strictly validated against
            # the identity derived from the approved input; the derived entry
            # is rendered below exactly once.
            require(
                bool(value.strip()),
                'ATTESTATION_MISSING',
                f'{name} must carry the reviewed identity, never a blank value',
            )
            pattern = COMMIT_RE if name == COMMIT_ATTESTATION_ENV else SHA256_RE
            require(
                bool(pattern.match(value)),
                'BAD_ATTESTATION',
                f'{name} must be the reviewed SHA identity in canonical form',
            )
            require(
                value == attestations[name],
                'ATTESTATION_CONFLICT',
                f'{name} conflicts with the reviewed input identity',
            )
            seen_names.add(name)
            continue
        require(
            name in INLINE_ENV_ALLOWLIST,
            'ENV_NOT_ALLOWED',
            f'{name} is not an allowlisted non-secret setting',
        )
        seen_names.add(name)
        spec_env.append({'name': name, 'value': value})
    for name in ATTESTATION_ENV_NAMES:
        # Derived from the approved input; an explicit declaration above was
        # required to match these values exactly.
        spec_env.append({'name': name, 'value': attestations[name]})
    settings = {entry['name']: entry['value'] for entry in spec_env if 'value' in entry}
    require(
        settings.get('INVENTREE_AUTO_UPDATE') == 'False',
        'MIGRATIONS_NOT_DISABLED',
        'INVENTREE_AUTO_UPDATE=False must be set explicitly (one migration owner)',
    )
    return spec_env


def _missing_value_code(flag: str) -> str:
    return 'MISSING_ACTOR' if flag == '--actor' else 'MISSING_FLAG'


def _looks_numeric(token: str) -> bool:
    try:
        float(token)
    except ValueError:
        return False
    return True


def _validate_args(command: str, args) -> tuple:
    """Parse command_args against the strict per-command grammar.

    Returns (argv_tail, parsed) where parsed carries validated numeric values.
    Rejects unknown/forbidden/duplicate switches, positional extras, missing
    or switch-shaped values, and malformed numbers.
    """
    require(isinstance(args, list), 'BAD_ARGS', 'command_args must be a list')
    require(
        all(isinstance(a, str) for a in args),
        'BAD_ARGS',
        'command_args must be strings',
    )
    require(
        not any(token.strip('-') in MIGRATION_TOKENS for token in args),
        'MIGRATION_COMMAND',
        'Schema/migration invocations are refused; the web app owns migrations',
    )
    spec = OPTION_SPECS[command]
    kinds = {**spec['required'], **spec['optional']}
    known = {}
    index = 0
    while index < len(args):
        token = args[index]
        require(
            token not in FORBIDDEN_SWITCHES,
            'FORBIDDEN_FLAG',
            f'{token} is refused: config-influencing switches are never allowed',
        )
        if not token.startswith('--'):
            require(
                token.startswith('-'),
                'BAD_ARGS',
                'Positional arguments are refused; use explicit switches only',
            )
            raise SpecError('UNKNOWN_FLAG', f'Unknown option for {command}: {token}')
        require(
            token in kinds, 'UNKNOWN_FLAG', f'{token} is not an option of {command}'
        )
        kind = kinds[token]
        require(token not in known, 'DUPLICATE_FLAG', f'{token} appears more than once')
        if kind == 'bool':
            known[token] = True
            index += 1
            continue
        require(
            index + 1 < len(args), _missing_value_code(token), f'{token} needs a value'
        )
        value = args[index + 1]
        switch_shaped = value.startswith('-') and not (
            kind == 'number' and _looks_numeric(value)
        )
        require(
            bool(value.strip()) and not switch_shaped,
            _missing_value_code(token),
            f'{token} needs a value',
        )
        known[token] = value
        index += 2

    for flag in spec['required']:
        require(flag in known, _missing_value_code(flag), f'{command} requires {flag}')

    numbers = {}
    for flag, kind in kinds.items():
        if flag not in known:
            continue
        if kind == 'hash':
            require(
                bool(HEX64_RE.match(known[flag])),
                'BAD_HASH',
                f'{flag} must be 64 lowercase hex characters',
            )
        elif kind == 'number':
            try:
                parsed = float(known[flag])
            except ValueError as error:
                raise SpecError('BAD_ARGS', f'{flag} must be a number') from error
            require(
                math.isfinite(parsed), 'BAD_ARGS', f'{flag} must be a finite number'
            )
            numbers[flag] = parsed

    if '--max-duration-seconds' in numbers:
        duration = numbers['--max-duration-seconds']
        require(
            0 < duration <= MAX_DURATION_SECONDS,
            'BAD_ARGS',
            f'--max-duration-seconds must be in (0, {MAX_DURATION_SECONDS:g}]',
        )
    if '--interval-seconds' in numbers:
        interval = numbers['--interval-seconds']
        require(
            0 <= interval <= MAX_INTERVAL_SECONDS,
            'BAD_ARGS',
            f'--interval-seconds must be in [0, {MAX_INTERVAL_SECONDS:g}]',
        )
    if '--interval-seconds' in numbers and '--max-duration-seconds' in numbers:
        require(
            numbers['--interval-seconds'] <= numbers['--max-duration-seconds'],
            'BAD_ARGS',
            '--interval-seconds must not exceed --max-duration-seconds',
        )

    if command == 'cleanup_demo_metrics':
        if known.get('--apply'):
            require(
                '--approved-cleanup-sha256' in known,
                'MISSING_FLAG',
                'cleanup --apply requires --approved-cleanup-sha256',
            )
        else:
            require(
                '--approved-cleanup-sha256' not in known,
                'BAD_ARGS',
                '--approved-cleanup-sha256 is only valid together with --apply',
            )
    return [command, *args], numbers


def build_job_spec(data: dict) -> dict:
    """Validate explicit inputs and render the bounded one-shot Job spec."""
    require(isinstance(data, dict), 'BAD_INPUT', 'Input must be a JSON object')
    allowed_keys = {
        'job_name',
        'resource_group',
        'location',
        'environment_resource_id',
        'git_commit',
        'image_reference',
        'expected_image_digest',
        'web_image_reference',
        'worker_image_reference',
        'python_interpreter',
        'working_directory',
        'command',
        'command_args',
        'replica_timeout_seconds',
        'environment',
        'secret_references',
    }
    require(
        set(data) == allowed_keys,
        'BAD_INPUT',
        'Input keys must be exactly the documented non-secret fields',
    )
    reject_placeholders(data)
    _require_no_credential_values(data, 'the input document')

    for field in (
        'job_name',
        'resource_group',
        'location',
        'environment_resource_id',
        'git_commit',
        'image_reference',
        'expected_image_digest',
        'web_image_reference',
        'worker_image_reference',
        'python_interpreter',
        'working_directory',
        'command',
    ):
        require(isinstance(data[field], str), 'BAD_INPUT', f'{field} must be a string')

    require(bool(JOB_NAME_RE.match(data['job_name'])), 'BAD_NAME', 'Bad job name')
    require(
        bool(RESOURCE_GROUP_RE.match(data['resource_group'])),
        'BAD_INPUT',
        'Bad resource group name',
    )
    require(bool(LOCATION_RE.match(data['location'])), 'BAD_INPUT', 'Bad location')
    require(
        bool(RESOURCE_ID_RE.match(data['environment_resource_id'])),
        'BAD_ENVIRONMENT_ID',
        'environment_resource_id must be the exact managed environment resource ID',
    )
    require(
        bool(COMMIT_RE.match(data['git_commit'])),
        'BAD_COMMIT',
        'git_commit must be 40 hex',
    )

    image_digest = _image_digest(data['image_reference'], 'MUTABLE_IMAGE')
    require(
        data['expected_image_digest'] == image_digest,
        'IMAGE_DIGEST_MISMATCH',
        'expected_image_digest does not match the pinned image digest',
    )
    for field in ('web_image_reference', 'worker_image_reference'):
        other = _image_digest(data[field], 'MUTABLE_IMAGE')
        require(
            other == image_digest,
            'IMAGE_SET_MISMATCH',
            'web, worker and Job must share one reviewed immutable digest',
        )

    interpreter = data['python_interpreter']
    require(
        bool(PY_RE.match(interpreter)),
        'BAD_INTERPRETER',
        'python_interpreter must be an absolute python3 path',
    )
    require(
        'init.sh' not in interpreter, 'BAD_INTERPRETER', 'The Job must bypass init.sh'
    )
    require(
        data['working_directory'] == BACKEND_DIR,
        'BAD_WORKDIR',
        'working_directory must declare the Django project directory',
    )

    command = data['command']
    require(
        command in ALLOWED_COMMANDS,
        'BAD_COMMAND',
        'Command is not an approved one-shot command',
    )
    argv_tail, numbers = _validate_args(command, data['command_args'])

    timeout = data['replica_timeout_seconds']
    require(
        isinstance(timeout, int) and not isinstance(timeout, bool),
        'BAD_TIMEOUT',
        'replica_timeout_seconds must be an integer',
    )
    require(
        60 <= timeout <= 3600,
        'BAD_TIMEOUT',
        'Timeout must be bounded between 60 and 3600 seconds',
    )
    if command == 'replay_demo_metrics':
        duration = numbers['--max-duration-seconds']
        require(
            timeout >= duration + 120,
            'BAD_TIMEOUT',
            'Replay timeout must be at least --max-duration-seconds + 120 seconds',
        )

    # Derive the runtime attestations only after the reviewed identities are
    # proven consistent (git_commit and the one digest shared by Job, web and
    # worker images above). The Job environment then attests exactly those.
    attestations = _derive_attestations(data)

    spec_env = _validate_env(
        data['environment'], data['secret_references'], attestations
    )

    return {
        'artifact': ARTIFACT,
        'version': VERSION,
        'input_sha256': hashlib.sha256(
            json.dumps(data, sort_keys=True, separators=(',', ':')).encode()
        ).hexdigest(),
        'document_contract': {
            'kind': ('Review artifact only; not an Azure ARM/CLI document as-is'),
            'consumption': (
                'The approved deployment step translates this envelope field '
                'by field per demo-metrics-runbook.md; never deploy this file '
                'directly'
            ),
        },
        'job': {
            'name': data['job_name'],
            'resource_group': data['resource_group'],
            'location': data['location'],
            'properties': {
                'environmentId': data['environment_resource_id'],
                'configuration': {
                    'triggerType': 'Manual',
                    'replicaTimeout': timeout,
                    'replicaRetryLimit': 0,
                    'manualTriggerConfig': {
                        'replicaCompletionCount': 1,
                        'parallelism': 1,
                    },
                },
                'template': {
                    'containers': [
                        {
                            'name': 'demo-metrics',
                            'image': data['image_reference'],
                            # command/args are set separately and absolutely so
                            # BOTH the image ENTRYPOINT (init.sh) and any
                            # inherited CMD (e.g. gunicorn arguments) are
                            # overridden, not concatenated.
                            'command': [interpreter],
                            'args': [MANAGE_PY, *argv_tail],
                            'env': sorted(spec_env, key=lambda e: e['name']),
                            'resources': {'cpu': 1, 'memory': '2Gi'},
                        }
                    ]
                },
            },
        },
        'provenance': {
            'git_commit': data['git_commit'],
            'image_reference': data['image_reference'],
            'image_digest': image_digest,
            'web_image_reference': data['web_image_reference'],
            'worker_image_reference': data['worker_image_reference'],
            'runtime_attestation': {
                'commit_env': COMMIT_ATTESTATION_ENV,
                'image_env': IMAGE_ATTESTATION_ENV,
                'commit_sha': attestations[COMMIT_ATTESTATION_ENV],
                'image_digest': attestations[IMAGE_ATTESTATION_ENV],
                'derived_from': ['git_commit', 'expected_image_digest'],
                'operator_declaration_only': True,
                'note': (
                    'These environment values declare the reviewed code/image '
                    'identity of the running process; they are operator '
                    'declarations, not cryptographic proof of the image '
                    'contents. The apply preflight compares them with the '
                    'approved mapping identity and refuses an absent or '
                    'different attestation'
                ),
            },
            'migrations': 'disabled on the Job; the web app is the single migration owner',
            'bypasses_init_sh': True,
            'startup_override': (
                'container command=[absolute interpreter] and args=[absolute '
                'manage.py, command, ...] replace both the image ENTRYPOINT '
                'and CMD'
            ),
            'image_workdir': IMAGE_WORKDIR,
            'declared_working_directory': BACKEND_DIR,
            'working_directory_applied_by_runtime': False,
            'working_directory_note': (
                'Container Apps has no working-directory override; the image '
                'WORKDIR stays /home/inventree and the absolute manage.py '
                'path supplies sys.path for the Django project'
            ),
        },
        'approvals_required': [
            'image-review',
            'job-provisioning',
            'plan-hash-approval',
            'single-apply',
            'verification-and-browser-acceptance',
            'cleanup-plan-approval',
        ],
    }


def main() -> int:
    """Validate one input document and write the rendered spec."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, help='Explicit non-secret input JSON')
    parser.add_argument(
        '--out', required=True, help='Path for the rendered Job spec JSON'
    )
    parser.add_argument(
        '--check', action='store_true', help='Validate only; do not write the spec file'
    )
    args = parser.parse_args()
    try:
        with open(args.input, encoding='utf-8') as handle:
            data = json.load(handle)
        spec = build_job_spec(data)
    except SpecError as error:
        print(f'refused: {error.code}')
        return 1
    except (OSError, json.JSONDecodeError):
        print('refused: BAD_INPUT_FILE')
        return 1
    if args.check:
        print(
            f'validated: {spec["job"]["name"]} {spec["provenance"]["image_digest"]} (no file written)'
        )
        return 0
    try:
        # 'x' never overwrites: an existing reviewed artifact is preserved.
        with open(args.out, 'x', encoding='utf-8') as handle:
            json.dump(spec, handle, indent=2, sort_keys=True)
            handle.write('\n')
    except FileExistsError:
        print('refused: OUTPUT_EXISTS')
        return 1
    except OSError:
        print('refused: BAD_OUTPUT_FILE')
        return 1
    print(f'rendered: {spec["job"]["name"]} {spec["provenance"]["image_digest"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
