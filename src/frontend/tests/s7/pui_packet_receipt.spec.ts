import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import {
  containment,
  openCloseout,
  story,
  values
} from '../s6/closeoutFixtures';
import {
  openPacketCloseout,
  packetCloseoutWork,
  packetReceipt
} from './packetReceiptFixtures';

nativeBaseline();
for (const scheme of ['light', 'dark'] as const) {
  for (const [workEnabled, wizardEnabled] of [
    [false, false],
    [false, true],
    [true, false]
  ]) {
    test(`packet manual final action with Work=${workEnabled}, wizard=${wizardEnabled}, ${scheme}`, async ({
      page
    }, info) => {
      const { setOverview, commands, markCompleted } = await packetCloseoutWork(
        page,
        { captures: [] }
      );
      setOverview({
        canonical_commands_enabled: workEnabled,
        closeout_wizard_enabled: false
      });
      const wizardRequests: string[] = [];
      await page.route('**/api/tasks/work-orders/73/closeout/**', (route) => {
        wizardRequests.push(route.request().url());
        return route.fulfill({ status: 404, json: { detail: 'Not found.' } });
      });
      await page.route('**/api/repair/packets/901/close/', (route) => {
        if (route.request().method() === 'GET') return route.fallback();
        const body = route.request().postDataJSON();
        commands.push({
          path: '/api/repair/packets/901/close/',
          method: 'POST',
          body
        });
        markCompleted();
        return route.fulfill({ json: packetReceipt(body) });
      });
      await openPacketCloseout(page, scheme);
      await expect(page.getByTestId('closeout-wizard-disabled')).toBeVisible();
      await expect(
        page.getByRole('link', { name: 'Open Work Closeout', exact: true })
      ).toHaveCount(0);
      await expect(page.getByTestId('closeout-capture-disclosure')).toHaveCount(
        0
      );
      const update = page.getByRole('button', {
        name: 'Update maintenance record',
        exact: true
      });
      await expect(update).toBeDisabled();
      for (const [label, value] of [
        ['Action taken', values.action],
        ['Result', values.result],
        ['Verification summary', values.verification_summary]
      ]) {
        await page
          .getByRole('textbox', { name: label, exact: true })
          .fill(value);
      }
      await expect(update).toBeEnabled();
      await expect(
        page.getByText(
          'Report sources are not enabled. Preview and download are unavailable.',
          { exact: true }
        )
      ).toBeVisible();
      await update.scrollIntoViewIfNeeded();
      await containment(page);
      await page.screenshot({
        path: info.outputPath(
          `packet-flags-${workEnabled}-${wizardEnabled}-${scheme}.png`
        ),
        fullPage: true
      });
      expect(commands).toHaveLength(0);
      await update.click();
      await expect(page.getByTestId('prepared-closeout')).toHaveAttribute(
        'data-update-status',
        'confirmed'
      );
      expect(commands).toHaveLength(1);
      expect(commands[0].body).toMatchObject({
        work_order_id: 73,
        closeout: values
      });
      expect(commands[0].body.capture_id).toBeUndefined();
      await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
      expect(wizardRequests).toEqual([]);
      await expect(page.getByRole('dialog')).toHaveCount(0);
      await page.screenshot({
        path: info.outputPath(
          `packet-flags-confirmed-${workEnabled}-${wizardEnabled}-${scheme}.png`
        ),
        fullPage: true
      });
    });
  }

  test(`packet update is one pending command then a matching receipt, ${scheme}`, async ({
    page
  }, info) => {
    const { commands, markCompleted } = await packetCloseoutWork(page);
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route('**/api/repair/packets/901/close/', async (route) => {
      if (route.request().method() === 'GET') return route.fallback();
      const body = route.request().postDataJSON();
      commands.push({
        path: '/api/repair/packets/901/close/',
        body,
        method: 'POST'
      });
      await gate;
      markCompleted();
      return route.fulfill({ json: packetReceipt(body) });
    });
    await page.route('**/api/tasks/work-orders/73/events/', (route) =>
      route.fulfill({
        json: {
          results: [
            {
              id: 501,
              event_type: 'COMPLETED',
              idempotency_key: commands[0]?.body.idempotency_key,
              created_at: '2026-10-19T09:17:00Z'
            }
          ]
        }
      })
    );
    await openPacketCloseout(page, scheme);
    const account = page.getByTestId('closeout-account');
    await expect(account.getByTestId('work-record-update')).toHaveCount(1);
    await account
      .getByRole('button', { name: 'Update maintenance record', exact: true })
      .evaluate((button: HTMLButtonElement) => {
        button.click();
        button.click();
      });
    const pending = account.getByRole('button', {
      name: 'Updating…',
      exact: true
    });
    await expect(pending).toBeDisabled();
    await expect(pending.locator('.mantine-Button-label')).toHaveCSS(
      'opacity',
      '1'
    );
    expect(commands).toHaveLength(1);
    expect(commands[0].body).toMatchObject({
      expected_version: 7,
      work_order_id: 73,
      capture_id: 12,
      idempotency_key: expect.any(String),
      closeout: values
    });
    await pending.scrollIntoViewIfNeeded();
    await containment(page);
    await page.screenshot({
      path: info.outputPath(`packet-pending-${scheme}.png`)
    });
    release();
    await expect(page.getByTestId('prepared-closeout')).toHaveAttribute(
      'data-update-status',
      'confirmed'
    );
    await expect(account).toContainText('Record updated');
    await expect(account).toContainText('19 Oct 2026, 12:17');
    await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
    await expect(account).not.toContainText('Amendment 40');
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await account.getByTestId('work-record-update').scrollIntoViewIfNeeded();
    await page.screenshot({
      path: info.outputPath(`packet-confirmed-${scheme}.png`)
    });
    expect(commands).toHaveLength(1);
  });

  test(`packet uncertain reload replays the exact original intent, ${scheme}`, async ({
    page
  }, info) => {
    const { commands, markCompleted } = await packetCloseoutWork(page);
    await page.route('**/api/repair/packets/901/close/', (route) => {
      if (route.request().method() === 'GET') return route.fallback();
      const body = route.request().postDataJSON();
      commands.push({
        path: '/api/repair/packets/901/close/',
        body,
        method: 'POST'
      });
      markCompleted();
      return commands.length === 1
        ? route.abort('timedout')
        : route.fulfill({ json: packetReceipt(body) });
    });
    await openCloseout(page, scheme);
    await page
      .getByRole('button', { name: 'Update maintenance record', exact: true })
      .click();
    await expect(page.getByTestId('prepared-closeout')).toHaveAttribute(
      'data-update-status',
      'uncertain'
    );
    await page.reload();
    const account = page.getByTestId('closeout-account');
    await expect(page.getByTestId('prepared-closeout')).toHaveAttribute(
      'data-update-status',
      'uncertain'
    );
    expect(commands).toHaveLength(1);
    await expect(
      page.getByRole('button', {
        name: 'Update maintenance record',
        exact: true
      })
    ).toHaveCount(0);
    await account.getByTestId('work-record-update').scrollIntoViewIfNeeded();
    await containment(page);
    await page.screenshot({
      path: info.outputPath(`packet-uncertain-${scheme}.png`)
    });
    await page
      .getByRole('button', { name: 'Check original update', exact: true })
      .click();
    await expect(page.getByTestId('prepared-closeout')).toHaveAttribute(
      'data-update-status',
      'confirmed'
    );
    expect(commands).toHaveLength(2);
    expect(commands[1]).toEqual(commands[0]);
  });

  for (const code of ['STALE_VERSION', 'REPAIR_CLOSEOUT_INVALID']) {
    test(`packet ${code} retains its account, ${scheme}`, async ({
      page
    }, info) => {
      const { commands } = await packetCloseoutWork(page);
      await page.route('**/api/repair/packets/901/close/', (route) => {
        if (route.request().method() === 'GET') return route.fallback();
        commands.push({
          path: '/api/repair/packets/901/close/',
          body: route.request().postDataJSON(),
          method: 'POST'
        });
        return route.fulfill({ status: 400, json: { code } });
      });
      await openCloseout(page, scheme);
      await page
        .getByRole('button', { name: 'Update maintenance record', exact: true })
        .click();
      await expect(page.getByTestId('prepared-closeout')).toHaveAttribute(
        'data-update-status',
        code === 'STALE_VERSION' ? 'stale' : 'failed'
      );
      await expect(page.getByTestId('closeout-account')).toContainText(
        values.action
      );
      await expect(page.getByRole('dialog')).toHaveCount(0);
      await page.getByTestId('work-record-update').scrollIntoViewIfNeeded();
      await containment(page);
      await page.screenshot({
        path: info.outputPath(`packet-${code}-${scheme}.png`)
      });
      expect(commands).toHaveLength(1);
    });
  }

  test(`a receipt naming another packet remains uncertain, ${scheme}`, async ({
    page
  }) => {
    const { commands } = await packetCloseoutWork(page);
    await page.route('**/api/repair/packets/901/close/', (route) => {
      if (route.request().method() === 'GET') return route.fallback();
      const body = route.request().postDataJSON();
      commands.push({
        path: '/api/repair/packets/901/close/',
        body,
        method: 'POST'
      });
      return route.fulfill({
        json: {
          ...packetReceipt(body),
          metadata: { closeout_id: 301, packet_id: 902 }
        }
      });
    });
    await openCloseout(page, scheme);
    await page
      .getByRole('button', { name: 'Update maintenance record', exact: true })
      .click();
    await expect(page.getByTestId('prepared-closeout')).toHaveAttribute(
      'data-update-status',
      'uncertain'
    );
    await expect(page.getByText('Record updated', { exact: true })).toHaveCount(
      0
    );
    expect(commands).toHaveLength(1);
  });

  test(`packet readiness for another Work cannot unlock the account, ${scheme}`, async ({
    page
  }) => {
    const { commands } = await packetCloseoutWork(page);
    await page.route('**/api/repair/packets/901/close/', (route) =>
      route.fulfill({
        json: {
          ready: true,
          work_order_id: 86,
          packet_id: 901,
          blockers: [],
          warnings: []
        }
      })
    );
    await openCloseout(page, scheme);
    await expect(
      page.getByRole('button', {
        name: 'Update maintenance record',
        exact: true
      })
    ).toBeDisabled();
    await expect(page.getByTestId('work-record-update')).toContainText(
      'Readiness is unavailable'
    );
    expect(commands).toHaveLength(0);
  });

  test(`a refreshed packet owner mismatch invalidates earlier ready status, ${scheme}`, async ({
    page
  }) => {
    const { commands } = await packetCloseoutWork(page);
    let targetWork = 73;
    await page.route('**/api/repair/packets/901/close/', (route) =>
      route.fulfill({
        json: {
          ready: true,
          work_order_id: targetWork,
          packet_id: 901,
          blockers: [],
          warnings: []
        }
      })
    );
    await page.route(
      '**/api/tasks/work-orders/73/closeout/readings/',
      (route) =>
        route.request().method() === 'GET'
          ? route.fallback()
          : route.fulfill({ status: 201, json: { id: 99 } })
    );
    await openCloseout(page, scheme);
    const action = page.getByRole('button', {
      name: 'Update maintenance record',
      exact: true
    });
    await expect(action).toBeEnabled();
    targetWork = 86;
    await page
      .getByRole('tab', { name: 'Verify & Return', exact: true })
      .click();
    await page
      .getByRole('textbox', { name: 'Label', exact: true })
      .fill('Synthetic reading');
    await page.getByRole('textbox', { name: 'Value', exact: true }).fill('1');
    await page
      .getByRole('button', { name: 'Add reading', exact: true })
      .click();
    await page.getByRole('tab', { name: 'Closeout', exact: true }).click();
    await expect(page.getByTestId('work-record-update')).toContainText(
      'Readiness is unavailable'
    );
    await expect(action).toBeDisabled();
    expect(commands).toHaveLength(0);
  });

  test(`packet dictation retains the existing final-action lock, ${scheme}`, async ({
    page
  }, info) => {
    const { commands } = await packetCloseoutWork(page, { captures: [] });
    await openCloseout(page, scheme);
    await page
      .getByRole('button', {
        name: 'What work was actually done, and why?',
        exact: true
      })
      .click();
    for (const [name, value] of Object.entries({
      'Action taken': values.action,
      Result: values.result,
      'Verification summary': values.verification_summary
    }))
      await page.getByRole('textbox', { name, exact: true }).fill(value);
    await expect(
      page.getByRole('button', {
        name: 'Update maintenance record',
        exact: true
      })
    ).toBeEnabled();
    // Completed manual fields hide the duplicate question card. The native
    // capture editor remains available, and dictation must still lock update.
    await expect(page.getByTestId('closeout-questions')).toHaveCount(0);
    await page
      .getByTestId('closeout-capture-disclosure')
      .getByText('Narrative', { exact: true })
      .click();
    await page
      .getByRole('button', {
        name: 'Dictate',
        exact: true
      })
      .click();
    await expect(
      page.getByRole('dialog', { name: 'Dictate closeout narrative' })
    ).toBeVisible();
    const update = page.getByRole('button', {
      name: 'Update maintenance record',
      exact: true,
      includeHidden: true
    });
    await expect(update).toBeDisabled();
    await page.screenshot({
      path: info.outputPath(`packet-voice-locked-${scheme}.png`),
      animations: 'disabled'
    });
    await page
      .getByRole('button', { name: 'Close dictation', exact: true })
      .click();
    await expect(update).toBeEnabled();
    expect(commands).toHaveLength(0);
  });

  test(`packet capture lock preserves the original story, ${scheme}`, async ({
    page
  }, info) => {
    const { commands, reads } = await packetCloseoutWork(page, {
      captures: [{ ...story, status: 'open' }],
      ready: false
    });
    await openCloseout(page, scheme);
    await expect(
      page.getByRole('button', {
        name: 'Update maintenance record',
        exact: true
      })
    ).toBeDisabled();
    await expect(
      page.getByRole('textbox', {
        name: 'Saved capture narrative',
        exact: true
      })
    ).toHaveValue(story.narrative);
    await expect(page.getByTestId('closeout-capture-lock')).toBeVisible();
    expect(reads).toContain('/api/repair/packets/901/close/');
    expect(reads.some((path) => path.endsWith('/readiness/'))).toBe(false);
    await page
      .getByTestId('closeout-capture-disclosure')
      .scrollIntoViewIfNeeded();
    await containment(page);
    await page.screenshot({
      path: info.outputPath(`packet-capture-locked-${scheme}.png`)
    });
    expect(commands).toHaveLength(0);
  });
}
