import { describe, expect, it } from 'vitest';
import { workOrder } from '../../../../../tests/s0/workFixtures';
import { savedRequest } from '../../../../../tests/s4/workExecutionFixtures';
import { workBrief } from './workBriefProjection';

describe('Work case brief', () => {
  it('keeps absent sources unknown even for completed work', () => {
    const value = workBrief({ ...workOrder(), lifecycle_status: 'completed' });
    expect(value.requests).toBeNull();
    expect(value.tests).toBeNull();
    expect(value.scope).toBeNull();
    expect(value.next).toBeNull();
  });
  it('selects explicit priority, ignoring resolved and unprioritized requests', () => {
    const unprioritized = { ...savedRequest(401), priority: null };
    const selected = { ...savedRequest(402), priority: 0 };
    const resolved = {
      ...savedRequest(403),
      status: 'resolved' as const,
      priority: 0
    };
    const work = {
      ...workOrder(),
      investigation: {
        rule: 'investigation.v1' as const,
        packet: null,
        explanation: null,
        findings: [],
        finding_history: 'not_applicable' as const,
        intervention: null,
        requests: {
          available: true,
          reason: '',
          items: [unprioritized, resolved, selected]
        }
      }
    };
    const brief = workBrief(work);
    expect(brief.next?.id).toBe(402);
    expect(brief.unresolved?.map((r) => r.id)).toEqual([401, 402]);
    expect(work.investigation.requests.items[0].id).toBe(401);
    work.investigation.requests.items[0].work_order = 86;
    expect(workBrief(work).requests).toBeNull();
  });
});
