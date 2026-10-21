import { describe, expect, it } from 'vitest';

import { WORK_PANELS, workPanelParams, workPanelState } from './workPanelState';

describe('native work-panel navigation', () => {
  it('preserves legacy links as the Work view without rewriting history', () => {
    expect(workPanelState(new URLSearchParams())).toBe('work');
  });
  it.each(WORK_PANELS)(
    'directly addresses %s without replacing scope or other route state',
    (panel) => {
      const origin = new URLSearchParams(
        'work_scope=mine&search=seal&work_page=2'
      );
      const next = workPanelParams(origin, panel);
      expect(workPanelState(next)).toBe(panel);
      expect(next.get('work_scope')).toBe('mine');
      expect(next.get('search')).toBe('seal');
      expect(next.get('work_page')).toBe('2');
      expect(origin.has('work_panel')).toBe(false);
    }
  );
  it.each(['complete', 'attachments', 'in_progress', 'unknown'])(
    'does not treat %s as a workflow panel or lifecycle command',
    (panel) => {
      expect(
        workPanelState(new URLSearchParams({ work_panel: panel }))
      ).toBeNull();
    }
  );
});
