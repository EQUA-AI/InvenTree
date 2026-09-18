"""Offline renderer cases, authored for the deferred validation stage."""

import copy
import unittest

from render_deployments import FORCED, render


class RendererTests(unittest.TestCase):
    """Credential references and disabled posture survive definition generation."""

    def fixture(self):
        """Only synthetic public coordinates; no real resource or credential."""
        identity = '/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/fixture/providers/Microsoft.ManagedIdentity/userAssignedIdentities/memory'
        env = {
            'INVENTREE_DB_HOST': 'fixture.postgres.database.azure.com',
            'INVENTREE_DB_NAME': 'fixture',
            'INVENTREE_DB_PORT': '6432',
            'INVENTREE_DB_USER': 'aimms_app',
            'AZURE_OPENAI_ENDPOINT': 'https://fixture.openai.azure.com',
            'AZURE_OPENAI_SUMMARIZATION_DEPLOYMENT': 'fixture-summary',
            'AIMMS_MEMORY_EXTRACTION_DEPLOYMENT': 'fixture-extract',
            'AIMMS_MEMORY_EMBEDDING_DEPLOYMENT': 'fixture-embed',
            'AIMMS_MEMORY_SHIELD_ENDPOINT': 'https://fixture.cognitiveservices.azure.com',
            'AIMMS_EGRESS_ALLOW': 'fixture.openai.azure.com',
            'FEATURE_SEMANTIC_MEMORY_RECALL': 'true',
        }
        bindings = [{'name': name, 'value': value} for name, value in env.items()]
        secrets = []
        for position, name in enumerate((
            'INVENTREE_DB_PASSWORD',
            'INVENTREE_SECRET_KEY',
            'AIMMS_MEMORY_FINGERPRINT_KEY',
        )):
            key = f'fixture-{position}'
            secrets.append({
                'name': key,
                'keyVaultUrl': f'https://fixture.vault.azure.net/secrets/{key}',
                'identity': identity,
            })
            bindings.append({'name': name, 'secretRef': key})
        source = {
            'location': 'eastus2',
            'properties': {
                'environmentId': '/fixture/environment',
                'configuration': {'secrets': secrets},
                'template': {'containers': [{'name': 'old-worker', 'env': bindings}]},
            },
        }
        target = {
            'name': 'fixture-memory-worker',
            'image': 'fixture.azurecr.io/backend@sha256:' + 'a' * 64,
            'database_role': 'aimms_app',
            'identity_resource_id': identity,
            'identity_client_id': '00000000-0000-0000-0000-000000000000',
        }
        return source, target

    def test_output_is_dark_and_source_is_not_mutated(self):
        """Reviewed source flags cannot accidentally activate the new consumer."""
        source, target = self.fixture()
        before = copy.deepcopy(source)
        result = render(source, target)
        values = {
            entry['name']: entry.get('value')
            for entry in result['properties']['template']['containers'][0]['env']
        }
        self.assertEqual({key: values[key] for key in FORCED}, FORCED)
        self.assertNotIn('ingress', result['properties']['configuration'])
        self.assertEqual(source, before)

    def test_inline_secret_or_database_option_credential_is_refused(self):
        """Neither source secret literals nor connection-option passwords pass."""
        source, target = self.fixture()
        source['properties']['configuration']['secrets'][0]['value'] = (
            'fixture-not-a-secret'
        )
        with self.assertRaises(ValueError):
            render(source, target)
        source, target = self.fixture()
        source['properties']['template']['containers'][0]['env'].append({
            'name': 'INVENTREE_DB_OPTIONS',
            'value': '{"password":"fixture-only"}',
        })
        with self.assertRaises(ValueError):
            render(source, target)


if __name__ == '__main__':
    unittest.main()
