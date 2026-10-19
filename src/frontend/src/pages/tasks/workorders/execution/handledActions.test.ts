import { describe, expect, it } from 'vitest';

import type { HandledAction } from '@lib/types/WorkExecution';

import { handledLinkTarget, readHandledActions } from './handledActions';

function row(
  id: string,
  state: HandledAction['state'],
  extra: Partial<HandledAction> = {}
): HandledAction {
  return {
    id,
    kind: 'packet_prepared',
    state,
    status: state === 'completed' ? 'succeeded' : 'running',
    occurred_at: '2026-10-18T12:00:00Z',
    started_at: '2026-10-18T11:58:00Z',
    finished_at: null,
    source: { record: 'repair_packet_generation_run', id: 1, provider: null },
    trigger: null,
    result: null,
    link: null,
    attempts: null,
    ...extra
  };
}

const projection = (actions: HandledAction[], counts?: object) => ({
  rule: 'handled_actions.v1',
  sources: [
    { key: 'packet_preparation', state: 'read' },
    { key: 'closeout_follow_up', state: 'not_enabled' }
  ],
  counts: counts ?? {
    completed: actions.filter((a) => a.state === 'completed').length,
    active: actions.filter((a) => a.state === 'active').length,
    failed: actions.filter((a) => a.state === 'failed').length,
    unknown: actions.filter((a) => a.state === 'unknown').length
  },
  actions
});

describe('readHandledActions', () => {
  it('treats an absent or foreign projection as unsupported, never as none', () => {
    expect(readHandledActions(undefined)).toEqual({ kind: 'unsupported' });
    expect(readHandledActions(null)).toEqual({ kind: 'unsupported' });
    expect(
      readHandledActions({ ...projection([]), rule: 'handled_actions.v2' })
    ).toEqual({ kind: 'unsupported' });
    expect(
      readHandledActions({ ...projection([]), actions: [{ id: 1 }] })
    ).toEqual({ kind: 'unsupported' });
  });

  it('separates active work from recorded results and names disabled sources', () => {
    const reading = readHandledActions(
      projection([
        row('a', 'completed'),
        row('b', 'active'),
        row('c', 'failed'),
        row('d', 'unknown')
      ])
    );
    expect(reading.kind).toBe('ready');
    if (reading.kind !== 'ready') return;
    expect(reading.handling.map((r) => r.id)).toEqual(['b']);
    expect(reading.recorded.map((r) => r.id)).toEqual(['a', 'c', 'd']);
    expect(reading.notEnabled).toEqual(['closeout_follow_up']);
    expect(reading.projection.counts).toEqual({
      completed: 1,
      active: 1,
      failed: 1,
      unknown: 1
    });
  });

  it('refuses served counts that disagree with the rows', () => {
    expect(
      readHandledActions(
        projection([row('a', 'completed')], {
          completed: 2,
          active: 0,
          failed: 0,
          unknown: 0
        })
      )
    ).toEqual({ kind: 'unsupported' });
  });

  it('accepts a successful empty projection as zero recorded actions', () => {
    const reading = readHandledActions(projection([]));
    expect(reading.kind).toBe('ready');
    if (reading.kind === 'ready')
      expect(reading.projection.counts.completed).toBe(0);
  });
});

describe('handledLinkTarget', () => {
  const params = new URLSearchParams('work_panel=work&search=seal');

  it('links a packet result to its native page only with a valid id', () => {
    expect(
      handledLinkTarget(
        { kind: 'repair_packet', id: 901, reference: 'RP-901' },
        params
      )
    ).toBe('/repair/packets/901/');
    expect(
      handledLinkTarget(
        { kind: 'repair_packet', id: 0, reference: 'RP-0' },
        params
      )
    ).toBeNull();
  });

  it('opens an owning panel on the same Work and keeps other query state', () => {
    expect(
      handledLinkTarget({ kind: 'work_panel', panel: 'closeout' }, params)
    ).toBe('?work_panel=closeout&search=seal');
    expect(
      handledLinkTarget({ kind: 'work_panel', panel: 'work' }, params)
    ).toBe('?search=seal');
    expect(handledLinkTarget(null, params)).toBeNull();
  });
});
