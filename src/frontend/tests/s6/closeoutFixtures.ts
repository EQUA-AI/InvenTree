import { type Page, expect } from '@playwright/test';
import type {
  CloseoutCapture,
  CloseoutProposal
} from '../../lib/types/Closeout';
import type { WorkOrderOverview } from '../../lib/types/WorkOrderOverview';
import { workOrder } from '../s0/workFixtures';
import { mockWorkList } from '../s1/workListFixtures';

export const values = {
  action: 'Replaced seal from recorded kit',
  result: 'Flow observed at the recorded test',
  verification_summary:
    'Retained test result; operating authority not supplied',
  cause: ''
};
export const story: CloseoutCapture = {
  id: 12,
  status: 'reviewed',
  source_type: 'voice',
  revision: 2,
  decision_version: 'a'.repeat(64),
  narrative:
    'Access was limited. The initial adjustment did not resolve the leak. Replaced the seal and observed flow.',
  transcript_reference: 'voice:capture:12:revision:2:fixture-hash',
  created_by: 3,
  created_by_name: 'Synthetic Technician',
  created_at: '2026-10-19T08:00:00Z',
  revisions: [
    {
      id: 120,
      revision: 1,
      narrative: 'Original transcript: adjusted seal.',
      source_content_hash: 'a'.repeat(64),
      work_order_version: 7,
      supersedes: null,
      created_by: 3,
      created_by_name: 'Synthetic Technician',
      created_at: '2026-10-19T08:00:00Z'
    },
    {
      id: 121,
      revision: 2,
      narrative:
        'Access was limited. The initial adjustment did not resolve the leak. Replaced the seal and observed flow.',
      source_content_hash: 'b'.repeat(64),
      work_order_version: 7,
      supersedes: 120,
      created_by: 3,
      created_by_name: 'Synthetic Technician',
      created_at: '2026-10-19T08:05:00Z'
    }
  ]
};
export const proposal: CloseoutProposal = {
  id: 20,
  decision_version: 'a'.repeat(64),
  extractor: 'fixture-v1',
  fields: Object.fromEntries(
    Object.entries(values).map(([key, value]) => [
      key,
      { value, spans: [], confidence: 0.8, warnings: [] }
    ])
  ),
  decisions: Object.entries(values)
    .filter(([, value]) => value)
    .map(([field_path, final_value]) => ({
      field_path,
      final_value,
      decision: 'accepted',
      decided_by: 3,
      decided_by_name: 'Synthetic Technician',
      decided_at: '2026-10-19T08:10:00Z'
    })),
  part_candidates: [],
  warnings: []
};
export const accepted: WorkOrderOverview['structured_closeout'] = {
  id: 301,
  ...values,
  result: 'Corrected recorded result',
  downtime_minutes: null,
  follow_up_required: true,
  follow_up: 'Operations to review independent return authority',
  completed_at: '2026-10-19T09:00:00Z',
  verified_at: null,
  amended: true,
  amendment_count: 1,
  original: { ...values, downtime_minutes: null },
  content_hash: 'c'.repeat(64),
  completed_by: 3,
  completed_by_name: 'Synthetic Technician'
};
export type Command = { path: string; body: any; method: string };
export async function closeoutWork(
  page: Page,
  options: {
    captures?: CloseoutCapture[];
    completed?: boolean;
    ready?: boolean;
    sourceError?: boolean;
  } = {}
) {
  // These two optional stylesheet fonts stay unavailable without external traffic.
  for (const extension of ['woff2', 'woff']) {
    await page.route(
      `https://cdn.svar.dev/fonts/roboto/regular.${extension}`,
      (route) => route.fulfill({ status: 503, body: '' })
    );
  }
  await mockWorkList(page);
  const commands: Command[] = [];
  const reads: string[] = [];
  let overview = {
    ...workOrder(),
    canonical_commands_enabled: true,
    lifecycle_status: options.completed ? 'completed' : 'verifying',
    lifecycle_version: options.completed ? 8 : 7,
    actual_completed_at: options.completed ? accepted!.completed_at : null,
    structured_closeout: options.completed ? accepted : null
  };
  await page.route('**/api/kanban/work-orders/73/overview/', (route) =>
    route.fulfill({ json: overview })
  );
  await page.route('**/api/tasks/work-orders/73/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() !== 'GET') {
      commands.push({
        path,
        body: request.postDataJSON(),
        method: request.method()
      });
      return route.fulfill({
        status: 400,
        json: { code: 'UNEXPECTED_COMMAND' }
      });
    }
    reads.push(path);
    if (path.endsWith('/closeout/captures/'))
      return route.fulfill({
        status: options.sourceError ? 500 : 200,
        json: options.sourceError
          ? { detail: 'Synthetic unreadable source' }
          : (options.captures ?? [
              overview.lifecycle_status === 'completed' &&
              overview.structured_closeout
                ? {
                    ...story,
                    status: 'consumed',
                    completed_closeout: overview.structured_closeout.id
                  }
                : story
            ])
      });
    if (path.endsWith('/proposal/')) return route.fulfill({ json: proposal });
    if (path.endsWith('/readiness/'))
      return route.fulfill({
        json: {
          ready: options.ready ?? true,
          blockers:
            options.ready === false
              ? [
                  {
                    code: 'CAPTURE_ACTIVE',
                    message: 'Finish the active capture review',
                    blocking: true
                  }
                ]
              : [],
          warnings: []
        }
      });
    if (path.endsWith('/readings/'))
      return route.fulfill({
        json: [
          {
            id: 51,
            label: 'Initial test',
            phase: 'after',
            raw_text: '6',
            value: '6',
            unit: 'mm/s',
            required: true,
            verification_state: 'failed',
            warnings: []
          },
          {
            id: 52,
            label: 'Repeat test',
            phase: 'after',
            raw_text: '0',
            value: '0',
            unit: 'mm/s',
            required: true,
            verification_state: 'verified',
            warnings: []
          }
        ]
      });
    if (path.endsWith('/part-usage/')) return route.fulfill({ json: [] });
    if (path.endsWith('/effects/'))
      return route.fulfill({
        json: [
          {
            id: 1,
            effect_type: 'notification',
            status: 'pending',
            attempts: 0,
            result_reference: ''
          }
        ]
      });
    if (path.endsWith('/amendments/'))
      return route.fulfill({
        json: [
          {
            id: 40,
            closeout: 301,
            status: 'applied',
            reason: 'Corrected the recorded observation',
            changes: { result: { from: values.result, to: accepted!.result } },
            requested_by: 3,
            requested_by_name: 'Synthetic Technician',
            decided_by: 4,
            decided_by_name: 'Synthetic Supervisor',
            created_at: '2026-10-19T10:00:00Z',
            applied_at: '2026-10-19T10:05:00Z',
            base_content_hash: 'c'.repeat(64),
            effective_snapshot: { closeout: { result: accepted!.result } }
          }
        ]
      });
    if (path.endsWith('/events/'))
      return route.fulfill({ json: { results: [] } });
    throw new Error(`Missing S6 fixture: ${path}`);
  });
  return {
    commands,
    reads,
    setOverview: (value: Partial<WorkOrderOverview>) => {
      overview = { ...overview, ...value } as typeof overview;
    }
  };
}
export async function openCloseout(
  page: Page,
  scheme: 'light' | 'dark' = 'light'
) {
  const params = new URLSearchParams({
    surface: 'maintenance-shell',
    sprint: 's1',
    work_access: 'reader',
    scheme,
    entry: '/maintenance/work-orders/73/?work_panel=closeout'
  });
  await page.goto(`/playwright/s0-native.html?${params}`);
  await expect(page.getByTestId('prepared-closeout')).toBeVisible();
  await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
}
export async function containment(page: Page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth
    )
  ).toBe(true);
}
