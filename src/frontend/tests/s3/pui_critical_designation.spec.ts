import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
nativeBaseline();
for (const scheme of ['light', 'dark'] as const) {
  test(`O15 engineer registry designation is attributed and versioned (${scheme})`, async ({
    page
  }, info) => {
    const station = {
      pk: 18,
      name: 'Synthetic station',
      uuid: 'fixture-station',
      source_key: 'PH_1',
      source_entity_uuid: 'fixture-source',
      parent: null
    };
    await page.route('**/api/assets/registry/**', async (route) => {
      const path = new URL(route.request().url()).pathname;
      const json = path.endsWith('/options/')
        ? { clients: [], parts: [], templates: [], assignments: [] }
        : path.endsWith('/activate/')
          ? {
              activated: false,
              enabled: false,
              source: null,
              sources: [],
              approved: 0,
              bound: 0,
              unbound: 1,
              last_poll_at: null,
              last_error_code: ''
            }
          : path.endsWith('/18/')
            ? { equipment: station, children: [] }
            : path.endsWith('/registry/')
              ? { count: 1, results: [station] }
              : { count: 0, results: [] };
      await route.fulfill({ json });
    });
    const history: unknown[] = [];
    const sent: unknown[] = [];
    let first = true;
    await page.route(
      '**/api/assets/recorded/18/critical-signals/',
      async (route) => {
        if (route.request().method() === 'POST') {
          const body = route.request().postDataJSON();
          sent.push(body);
          if (!first) {
            expect(route.request().headers()['x-aimms-reconcile-only']).toBe(
              '1'
            );
            await route.fulfill({ json: history[0] });
            return;
          }
          first = false;
          const saved = {
            critical: body.fields.critical,
            version: 1,
            reason: body.fields.reason,
            recorded_by: 'Synthetic engineer',
            recorded_at: '2026-10-20T08:00:00Z'
          };
          history.unshift(saved);
          // The server committed; its response was lost. A reload can read the
          // recorded fact, while the exact original command still needs resolution.
          await route.abort('failed');
        } else
          await route.fulfill({
            json: {
              results: [
                {
                  binding_id: 201,
                  display_name: 'Critical flow',
                  history,
                  external_key: '/pd/P1/flow',
                  source_name: 'Synthetic historian',
                  unit: 'm3/h',
                  binding_signature: 'synthetic-binding-signature'
                },
                {
                  active: false,
                  binding_id: 202,
                  display_name: 'Other flow',
                  history: [],
                  external_key: '/pd/P2/flow',
                  source_name: 'Synthetic historian',
                  unit: 'm3/h',
                  binding_signature: 'other-binding-signature'
                }
              ]
            }
          });
      }
    );
    await page.goto(
      `/playwright/s0-native.html?surface=machine&entry=${encodeURIComponent('/machines/registry/?station=18')}&scheme=${scheme}`
    );
    await page
      .getByRole('tab', { name: 'Critical signals', exact: true })
      .click();
    const binding = page.getByTestId('critical-binding-201');
    const other = page.getByTestId('critical-binding-202');
    await expect(
      binding.getByText('Not designated', { exact: true })
    ).toBeVisible();
    await expect(other).toContainText('Inactive binding.');
    await expect(other).not.toContainText(
      'A retained critical designation remains'
    );
    await binding.getByLabel('Critical signal', { exact: true }).check();
    await binding
      .getByRole('textbox', { name: 'Designation reason' })
      .fill('Reviewed against critical signal requirement');
    await binding.getByRole('button', { name: 'Record designation' }).click();
    await expect(binding).toContainText('Original outcome remains unconfirmed');
    await page.reload();
    await page
      .getByRole('tab', { name: 'Critical signals', exact: true })
      .click();
    await expect(
      binding.getByRole('button', { name: 'Retry retained operation' })
    ).toBeVisible();
    await expect(
      other.getByRole('button', { name: 'Retry retained operation' })
    ).toHaveCount(0);
    await binding
      .getByRole('button', { name: 'Retry retained operation' })
      .click();
    await expect(
      page.getByText('Designated critical', { exact: true })
    ).toBeVisible();
    await expect(page.getByText(/Synthetic engineer.*Version 1/)).toBeVisible();
    await expect(
      other.getByText('Not designated', { exact: true })
    ).toBeVisible();
    expect(sent).toHaveLength(2);
    expect(history).toHaveLength(1);
    expect(sent[1]).toEqual(sent[0]);
    expect(sent[0]).toMatchObject({
      operation: 'designate',
      expected_version: 0,
      fields: { binding_id: 201, critical: true }
    });
    await page.screenshot({
      path: info.outputPath(`o15-registry-critical-${scheme}.png`),
      fullPage: true,
      animations: 'disabled'
    });
  });
}
