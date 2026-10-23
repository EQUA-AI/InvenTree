import { type Locator, type Page, expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import { continuityWork } from '../s4/continuityFixtures';
import { openExecutionPanel } from '../s4/workExecutionFixtures';
import { closeoutWork, containment } from '../s6/closeoutFixtures';
import { phoneAssistant } from './phoneCompanionFixtures';

nativeBaseline();

test.beforeEach(async ({ page }) => {
  // The native user-store import also loads the Gantt library stylesheet.
  // Linux requests its optional Roboto font; keep that known CDN read offline.
  await page.route(
    /^https:\/\/cdn\.svar\.dev\/fonts\/roboto\/regular\.woff2?$/,
    (route) => route.fulfill({ status: 503, body: '' })
  );
});

const panels = [
  'Work',
  'Diagnose',
  'Plan & Safety',
  'Parts',
  'Verify & Return',
  'Closeout',
  'History'
];

async function phoneDock(page: Page) {
  const dock = page.getByTestId('work-panel-navigation');
  await expect(dock).toBeInViewport({ ratio: 1 });
  await expect(page.getByTestId('work-phone-boundary')).toContainText(
    'Plan & Safety · —'
  );
  for (const name of panels) {
    const tab = dock.getByRole('tab', { name, exact: true });
    await expect(tab).toBeInViewport({ ratio: 1 });
    const bounds = await tab.boundingBox();
    expect(bounds!.height).toBeGreaterThanOrEqual(44);
    expect(bounds!.width).toBeGreaterThanOrEqual(44);
  }
  await containment(page);
}

async function showWorkIdentity(page: Page) {
  // Scroll the test-only route/debug controls out of the viewport. Keep the
  // actual production header and Work identity in the captured phone screen.
  await page.getByRole('heading', { name: /^WO-73: / }).evaluate((node) => {
    window.scrollBy(0, node.getBoundingClientRect().top - 100);
  });
}

async function uncovered(page: Page, control: Locator) {
  await control.scrollIntoViewIfNeeded();
  await expect(control).toBeInViewport({ ratio: 1 });
  const bounds = await control.boundingBox();
  const dock = await page.getByTestId('work-panel-navigation').boundingBox();
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(dock!.y);
  // A real pointer must reach the control, not the fixed navigation over it.
  await control.click({ trial: true });
}

async function completionAccess(page: Page, allowed = true) {
  // Native CRUD roles do not grant the separate completion permission (P32).
  await page.evaluate(async (allowed) => {
    const modulePath = '/src/states/UserState.tsx';
    const { useUserState } = await import(modulePath);
    const state = useUserState.getState();
    // Set the fixture actor's grants like s0-native's initialization. Calling
    // setUser would also reset the unrelated chat session and move focus.
    useUserState.setState({
      user: {
        ...state.user,
        permissions: {
          ...state.user?.permissions,
          workorder: allowed ? ['view', 'complete'] : ['view']
        }
      }
    });
  }, allowed);
}

for (const width of [390, 360]) {
  for (const scheme of ['light', 'dark'] as const) {
    test(`all seven panels and recorded boundaries remain reachable at ${width}px, ${scheme}`, async ({
      page
    }, info) => {
      await page.setViewportSize({ width, height: 844 });
      const fixture = await closeoutWork(page);
      const overview = continuityWork();
      fixture.setOverview(overview);
      await page.route('**/api/tasks/work-orders/73/materials/', (route) =>
        route.fulfill({ status: 404, json: { detail: 'Not found.' } })
      );
      await openExecutionPanel(page, 'work', scheme);
      await showWorkIdentity(page);
      await phoneDock(page);
      const brief = page.getByTestId('work-brief');
      await expect(
        brief.getByRole('heading', { name: 'Current work outcome' })
      ).toBeVisible();
      await expect(brief).toContainText(
        overview.investigation!.requests.items![0].next_check
      );
      const recordDetails = await page
        .getByRole('button', { name: 'Record details', exact: true })
        .boundingBox();
      const briefBounds = await brief.boundingBox();
      expect(recordDetails!.y).toBeGreaterThan(briefBounds!.y);
      await page.screenshot({
        path: info.outputPath(`phone-work-${width}-${scheme}.png`)
      });

      for (const name of panels.slice(1)) {
        await page.getByRole('tab', { name, exact: true }).click();
        await expect(
          page.getByRole('tab', { name, exact: true })
        ).toHaveAttribute('aria-selected', 'true');
        await expect(
          page.getByRole('tabpanel', { name, exact: true })
        ).toBeVisible();
        await phoneDock(page);
        await page.screenshot({
          path: info.outputPath(
            `phone-${name.split(' ')[0].toLowerCase()}-${width}-${scheme}.png`
          )
        });
      }
      await page.getByTestId('work-phone-boundary').click();
      await expect(
        page.getByRole('tab', { name: 'Plan & Safety', exact: true })
      ).toHaveAttribute('aria-selected', 'true');
      await expect(
        page
          .getByTestId('prepared-package')
          .getByRole('button', { name: /no physical-authority provider/ })
      ).toHaveText('—');
      await page.getByRole('tab', { name: 'Work', exact: true }).click();
      await expect(page.getByTestId('work-continuity')).toContainText(
        'Synthetic equipment access unavailable'
      );
      await expect(
        page.getByTestId('work-continuity').getByRole('button', {
          name: /Information: Assignment, a read receipt.*do not accept responsibility/
        })
      ).toBeVisible();
      await page.getByRole('button', { name: /^Attachments \(/ }).click();
      await expect(
        page.getByRole('heading', { name: 'Work attachments', exact: true })
      ).toBeVisible();
      await page
        .getByRole('button', { name: 'Close attachments', exact: true })
        .click();
      await expect(
        page.getByRole('button', { name: /^Attachments \(/ })
      ).toBeFocused();
      await phoneDock(page);
      expect(fixture.commands).toHaveLength(0);
    });
  }
}

// Review regressions use the actual native controls and page focus sequence.
for (const width of [360, 390]) {
  const scheme = width === 360 ? 'dark' : 'light';

  test(`phone downtime steppers stay within their input and unknown stays blank at ${width}px`, async ({
    page
  }, info) => {
    await page.setViewportSize({ width, height: 844 });
    const fixture = await closeoutWork(page);
    await openExecutionPanel(page, 'closeout', scheme);
    const downtime = page.getByLabel('Downtime (minutes)', { exact: true });
    await expect(downtime).toBeDisabled();
    await completionAccess(page);
    await expect(downtime).toBeEnabled();
    await uncovered(page, downtime);
    await expect(downtime).toHaveValue('');
    const controls = page
      .locator('.mantine-NumberInput-root')
      .filter({ has: downtime })
      .locator('.mantine-NumberInput-control');
    await expect(controls).toHaveCount(2);
    const inputBounds = await downtime.boundingBox();
    for (const control of await controls.all()) {
      const bounds = await control.boundingBox();
      expect(bounds!.x).toBeGreaterThanOrEqual(inputBounds!.x);
      expect(bounds!.y).toBeGreaterThanOrEqual(inputBounds!.y);
      expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(
        inputBounds!.x + inputBounds!.width
      );
      expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(
        inputBounds!.y + inputBounds!.height
      );
    }
    await page
      .getByRole('checkbox', { name: 'Follow-up required', exact: true })
      .check();
    await expect(
      page.getByRole('textbox', { name: 'Follow-up', exact: true })
    ).toBeVisible();
    await expect(downtime).toHaveValue('');
    await controls.nth(0).click();
    await expect(downtime).toHaveValue('0');
    await controls.nth(0).click();
    await expect(downtime).toHaveValue('1');
    await controls.nth(1).click();
    await expect(downtime).toHaveValue('0');
    await downtime.fill('');
    await expect(downtime).toHaveValue('');
    await page.screenshot({
      path: info.outputPath(`phone-downtime-${width}-${scheme}.png`)
    });
    // Native icon controls retain their square geometry as well.
    const back = await page.getByTestId('work-order-back').boundingBox();
    expect(back!.width).toBe(back!.height);
    await completionAccess(page, false);
    await expect(downtime).toBeDisabled();
    await expect(
      page.getByRole('checkbox', { name: 'Follow-up required', exact: true })
    ).toBeDisabled();
    await expect(
      page.getByRole('button', {
        name: 'Update maintenance record',
        exact: true
      })
    ).toBeDisabled();
    expect(fixture.commands).toHaveLength(0);
  });

  test(`phone keyboard reaches the workflow before secondary record details at ${width}px`, async ({
    page
  }) => {
    await page.setViewportSize({ width, height: 844 });
    const fixture = await closeoutWork(page);
    await openExecutionPanel(page, 'work', scheme);
    await page.getByRole('button', { name: /^Attachments \(/ }).focus();
    await page.keyboard.press('Tab');
    await expect(
      page.getByRole('button', {
        name: 'Talk about this work order',
        exact: true
      })
    ).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(
      page.getByRole('button', { name: 'Ask about this work', exact: true })
    ).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(page.getByTestId('work-phone-boundary')).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(
      page.getByRole('tab', { name: 'Work', exact: true })
    ).toBeFocused();
    const details = page.getByRole('button', {
      name: 'Record details',
      exact: true
    });
    await expect(details).toHaveCount(1);
    expect(
      await details.evaluate((node) => {
        const brief = document.querySelector('[data-testid="work-brief"]');
        return Boolean(
          brief &&
            brief.compareDocumentPosition(node) &
              Node.DOCUMENT_POSITION_FOLLOWING
        );
      })
    ).toBe(true);
    expect(fixture.commands).toHaveLength(0);
  });

  test(`critical active source alert precedes Work and Closeout in phone reading and visual order at ${width}px`, async ({
    page
  }, info) => {
    await page.setViewportSize({ width, height: 844 });
    const fixture = await closeoutWork(page);
    fixture.setOverview({
      source_alert: {
        id: 901,
        title: 'Synthetic active source alarm',
        severity: 'critical',
        status: 'active',
        alarm_code: 'TEST-CRITICAL-901',
        external_id: 'fixture-alert-901',
        detector: 'Synthetic detector',
        detector_version: '1',
        evidence_summary:
          'Synthetic evidence retained from the source occurrence.',
        first_observed_at: '2026-10-19T08:00:00Z',
        last_observed_at: '2026-10-19T08:05:00Z',
        source_name: 'Synthetic source',
        source_type: 'test',
        machine_id: 18
      }
    });
    await openExecutionPanel(page, 'work', scheme);
    const alert = page
      .getByRole('note')
      .filter({ hasText: 'Raised from a health alert' });
    for (const name of ['Work', 'Closeout']) {
      await page.getByRole('tab', { name, exact: true }).click();
      await expect(alert).toContainText('critical');
      await expect(alert).toContainText('active');
      await expect(alert.getByText('critical', { exact: true })).toBeVisible();
      await expect(alert.getByText('active', { exact: true })).toBeVisible();
      await expect(
        alert.getByRole('link', { name: "Open the machine's Health blade" })
      ).toHaveAttribute('href', '/machines/machine/18/health');
      await expect(alert).toContainText('TEST-CRITICAL-901');
      await expect(alert).toContainText(
        'Synthetic evidence retained from the source occurrence.'
      );
      const panel = page.getByRole('tabpanel', { name, exact: true });
      const alertBounds = await alert.boundingBox();
      const panelBounds = await panel.boundingBox();
      expect(alertBounds!.y + alertBounds!.height).toBeLessThanOrEqual(
        panelBounds!.y
      );
      expect(
        await alert.evaluate(
          (node, panelId) => {
            const panel = document.getElementById(panelId!);
            return Boolean(
              panel &&
                node.compareDocumentPosition(panel) &
                  Node.DOCUMENT_POSITION_FOLLOWING
            );
          },
          await panel.getAttribute('id')
        )
      ).toBe(true);
      // Frame the source context above the dock, leaving the native header clear.
      // The harness has additional route/debug controls above the production page.
      await alert.evaluate((node) => {
        window.scrollBy(0, node.getBoundingClientRect().top - 100);
      });
      await expect(alert).toBeInViewport({ ratio: 1 });
      await uncovered(page, alert);
      await phoneDock(page);
      await page.screenshot({
        path: info.outputPath(
          `phone-critical-${name.toLowerCase()}-${width}-${scheme}.png`
        )
      });
    }
    expect(fixture.commands).toHaveLength(0);
  });
}

test('narrow phone retains targeted Closeout and its draft across panels, keyboard navigation and rotation', async ({
  page
}, info) => {
  await page.setViewportSize({ width: 360, height: 844 });
  const fixture = await closeoutWork(page, { captures: [] });
  await openExecutionPanel(page, 'closeout', 'dark');
  await expect(
    page
      .getByTestId('closeout-account')
      .getByRole('button', { name: 'Action taken: No value recorded' })
  ).toHaveText('—');
  await expect(page.getByTestId('closeout-questions')).toContainText(
    'Complete account'
  );
  await expect(
    page.getByTestId('closeout-questions').getByRole('button', {
      name: 'What was the observed result?',
      exact: true
    })
  ).toBeVisible();
  await page.getByText('Edit or add to the capture', { exact: true }).click();
  const draft = page.getByRole('textbox', {
    name: 'What did you do?',
    exact: true
  });
  await draft.fill(
    'Synthetic phone draft retained while checking the saved verification.'
  );
  await page.getByRole('tab', { name: 'Verify & Return', exact: true }).click();
  await expect(page.getByTestId('verification-return')).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Update maintenance record', exact: true })
  ).toBeHidden();
  await page.getByRole('tab', { name: 'Closeout', exact: true }).click();
  await expect(draft).toHaveValue(
    'Synthetic phone draft retained while checking the saved verification.'
  );
  await uncovered(page, draft);
  await page.screenshot({ path: info.outputPath('phone-draft-360-dark.png') });
  await page.getByRole('tab', { name: 'Closeout', exact: true }).focus();
  await page.keyboard.press('End');
  await expect(
    page.getByRole('tab', { name: 'History', exact: true })
  ).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(
    page.getByRole('tab', { name: 'History', exact: true })
  ).toHaveAttribute('aria-selected', 'true');
  await page.setViewportSize({ width: 1100, height: 800 });
  await expect(page.getByTestId('work-phone-boundary')).toBeVisible();
  await page.getByRole('tab', { name: 'Closeout', exact: true }).click();
  await expect(draft).toHaveValue(
    'Synthetic phone draft retained while checking the saved verification.'
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await phoneDock(page);
  await uncovered(page, draft);
  expect(fixture.commands).toHaveLength(0);
});

test('ready phone account exposes one uncovered native final update and a readable report', async ({
  page
}, info) => {
  await page.setViewportSize({ width: 360, height: 844 });
  const fixture = await closeoutWork(page);
  await openExecutionPanel(page, 'closeout', 'light');
  await completionAccess(page);
  const account = page.getByTestId('closeout-account');
  const update = account.getByRole('button', {
    name: 'Update maintenance record',
    exact: true
  });
  await expect(update).toBeEnabled();
  await uncovered(page, update);
  await phoneDock(page);
  await page.screenshot({
    path: info.outputPath('phone-update-360-light.png')
  });
  await account.getByText('Read report', { exact: true }).click();
  await expect(account.getByTestId('work-record-update')).toHaveCount(1);
  await containment(page);
  await page.getByRole('tab', { name: 'Work', exact: true }).click();
  await expect(update).toBeHidden();
  await phoneDock(page);
  expect(fixture.commands).toHaveLength(0);
});

test('desktop keeps native tabs and the outcome-led Work brief', async ({
  page
}, info) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const fixture = await closeoutWork(page);
  await openExecutionPanel(page, 'work', 'light');
  await expect(page.getByTestId('work-phone-boundary')).toBeVisible();
  const tabs = page.getByRole('tablist', {
    name: 'Work workflow',
    exact: true
  });
  await expect(tabs.getByRole('tab')).toHaveCount(7);
  const rows = await tabs
    .getByRole('tab')
    .evaluateAll((nodes) =>
      nodes.map((node) => Math.round(node.getBoundingClientRect().y))
    );
  expect(new Set(rows).size).toBe(1);
  const details = page.getByRole('button', {
    name: 'Record details',
    exact: true
  });
  await expect(details).toHaveCount(1);
  expect(
    await details.evaluate((node) => {
      const tabs = document.querySelector(
        '[data-testid="work-panel-navigation"] [role="tablist"]'
      );
      return Boolean(
        tabs &&
          tabs.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING
      );
    })
  ).toBe(true);
  await containment(page);
  await page.screenshot({
    path: info.outputPath('desktop-regression-light.png')
  });
  expect(fixture.commands).toHaveLength(0);
});

for (const width of [360, 390]) {
  test(`phone request chat preserves refusal, exact revision, evidence and return at ${width}px`, async ({
    page
  }, info) => {
    await page.setViewportSize({ width, height: 844 });
    const fixture = await closeoutWork(page);
    const work = continuityWork();
    fixture.setOverview(work);
    const assistant = await phoneAssistant(page);
    await openExecutionPanel(page, 'diagnose', 'dark', false, 'editor');
    const request = page.getByTestId('investigation-request-401');
    await expect(request).toContainText(
      'Synthetic retained original observation'
    );
    const capture = request.getByTestId('native-observation-401');
    await capture
      .getByRole('button', { name: 'Record observation manually', exact: true })
      .click();
    await capture
      .getByLabel('Observed fact', { exact: true })
      .fill('Synthetic unsaved observation for request 401.');
    const evidenceBefore = work.investigation!.requests.items![0].artifacts;
    await request
      .getByRole('button', { name: 'Discuss request 401', exact: true })
      .click();
    await expect(page.getByTestId('ai-chat-work-focus')).toContainText('WO-73');
    await expect(page.getByTestId('ai-chat-work-focus')).toContainText(
      'Request 401'
    );
    await expect(page.getByTestId('ai-chat-physical-boundary')).toBeInViewport({
      ratio: 1
    });
    const composer = page.getByRole('textbox', {
      name: 'Message',
      exact: true
    });
    await composer.fill('Synthetic phone observation for this saved request.');
    await page.getByTestId('send-ai-chat-message').click();
    await expect(
      page
        .getByRole('alert')
        .filter({ hasText: 'Investigation request not confirmed' })
    ).toBeVisible();
    await expect(composer).toHaveValue(
      'Synthetic phone observation for this saved request.'
    );
    expect(assistant.sends).toHaveLength(0);
    await containment(page);
    await page.screenshot({
      path: info.outputPath(`phone-request-refused-${width}-dark.png`)
    });
    assistant.confirm();
    await page.getByTestId('send-ai-chat-message').click();
    await expect(
      page.getByText('Synthetic phone answer; no evidence published.', {
        exact: true
      })
    ).toBeVisible();
    await expect(page.getByTestId('ai-chat-physical-boundary')).toBeInViewport({
      ratio: 1
    });
    expect(assistant.acknowledgements).toEqual([
      {
        work_order_id: 73,
        expected_version: 7,
        request_id: 401,
        request_version: 3
      },
      {
        work_order_id: 73,
        expected_version: 7,
        request_id: 401,
        request_version: 3
      }
    ]);
    expect(assistant.sends).toHaveLength(1);
    expect(assistant.sends[0].work_focus_receipt).toBe(
      'synthetic-phone-signed-receipt'
    );
    expect(assistant.sends[0]).not.toHaveProperty('context');
    const back = page
      .getByTestId('ai-chat-message-work-focus')
      .last()
      .getByRole('link');
    await expect(back).toHaveAttribute(
      'href',
      '/maintenance/work-orders/73?work_panel=diagnose&investigation_request=401'
    );
    await page.screenshot({
      path: info.outputPath(`phone-request-answer-${width}-dark.png`)
    });
    await back.click();
    await expect(composer).toBeHidden();
    await expect(page.getByTestId('destination')).toHaveText(
      '/maintenance/work-orders/73?work_panel=diagnose&investigation_request=401'
    );
    await expect(
      page.getByRole('tab', { name: 'Diagnose', exact: true })
    ).toHaveAttribute('aria-selected', 'true');
    await expect(request).toContainText('Request 401');
    await expect(
      capture.getByLabel('Observed fact', { exact: true })
    ).toHaveValue('Synthetic unsaved observation for request 401.');
    await expect(request).toContainText(
      'Synthetic retained original observation'
    );
    expect(work.investigation!.requests.items![0].artifacts).toEqual(
      evidenceBefore
    );
    expect(fixture.commands).toHaveLength(0);
    await phoneDock(page);
    await page.screenshot({
      path: info.outputPath(`phone-request-return-${width}-dark.png`)
    });
  });
}

test('phone Work chat returns to the same unsaved capture without publishing or completing it', async ({
  page
}, info) => {
  await page.setViewportSize({ width: 360, height: 844 });
  const fixture = await closeoutWork(page, { captures: [] });
  const assistant = await phoneAssistant(page);
  assistant.confirm();
  await openExecutionPanel(page, 'closeout', 'light');
  await page.getByText('Edit or add to the capture', { exact: true }).click();
  const draft = page.getByRole('textbox', {
    name: 'What did you do?',
    exact: true
  });
  await draft.fill('Synthetic phone typed capture; not saved.');
  await page
    .getByRole('button', { name: 'Ask about this work', exact: true })
    .click();
  await page
    .getByRole('textbox', { name: 'Message', exact: true })
    .fill('Which account fact remains missing?');
  await page.getByTestId('send-ai-chat-message').click();
  await expect(
    page.getByText('Synthetic phone answer; no evidence published.', {
      exact: true
    })
  ).toBeVisible();
  expect(assistant.acknowledgements).toEqual([
    { work_order_id: 73, expected_version: 7 }
  ]);
  await page
    .getByTestId('ai-chat-message-work-focus')
    .last()
    .getByRole('link')
    .click();
  await expect(
    page.getByRole('textbox', { name: 'Message', exact: true })
  ).toBeHidden();
  await expect(draft).toHaveValue('Synthetic phone typed capture; not saved.');
  await uncovered(page, draft);
  await phoneDock(page);
  expect(fixture.commands).toHaveLength(0);
  await page.screenshot({
    path: info.outputPath('phone-capture-return-360-light.png')
  });
});

test('phone navigation reflows long identities and enlarged text without covering the native action', async ({
  page
}, info) => {
  await page.setViewportSize({ width: 360, height: 844 });
  const fixture = await closeoutWork(page);
  fixture.setOverview({
    reference: 'WO-WWTP-2026-004731-A',
    title:
      'Synthetic pump train with retained seal and motor inspection references',
    machine_name:
      'Synthetic duty pump with a long equipment tag and station identity'
  });
  await page.goto(
    '/playwright/s0-native.html?surface=maintenance-shell&sprint=s1&role=editor&work_access=reader&scheme=dark&entry=/maintenance/work-orders/73/?work_panel=closeout'
  );
  await expect(page.getByTestId('prepared-closeout')).toBeVisible();
  await completionAccess(page);
  await page.evaluate(() => {
    document.documentElement.style.fontSize = '24px';
  });
  await phoneDock(page);
  const update = page
    .getByTestId('closeout-account')
    .getByRole('button', { name: 'Update maintenance record', exact: true });
  await uncovered(page, update);
  await page.screenshot({
    path: info.outputPath('phone-enlarged-text-360-dark.png')
  });
  await page.setViewportSize({ width: 430, height: 844 });
  await phoneDock(page);
  await uncovered(page, update);
  expect(fixture.commands).toHaveLength(0);
});
