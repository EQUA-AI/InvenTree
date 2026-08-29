"""Generate the TypeScript wire contract from backend definitions (S43).

One committed artifact — ``src/frontend/lib/types/AimmsWire.generated.ts``
— emitted from the enums and payload models the backend actually serves:

- ``ai.core.streaming.EventType``            -> ``AGUIEventType`` enum
- ``aichat.models.ProposalAction``/``State`` -> unions + en label maps
- ``repair.risk_models`` state/severity      -> unions
- ``RiskFindingSerializer.Meta.fields``      -> ``RISK_FINDING_FIELDS``
- ``ai.core.voice.wire`` pydantic models     -> interfaces
- ``SERVER_VOICE_ERROR_CODES``               -> ``ServerVoiceErrorCode``
- ``ai.core.analysis.scope``/``.wire``       -> scope unions + interfaces

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


def _emit_model_interface(
    model_cls,
    overrides: dict[str, str] | None = None,
    optional_fields: tuple[str, ...] = (),
) -> str:
    """Emit one interface; ``overrides`` maps a field name to a full TS type.

    The wire name is the field's alias when one is declared (``from`` is a
    Python keyword, so its model field is ``from_`` with ``alias='from'``).
    ``optional_fields`` marks REQUEST-model fields as ``?`` — response
    models keep every field required because the server always emits them.
    """
    rows = []
    for field_name, field in model_cls.model_fields.items():
        wire_name = field.alias or field_name
        ts_type = (overrides or {}).get(field_name) or _ts_type(field.annotation)
        marker = '?' if field_name in optional_fields else ''
        rows.append(f'  {wire_name}{marker}: {ts_type};')
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

        sections.append(
            '// --- S49 /agui CUSTOM channels (ai.core.agui.translate) ---\n'
        )
        from ai.core.agui.translate import CUSTOM_CHANNELS

        sections.append(
            f'export type AimmsCustomChannel =\n  | {_ts_string_union(list(CUSTOM_CHANNELS))};\n'
        )

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

        sections.append('// --- Analysis scope (ai.core.analysis.scope / .wire) ---\n')
        from ai.core.analysis.scope import SOURCE_CLASSES, WIRE_MODES
        from ai.core.analysis.wire import (
            SCOPE_ERROR_CODES,
            ActiveScopeSummary,
            AnalysisScopeDateWindow,
            AnalysisScopePayload,
            AnalysisScopeUpdate,
            ThreadScopePayload,
            ThreadScopeUpdateRequest,
        )

        sections.append(
            f'export type AnalysisScopeMode =\n  | {_ts_string_union(list(WIRE_MODES))};\n'
        )
        sections.append(
            f'export type AnalysisSourceClass =\n  | {_ts_string_union(list(SOURCE_CLASSES))};\n'
        )
        sections.append(_emit_model_interface(AnalysisScopeDateWindow))
        sections.append(
            _emit_model_interface(
                AnalysisScopePayload,
                overrides={
                    'mode': 'AnalysisScopeMode',
                    'source_classes': 'AnalysisSourceClass[]',
                },
            )
        )
        sections.append(
            _emit_model_interface(
                AnalysisScopeUpdate,
                overrides={
                    'mode': 'AnalysisScopeMode',
                    'source_classes': 'AnalysisSourceClass[] | null',
                },
                optional_fields=(
                    'machine_ids',
                    'date_window',
                    'source_classes',
                    'display_label',
                ),
            )
        )
        sections.append(
            _emit_model_interface(
                ActiveScopeSummary, overrides={'mode': 'AnalysisScopeMode'}
            )
        )
        sections.append(_emit_model_interface(ThreadScopePayload))
        sections.append(_emit_model_interface(ThreadScopeUpdateRequest))
        sections.append(
            f'export type ScopeErrorCode =\n  | {_ts_string_union(list(SCOPE_ERROR_CODES))};\n'
        )

        sections.append('// --- Quota / admission (ai.core.quota.wire) ---\n')
        from ai.core.quota.wire import (
            QUOTA_ERROR_CODES,
            QuotaPreflightPayload,
            QuotaStoreStatus,
            QuotaTokenLevel,
            QuotaWindowStatus,
        )

        sections.append(
            f'export type QuotaErrorCode =\n  | {_ts_string_union(list(QUOTA_ERROR_CODES))};\n'
        )
        from ai.core.pilot_latch import PILOT_ERROR_CODES

        sections.append(
            f'export type PilotErrorCode =\n  | {_ts_string_union(list(PILOT_ERROR_CODES))};\n'
        )
        sections.append(_emit_model_interface(QuotaWindowStatus))
        sections.append(_emit_model_interface(QuotaTokenLevel))
        sections.append(_emit_model_interface(QuotaStoreStatus))
        sections.append(
            _emit_model_interface(
                QuotaPreflightPayload,
                overrides={
                    'tokens': 'Record<string, QuotaTokenLevel>',
                    'requests': 'Record<string, QuotaWindowStatus>',
                    'store': 'QuotaStoreStatus',
                },
            )
        )

        sections.append(
            '// --- Evidence analysis v2 (ai.core.analysis.schemas / .wire) ---\n'
        )
        from ai.core.analysis.schemas import EvidenceClassification
        from ai.core.analysis.wire import (
            ANALYSIS_NO_DATA_REASONS,
            ANALYSIS_PROGRESS_STAGES,
            AnalysisIncompleteReasonPayload,
            AnalysisScopeStamp,
            CitationLocator,
            CitationManifestEntry,
            ClaimPayload,
            EvidenceSetMember,
            EvidenceSetPage,
            RetrievalCoveragePayload,
        )

        classification_values = [str(choice.value) for choice in EvidenceClassification]
        sections.append(
            'export type EvidenceClassification =\n'
            f'  | {_ts_string_union(classification_values)};\n'
        )
        sections.append(
            'export type AnalysisProgressStage =\n'
            f'  | {_ts_string_union(list(ANALYSIS_PROGRESS_STAGES))};\n'
        )
        sections.append(
            'export type AnalysisNoDataReason =\n'
            f'  | {_ts_string_union(list(ANALYSIS_NO_DATA_REASONS))};\n'
        )
        sections.append(_emit_model_interface(RetrievalCoveragePayload))
        sections.append(_emit_model_interface(CitationLocator))
        sections.append(_emit_model_interface(CitationManifestEntry))
        sections.append(
            _emit_model_interface(
                ClaimPayload,
                overrides={'evidence_classification': 'EvidenceClassification'},
            )
        )
        sections.append(_emit_model_interface(AnalysisScopeStamp))
        sections.append(_emit_model_interface(AnalysisIncompleteReasonPayload))
        sections.append(_emit_model_interface(EvidenceSetMember))
        sections.append(_emit_model_interface(EvidenceSetPage))

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
