"""M1 B8: the prompt-cache probe prints numbers and class names, never text."""

from io import StringIO
from types import SimpleNamespace
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase

from aichat.management.commands import prompt_cache_probe as probe


class _Usage:
    """Minimal SDK-shaped usage object."""

    def __init__(self, prompt: int, cached: int, write: int | None = None):
        self._data = {
            'prompt_tokens': prompt,
            'completion_tokens': 1,
            'total_tokens': prompt + 1,
            'prompt_tokens_details': {'cached_tokens': cached, 'audio_tokens': 0},
        }
        if write is not None:
            self._data['cache_write_tokens'] = write

    def model_dump(self):
        """Mirror pydantic."""
        return dict(self._data)


class _Completions:
    def __init__(self, usages, error=None):
        """Queue the usage objects (or the error) to answer with."""
        self.usages = list(usages)
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        """Record the call; raise or answer with the next usage."""
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(usage=self.usages.pop(0))


class _Client:
    def __init__(self, usages, error=None):
        """Expose ``chat.completions`` like the SDK client."""
        self.chat = SimpleNamespace(completions=_Completions(usages, error))


def _settings(**over):
    base = {
        'azure_openai_endpoint': 'https://example.invalid',
        'azure_openai_api_key': 'k',
        'azure_openai_api_version': '2026-01-01',
        'azure_openai_deployment': 'gpt-5.6-luna',
        'azure_openai_fast_deployment': 'gpt-4.1-mini',
        'azure_openai_summarization_deployment': 'gpt-5.6-luna-dz',
    }
    base.update(over)
    return SimpleNamespace(**base)


class PromptCacheProbeTest(SimpleTestCase):
    """Mode (i) verdicts and the value-free output contract."""

    def _run(self, client, *args, settings=None):
        """Invoke the command with a patched client and settings."""
        out = StringIO()
        with (
            mock.patch.object(probe, '_build_client', return_value=client),
            mock.patch(
                'ai.core.config.get_settings', return_value=settings or _settings()
            ),
        ):
            call_command('prompt_cache_probe', *args, stdout=out)
        return out.getvalue(), client

    def test_second_call_cache_hit_is_a_pass_with_counters(self):
        """A cache hit on the second call passes and prints the counters."""
        client = _Client([_Usage(1400, 0), _Usage(1400, 1280, write=1280)])
        out, client = self._run(client, '--deployments', 'gpt-5.6-luna')
        self.assertIn('deployment=gpt-5.6-luna', out)
        self.assertIn('call1: prompt=1400 cached=0', out)
        self.assertIn('call2: prompt=1400 cached=1280 cache_write=1280', out)
        self.assertIn('prompt_tokens_details.cached_tokens', out)
        self.assertIn('result=PASS', out)
        self.assertTrue(out.rstrip().endswith('PASS'))
        # Two identical requests, each above the cacheable floor.
        calls = client.chat.completions.calls
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]['messages'], calls[1]['messages'])
        prefix = calls[0]['messages'][0]['content']
        self.assertGreaterEqual(len(prefix) // 4, probe._MIN_PREFIX_TOKENS)
        # The prefix itself never reaches stdout.
        self.assertNotIn(probe._PROMPT_ATOM.strip(), out)

    def test_no_cache_hit_is_a_fail_and_cache_write_is_na(self):
        """No cache hit fails; an unreported write counter prints n/a."""
        client = _Client([_Usage(1400, 0), _Usage(1400, 0)])
        out, _ = self._run(client, '--deployments', 'gpt-5.6-luna')
        self.assertIn('cache_write=n/a', out)
        self.assertIn('result=FAIL', out)
        self.assertTrue(out.rstrip().endswith('FAIL'))

    def test_provider_error_prints_the_class_only(self):
        """Provider errors surface as a class name, never their text."""
        client = _Client([], error=RuntimeError('Bearer sk-secret-echoed-back'))
        out, _ = self._run(client, '--deployments', 'gpt-5.6-luna')
        self.assertIn('deployment=gpt-5.6-luna ERROR RuntimeError', out)
        self.assertNotIn('sk-secret', out)
        self.assertTrue(out.rstrip().endswith('FAIL'))

    def test_default_deployments_are_the_configured_gpt5_names(self):
        """Without --deployments the configured gpt-5 names are probed."""
        client = _Client([_Usage(1400, 0), _Usage(1400, 1280)] * 2)
        out, client = self._run(client)
        models = [call['model'] for call in client.chat.completions.calls]
        self.assertEqual(models, ['gpt-5.6-luna'] * 2 + ['gpt-5.6-luna-dz'] * 2)
        self.assertNotIn('gpt-4.1-mini', out)

    def test_json_out_carries_numbers_only(self):
        """The JSON evidence holds counters, never the prompt."""
        import json
        import tempfile
        from pathlib import Path

        client = _Client([_Usage(1400, 0), _Usage(1400, 1280)])
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'probe.json'
            self._run(
                client, '--deployments', 'gpt-5.6-luna', '--json-out', str(target)
            )
            payload = json.loads(target.read_text())
        self.assertEqual(payload['deployments'][0]['result'], 'PASS')
        self.assertEqual(payload['deployments'][0]['calls'][1]['cached'], 1280)
        self.assertNotIn(
            probe._PROMPT_ATOM.strip(), target.read_text() if target.exists() else ''
        )


class CacheCounterShapeTest(SimpleTestCase):
    """Both provider usage shapes normalize to the same three counters."""

    def test_chat_completions_shape(self):
        """Chat Completions usage normalizes to the three counters."""
        usage = {
            'prompt_tokens': 2000,
            'prompt_tokens_details': {'cached_tokens': 1024},
        }
        self.assertEqual(
            probe.cache_counters(usage),
            {'prompt': 2000, 'cached': 1024, 'cache_write': None},
        )

    def test_responses_shape(self):
        """Responses-API usage normalizes to the same counters."""
        usage = {'input_tokens': 2000, 'input_tokens_details': {'cached_tokens': 1536}}
        self.assertEqual(
            probe.cache_counters(usage),
            {'prompt': 2000, 'cached': 1536, 'cache_write': None},
        )

    def test_missing_usage_is_all_none(self):
        """A missing usage object yields None for every counter."""
        self.assertEqual(
            probe.cache_counters(None),
            {'prompt': None, 'cached': None, 'cache_write': None},
        )
