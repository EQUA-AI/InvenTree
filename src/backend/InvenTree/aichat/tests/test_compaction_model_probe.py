"""CR-2: the compaction deployment probe prints verdicts, never values."""

import json
import sys
import types
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase

from ai.core.config import Settings
from ai.core.integrations.azure_openai_client import reset_token_provider_cache
from aichat.tasks import COMPACTION_SCHEMA


def _summary_body():
    return {
        key: ([] if key != 'label' and key != 'narrative' else 'x')
        for key in COMPACTION_SCHEMA['required']
    }


class _Completions:
    def __init__(self, body=None, error=None):
        self.body = body
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        message = mock.Mock(content=json.dumps(self.body))
        return mock.Mock(
            choices=[mock.Mock(message=message)],
            usage=mock.Mock(prompt_tokens=120, completion_tokens=40),
        )


class _Client:
    completions = None
    constructed: list[dict] = []

    def __init__(self, **kwargs):
        type(self).constructed.append(kwargs)
        self.chat = mock.Mock(completions=type(self).completions)


def _settings(**overrides):
    base = {
        'AZURE_OPENAI_ENDPOINT': 'https://example.openai.azure.com',
        'AZURE_OPENAI_API_KEY': 'test-key',
        'AZURE_OPENAI_DEPLOYMENT': 'standard-4o',
        'AZURE_OPENAI_FAST_DEPLOYMENT': 'fast-mini',
        'AZURE_OPENAI_SUMMARIZATION_DEPLOYMENT': '',
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


class CompactionModelProbeTest(SimpleTestCase):
    """``compaction_model_probe`` prints verdict lines and never values."""

    def setUp(self):
        """The credential is process-cached; a fake must never outlive its test."""
        super().setUp()
        reset_token_provider_cache()
        self.addCleanup(reset_token_provider_cache)

    def _run(self, completions, settings, **options):
        """Run the command against a fake client and return output + calls."""
        _Client.completions = completions
        _Client.constructed = []
        out = StringIO()
        with (
            mock.patch('openai.AzureOpenAI', _Client),
            mock.patch('ai.core.config.get_settings', lambda: settings),
        ):
            call_command('compaction_model_probe', stdout=out, **options)
        return out.getvalue(), completions.calls

    def test_pass_on_the_override_with_redaction_and_effort(self):
        """Override deployment: effort sent, payload redacted, PASS printed."""
        completions = _Completions(body=_summary_body())
        output, calls = self._run(
            completions,
            _settings(AZURE_OPENAI_SUMMARIZATION_DEPLOYMENT='gpt-5.6-luna-dz'),
        )
        self.assertIn('deployment                = gpt-5.6-luna-dz', output)
        self.assertIn('client                    = key', output)
        self.assertIn('reasoning_effort_sent     = low', output)
        self.assertIn('seed_leaked               = false', output)
        self.assertIn('schema_ok                 = true', output)
        self.assertIn('reasoning_effort_accepted = true', output)
        self.assertIn('PASS', output)
        call = calls[0]
        self.assertEqual(call['model'], 'gpt-5.6-luna-dz')
        self.assertEqual(call['reasoning_effort'], 'low')
        self.assertEqual(
            call['response_format']['json_schema']['schema'], COMPACTION_SCHEMA
        )
        payload = call['messages'][1]['content']
        self.assertIn('[REDACTED:password]', payload)
        self.assertNotIn('hunter2', payload)

    def test_no_override_sends_no_effort_and_reports_n_a(self):
        """Standard tier: no reasoning_effort kwarg, acceptance reported n/a."""
        completions = _Completions(body=_summary_body())
        output, calls = self._run(completions, _settings())
        self.assertIn('deployment                = standard-4o', output)
        self.assertIn('reasoning_effort_accepted = n/a', output)
        self.assertNotIn('reasoning_effort', calls[0])

    def test_provider_error_prints_only_the_exception_class(self):
        """Provider errors surface as a class name, never their message."""
        completions = _Completions(
            error=RuntimeError('sk-secret-value leaked in message')
        )
        output, _ = self._run(
            completions, _settings(AZURE_OPENAI_SUMMARIZATION_DEPLOYMENT='dz')
        )
        self.assertIn('call                      = ERROR RuntimeError', output)
        self.assertIn('schema_ok                 = false', output)
        self.assertIn('reasoning_effort_accepted = false', output)
        self.assertNotIn('sk-secret-value', output)

    def test_explicit_deployment_wins(self):
        """--deployment overrides the policy choice."""
        completions = _Completions(body=_summary_body())
        output, calls = self._run(completions, _settings(), deployment='gpt-5.6-luna')
        self.assertIn('deployment                = gpt-5.6-luna', output)
        self.assertEqual(calls[0]['model'], 'gpt-5.6-luna')

    def test_keyless_settings_print_keyless_and_build_a_token_provider(self):
        """M2 PR 7 (GR-23): the probe reports the credential path it used."""
        created = {'credentials': 0}

        class FakeCredential:
            def __init__(self):
                created['credentials'] += 1

        identity = types.ModuleType('azure.identity')
        identity.DefaultAzureCredential = FakeCredential
        identity.get_bearer_token_provider = lambda cred, *scopes: lambda: 'fake-token'
        completions = _Completions(body=_summary_body())
        with mock.patch.dict(sys.modules, {'azure.identity': identity}):
            output, _ = self._run(completions, _settings(AIMMS_OPENAI_KEYLESS=True))
        self.assertIn('client                    = keyless', output)
        self.assertIn('PASS', output)
        self.assertEqual(created['credentials'], 1)
        kwargs = _Client.constructed[0]
        self.assertNotIn('api_key', kwargs)
        self.assertEqual(kwargs['azure_ad_token_provider'](), 'fake-token')
        self.assertEqual(kwargs['azure_endpoint'], 'https://example.openai.azure.com')
        self.assertNotIn('test-key', output)

    def test_key_settings_print_key_and_pass_the_api_key(self):
        """Key mode is unchanged: the SDK receives the key, the line says key."""
        completions = _Completions(body=_summary_body())
        output, _ = self._run(completions, _settings())
        self.assertIn('client                    = key', output)
        kwargs = _Client.constructed[0]
        self.assertEqual(kwargs['api_key'], 'test-key')
        self.assertNotIn('azure_ad_token_provider', kwargs)
        self.assertNotIn('test-key', output)
