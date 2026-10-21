import { describe, expect, it } from 'vitest';
import { workOrder } from '../../../../../tests/s0/workFixtures';
import { revisionWork } from '../../../../../tests/s4/scopeRevisionFixtures';
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
  it('uses the same effective scope boundary as Plan & Safety', () => {
    const work = revisionWork();
    const current = work.prepared_package!.scopes.find(
      (scope) => scope.current
    )!;
    const earlier = work.prepared_package!.scopes.find(
      (scope) => !scope.current
    )!;
    expect(workBrief(work).scope?.id).toBe(current.id);

    work.prepared_package!.revision_review!.current_scope_id = null;
    expect(workBrief(work).scope).toBeNull();
    delete work.prepared_package!.revision_review;
    expect(workBrief(work).scope?.id).toBe(current.id);

    current.current = false;
    earlier.current = true;
    expect(workBrief(work).scope).toBeNull();
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
