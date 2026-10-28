import type { Page } from '@playwright/test';

import type { BoardCard } from '../../lib/types/Tasks';
import type { WorkOrderOverview } from '../../lib/types/WorkOrderOverview';

const recordedAt = '2026-10-18T12:00:00Z';

export function workOrder(id = 73): WorkOrderOverview {
  return {
    id,
    reference: `WO-${id}`,
    title: `Synthetic job ${id}`,
    description: 'Synthetic native workflow baseline',
    status: 'backlog',
    priority: 'medium',
    due_date: null,
    assignee: '',
    machine: 18,
    machine_name: 'Synthetic pump',
    machine_location: 'Synthetic station',
    assigned_to: null,
    assigned_to_username: null,
    assigned_to_name: null,
    scheduled_start: null,
    scheduled_end: null,
    estimated_minutes: null,
    work_order_type: 'corrective',
    lifecycle_status: 'in_progress',
    lifecycle_version: 7,
    actual_started_at: recordedAt,
    actual_completed_at: null,
    tags: [],
    company: '',
    company_contact_name: '',
    company_contact_phone: '',
    job_number: '',
    service_quote: '',
    is_active: true,
    created_at: recordedAt,
    updated_at: recordedAt,
    parts: [],
    cards: [],
    dependencies: [],
    events: [],
    repair_packet: null,
    source_alert: null,
    maintenance_record: null,
    structured_closeout: null,
    canonical_commands_enabled: false
  };
}

export const boardCards: BoardCard[] = [
  { id: 501, work_order: 73, title: 'Synthetic inspection', card_kind: 'work' },
  {
    id: 502,
    work_order: 73,
    title: 'Synthetic sourcing',
    card_kind: 'sourcing'
  },
  { id: 601, work_order: 86, title: 'Synthetic second job', card_kind: 'work' }
].map((card, index) => ({
  ...card,
  status: 'backlog',
  board_order: index,
  description: '',
  assigned_to: null,
  assignee: '',
  scheduled_start: null,
  scheduled_end: null,
  estimated_minutes: null,
  is_active: true,
  created_at: recordedAt,
  updated_at: recordedAt,
  work_order_reference: `WO-${card.work_order}`,
  work_order_title: `Synthetic job ${card.work_order}`,
  priority: 'medium',
  lifecycle_status: 'in_progress',
  lifecycle_version: 7,
  machine: 18,
  machine_name: 'Synthetic pump',
  tags: [],
  effective_assignee: null,
  effective_start: null,
  effective_end: null
}));

export function repairPacket(ownsWork = true) {
  return {
    pk: 901,
    reference: 'RP-901',
    status: 'executing',
    status_label: 'Executing',
    machine: 18,
    machine_name: 'Synthetic pump',
    criticality: 'high',
    generation_status: 'succeeded',
    symptom: 'Synthetic observation',
    fault_summary: 'Synthetic fault',
    production_impact: '',
    diagnosis: {},
    findings: [],
    approved_scope: null,
    gates: [],
    parts: [],
    approvals: [],
    events: [],
    closeout: {},
    work_order: ownsWork ? 73 : null,
    work_order_reference: ownsWork ? 'WO-73' : null,
    work_order_lifecycle_version: ownsWork ? 7 : null
  };
}

export async function mockWork(page: Page, overview = workOrder()) {
  const reads: URL[] = [];
  await page.route('**/api/tasks/work-orders/*/repair-scope-draft/', (route) =>
    route.fulfill({
      json: {
        work_order_id: overview.id,
        machine_id: overview.machine,
        repair_packet_id: overview.repair_packet?.id ?? null,
        lifecycle_version: overview.lifecycle_version,
        draft: null
      }
    })
  );
  await page.route('**/api/kanban/work-orders/', (route) =>
    route.fulfill({ json: [workOrder(), workOrder(86)] })
  );
  await page.route('**/api/kanban/cards/', (route) =>
    route.fulfill({ json: boardCards })
  );
  await page.route('**/api/kanban/columns/', (route) =>
    route.fulfill({ json: [] })
  );
  await page.route('**/api/assets/machines/**', (route) =>
    route.fulfill({ json: [{ pk: 18, name: 'Synthetic pump', location: '' }] })
  );
  await page.route('**/api/kanban/work-orders/*/overview/', (route) => {
    const url = new URL(route.request().url());
    reads.push(url);
    const id = Number(url.pathname.split('/').at(-3));
    const data = id === overview.id ? overview : workOrder(id);
    return route.fulfill({
      json: {
        ...data,
        cards: boardCards.filter((card) => card.work_order === id)
      }
    });
  });
  // R6 P22: a server without the material readout; the Parts panel then
  // shows the job's native requirement table.
  await page.route('**/api/tasks/work-orders/*/materials/', (route) =>
    route.fulfill({ status: 404, json: { detail: 'Not found.' } })
  );
  await page.route('**/api/attachment/**', (route) =>
    route.fulfill({
      json:
        route.request().method() === 'OPTIONS'
          ? { actions: {} }
          : { count: 0, results: [] }
    })
  );
  return reads;
}
