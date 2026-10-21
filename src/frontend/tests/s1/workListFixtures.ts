import { type Page, expect } from '@playwright/test';

import { workListPreferenceKey } from '../../src/pages/maintenance/workListState';
import { mockWork, workOrder } from '../s0/workFixtures';

export const workListKey = (actorId = 11) =>
  workListPreferenceKey('s1-synthetic', actorId);

export async function mockWorkList(page: Page) {
  await mockWork(page);
  await page.route('**/api/notifications/?*', (route) =>
    route.fulfill({ json: { count: 0, results: [], next: null } })
  );
  await page.route(/\/api\/ai\/threads(?:\?|$)/, (route) =>
    route.fulfill({ json: { threads: [], shared_threads: [] } })
  );
  await page.route('**/api/ai/voice/capability', (route) =>
    route.fulfill({ json: { enabled: false, voices: [] } })
  );
  await page.route('**/api/aichat/ui/capabilities/', (route) =>
    route.fulfill({
      json: {
        version: 1,
        risk_radar: false,
        command_center: false,
        maintenance_metrics: 1
      }
    })
  );
  const records = Array.from({ length: 50 }, (_, index) => ({
    ...workOrder(73 + index),
    title:
      index === 0
        ? 'Inspect the North transfer pump mechanical seal and retained vibration evidence before the next planned service window — synthetic work, not an actual plant event'
        : `Synthetic job ${73 + index}`,
    machine_name:
      'Synthetic North transfer pump · EQUIPMENT-REFERENCE-0123456789',
    priority: ['high', 'medium', 'low'][index % 3],
    lifecycle_status: ['planned', 'draft', 'on_hold', 'in_progress'][index % 4],
    assigned_to: index < 30 ? 11 : index === 49 ? null : 12,
    assigned_to_name:
      index < 30
        ? 'Synthetic owner 11'
        : index === 49
          ? null
          : 'Synthetic owner 12',
    assigned_to_username:
      index < 30
        ? 'synthetic-owner-11'
        : index === 49
          ? null
          : 'synthetic-owner-12',
    assignee: index === 49 ? 'Legacy shift note' : '',
    scheduled_start: index % 2 ? null : '2026-10-18T12:00:00Z',
    scheduled_end: index % 2 ? null : '2026-10-18T14:00:00Z',
    due_date: '2026-10-30',
    work_order_type: index % 2 ? 'corrective' : 'inspection'
  }));
  const requests: { method: string; params: URLSearchParams }[] = [];
  const state = {
    status: 200,
    emptyMine: false,
    emptyAttention: false,
    legacyReads: 0,
    // tasks.persona (work_persona.v1) answer for the reader; neutral unless a
    // test names one. ``personaHold`` keeps the answer back until released.
    persona: null as 'supervisor' | 'technician' | null,
    personaStatus: 200,
    personaReads: 0,
    personaHold: null as Promise<void> | null
  };
  // Mirrors tasks.attention (needs_attention.v1) for the synthetic rows; the
  // backend tests own the rule itself.
  const OPEN = ['planned', 'ready', 'in_progress', 'on_hold', 'verifying'];
  const attentionReasons = (work: (typeof records)[number]) => {
    if (!OPEN.includes(work.lifecycle_status)) return [];
    const reasons: string[] = [];
    if (work.priority === 'high') reasons.push('high_priority');
    if (work.due_date && work.due_date < '2026-10-19') reasons.push('overdue');
    if (work.lifecycle_status === 'on_hold') reasons.push('on_hold');
    if (work.assigned_to === null && !work.assignee) reasons.push('no_owner');
    return reasons;
  };
  const rank = { high: 3, medium: 2, low: 1 } as Record<string, number>;
  await page.route('**/api/kanban/work-orders/', (route) => {
    state.legacyReads += 1;
    return route.fulfill({ json: records });
  });
  await page.route('**/api/kanban/work-orders/persona/', async (route) => {
    state.personaReads += 1;
    if (state.personaHold) await state.personaHold;
    if (state.personaStatus !== 200)
      return route.fulfill({
        status: state.personaStatus,
        json: { detail: 'Synthetic unavailable persona' }
      });
    return route.fulfill({
      json: {
        rule: 'work_persona.v1',
        persona: state.persona,
        default_scope:
          state.persona === 'supervisor'
            ? 'attention'
            : state.persona === 'technician'
              ? 'mine'
              : 'all'
      }
    });
  });
  await page.route('**/api/kanban/work-orders/list/**', (route) => {
    const params = new URL(route.request().url()).searchParams;
    requests.push({ method: route.request().method(), params });
    if (state.status !== 200)
      return route.fulfill({
        status: state.status,
        json: { detail: 'Synthetic unavailable source' }
      });
    const search = (params.get('search') ?? '').toLowerCase();
    const attention = params.get('attention') === 'true';
    let rows = records.filter((work) => {
      if (attention && (state.emptyAttention || !attentionReasons(work).length))
        return false;
      if (
        params.has('assigned_to') &&
        (state.emptyMine ||
          String(work.assigned_to) !== params.get('assigned_to'))
      )
        return false;
      if (params.has('priority') && work.priority !== params.get('priority'))
        return false;
      if (
        params.has('lifecycle_status') &&
        work.lifecycle_status !== params.get('lifecycle_status')
      )
        return false;
      if (
        params.has('work_order_type') &&
        work.work_order_type !== params.get('work_order_type')
      )
        return false;
      return [
        work.reference,
        work.title,
        work.machine_name,
        work.assigned_to_name,
        work.assigned_to_username,
        work.assignee
      ].some((value) => value?.toLowerCase().includes(search));
    });
    rows = attention
      ? rows
          .map((work) => ({
            ...work,
            attention_reasons: attentionReasons(work)
          }))
          .sort(
            (left, right) =>
              (rank[right.priority] ?? 0) - (rank[left.priority] ?? 0) ||
              (left.due_date ?? '9999').localeCompare(
                right.due_date ?? '9999'
              ) ||
              right.id - left.id
          )
      : rows.map((work) => ({ ...work, attention_reasons: null }));
    const offset = Number(params.get('offset') ?? 0);
    const limit = Number(params.get('limit') ?? 25);
    return route.fulfill({
      json: {
        count: rows.length,
        results: rows.slice(offset, offset + limit),
        next: offset + limit < rows.length ? 'next' : null
      }
    });
  });
  await page.route('**/api/kanban/work-orders/*/overview/', (route) => {
    const id = Number(
      new URL(route.request().url()).pathname.split('/').at(-3)
    );
    return route.fulfill({ json: records.find((work) => work.id === id) });
  });
  return { requests, state };
}

export async function openWorkList(
  page: Page,
  options: {
    entry?: string;
    actor?: number;
    scheme?: string;
    denied?: boolean;
    // Wait for the first-use starting view (the scope control is disabled
    // until the persona answers). False to observe that wait.
    settled?: boolean;
  } = {}
) {
  const params = new URLSearchParams({
    surface: 'maintenance-shell',
    sprint: 's1',
    role: 'viewer',
    actor: String(options.actor ?? 11),
    scheme: options.scheme ?? 'light',
    entry: options.entry ?? '/maintenance/work-list/',
    work_access: options.denied ? 'denied' : 'reader'
  });
  await page.goto(`/playwright/s0-native.html?${params}`);
  await expect(
    page.getByRole('tab', { name: 'Work list', exact: true })
  ).toHaveAttribute('aria-selected', 'true');
  if (options.settled ?? true)
    await expect(
      page
        .locator(
          '[role="radiogroup"][aria-label="Work scope"][data-disabled="false"]'
        )
        .or(page.getByRole('alert'))
        .first()
    ).toBeAttached();
}

export async function savedWorkScope(page: Page, value: string, actorId = 11) {
  await page.addInitScript(
    ({ key, scope }) => localStorage.setItem(key, scope),
    { key: workListKey(actorId), scope: value }
  );
}
