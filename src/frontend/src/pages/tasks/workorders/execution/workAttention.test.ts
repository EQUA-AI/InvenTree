import { describe, expect, it } from 'vitest';

import type { WorkOrderOverview } from '@lib/types/WorkOrderOverview';

import { workAttention } from './workAttention';

type Work = Parameters<typeof workAttention>[0];

function handover(
  extra: Partial<{ state: string; can_accept: boolean; id: number | null }>
) {
  return {
    rule: 'work_continuity.v1',
    accepted_handover: {
      available: true,
      id: 31,
      state: 'pending',
      can_accept: true,
      ...extra
    }
  } as unknown as WorkOrderOverview['work_continuity'];
}

function interval(
  id: number,
  person_id: number,
  extra: Partial<{ ended_at: string | null; superseded: boolean }> = {}
) {
  return {
    id,
    person_id,
    person: null,
    started_at: '2026-10-20T08:00:00Z',
    ended_at: null,
    end_action: null,
    recorded_by: null,
    recorded_at: '2026-10-20T08:00:00Z',
    ended_by: null,
    ended_recorded_at: null,
    reason: null,
    supersedes: null,
    superseded: false,
    ...extra
  };
}

function work(extra: Partial<Work> = {}): Work {
  return {
    lifecycle_status: 'in_progress',
    lifecycle_version: 7,
    work_continuity: undefined,
    time_assignment: undefined,
    ...extra
  };
}

function time(
  intervals: ReturnType<typeof interval>[],
  open_at_closure = false
): WorkOrderOverview['time_assignment'] {
  return {
    rule: 'time_assignment.v1',
    time_records: {
      observed_at: '2026-10-20T09:00:00Z',
      coverage: 'partial',
      recorded_seconds: null,
      intervals,
      gaps: [],
      open_at_closure
    }
  } as unknown as WorkOrderOverview['time_assignment'];
}

describe('Work context attention', () => {
  it('is quiet when nothing needs this person', () => {
    expect(workAttention(work(), 5)).toEqual({
      continuity: null,
      assignment: null,
      keys: []
    });
  });

  it('opens continuity only for a pending handover the server says can be accepted', () => {
    expect(
      workAttention(work({ work_continuity: handover({}) }), 5).continuity
    ).toBe('handover');
    for (const extra of [
      { can_accept: false },
      { state: 'accepted' },
      { id: null }
    ])
      expect(
        workAttention(work({ work_continuity: handover(extra) }), 5).continuity
      ).toBeNull();
    const unsupported = {
      ...handover({}),
      rule: 'work_continuity.v0'
    } as unknown as WorkOrderOverview['work_continuity'];
    expect(
      workAttention(work({ work_continuity: unsupported }), 5).continuity
    ).toBeNull();
  });

  it('keeps held Work as a continuity reason with a versioned key', () => {
    const held = workAttention(work({ lifecycle_status: 'on_hold' }), 5);
    expect(held.continuity).toBe('hold');
    expect(held.keys).toEqual(['continuity:hold:7']);
    const both = workAttention(
      work({ lifecycle_status: 'on_hold', work_continuity: handover({}) }),
      5
    );
    expect(both.continuity).toBe('handover');
    expect(both.keys).toEqual(['continuity:handover:31', 'continuity:hold:7']);
  });

  it("opens assignment for this person's own running interval only", () => {
    expect(
      workAttention(work({ time_assignment: time([interval(4, 5)]) }), 5)
    ).toMatchObject({
      assignment: 'own_running',
      keys: ['assignment:running:4']
    });
    for (const rows of [
      [interval(4, 6)],
      [interval(4, 5, { ended_at: '2026-10-20T08:30:00Z' })],
      [interval(4, 5, { superseded: true })]
    ])
      expect(
        workAttention(work({ time_assignment: time(rows) }), 5).assignment
      ).toBeNull();
    expect(
      workAttention(work({ time_assignment: time([interval(4, 5)]) }), null)
        .assignment
    ).toBeNull();
  });

  it('opens assignment when Work is closed with an open interval', () => {
    const closed = workAttention(
      work({
        lifecycle_status: 'completed',
        time_assignment: time([interval(4, 5)], true)
      }),
      5
    );
    expect(closed.assignment).toBe('open_at_closure');
    expect(closed.keys).toEqual([
      'assignment:closure:7',
      'assignment:running:4'
    ]);
  });
});
