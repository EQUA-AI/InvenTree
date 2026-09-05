"""M1 prerequisite B8: does Azure actually serve a prompt cache to us?

Diagnostic only — prints numbers and exception class names, never prompt
text, never a credential. Two modes:

(i)  ``--mode i`` (default): for each deployment, two IDENTICAL chat
     completions with a >= 1,024-token prefix (the provider's minimum
     cacheable prefix) through the same client construction the compaction
     job uses. PASS iff the second call reports ``cached_tokens > 0``. The
     ``usage`` keys are printed as evidence of what the provider returns
     (``cache_write_tokens`` is reported only when Azure sends it).

(ii) ``--mode ii``: drive the real turn service over one committed
     memory-battery case on a fresh thread as ``--user`` and print the
     per-turn ``input_tokens`` / ``cached_input_tokens`` (and
     ``cache_write_tokens`` when the ledger carries it) from the persisted
     ``ChatMessage.metadata['usage']`` totals. Needs
     ``FEATURE_TURN_USAGE_PERSISTENCE`` on the deployment.

Run on the worker via ``az containerapp exec``; attach ``--json-out`` (sha in
the change note) to the M1 entry record.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

#: Chat-cache floors: Azure caches only prefixes >= 1,024 tokens.
_MIN_PREFIX_TOKENS = 1_024

#: Sanitized, deterministic filler — repeated until the prefix clears the
#: floor with margin. No customer text, no identifiers.
_PROMPT_ATOM = (
    'Operating note: inspect the inverter cabinet air filter, confirm the DC '
    'isolator is rated for the string voltage, torque the AC surge arrester '
    'lugs to the manual value, record the ambient temperature, and log the '
    'coolant pump hours before returning the unit to service. '
)


def _build_client(settings: Any):
    """The SAME client construction ``aichat.tasks._summarize`` uses."""
    from openai import AzureOpenAI

    return AzureOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
    )


def _prefix() -> str:
    """A fixed prefix comfortably above the cacheable floor."""
    repeats = (_MIN_PREFIX_TOKENS * 5) // len(_PROMPT_ATOM) + 2
    return _PROMPT_ATOM * repeats


def _usage_dict(usage: Any) -> dict[str, Any]:
    """Normalize an SDK usage object to a plain dict (Chat or Responses shape)."""
    if usage is None:
        return {}
    if hasattr(usage, 'model_dump'):
        return dict(usage.model_dump() or {})
    if isinstance(usage, dict):
        return dict(usage)
    return {}


def cache_counters(usage: Any) -> dict[str, int | None]:
    """``prompt``, ``cached`` and ``cache_write`` token counts from either shape.

    Chat Completions reports ``prompt_tokens`` + ``prompt_tokens_details.cached_tokens``;
    the Responses API (the Luna path) reports ``input_tokens`` +
    ``input_tokens_details.cached_tokens``. A write counter is reported only by
    providers that expose one — absent stays None (printed ``n/a``).
    """
    data = _usage_dict(usage)
    prompt = data.get('prompt_tokens')
    if prompt is None:
        prompt = data.get('input_tokens')
    details = (
        data.get('prompt_tokens_details') or data.get('input_tokens_details') or {}
    )
    if hasattr(details, 'model_dump'):
        details = details.model_dump()
    cached = (details or {}).get('cached_tokens')
    write = None
    for key in ('cache_write_tokens', 'cache_creation_input_tokens'):
        if data.get(key) is not None:
            write = data[key]
            break
        if (details or {}).get(key) is not None:
            write = details[key]
            break

    def _int(value: Any) -> int | None:
        return (
            int(value)
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            else None
        )

    return {'prompt': _int(prompt), 'cached': _int(cached), 'cache_write': _int(write)}


def _usage_keys(usage: Any) -> list[str]:
    data = _usage_dict(usage)
    keys = sorted(str(key) for key in data)
    for nested in ('prompt_tokens_details', 'input_tokens_details'):
        block = data.get(nested)
        if hasattr(block, 'model_dump'):
            block = block.model_dump()
        if isinstance(block, dict):
            keys.extend(f'{nested}.{key}' for key in sorted(block))
    return keys


def probe_deployment(client: Any, deployment: str) -> dict[str, Any]:
    """Two identical calls; PASS iff the second reports a cache hit."""
    prefix = _prefix()
    messages = [
        {'role': 'system', 'content': prefix},
        {'role': 'user', 'content': 'Reply with the single word OK.'},
    ]
    result: dict[str, Any] = {'deployment': deployment, 'calls': [], 'usage_keys': []}
    for _ in range(2):
        try:
            response = client.chat.completions.create(
                model=deployment, messages=messages, max_completion_tokens=16
            )
        except Exception as exc:
            # Value-free: provider errors can echo the request; class only.
            result['error'] = type(exc).__name__
            result['result'] = 'ERROR'
            return result
        usage = getattr(response, 'usage', None)
        result['calls'].append(cache_counters(usage))
        result['usage_keys'] = _usage_keys(usage)
    second = result['calls'][1]
    result['result'] = 'PASS' if (second.get('cached') or 0) > 0 else 'FAIL'
    return result


def _default_deployments(settings: Any) -> list[str]:
    names = [
        getattr(settings, 'azure_openai_deployment', ''),
        getattr(settings, 'azure_openai_fast_deployment', ''),
        getattr(settings, 'azure_openai_summarization_deployment', ''),
    ]
    seen: list[str] = []
    for name in names:
        name = (name or '').strip()
        if name and 'gpt-5' in name and name not in seen:
            seen.append(name)
    return seen


def _last_assistant_metadata(thread_id: str) -> dict[str, Any]:
    """Persisted metadata of the newest assistant message on ``thread_id`` (sync)."""
    from aichat.models import ChatMessage

    message = (
        ChatMessage.objects
        .filter(thread_id=thread_id, role='assistant')
        .order_by('-sequence')
        .first()
    )
    return dict(getattr(message, 'metadata', None) or {})


def _turn_rows(case: Any, username: str) -> list[dict[str, Any]]:
    """Mode (ii): the real turn service over one battery case, fresh thread."""
    from django.contrib.auth import get_user_model

    from ai.core.app import get_turn_service
    from ai.core.auth import AIBoundaryPolicy, AIPrincipal
    from ai.core.trusted_context import build_trusted_turn_context

    user = get_user_model().objects.filter(username=username).first()
    if user is None:
        raise CommandError(f'user {username!r} does not exist')
    policy = AIBoundaryPolicy.from_settings()
    principal = AIPrincipal(
        subject=f'user:{user.pk}',
        actor=f'user:{user.pk}',
        user_pk=str(user.pk),
        username=str(user.get_username()),
        authentication_method='prompt_cache_probe',
        scope=policy.single_site_policy_key,
        policy_version=policy.policy_version,
        is_staff=bool(user.is_staff),
        is_superuser=bool(user.is_superuser),
    )
    thread_id = f'probe_{uuid.uuid4().hex[:16]}'
    rows: list[dict[str, Any]] = []

    async def drive() -> None:
        from asgiref.sync import sync_to_async

        service = get_turn_service()
        for index, turn in enumerate(case.turns):
            await service.process(
                actor=principal,
                thread_id=thread_id,
                content=turn.question,
                modality='text',
                trusted_context=build_trusted_turn_context(
                    principal, server_route_hints=('/chat',), locale='en'
                ),
                modality_metadata={'transport': 'prompt_cache_probe'},
                idempotency_key=f'probe:{case.id}:{index}:{thread_id}',
                correlation_id=str(uuid.uuid4()),
            )
            # The ORM must never run on the event loop (SynchronousOnlyOperation,
            # seen live 2026-09-05): hop to a worker thread for the read.
            metadata = await sync_to_async(
                _last_assistant_metadata, thread_sensitive=True
            )(thread_id)
            totals = dict((metadata.get('usage') or {}).get('totals') or {})
            rows.append({
                'turn': index,
                'workflow': metadata.get('workflow_id'),
                'input_tokens': totals.get('input_tokens'),
                'cached_input_tokens': totals.get('cached_input_tokens'),
                'cache_write_tokens': totals.get('cache_write_tokens'),
                'usage_present': bool(totals),
            })

    asyncio.run(drive())
    return rows


def _fmt(value: Any) -> str:
    return 'n/a' if value is None else str(value)


class Command(BaseCommand):
    """Probe the live prompt cache; print numbers and class names only."""

    help = (
        'Prompt-cache probe (M1 B8): (i) two identical >=1,024-token calls per '
        'deployment must show cached_tokens on the second; (ii) drive one '
        'memory-battery case through the turn service and print per-turn cache '
        'counters from persisted usage. Never prints prompt text or secrets.'
    )

    def add_arguments(self, parser):
        """Register the probe options."""
        parser.add_argument(
            '--deployments',
            default='',
            help='Comma-separated deployment names (default: configured gpt-5 deployments)',
        )
        parser.add_argument(
            '--case', default='M-MEM-01', help='memory_battery.yaml case id'
        )
        parser.add_argument(
            '--user', default='yesworkorders', help='Django username for mode ii'
        )
        parser.add_argument('--mode', choices=('i', 'ii', 'both'), default='i')
        parser.add_argument(
            '--json-out', default='', help='Write the numbers as JSON here'
        )

    def handle(self, *args, **options):
        """Run the selected modes and print one line per measurement."""
        from ai.core.config import get_settings

        settings = get_settings()
        report: dict[str, Any] = {
            'mode': options['mode'],
            'deployments': [],
            'turns': [],
        }
        failed = False

        if options['mode'] in ('i', 'both'):
            names = [
                n.strip() for n in str(options['deployments']).split(',') if n.strip()
            ]
            names = names or _default_deployments(settings)
            if not names:
                raise CommandError('no deployment to probe (pass --deployments)')
            client = _build_client(settings)
            for name in names:
                outcome = probe_deployment(client, name)
                report['deployments'].append(outcome)
                if outcome['result'] == 'ERROR':
                    failed = True
                    self.stdout.write(f'deployment={name} ERROR {outcome["error"]}')
                    continue
                first, second = outcome['calls']
                line = (
                    f'deployment={name} '
                    f'call1: prompt={_fmt(first["prompt"])} cached={_fmt(first["cached"])} | '
                    f'call2: prompt={_fmt(second["prompt"])} cached={_fmt(second["cached"])} '
                    f'cache_write={_fmt(second["cache_write"])} '
                    f'usage_keys={",".join(outcome["usage_keys"]) or "-"} '
                    f'result={outcome["result"]}'
                )
                self.stdout.write(line)
                failed = failed or outcome['result'] != 'PASS'

        if options['mode'] in ('ii', 'both'):
            from ai.core.evals.scenarios import BATTERY_DIR, load_battery

            path = BATTERY_DIR / 'memory_battery.yaml'
            if not path.is_file():
                raise CommandError('memory_battery.yaml is not committed yet')
            case = load_battery(path).case(options['case'])
            if case is None:
                raise CommandError(f'unknown case {options["case"]!r}')
            self.stdout.write(
                f'case={case.id} turns={len(case.turns)} user={options["user"]} '
                f'scope=unset (the probe measures cache counters only)'
            )
            rows = _turn_rows(case, options['user'])
            report['turns'] = rows
            for row in rows:
                if not row['usage_present']:
                    self.stdout.write(
                        f'turn={row["turn"]} usage=absent (FEATURE_TURN_USAGE_PERSISTENCE off?)'
                    )
                    continue
                self.stdout.write(
                    f'turn={row["turn"]} workflow={row["workflow"] or "-"} '
                    f'input={_fmt(row["input_tokens"])} '
                    f'cached={_fmt(row["cached_input_tokens"])} '
                    f'cache_write={_fmt(row["cache_write_tokens"])}'
                )

        if options['json_out']:
            Path(options['json_out']).write_text(
                json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8'
            )
            self.stdout.write(f'json_out={options["json_out"]}')

        if options['mode'] in ('i', 'both'):
            self.stdout.write(
                self.style.ERROR('FAIL') if failed else self.style.SUCCESS('PASS')
            )
