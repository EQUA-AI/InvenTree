"""CR-2 pre-edit gate: prove the SUMMARIZATION override deployment live.

Diagnostic only — this command writes nothing and changes no configuration.
It exists because the D-10 routing override moves compaction from gpt-4.1 to
a reasoning deployment (gpt-5.6-luna-dz) over the Chat Completions API, and
two things are proven today only on the Responses API: strict ``json_schema``
output and ``reasoning_effort``. One call answers both before the worker env
is edited.

Output is value-free: booleans, counts and exception class names only. The
seeded transcript is redacted through ``ai.core.redaction`` exactly as
``_summarize`` does, and the probe checks that no seed reaches the request
payload (``seed_leaked``) — the same evidence the §7.13 redaction row asks
for, captured from a real request rather than a unit test.
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand

#: Seeds cover one keep-key family, one whole-match family and one contact
#: family; the fragments must be absent from the payload after redaction.
_SEEDS: tuple[tuple[str, str], ...] = (
    ('the password is hunter2', 'hunter2'),
    ('reach me at tech@example.com', 'tech@example.com'),
    ('call +1 (555) 123-4567 tomorrow', '123-4567'),
)


class Command(BaseCommand):
    """Probe the compaction deployment; print, never mutate."""

    help = (
        'One strict-schema compaction call against the SUMMARIZATION deployment '
        '(or --deployment) to prove json_schema and reasoning_effort are accepted '
        'and the payload is redacted. Prints booleans and counts only.'
    )

    def add_arguments(self, parser):
        """Register the optional deployment override and the schema switch."""
        parser.add_argument(
            '--deployment',
            default='',
            help='Deployment to call; defaults to select_deployment(SUMMARIZATION)',
        )
        parser.add_argument(
            '--delta-ops',
            action='store_true',
            dest='delta_ops',
            help=(
                'Send schema v2 (removals/expirations/supersessions) with an '
                'id-prefixed prior item so the deployment can be proven before '
                'AIMMS_COMPACTION_DELTA_OPS flips (M2 PR 3).'
            ),
        )

    def handle(self, *args, **options):
        """Build the redacted payload, make one call, print the verdict lines."""
        from ai.core.config import get_settings
        from ai.core.integrations.azure_openai_client import (
            build_openai_client,
            client_auth_mode,
        )
        from ai.core.memory.summary_body import upgrade_body
        from ai.core.model_policy import ModelPurpose, call_options, select_deployment
        from ai.core.redaction import format_counts, redact_payload
        from aichat.tasks import (
            _COMPACTION_SYSTEM_PROMPT,
            _COMPACTION_SYSTEM_PROMPT_V2,
            COMPACTION_SCHEMA,
            COMPACTION_SCHEMA_V2,
            project_prior_for_model,
        )

        settings = get_settings()
        delta_ops = bool(options.get('delta_ops'))
        schema = COMPACTION_SCHEMA_V2 if delta_ops else COMPACTION_SCHEMA
        system_prompt = (
            _COMPACTION_SYSTEM_PROMPT_V2 if delta_ops else _COMPACTION_SYSTEM_PROMPT
        )
        # Under delta ops the prior carries one id-prefixed item so the model
        # has an id it may legitimately reference in the ops lists.
        prior_summary: dict = {}
        if delta_ops:
            prior_summary = project_prior_for_model(
                upgrade_body(
                    {'label': 'Probe', 'machine_facts': ['pump 3 seal is OEM']},
                    created_seq=1,
                ),
                delta_ops=True,
            )
        deployment = str(options.get('deployment') or '').strip() or select_deployment(
            ModelPurpose.SUMMARIZATION
        )
        options_sent = call_options(ModelPurpose.SUMMARIZATION)

        transcript = [{'role': 'user', 'content': text} for text, _ in _SEEDS]
        transcript.append({
            'role': 'assistant',
            'content': 'Noted the pump 3 seal wear.',
        })
        redacted = redact_payload({
            'prior_summary': prior_summary,
            'new_messages': transcript,
        })
        payload = json.dumps(redacted.value, ensure_ascii=True)
        seed_leaked = any(fragment in payload for _, fragment in _SEEDS)

        self.stdout.write(f'deployment                = {deployment}')
        self.stdout.write(f'schema                    = {"v2" if delta_ops else "v1"}')
        self.stdout.write(f'client                    = {client_auth_mode(settings)}')
        self.stdout.write(f'override_set              = {bool(options_sent)}')
        self.stdout.write(
            f'reasoning_effort_sent     = {options_sent.get("reasoning_effort", "")}'
        )
        self.stdout.write(
            f'redacted_categories       = {format_counts(redacted.counts)}'
        )
        self.stdout.write(f'seed_leaked               = {str(seed_leaked).lower()}')
        if seed_leaked:
            self.stdout.write(
                self.style.ERROR('redaction gap: a seed reached the payload')
            )

        # M2 PR 7 (GR-23): the shared factory picks key vs managed identity,
        # so the probe proves the same credential path the worker job uses.
        client = build_openai_client(settings=settings)
        try:
            response = client.chat.completions.create(
                model=deployment,
                **options_sent,
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': payload},
                ],
                response_format={
                    'type': 'json_schema',
                    'json_schema': {
                        'name': 'thread_summary',
                        'strict': True,
                        'schema': schema,
                    },
                },
            )
        except Exception as exc:
            # Value-free: provider errors can echo the request, so only the
            # exception class reaches the transcript.
            self.stdout.write(f'call                      = ERROR {type(exc).__name__}')
            self.stdout.write('schema_ok                 = false')
            self.stdout.write(
                f'reasoning_effort_accepted = {"false" if options_sent else "n/a"}'
            )
            return

        ops_keys_ok = False
        try:
            body = json.loads(response.choices[0].message.content)
            schema_ok = isinstance(body, dict) and set(schema['required']) <= set(body)
            ops_keys_ok = isinstance(body, dict) and all(
                isinstance(body.get(key), list)
                for key in ('removals', 'expirations', 'supersessions')
            )
        except Exception as exc:
            self.stdout.write(f'parse                     = ERROR {type(exc).__name__}')
            schema_ok = False
        self.stdout.write('call                      = OK')
        self.stdout.write(f'schema_ok                 = {str(schema_ok).lower()}')
        if delta_ops:
            self.stdout.write(f'ops_keys_ok               = {str(ops_keys_ok).lower()}')
        self.stdout.write(
            f'reasoning_effort_accepted = {"true" if options_sent else "n/a"}'
        )
        usage = getattr(response, 'usage', None)
        if usage is not None:
            self.stdout.write(
                f'usage_tokens              = in={getattr(usage, "prompt_tokens", "?")} '
                f'out={getattr(usage, "completion_tokens", "?")}'
            )
        self.stdout.write(
            self.style.SUCCESS('PASS')
            if schema_ok and not seed_leaked
            else self.style.ERROR('FAIL')
        )
