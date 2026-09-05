"""CR-2: the compaction deployment probe prints verdicts, never values."""

import json
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase

from ai.core.config import Settings
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

    def __init__(self, **kwargs):
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

    def _run(self, completions, settings, **options):
        """Run the command against a fake client and return output + calls."""
        _Client.completions = completions
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
