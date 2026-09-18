"""Prepare two complete ACA worker specifications offline, without deployment.

Inputs are reviewed source-worker JSON exports with Key Vault secret references.
Inline secret values are refused. Output files are new, private, and never sent
to Azure by this program. Runtime features and workers remain disabled in output.
"""

import argparse
import copy
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

SECRET_ENV = re.compile(
    r'(?:SECRET|PASSWORD|TOKEN|API_KEY|CREDENTIAL|CONNECTION_STRING)', re.I
)
FORCED = {
    'Q_CLUSTER_NAME': 'ai-memory',
    'INVENTREE_MEMORY_TIMEOUT': '300',
    'AIMMS_MEMORY_WORKER_ENABLED': 'false',
    'FEATURE_THREAD_COMPACTION': 'false',
    'AIMMS_FEATURE_THREAD_COMPACTION': 'false',
    'FEATURE_THREAD_COMPACTION_SHADOW': 'false',
    'AIMMS_FEATURE_THREAD_COMPACTION_SHADOW': 'false',
    'FEATURE_SEMANTIC_MEMORY_EXTRACT_SHADOW': 'false',
    'AIMMS_FEATURE_SEMANTIC_MEMORY_EXTRACT_SHADOW': 'false',
    'FEATURE_SEMANTIC_MEMORY_RECALL': 'false',
    'AIMMS_FEATURE_SEMANTIC_MEMORY_RECALL': 'false',
    'FEATURE_SEMANTIC_MEMORY_MEM0': 'false',
    'AIMMS_FEATURE_SEMANTIC_MEMORY_MEM0': 'false',
    'FEATURE_RAG_PROJECTION_AUDIT': 'false',
    'AIMMS_MEMORY_DEFAULT_MODE': 'off',
    'AIMMS_EGRESS_MODE': 'enforce',
    'MEM0_TELEMETRY': 'false',
    'MEM0_DIR': '/tmp/mem0',
    'INVENTREE_DB_CONN_MAX_AGE': '300',
    'INVENTREE_DB_CONN_HEALTH_CHECKS': 'true',
    'INVENTREE_DB_DISABLE_SERVER_SIDE_CURSORS': 'true',
}


def render(source, target):
    """Clone reviewed connectivity while replacing identity, image and workload."""
    if not re.fullmatch(r'[a-z][a-z0-9-]{1,30}[a-z0-9]', target['name']):
        raise ValueError('Invalid target app name')
    if not re.fullmatch(r'[a-z0-9./_-]+@sha256:[0-9a-f]{64}', target['image']):
        raise ValueError('A reviewed immutable image digest is required')
    identity = target['identity_resource_id']
    UUID(target['identity_client_id'])
    if not re.fullmatch(
        r'/subscriptions/[a-f0-9-]+/resourceGroups/[^/]+/providers/Microsoft.ManagedIdentity/userAssignedIdentities/[^/]+',
        identity,
        re.I,
    ):
        raise ValueError('A user-assigned identity resource is required')
    properties = source['properties']
    environment = properties.get('environmentId') or properties.get(
        'managedEnvironmentId'
    )
    if not environment:
        raise ValueError('Reviewed managed environment is missing')
    config = properties['configuration']
    secrets = copy.deepcopy(config.get('secrets', []))
    names = set()
    for secret in secrets:
        if set(secret) - {'name', 'keyVaultUrl', 'identity'} or not secret.get(
            'keyVaultUrl'
        ):
            raise ValueError('Only Key Vault secret references are accepted')
        url = urlsplit(secret['keyVaultUrl'])
        if (
            url.scheme != 'https'
            or not (url.hostname or '').endswith('.vault.azure.net')
            or url.query
            or url.fragment
            or url.username
            or url.password
        ):
            raise ValueError('Invalid Key Vault secret reference')
        secret['identity'] = identity
        if secret['name'] in names:
            raise ValueError('Duplicate secret reference')
        names.add(secret['name'])
    containers = properties['template']['containers']
    if len(containers) != 1:
        raise ValueError('Review multi-container source workers manually')
    container = copy.deepcopy(containers[0])
    values = {}
    for entry in container.get('env', []):
        name = entry['name']
        if (
            name in values
            or set(entry) - {'name', 'value', 'secretRef'}
            or ('value' in entry) == ('secretRef' in entry)
        ):
            raise ValueError('Invalid or duplicate environment binding')
        if 'secretRef' in entry and entry['secretRef'] not in names:
            raise ValueError('Unresolved secret reference')
        if SECRET_ENV.search(name) and entry.get('value'):
            raise ValueError('Inline credential-like environment value refused')
        values[name] = entry
    if values.get('INVENTREE_DB_USER', {}).get('value') != target[
        'database_role'
    ] or not target['database_role'].startswith('aimms_'):
        raise ValueError('Reviewed non-admin application database role required')
    for required in (
        'INVENTREE_DB_PASSWORD',
        'INVENTREE_SECRET_KEY',
        'AIMMS_MEMORY_FINGERPRINT_KEY',
    ):
        if not values.get(required, {}).get('secretRef'):
            raise ValueError('Required secret reference missing')
    for required in (
        'INVENTREE_DB_HOST',
        'INVENTREE_DB_NAME',
        'INVENTREE_DB_PORT',
        'AZURE_OPENAI_ENDPOINT',
        'AZURE_OPENAI_SUMMARIZATION_DEPLOYMENT',
        'AIMMS_MEMORY_EXTRACTION_DEPLOYMENT',
        'AIMMS_MEMORY_EMBEDDING_DEPLOYMENT',
        'AIMMS_MEMORY_SHIELD_ENDPOINT',
        'AIMMS_EGRESS_ALLOW',
    ):
        if not values.get(required, {}).get('value'):
            raise ValueError('Required runtime setting missing')
    # Passwords/API keys may be in source secret references, but memory provider
    # clients must use the specified identity. Never carry their API-key envs.
    for name in ('AZURE_OPENAI_API_KEY', 'AZURE_LUNA_API_KEY'):
        values.pop(name, None)
    options = json.loads(values.get('INVENTREE_DB_OPTIONS', {}).get('value', '{}'))
    if not isinstance(options, dict) or set(options) - {
        'sslmode',
        'sslrootcert',
        'connect_timeout',
        'application_name',
        'prepare_threshold',
    }:
        raise ValueError('Database options need explicit review')
    options.update(
        sslmode='verify-full',
        sslrootcert='/etc/ssl/certs/ca-certificates.crt',
        connect_timeout=10,
    )
    values['INVENTREE_DB_OPTIONS'] = {
        'name': 'INVENTREE_DB_OPTIONS',
        'value': json.dumps(options),
    }
    for name, value in FORCED.items():
        values[name] = {'name': name, 'value': value}
    values['AZURE_CLIENT_ID'] = {
        'name': 'AZURE_CLIENT_ID',
        'value': target['identity_client_id'],
    }
    container.update(
        name='memory-worker',
        image=target['image'],
        command=['invoke'],
        args=['worker'],
        resources={'cpu': 0.5, 'memory': '1Gi'},
        env=list(values.values()),
    )
    container.pop('probes', None)  # HTTP web probes do not qualify a Q cluster.
    used_secrets = {
        entry['secretRef'] for entry in values.values() if 'secretRef' in entry
    }
    secrets = [secret for secret in secrets if secret['name'] in used_secrets]
    configuration = {'activeRevisionsMode': 'Single', 'secrets': secrets}
    registries = copy.deepcopy(config.get('registries', []))
    for registry in registries:
        if set(registry) != {'server', 'identity'}:
            raise ValueError('Registry authentication must use managed identity')
        registry['identity'] = identity
    configuration['registries'] = registries
    template = {
        'containers': [container],
        'scale': {'minReplicas': 1, 'maxReplicas': 1},
    }
    if properties['template'].get('volumes'):
        template['volumes'] = copy.deepcopy(properties['template']['volumes'])
    return {
        'name': target['name'],
        'type': 'Microsoft.App/containerApps',
        'location': source['location'],
        'identity': {'type': 'UserAssigned', 'userAssignedIdentities': {identity: {}}},
        'properties': {
            'environmentId': environment,
            'configuration': configuration,
            'template': template,
        },
    }


def main():
    """Validate both definitions before writing either; no cloud commands exist."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text())
        if set(manifest) != {'production', 'experimental'}:
            raise ValueError('Both environment definitions are required')
        rendered = {}
        for label, target in manifest.items():
            if (
                target['database_role']
                != {'production': 'aimms_app', 'experimental': 'aimms_dev_app'}[label]
            ):
                raise ValueError('Wrong environment database role')
            source = json.loads(
                (args.manifest.parent / target['source_file']).read_text()
            )
            rendered[label] = render(source, target)
        if rendered['production']['name'] == rendered['experimental']['name']:
            raise ValueError('Environment app names must be distinct')
        args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        for label, spec in rendered.items():
            path = args.output_dir / f'{label}.memory-worker.json'
            with os.fdopen(
                os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w'
            ) as output:
                json.dump(spec, output, indent=2)
                output.write('\n')
        print('Prepared two private worker definitions; no resources deployed.')
    except (KeyError, TypeError, ValueError, OSError):
        parser.exit(
            1,
            'Deployment preparation refused; review manifest structure and reference-only source exports.\n',
        )


if __name__ == '__main__':
    main()
