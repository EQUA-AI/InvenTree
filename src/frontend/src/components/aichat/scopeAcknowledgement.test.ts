import { describe, expect, it } from 'vitest';

import type {
  AnalysisScopeUpdate,
  ThreadScopePayload
} from '@lib/types/AimmsWire.generated';

import {
  parseScopeAcknowledgement,
  parseThreadScopePayload
} from './scopeAcknowledgement';

const threadId = 'thread-canonical-context';
const request: AnalysisScopeUpdate = {
  mode: 'explicit_assets',
  machine_ids: [7],
  display_label: 'Pump A'
};

function payload(): ThreadScopePayload {
  return {
    thread_id: threadId,
    version: 1,
    hash: 'a'.repeat(64),
    editable: true,
    display_label: 'Pump A',
    scope: {
      schema_version: 1,
      mode: 'explicit_assets',
      machine_ids: [7],
      date_window: { from: null, to: null },
      source_classes: [
        'controlled_document',
        'asset_attachment',
        'work_order',
        'maintenance_record'
      ],
      display_label: 'Pump A'
    }
  };
}

describe('native scope payload boundary', () => {
  it('does not coerce a non-string fleet mode into a supported mode', () => {
    const original = payload();
    expect(
      parseThreadScopePayload(
        {
          ...original,
          scope: {
            ...original.scope,
            machine_ids: [],
            mode: ['all_authorized_assets']
          }
        },
        threadId
      )
    ).toBeNull();
  });
  it('accepts the full typed response without discarding fields', () => {
    const original = payload();
    expect(parseThreadScopePayload(original, threadId)).toBe(original);
  });

  it('accepts native legacy shared read-only reads', () => {
    const legacy = payload();
    legacy.version = 0;
    legacy.hash = '';
    legacy.editable = false;
    legacy.scope.mode = 'legacy_unconfirmed';
    legacy.scope.machine_ids = [];
    expect(parseThreadScopePayload(legacy, threadId)).toBe(legacy);
  });

  it.each([
    ['thread_id', 'other-thread'],
    ['version', '1'],
    ['version', -1],
    ['version', Number.MAX_SAFE_INTEGER + 1],
    ['hash', ''],
    ['hash', 'z'.repeat(64)],
    ['editable', 'true'],
    ['display_label', null]
  ])('rejects invalid outer %s: %s', (field, value) => {
    expect(
      parseThreadScopePayload(
        { ...payload(), [String(field)]: value },
        threadId
      )
    ).toBeNull();
  });

  it.each([
    ['schema_version', 2],
    ['mode', 'site_group'],
    ['mode', 'all_authorized_assets'],
    ['machine_ids', [true]],
    ['machine_ids', ['7']],
    ['machine_ids', [7, 7]],
    ['machine_ids', []],
    ['machine_ids', [0]],
    ['source_classes', ['unknown']],
    ['source_classes', []],
    ['date_window', null],
    ['date_window', { from: '2026-02-31', to: null }],
    ['date_window', { from: '2026-10-20', to: '2026-10-19' }],
    ['display_label', 'a'.repeat(121)]
  ])('rejects invalid scope %s: %s', (field, value) => {
    const original = payload();
    expect(
      parseThreadScopePayload(
        { ...original, scope: { ...original.scope, [String(field)]: value } },
        threadId
      )
    ).toBeNull();
  });

  it.each([null, [], {}, { scope: null }])(
    'rejects missing contract fields: %s',
    (value) => {
      expect(parseThreadScopePayload(value, threadId)).toBeNull();
    }
  );
});

describe('exact native scope acknowledgement', () => {
  it('accepts the next version for the exact request', () => {
    const original = payload();
    expect(parseScopeAcknowledgement(original, threadId, 0, request)).toBe(
      original
    );
  });

  it('compares normalized sets without mutating the request', () => {
    const original = payload();
    original.scope.machine_ids = [7, 9];
    const selected = { ...request, machine_ids: [9, 7, 7] };
    expect(parseScopeAcknowledgement(original, threadId, 0, selected)).toBe(
      original
    );
    expect(selected.machine_ids).toEqual([9, 7, 7]);
  });

  it('accepts authorized fleet and supplied date/source narrowing', () => {
    const original = payload();
    original.scope.mode = 'all_authorized_assets';
    original.scope.machine_ids = [];
    original.scope.display_label = '';
    original.scope.source_classes = ['work_order'];
    original.scope.date_window = { from: '2026-10-01', to: '2026-10-19' };
    expect(
      parseScopeAcknowledgement(original, threadId, 0, {
        mode: 'all_authorized_assets',
        source_classes: ['work_order'],
        date_window: original.scope.date_window
      })
    ).toBe(original);
  });

  it.each([
    ['machine_ids', [8]],
    ['machine_ids', [7, 8]],
    ['display_label', 'Other context'],
    ['source_classes', ['work_order']],
    ['date_window', { from: '2026-10-01', to: null }]
  ])('rejects mismatched acknowledged %s', (field, value) => {
    const original = payload();
    expect(
      parseScopeAcknowledgement(
        { ...original, scope: { ...original.scope, [String(field)]: value } },
        threadId,
        0,
        request
      )
    ).toBeNull();
  });

  it('rejects a read-only acknowledgement', () => {
    expect(
      parseScopeAcknowledgement(
        { ...payload(), editable: false },
        threadId,
        0,
        request
      )
    ).toBeNull();
  });

  it('rejects a nonincrementing acknowledgement', () => {
    expect(
      parseScopeAcknowledgement(
        { ...payload(), version: 2 },
        threadId,
        0,
        request
      )
    ).toBeNull();
  });

  it.each([-1, 0.5, Number.MAX_SAFE_INTEGER, Number.NaN])(
    'rejects invalid expected version %s',
    (version) => {
      expect(
        parseScopeAcknowledgement(payload(), threadId, version, request)
      ).toBeNull();
    }
  );
});
