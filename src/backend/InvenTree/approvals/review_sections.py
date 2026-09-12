"""Deterministic, content-bound review contracts shared by screen and voice."""

import hashlib
import json

from .models import ActionType


def _text(value):
    if value is None or value == '':
        return 'Not supplied'
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return str(value)


def _section(key, label, value, *, required=True):
    return {'id': key, 'label': label, 'text': _text(value), 'required': required}


def _addresses(value):
    return (
        ', '.join(str(v) for v in value) if isinstance(value, list) else value or 'None'
    )


def build_review_sections(approval):
    """Build every required fact from the persisted request, never model prose."""
    payload = approval.payload if isinstance(approval.payload, dict) else {}
    action = approval.action_type
    sections = [_section('summary', 'Request', approval.summary)]
    if action == ActionType.EMAIL:
        body = str(payload.get('body', payload.get('body_text', '')))
        if '_mailbox' in payload:
            sections += [
                _section('mailbox', 'Mailbox', payload['_mailbox'].get('name')),
                _section('sender', 'From', payload.get('sender')),
                _section(
                    'reply_context',
                    'Reply to message',
                    payload.get('reply_message_id') or 'New conversation',
                ),
            ]
        sections += [
            _section('recipients', 'To', _addresses(payload.get('to'))),
            _section('cc', 'CC', _addresses(payload.get('cc'))),
            _section('bcc', 'BCC', _addresses(payload.get('bcc'))),
            _section('reply_to', 'Reply-To', _addresses(payload.get('reply_to'))),
            _section('subject', 'Subject', payload.get('subject')),
            _section(
                'body_summary',
                'Body summary and length',
                f'{body[:400]} ({len(body)} characters)',
            ),
            _section('body', 'Full message', body),
            _section(
                'attachments', 'Attachments', payload.get('attachments') or 'None'
            ),
            _section(
                'external_effect',
                'External effect',
                'Sends this message to every To, CC and BCC recipient. Sending cannot be undone.',
            ),
        ]
    elif action in (ActionType.PURCHASE_ORDER, ActionType.SALES_ORDER):
        purchasing = action == ActionType.PURCHASE_ORDER
        party = 'supplier' if purchasing else 'customer'
        sections.append(
            _section(
                'party',
                party.title(),
                payload.get(f'{party}_name') or payload.get(f'{party}_id'),
            )
        )
        lines = payload.get('line_items', payload.get('lines', []))
        if not isinstance(lines, list):
            sections.append(_section('invalid_lines', 'Invalid lines', lines))
            lines = []
        for index, line in enumerate(lines):
            if not isinstance(line, dict):
                sections.append(_section(f'line_{index + 1}', 'Invalid line', line))
                continue
            sections.append(
                _section(
                    f'line_{index + 1}',
                    f'Line {index + 1}',
                    f'Part {_text(line.get("part_name") or line.get("part_id") or line.get("supplier_part_id"))}; '
                    f'quantity {_text(line.get("quantity"))}; unit {_text(line.get("unit") or line.get("units"))}; '
                    f'unit price {_text(line.get("unit_price", line.get("purchase_price")))}; '
                    f'currency {_text(line.get("currency") or payload.get("currency"))}.',
                )
            )
        sections += [
            _section(
                'total',
                'Total and currency',
                f'{_text(payload.get("total"))} {_text(payload.get("currency"))}',
            ),
            _section(
                'external_effect',
                'External effect',
                {
                    'create_purchase_order': 'Creates a draft only; no order is issued and no email is sent.',
                    'add_po_line_item': 'Adds this reviewed line to the named draft; no email is sent.',
                    'issue_purchase_order': 'Changes the named order to Placed; this does not send an email.',
                }.get(
                    payload.get('operation', 'create_purchase_order'),
                    'Unavailable operation',
                )
                if purchasing
                else 'Creates a sales order record. Any external notification requires separate confirmation.',
            ),
        ]
        if purchasing:
            operation = payload.get('operation', 'create_purchase_order')
            sections += [
                _section('operation', 'Purchasing action', operation),
                _section(
                    'order_reference',
                    'Existing order',
                    payload.get('order_reference') or 'New draft',
                ),
                _section('purchasing_details', 'Complete purchasing details', payload),
                _section(
                    'order_baseline',
                    'Existing order at review time',
                    approval.baseline_context or 'New draft',
                ),
            ]
    elif action == ActionType.STOCK_UPDATE:
        for key, label in [
            ('stock_item_id', 'Stock item'),
            ('location_id', 'Location'),
            ('quantity', 'Quantity'),
            ('units', 'Units'),
            ('direction', 'Direction'),
        ]:
            sections.append(_section(key, label, payload.get(key)))
    elif action == ActionType.REPAIR_WORK_PACKAGE:
        for key, label in [
            ('machine_id', 'Machine'),
            ('title', 'Title'),
            ('fault', 'Fault'),
            ('parts', 'Parts and quantities'),
            ('priority', 'Priority'),
            ('safety', 'Safety packet'),
        ]:
            value = payload.get(key)
            if key == 'machine_id':
                value = payload.get('machine_name') or value or payload.get('machine')
            if key == 'fault':
                value = (
                    value
                    or payload.get('fault_description')
                    or payload.get('description')
                )
            if key == 'safety':
                value = (
                    value
                    or payload.get('safety_packet')
                    or (
                        'The canonical planning command determines the safety packet and gates. '
                        'Creating this package does not authorize starting work.'
                    )
                )
            sections.append(_section(key, label, value))
        sections.append(
            _section(
                'effect',
                'Effect',
                'Plans a repair work package. Does not start work or satisfy any safety gate.',
            )
        )
    else:
        # Still show the complete record on screen; a generic contract never
        # confers voice eligibility on a new or unsupported action type.
        sections.append(_section('details', 'Request details', payload))
    return sections


SCREEN_ONLY_REASONS = {
    ActionType.SAFETY_GATE: 'Safety-gate waivers require visual evidence inspection.',
    ActionType.PROCEDURE_PUBLISH: 'Procedure publishing requires screen review; the second-read policy is unsettled.',
    ActionType.JOB_KIT_SUBSTITUTION: 'Job-kit substitution has no registered business executor.',
    ActionType.WORKFLOW: 'Workflow auditory-review and real-executor contracts are not implemented.',
    ActionType.NOTIFICATION: 'Notification auditory-review and real-executor contracts are not implemented.',
}


def voice_eligibility(approval):
    """Fail closed for incomplete contracts, attachments and missing executors."""
    payload = approval.payload if isinstance(approval.payload, dict) else {}
    if payload.get('attachments'):
        return False, 'Attachments require visual inspection on screen.'
    if approval.risk_tier >= 3:
        return False, 'Tier-3 approval is screen-only under the Phase C pilot policy.'
    if approval.action_type in SCREEN_ONLY_REASONS:
        return False, SCREEN_ONLY_REASONS[approval.action_type]
    if approval.action_type not in ActionType.values:
        return False, 'No reviewed auditory contract exists for this action.'
    if approval.action_type in (ActionType.SALES_ORDER, ActionType.STOCK_UPDATE):
        return (
            False,
            'The canonical business executor is not yet implemented for voice.',
        )
    from .executors import registry

    if not registry.has(approval.action_type) or not getattr(
        registry.get(approval.action_type), 'implemented', False
    ):
        return False, 'A real registered business executor is required.'
    try:
        invalid = registry.get(approval.action_type).validate(payload)
    except Exception:
        # Eligibility is a read contract, not an opportunity to crash the inbox
        # or bypass validation when a stored legacy payload has a bad shape.
        invalid = True
    if invalid:
        return (
            False,
            'The request is incomplete; correct it on screen before voice review.',
        )
    return True, None


def compute_review_hash(approval):
    """Bind acknowledgment to the actor-independent complete review content."""
    content = {
        'schema': 'approval-review-v1',
        'approval_id': str(approval.pk),
        'revision': approval.current_revision_number,
        'action_type': approval.action_type,
        'risk_tier': approval.risk_tier,
        'payload': approval.payload,
        'baseline': approval.baseline_context,
        'preconditions': approval.preconditions,
        'sections': build_review_sections(approval),
    }
    encoded = json.dumps(
        content, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str
    )
    return hashlib.sha256(encoded.encode()).hexdigest()
