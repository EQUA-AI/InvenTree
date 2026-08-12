"""Generate the TypeScript wire contract from backend definitions (S43).

One committed artifact — ``src/frontend/lib/types/AimmsWire.generated.ts``
— emitted from the enums and payload models the backend actually serves:

- ``ai.core.streaming.EventType``            -> ``AGUIEventType`` enum
- ``aichat.models.ProposalAction``/``State`` -> unions + en label maps
- ``repair.risk_models`` state/severity      -> unions
- ``RiskFindingSerializer.Meta.fields``      -> ``RISK_FINDING_FIELDS``
- ``ai.core.voice.wire`` pydantic models     -> interfaces
- ``SERVER_VOICE_ERROR_CODES``               -> ``ServerVoiceErrorCode``

The output is byte-deterministic (definition order, forced ``en`` labels),
so ``--check`` can compare bytes: CI runs it and fails on drift, making the
hand-mirroring era's silent divergence structurally impossible.
"""

from __future__ import annotations

import types
import typing
from pathlib import Path

from django.core.management.base import BaseCommand
from django.utils import translation

HEADER = """\
// GENERATED FILE — DO NOT EDIT.
//
// Source of truth: backend definitions (see
// aichat/management/commands/generate_wire_contract.py). Regenerate with:
//     python manage.py generate_wire_contract
// CI runs `generate_wire_contract --check` and fails on drift.
"""


def _artifact_path() -> Path:
    # .../src/backend/InvenTree/aichat/management/commands/<this file>
    src_dir = Path(__file__).resolve().parents[5]
    return src_dir / 'frontend' / 'lib' / 'types' / 'AimmsWire.generated.ts'


def _ts_string_union(values: list[str]) -> str:
    return '\n  | '.join(f"'{value}'" for value in values)


def _emit_event_enum() -> str:
    from ai.core.streaming import EventType

    members = '\n'.join(f"  {member.name} = '{member.value}'," for member in EventType)
    return f'export enum AGUIEventType {{\n{members}\n}}\n'


def _ts_type(annotation) -> str:
    """Map a pydantic field annotation to its TS type."""
    from pydantic import BaseModel

    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        parts = list(typing.get_args(annotation))
        has_none = type(None) in parts
        rest = [_ts_type(arg) for arg in parts if arg is not type(None)]
        joined = ' | '.join(dict.fromkeys(rest))
        return f'{joined} | null' if has_none else joined
    if origin is typing.Literal:
        return ' | '.join(f"'{arg}'" for arg in typing.get_args(annotation))
    if origin in (list, tuple):
        args = typing.get_args(annotation)
        inner = _ts_type(args[0]) if args else 'unknown'
        return f'{inner}[]'
    if annotation is str:
        return 'string'
    if annotation is bool:
        return 'boolean'
    if annotation in (int, float):
        return 'number'
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation.__name__
    return 'unknown'


def _emit_model_interface(model_cls) -> str:
    rows = []
    for field_name, field in model_cls.model_fields.items():
        rows.append(f'  {field_name}: {_ts_type(field.annotation)};')
    body = '\n'.join(rows)
    return f'export interface {model_cls.__name__} {{\n{body}\n}}\n'


class Command(BaseCommand):
    """Emit (or verify) the generated TypeScript wire contract."""

    help = 'Generate src/frontend/lib/types/AimmsWire.generated.ts from backend definitions'

    def add_arguments(self, parser) -> None:
        """Register the drift-check mode."""
        parser.add_argument(
            '--check',
            action='store_true',
            help='Compare against the committed artifact; exit 1 on drift',
        )

    def _render(self) -> str:
        from ai.core.voice.wire import (
            SERVER_VOICE_ERROR_CODES,
            VoicePendingQuestion,
            VoicePendingQuestionOption,
            VoiceSessionPayload,
            VoiceSpokenPayload,
            VoiceTransportsAllowed,
            VoiceTurnResponse,
        )
        from aichat.models import ProposalAction, ProposalState
        from repair.risk_models import RiskFindingState, RiskScanStatus, RiskSeverity
        from repair.risk_serializers import RiskFindingSerializer

        sections: list[str] = [HEADER]

        sections.append('// --- AG-UI event types (ai.core.streaming.EventType) ---\n')
        sections.append(_emit_event_enum())

        sections.append('// --- Proposal rail (aichat.models) ---\n')
        action_values = [str(choice.value) for choice in ProposalAction]
        sections.append(
            f'export type ProposalActionType =\n  | {_ts_string_union(action_values)};\n'
        )
        with translation.override('en'):
            action_rows = '\n'.join(
                f"  '{choice.value}': '{choice.label}'," for choice in ProposalAction
            )
        sections.append(
            'export const PROPOSAL_ACTION_LABELS: Record<ProposalActionType, string> = {\n'
            f'{action_rows}\n}};\n'
        )
        state_values = [str(choice.value) for choice in ProposalState]
        sections.append(
            f'export type ProposalStateType =\n  | {_ts_string_union(state_values)};\n'
        )

        sections.append('// --- Risk radar (repair.risk_models / serializers) ---\n')
        for name, cls in (
            ('RiskSeverity', RiskSeverity),
            ('RiskFindingState', RiskFindingState),
            ('RiskScanStatus', RiskScanStatus),
        ):
            values = [str(choice.value) for choice in cls]
            sections.append(f'export type {name} =\n  | {_ts_string_union(values)};\n')
        finding_fields = ',\n'.join(
            f"  '{field}'" for field in RiskFindingSerializer.Meta.fields
        )
        sections.append(
            f'export const RISK_FINDING_FIELDS = [\n{finding_fields},\n] as const;\n'
        )

        sections.append('// --- Voice wire payloads (ai.core.voice.wire) ---\n')
        for model in (
            VoiceTransportsAllowed,
            VoiceSessionPayload,
            VoiceSpokenPayload,
            VoicePendingQuestionOption,
            VoicePendingQuestion,
            VoiceTurnResponse,
        ):
            sections.append(_emit_model_interface(model))
        sections.append(
            'export type ServerVoiceErrorCode =\n'
            f'  | {_ts_string_union(list(SERVER_VOICE_ERROR_CODES))};\n'
        )

        return '\n'.join(sections)

    def handle(self, *args, **options) -> None:
        """Render; write or verify per --check."""
        rendered = self._render()
        path = _artifact_path()
        if options['check']:
            current = path.read_text() if path.exists() else ''
            if current != rendered:
                self.stderr.write(
                    f'WIRE CONTRACT DRIFT: {path} does not match backend '
                    'definitions. Run: python manage.py generate_wire_contract'
                )
                raise SystemExit(1)
            self.stdout.write('wire contract up to date')
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered)
        self.stdout.write(f'wrote {path}')
