import type { Locator, Page } from '@playwright/test';
import type {
  AuthorityKind,
  AuthorityRecord,
  OperationalAuthority
} from '../../lib/types/OperationalAuthority';
import type {
  AuthorityIntent,
  AuthorityReceipt
} from '../../src/pages/tasks/workorders/execution/authorityCommand';
import { revisionWork } from './scopeRevisionFixtures';
import { mockExecutionWork } from './workExecutionFixtures';

/** Clip the full document at scroll zero, so a tall region is not crossed by sticky chrome. */
export async function captureAuthorityRegion(
  page: Page,
  region: Locator,
  path: string
) {
  await page.evaluate(() => window.scrollTo(0, 0));
  const box = await region.boundingBox();
  if (!box) throw new Error('Authority capture region is not rendered.');
  await page.screenshot({
    path,
    fullPage: true,
    clip: box,
    animations: 'disabled'
  });
}

export function authorityRecord(
  kind: AuthorityKind = 'permit',
  extra: Partial<AuthorityRecord> = {}
): AuthorityRecord {
  return {
    id: 801,
    work_order_id: 73,
    version: 7,
    kind,
    outcome:
      kind === 'permit'
        ? 'active'
        : kind === 'test_acceptance'
          ? 'accepted'
          : kind === 'operating_permission'
            ? 'withheld'
            : kind === 'observed_return'
              ? 'observed_stopped'
              : 'operator_recorded',
    statement: `Synthetic recorded ${kind}`,
    source_reference: 'Synthetic site document / revision 4',
    recorded_by: 'Synthetic Recorder',
    recorded_by_id: 999,
    recorded_at: '2026-10-19T08:00:00Z',
    observed_at: '2026-10-19T07:30:00Z',
    effective_from: '2026-10-19T07:00:00Z',
    effective_until: '2099-10-20T08:00:00Z',
    effective_state: 'within_window',
    basis_state: 'current',
    current: true,
    supersedes: null,
    gate_id:
      kind === 'permit' || kind === 'isolation' || kind === 'access_window'
        ? 501
        : null,
    verified_by: null,
    verified_by_id: null,
    gate_name: ['isolation', 'permit', 'access_window'].includes(kind)
      ? 'Synthetic existing isolation / permit gate'
      : null,
    target_name: ['isolation', 'permit', 'access_window'].includes(kind)
      ? null
      : 'Synthetic pump',
    verified_at: null,
    target_id: kind === 'permit' ? null : 18,
    details: {},
    basis: {},
    ...extra
  };
}

export function authorityProjection(writable = false): OperationalAuthority {
  return {
    rule: 'operational_authority.v1',
    work_order_id: 73,
    work_version: 7,
    context_token: 'a'.repeat(64),
    commands: {
      test_acceptance: writable,
      operating_permission: writable,
      observed_return: writable,
      service_state: writable
    },
    gates: [
      {
        id: 501,
        name: 'Synthetic existing isolation / permit gate',
        requires_second_person: true,
        can_record: writable
      }
    ],
    targets: [
      { id: 18, name: 'Synthetic pump' },
      { id: 19, name: 'Synthetic station' }
    ],
    physical: [],
    operational: [],
    telemetry: []
  };
}

/** Synthetic command server, retaining the exact durable result on an interrupted response. */
export async function mockAuthority(page: Page, value = authorityProjection()) {
  const overview = {
    ...revisionWork(),
    operational_authority: value
  };
  if (overview.repair_packet)
    overview.repair_packet.gates = value.gates.map((gate) => ({
      id: gate.id,
      name: gate.name,
      gate_type: 'isolation',
      status: 'confirmed',
      is_satisfied: false,
      unsatisfied_reason: value.physical.some(
        (row) =>
          row.current &&
          row.effective_until &&
          Date.parse(row.effective_until) < Date.now()
      )
        ? 'recorded authority outside its effective window'
        : 'second-person verification missing',
      is_blocking: true,
      is_mandatory: true,
      requires_photo: false,
      requires_second_person: gate.requires_second_person
    }));
  await mockExecutionWork(page, overview);
  const commands: AuthorityIntent['payload'][] = [];
  const results = new Map<string, AuthorityReceipt>();
  let uncertain = false;
  let refusal: number | undefined;
  await page.route(
    '**/api/kanban/work-orders/73/operational-authority/',
    async (route) => {
      const payload = route
        .request()
        .postDataJSON() as AuthorityIntent['payload'];
      commands.push(payload);
      if (refusal)
        return route.fulfill({
          status: refusal,
          json: {
            code: 'STALE_VERSION',
            detail: 'Synthetic authority refused; review latest Work'
          }
        });
      let receipt = results.get(payload.idempotency_key);
      if (!receipt) {
        if (payload.expected_context !== value.context_token)
          return route.fulfill({
            status: 409,
            json: {
              code: 'STALE_VERSION',
              detail:
                'Synthetic scope or test context changed; review latest Work'
            }
          });
        const fields = payload.fields;
        if (
          fields.kind === 'service_state' &&
          ['confirmed', 'corrected'].includes(String(fields.outcome))
        ) {
          const reading = value.telemetry.find(
            (row) => row.binding_id === fields.binding_id
          );
          if (
            !reading ||
            fields.telemetry_token !== reading.reading_token ||
            fields.telemetry_observed_at !== reading.observed_at
          )
            return route.fulfill({
              status: 409,
              json: {
                code: 'STALE_VERSION',
                detail: 'Synthetic selected telemetry observation changed'
              }
            });
        }
        const prior = payload.supersedes
          ? value.physical.find((row) => row.id === payload.supersedes)
          : undefined;
        const recordId =
          Math.max(
            801,
            ...value.physical.map((row) => row.id),
            ...value.operational.map((row) => row.id)
          ) + 1;
        const row =
          payload.operation === 'verify' && prior
            ? {
                ...prior,
                id: recordId,
                version: payload.expected_version + 1,
                supersedes: prior.id,
                verified_by: 'Synthetic Second Person',
                verified_by_id: 11,
                verified_at: new Date().toISOString()
              }
            : authorityRecord(fields.kind as AuthorityKind, {
                id: recordId,
                version: payload.expected_version + 1,
                statement: String(fields.statement),
                outcome: String(fields.outcome),
                source_reference: String(fields.source_reference),
                recorded_by_id: 11,
                recorded_at: new Date().toISOString(),
                observed_at: String(fields.observed_at),
                effective_from: String(fields.effective_from),
                effective_until: fields.effective_until
                  ? String(fields.effective_until)
                  : null,
                supersedes: payload.supersedes ?? null,
                gate_id: fields.gate_id ? Number(fields.gate_id) : null,
                target_id: fields.target_id ? Number(fields.target_id) : null,
                details: {
                  duty: fields.duty ? String(fields.duty) : null,
                  standby: fields.standby ? String(fields.standby) : null,
                  redundancy: fields.redundancy
                    ? String(fields.redundancy)
                    : null,
                  restrictions: fields.restrictions
                    ? String(fields.restrictions)
                    : null
                }
              });
        if (prior) prior.current = false;
        if (fields.binding_id)
          row.basis = {
            telemetry: value.telemetry.find(
              (reading) => reading.binding_id === fields.binding_id
            )
          };
        if (row.gate_id) value.physical.unshift(row);
        else value.operational.unshift(row);
        value.work_version = row.version;
        overview.lifecycle_version = row.version;
        receipt = {
          work_order_id: 73,
          work_version: row.version,
          idempotency_key: payload.idempotency_key,
          expected_context: payload.expected_context,
          record: row
        };
        results.set(payload.idempotency_key, receipt);
      }
      if (uncertain) {
        uncertain = false;
        return route.abort('failed');
      }
      return route.fulfill({ json: receipt });
    }
  );
  return {
    overview,
    value,
    commands,
    interrupt: () => {
      uncertain = true;
    },
    refuse: (status?: number) => {
      refusal = status;
    }
  };
}

export async function selectOption(page: Page, label: string, text: string) {
  await page.getByRole('combobox', { name: label, exact: true }).click();
  await page.getByRole('option', { name: text, exact: true }).click();
}

export async function fillAuthority(
  page: Page,
  kind = 'Permit',
  outcome = 'active'
) {
  await selectOption(page, 'Record kind', kind);
  await selectOption(page, 'Recorded outcome', outcome);
  await page
    .getByRole('textbox', { name: 'Recorded fact and conditions', exact: true })
    .fill('Synthetic permit P-204, restricted scope');
  await page
    .getByRole('textbox', {
      name: 'Source document / record reference',
      exact: true
    })
    .fill('Synthetic document / P-204 / revision 2');
  await page
    .getByRole('textbox', { name: 'Effective until / expiry', exact: true })
    .fill('2099-10-20T18:00');
}
