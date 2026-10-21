import { type Page, expect } from '@playwright/test';
import { repairPacket } from '../s0/workFixtures';
import { accepted, closeoutWork, values } from '../s6/closeoutFixtures';

export async function packetCloseoutWork(
  page: Page,
  options: Parameters<typeof closeoutWork>[1] = {}
) {
  const fixture = await closeoutWork(page, options);
  const packet = repairPacket();
  const packetOverview = {
    id: packet.pk,
    reference: packet.reference,
    status: packet.status,
    criticality: packet.criticality,
    fault_summary: packet.fault_summary,
    symptom: packet.symptom,
    production_impact: '',
    generation_status: 'succeeded',
    diagnosis: {},
    diagnosis_is_preliminary: false,
    diagnosis_status: null,
    findings: [],
    approved_scope: null,
    gates: []
  };
  fixture.setOverview({
    assigned_to: 11,
    assigned_to_username: 'synthetic-maintainer',
    assigned_to_name: 'Synthetic Maintainer',
    repair_packet: packetOverview
  });
  // The failed attempt is retained history; only the later verified reading is
  // required now. This agrees with the synthetic packet readiness response.
  await page.route('**/api/tasks/work-orders/73/closeout/readings/', (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({
          json: [
            {
              id: 51,
              label: 'Initial test',
              phase: 'after',
              raw_text: '6',
              value: '6',
              unit: 'mm/s',
              required: false,
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
        })
      : route.fallback()
  );
  await page.route('**/api/repair/packets/901/', (route) =>
    route.fulfill({ json: packet })
  );
  await page.route(
    '**/api/tasks/work-orders/73/closeout/amendments/',
    (route) => route.fulfill({ json: [] })
  );
  await page.route('**/api/repair/packets/901/close/', (route) => {
    if (route.request().method() !== 'GET') {
      fixture.commands.push({
        path: new URL(route.request().url()).pathname,
        method: route.request().method(),
        body: route.request().postDataJSON()
      });
      return route.fulfill({
        status: 400,
        json: { code: 'UNEXPECTED_COMMAND' }
      });
    }
    fixture.reads.push('/api/repair/packets/901/close/');
    return route.fulfill({
      json: {
        work_order_id: 73,
        packet_id: 901,
        ready: options.ready ?? true,
        blockers:
          options.ready === false
            ? [
                {
                  code: 'CLOSEOUT_REQUIRED',
                  message: 'Finish the packet capture review',
                  blocking: true
                }
              ]
            : [],
        warnings: []
      }
    });
  });
  return {
    ...fixture,
    markCompleted: () => {
      packet.status = 'closed';
      packet.status_label = 'Closed';
      packet.work_order_lifecycle_version = 8;
      fixture.setOverview({
        repair_packet: { ...packetOverview, status: 'closed' },
        status: 'done',
        lifecycle_status: 'completed',
        lifecycle_version: 8,
        actual_completed_at: '2026-10-19T09:17:00Z',
        structured_closeout: {
          ...accepted!,
          ...values,
          downtime_minutes: null,
          follow_up: '',
          follow_up_required: false,
          amended: false,
          amendment_count: 0,
          completed_at: '2026-10-19T09:17:00Z',
          completed_by: 11,
          completed_by_name: 'Synthetic Maintainer',
          original: { ...values, downtime_minutes: null }
        }
      });
    }
  };
}
export async function openPacketCloseout(
  page: Page,
  scheme: 'light' | 'dark' = 'light',
  onLanding?: () => Promise<void>
) {
  await page.goto(
    `/playwright/s0-native.html?surface=packet&scheme=${scheme}&entry=/repair/packets/901/`
  );
  await page.getByRole('link', { name: 'Open Closeout', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await onLanding?.();
  await page
    .getByRole('link', { name: 'Open Work Closeout', exact: true })
    .click();
  await expect(page.getByTestId('prepared-closeout')).toBeVisible();
  await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
}
export function packetReceipt(body: any) {
  return {
    work_order_id: 73,
    command: 'complete_work_order',
    event_id: 501,
    idempotency_key: body.idempotency_key,
    lifecycle_status: 'completed',
    lifecycle_version: body.expected_version + 1,
    metadata: { closeout_id: 301, packet_id: 901 }
  };
}
