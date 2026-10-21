import { describe, expect, it } from 'vitest';

import { boardCardIdentity, projectCloseoutWorkOrder } from './workProjection';

const work = Object.freeze({
  id: 73,
  reference: 'WO-73',
  title: 'Synthetic maintenance',
  lifecycle_status: 'verifying',
  lifecycle_version: 7
});

describe('canonical work display seams', () => {
  it.each([501, 502])(
    'keeps card %s distinct from the same owning work',
    (cardId) => {
      const source = Object.freeze({ id: cardId, work_order: 73 });
      expect(boardCardIdentity(source)).toEqual({
        id: cardId,
        workOrderId: 73
      });
      expect(source).toEqual({ id: cardId, work_order: 73 });
    }
  );

  it('preserves a different owner instead of reusing previous case state', () => {
    expect(boardCardIdentity({ id: 601, work_order: 86 })).toEqual({
      id: 601,
      workOrderId: 86
    });
  });

  it('preserves the exact canonical command identity and version', () => {
    expect(projectCloseoutWorkOrder(work)).toEqual(work);
  });

  it('keeps the existing display fallback separate from the owning ID', () => {
    expect(projectCloseoutWorkOrder({ ...work, reference: null })).toEqual({
      ...work,
      reference: 'WO-73'
    });
  });

  it('does not normalize an explicit empty reference or version zero', () => {
    expect(
      projectCloseoutWorkOrder({ ...work, reference: '', lifecycle_version: 0 })
    ).toEqual({ ...work, reference: '', lifecycle_version: 0 });
  });

  it('does not infer physical return or external delivery from completion', () => {
    const result = projectCloseoutWorkOrder({
      ...work,
      lifecycle_status: 'completed'
    });
    expect(Object.keys(result).sort()).toEqual(Object.keys(work).sort());
    expect(result).not.toHaveProperty('returned_to_service');
    expect(result).not.toHaveProperty('external_acknowledged');
  });
});
