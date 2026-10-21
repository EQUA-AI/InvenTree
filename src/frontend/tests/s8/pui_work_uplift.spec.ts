import { writeFile } from 'node:fs/promises';
import { type Page, expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import {
  mockExecutionWork,
  openExecutionPanel,
  timeAssignment
} from '../s4/workExecutionFixtures';
import { closeoutWork } from '../s6/closeoutFixtures';
import { phoneAssistant } from '../s7/phoneCompanionFixtures';
import { textZoom, workScenario } from './workAccessibilityFixtures';
import { renderedTextContrast } from './workContrast';

nativeBaseline();
const panels = [
  ['work', 'Work'],
  ['diagnose', 'Diagnose'],
  ['plan', 'Plan & Safety'],
  ['parts', 'Parts'],
  ['verify', 'Verify & Return'],
  ['closeout', 'Closeout'],
  ['history', 'History']
] as const;

async function frameApp(page: Page) {
  // Hide only the harness's synthetic controls, retaining the production shell.
  for (const control of [
    page.getByText('S1 synthetic fixture — native resources', { exact: true }),
    page.getByRole('button', {
      name: 'Refresh fixture',
      exact: true,
      includeHidden: true
    }),
    page.getByRole('button', { name: 'Fixture back', exact: true }),
    page.getByRole('button', { name: 'Fixture forward', exact: true }),
    page.getByTestId('destination'),
    page.getByTestId('fixture-fetching'),
    page.getByTestId('fixture-theme-primary'),
    page.getByTestId('fixture-navigation-type')
  ])
    await control.evaluateAll((nodes) =>
      nodes.forEach((node) => node.setAttribute('data-uplift-harness', ''))
    );
  await page.addStyleTag({
    content:
      '[data-uplift-harness] { display: none; } #root > .mantine-Stack-root { padding: 0; gap: 0; }'
  });
}
async function allWork(page: Page) {
  const fixture = await closeoutWork(page);
  // A known unsupported material reader exercises the existing native fallback.
  await page.route('**/api/tasks/work-orders/73/materials/', (route) =>
    route.fulfill({
      status: 404,
      json: { detail: 'Synthetic material projection unavailable' }
    })
  );
  return fixture;
}

async function expectReadingsContained(page: Page) {
  const routine = page.getByTestId('routine-work');
  const cards = routine.locator('[data-testid^="routine-reading-"]');
  await expect(cards).toHaveCount(4);
  const clipped = await cards.evaluateAll((nodes) =>
    nodes.flatMap((node) => {
      const bounds = node.getBoundingClientRect();
      const parent = node.closest('[data-testid="routine-work"]')!;
      const container = parent.getBoundingClientRect();
      return bounds.right > container.right + 1 ||
        bounds.left < container.left - 1 ||
        node.scrollWidth > node.clientWidth + 1
        ? [node.getAttribute('data-testid')]
        : [];
    })
  );
  expect(
    clipped,
    'Every reading card and its content fit the routine panel'
  ).toEqual([]);
}

async function enlargeText(page: Page) {
  const rootFont = await page.evaluate(
    () => getComputedStyle(document.documentElement).fontSize
  );
  const sample = page.getByTestId('routine-work').locator('p').first();
  const original = await sample.evaluate((node) =>
    Number.parseFloat(getComputedStyle(node).fontSize)
  );
  await textZoom(page, true);
  // The P31 stress helper also enlarges the root, scaling rem-based spacing.
  // These captures exercise text-only enlargement; P31's stress is retained.
  await page.evaluate((font) => {
    document.documentElement.style.setProperty('font-size', font, 'important');
  }, rootFont);
  await expect
    .poll(() =>
      sample.evaluate((node) =>
        Number.parseFloat(getComputedStyle(node).fontSize)
      )
    )
    .toBe(original * 2);
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
      )
  );
}

function routineWork(type: 'inspection' | 'calibration') {
  const work = workScenario();
  work.title = `Synthetic ${type} of transfer pump`;
  work.work_order_type = type;
  const step = work.verification_return!.tests[0].step;
  work.routine_work = {
    rule: 'routine_work.v1',
    work_order_type: type,
    procedures_state: 'read',
    readings_state: 'read',
    applications: [
      {
        id: 7,
        revision: 3,
        snapshot_hash: 'a'.repeat(64),
        applied_by: 'Synthetic planner',
        applied_at: '2026-10-20T08:00:00Z',
        tasks: [
          {
            id: 1,
            step,
            status: 'completed',
            version: 4,
            value: { number: 0 },
            note: 'Synthetic retained measurement',
            disposition_reason: '',
            recorded_by: 'Synthetic technician',
            recorded_at: '2026-10-20T09:00:00Z'
          }
        ]
      }
    ],
    readings: [
      {
        id: 701,
        label: 'Synthetic pressure',
        phase: 'before',
        raw_text: '4.5 bar',
        value: '4.5',
        unit: 'bar',
        required: true,
        verification_state: 'failed',
        warnings: ['Synthetic outside recorded limit'],
        expected_min: '0',
        expected_max: '4',
        disposition_reason: '',
        step_execution: 1,
        recorded_by_name: 'Synthetic technician',
        recorded_at: '2026-10-20T09:00:00Z'
      },
      {
        id: 702,
        label: 'Synthetic pressure',
        phase: 'after',
        raw_text: '3 bar',
        value: '3',
        unit: 'bar',
        required: true,
        verification_state: 'verified',
        warnings: [],
        expected_min: '0',
        expected_max: '4',
        disposition_reason: '',
        step_execution: 1,
        recorded_by_name: 'Synthetic technician',
        recorded_at: '2026-10-20T10:00:00Z'
      },
      {
        id: 703,
        label: 'Synthetic unbounded value',
        phase: 'after',
        raw_text: '0',
        value: '0',
        unit: '',
        required: false,
        verification_state: 'verified',
        warnings: [],
        expected_min: null,
        expected_max: null,
        disposition_reason: '',
        step_execution: null,
        recorded_by_name: null,
        recorded_at: '2026-10-20T10:00:00Z'
      },
      {
        id: 704,
        label: 'Synthetic retained disposition',
        phase: 'before',
        raw_text: 'unclear',
        value: null,
        unit: '',
        required: false,
        verification_state: 'dispositioned',
        warnings: [],
        expected_min: null,
        expected_max: null,
        disposition_reason: 'Synthetic source unavailable',
        step_execution: null,
        recorded_by_name: 'Synthetic technician',
        recorded_at: '2026-10-20T10:00:00Z'
      }
    ]
  };
  return work;
}

for (const scheme of ['light', 'dark'] as const) {
  for (const [panel, name] of panels)
    test(`uplift phone390 ${scheme} ${panel}`, async ({ page }, info) => {
      await page.setViewportSize({ width: 390, height: 844 });
      const fixture = await allWork(page);
      fixture.setOverview(workScenario());
      await openExecutionPanel(page, panel, scheme);
      const region = page.getByRole('tabpanel', { name, exact: true });
      await expect(region).toBeVisible();
      await expect(page.getByTestId('work-phone-boundary')).toBeVisible();
      await expect(page.getByTestId('scope-revision-notice')).toHaveCount(
        panel === 'closeout' || panel === 'history' ? 0 : 1
      );
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth)
      ).toBe(390);
      await frameApp(page);
      await page.screenshot({
        path: info.outputPath(`phone390-${scheme}-${panel}.png`),
        fullPage: true
      });
      await frameApp(page);
      await region.evaluate((node) => node.scrollIntoView({ block: 'start' }));
      await page.screenshot({
        path: info.outputPath(`phone390-${scheme}-${panel}-viewport.png`)
      });
      expect(fixture.commands).toHaveLength(0);
    });

  for (const width of [1100, 1440, 1920]) {
    if (width === 1100 && scheme === 'dark') continue;
    for (const variant of [
      'work',
      'closeout',
      'inspection',
      'calibration'
    ] as const)
      test(`uplift ${width} ${scheme} ${variant}`, async ({ page }, info) => {
        await page.setViewportSize({ width, height: 1000 });
        const fixture = await allWork(page);
        fixture.setOverview(
          variant === 'inspection' || variant === 'calibration'
            ? routineWork(variant)
            : workScenario()
        );
        await openExecutionPanel(
          page,
          variant === 'closeout' ? 'closeout' : 'work',
          scheme
        );
        await expect(page.getByTestId('work-phone-boundary')).toBeVisible();
        if (variant === 'inspection' || variant === 'calibration') {
          const routine = page.getByTestId('routine-work');
          await expect(
            routine.getByRole('heading', { name: 'Saved readings' })
          ).toBeVisible();
          await expectReadingsContained(page);
          await expect(routine).toContainText(
            'Within recorded limits · acceptance separate'
          );
          await expect(routine).toContainText(
            'Value parsed · no limits recorded · not evaluated'
          );
          await expect(
            routine
              .locator('.mantine-Badge-root')
              .filter({ hasText: /Within recorded|Completed/ })
              .first()
          ).toHaveAttribute('style', /gray/);
        }
        expect(
          await page.evaluate(() => document.documentElement.scrollWidth)
        ).toBe(width);
        if (width >= 1440 && variant !== 'closeout') {
          const execution = page.getByTestId(
            variant === 'work' ? 'work-brief' : 'routine-work'
          );
          const context = page.getByTestId('service-restoration');
          const main = await execution.boundingBox();
          const side = await context.boundingBox();
          expect(side!.x).toBeGreaterThan(main!.x + main!.width);
        }
        await frameApp(page);
        await page.screenshot({
          path: info.outputPath(`desktop${width}-${scheme}-${variant}.png`),
          fullPage: true
        });
        await frameApp(page);
        await page.screenshot({
          path: info.outputPath(
            `desktop${width}-${scheme}-${variant}-viewport.png`
          )
        });
        expect(fixture.commands).toHaveLength(0);
      });
  }
  for (const width of [390, 360, 1440])
    test(`uplift routine and text200 ${width} ${scheme}`, async ({
      page
    }, info) => {
      await page.setViewportSize({ width, height: 900 });
      const fixture = await allWork(page);
      fixture.setOverview(routineWork('calibration'));
      await openExecutionPanel(page, 'work', scheme);
      await expectReadingsContained(page);
      await frameApp(page);
      await page.screenshot({
        path: info.outputPath(`routine-calibration-${width}-${scheme}.png`),
        fullPage: true
      });
      if (width === 390) {
        fixture.setOverview(routineWork('inspection'));
        await page
          .getByRole('button', {
            name: 'Refresh fixture',
            exact: true,
            includeHidden: true
          })
          .evaluate((node) => (node as HTMLButtonElement).click());
        await expect(
          page.getByRole('heading', { name: 'Inspection tasks and readings' })
        ).toBeVisible();
        await expectReadingsContained(page);
        await frameApp(page);
        await page.screenshot({
          path: info.outputPath(`routine-inspection-${width}-${scheme}.png`),
          fullPage: true
        });
      }
      await enlargeText(page);
      await expectReadingsContained(page);
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth)
      ).toBe(width);
      await frameApp(page);
      await page.screenshot({
        path: info.outputPath(`routine-text200-${width}-${scheme}.png`),
        fullPage: true
      });
      await frameApp(page);
      await page.screenshot({
        path: info.outputPath(`routine-text200-${width}-${scheme}-viewport.png`)
      });
    });
}

test('uplift keyboard panels take focus, history is replaced and static callouts do not announce alerts', async ({
  page
}, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockExecutionWork(page, workScenario());
  await openExecutionPanel(page);
  await page.getByRole('tab', { name: 'Diagnose', exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect(
    page.getByRole('tabpanel', { name: 'Diagnose', exact: true })
  ).toBeFocused();
  await page.getByRole('tab', { name: 'Plan & Safety', exact: true }).focus();
  await frameApp(page);
  await page.screenshot({ path: info.outputPath('focus-visible-tab.png') });
  await page.keyboard.press('Enter');
  await expect(
    page.getByRole('tabpanel', { name: 'Plan & Safety', exact: true })
  ).toBeFocused();
  await expect(
    page
      .getByRole('tabpanel', { name: 'Plan & Safety', exact: true })
      .getByTestId('scope-revision-notice')
  ).toHaveAttribute('role', 'note');
  await frameApp(page);
  await page.screenshot({ path: info.outputPath('focus-visible-panel.png') });
  // The harness exposes MemoryRouter's navigation type for a direct assertion.
  await expect(page.getByTestId('fixture-navigation-type')).toHaveText(
    'REPLACE'
  );
});

test('uplift desktop keyboard activation keeps focus and the tab row in view', async ({
  page
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const fixture = await allWork(page);
  fixture.setOverview(workScenario());
  await openExecutionPanel(page);
  const tablist = page.getByRole('tablist', {
    name: 'Work workflow',
    exact: true
  });
  const inView = async () =>
    tablist.evaluate((node) => {
      const r = node.getBoundingClientRect();
      return r.top >= 0 && r.bottom <= window.innerHeight;
    });
  const diagnose = page.getByRole('tab', { name: 'Diagnose', exact: true });
  await diagnose.focus();
  await page.keyboard.press('Enter');
  await expect(
    page.getByRole('tabpanel', { name: 'Diagnose', exact: true })
  ).toBeVisible();
  await expect(diagnose).toBeFocused();
  expect(await inView()).toBe(true);
  // Arrow keys move within the tablist without activating (manual mode).
  await page.keyboard.press('ArrowRight');
  await expect(
    page.getByRole('tab', { name: 'Plan & Safety', exact: true })
  ).toBeFocused();
  await expect(diagnose).toHaveAttribute('aria-selected', 'true');
  // An in-panel link that hides its own panel returns focus to the active
  // tab with the tab row in view, and replaces history like the tabs do.
  await page.getByRole('tab', { name: 'Work', exact: true }).click();
  const link = page
    .getByRole('tabpanel', { name: 'Work', exact: true })
    .getByRole('link', {
      name: 'View maintenance account and report',
      exact: true
    });
  await link.focus();
  await page.keyboard.press('Enter');
  const closeout = page.getByRole('tab', { name: 'Closeout', exact: true });
  await expect(closeout).toHaveAttribute('aria-selected', 'true');
  await expect(closeout).toBeFocused();
  expect(await inView()).toBe(true);
  await expect(page.getByTestId('fixture-navigation-type')).toHaveText(
    'REPLACE'
  );
});

test('uplift actionable context disclosures open on load and on later reads', async ({
  page
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const work = workScenario();
  await mockExecutionWork(page, work);
  await openExecutionPanel(page, 'work', 'light', false, 'editor');
  const region = page.getByRole('tabpanel', { name: 'Work', exact: true });
  const continuity = region.getByRole('button', {
    name: /^Continuity and handover/
  });
  const assignment = region.getByRole('button', {
    name: /^Assignment and person-work time/
  });
  await expect(continuity).toHaveAttribute('aria-expanded', 'false');
  await expect(assignment).toHaveAttribute('aria-expanded', 'false');
  // A later server read supplies a handover this person can accept and their
  // own running interval (harness actor 11); both disclosures open and say why.
  work.work_continuity!.accepted_handover = {
    available: true,
    id: 19,
    state: 'pending',
    previous_owner: 'Synthetic outgoing technician',
    incoming_owner: 'Synthetic incoming technician',
    responsible_owner: 'Synthetic outgoing technician',
    proposed_by: 'Synthetic supervisor',
    proposed_at: '2026-10-20T08:00:00Z',
    proposed_version: work.lifecycle_version,
    accepted_by: null,
    accepted_at: null,
    accepted_version: null,
    can_accept: true
  };
  work.time_assignment = {
    ...(work.time_assignment ?? timeAssignment()),
    time_records: {
      observed_at: '2026-10-20T09:00:00Z',
      coverage: 'partial',
      recorded_seconds: 600,
      intervals: [
        {
          id: 501,
          person_id: 11,
          person: 'Synthetic technician',
          started_at: '2026-10-20T08:50:00Z',
          ended_at: null,
          end_action: null,
          recorded_by: 'Synthetic technician',
          recorded_at: '2026-10-20T08:50:00Z',
          ended_by: null,
          ended_recorded_at: null,
          reason: null,
          supersedes: null,
          superseded: false
        }
      ],
      gaps: []
    }
  };
  await page.getByRole('button', { name: 'Refresh fixture' }).click();
  await expect(continuity).toHaveAttribute('aria-expanded', 'true');
  await expect(continuity).toContainText('Handover awaiting your acceptance');
  await expect(assignment).toHaveAttribute('aria-expanded', 'true');
  await expect(assignment).toContainText('Your work time is running');
  await expect(
    region.getByRole('button', { name: 'Accept handover', exact: true })
  ).toBeVisible();
  // A person can still collapse it; the same records do not reopen it.
  await continuity.click();
  await expect(continuity).toHaveAttribute('aria-expanded', 'false');
  const reread = page.waitForResponse('**/api/kanban/work-orders/73/overview/');
  await page.getByRole('button', { name: 'Refresh fixture' }).click();
  await reread;
  await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
  await expect(continuity).toHaveAttribute('aria-expanded', 'false');
});

for (const status of [500, 403])
  test(`uplift load ${status} is honest and retry recovers`, async ({
    page
  }, info) => {
    await mockExecutionWork(page, workScenario());
    await page.route('**/api/kanban/work-orders/73/overview/', (route) =>
      route.fulfill({
        status,
        json: { detail: 'Synthetic refusal or failure' }
      })
    );
    await page.goto(
      '/playwright/s0-native.html?surface=maintenance-shell&sprint=s1&role=viewer&work_access=reader&entry=/maintenance/work-orders/73/'
    );
    if (status === 500) {
      await expect(
        page.getByText('Work could not be loaded', { exact: true })
      ).toBeVisible();
      await expect(
        page.getByText(
          'This work order does not exist or is outside your access scope.'
        )
      ).toHaveCount(0);
      const failureContrast = await renderedTextContrast(
        page.getByRole('alert').filter({ hasText: 'Work could not be loaded' })
      );
      await writeFile(
        info.outputPath('failed-load-contrast.json'),
        JSON.stringify(failureContrast, null, 2)
      );
      for (const row of failureContrast)
        expect(row.ratio, row.text).toBeGreaterThanOrEqual(4.5);
      await frameApp(page);
      await page.screenshot({ path: info.outputPath('failed-load-500.png') });
      await page.route('**/api/kanban/work-orders/73/overview/', (route) =>
        route.fulfill({ json: workScenario() })
      );
      await page.getByRole('button', { name: 'Retry Work' }).click();
      await expect(
        page.getByRole('heading', { name: /^WO-73:/ })
      ).toBeVisible();
    } else {
      await expect(
        page.getByText(
          'This work order does not exist or is outside your access scope.'
        )
      ).toBeVisible();
      await expect(
        page.getByRole('button', { name: 'Retry Work' })
      ).toHaveCount(0);
    }
  });

for (const width of [390, 1440])
  for (const scheme of ['light', 'dark'] as const)
    test(`uplift assistant refusal and focus ${width} ${scheme}`, async ({
      page
    }, info) => {
      await page.setViewportSize({ width, height: 900 });
      const fixture = await allWork(page);
      fixture.setOverview(workScenario());
      const assistant = await phoneAssistant(page);
      await openExecutionPanel(page, 'work', scheme);
      const trigger = page.getByRole('button', { name: 'Ask about this work' });
      await trigger.click();
      await expect(page.getByTestId('ai-chat-physical-boundary')).toBeVisible();
      const composer = page.getByRole('textbox', {
        name: 'Message',
        exact: true
      });
      await composer.fill('Synthetic question retained on refusal');
      await page.getByTestId('send-ai-chat-message').click();
      await expect(
        page.getByText('Work order not confirmed', { exact: true })
      ).toBeVisible();
      expect(assistant.sends).toHaveLength(0);
      const refusalContrast = await renderedTextContrast(
        page
          .getByTestId('ai-chat-drawer')
          .getByRole('alert')
          .filter({ hasText: 'Work order not confirmed' })
      );
      await writeFile(
        info.outputPath('assistant-refusal-contrast.json'),
        JSON.stringify(refusalContrast, null, 2)
      );
      for (const row of refusalContrast)
        expect(row.ratio, row.text).toBeGreaterThanOrEqual(4.5);
      await expect(composer).toHaveValue(
        'Synthetic question retained on refusal'
      );
      await expect(composer).toBeFocused();
      await frameApp(page);
      await page.screenshot({
        path: info.outputPath(`assistant-refusal-${width}-${scheme}.png`)
      });
      await page.keyboard.press('Escape');
      await expect(trigger).toBeFocused();
      await frameApp(page);
      await page.screenshot({
        path: info.outputPath(`assistant-focus-return-${width}-${scheme}.png`)
      });
    });

for (const scheme of ['light', 'dark'] as const)
  test(`uplift short viewport keyboard ${scheme}`, async ({ page }, info) => {
    await page.setViewportSize({ width: 390, height: 420 });
    await mockExecutionWork(page, workScenario());
    await openExecutionPanel(page, 'work', scheme);
    await frameApp(page);
    await expect(page.getByTestId('work-panel-navigation')).toHaveCSS(
      'position',
      'static'
    );
    const tab = page.getByRole('tab', { name: 'Diagnose', exact: true });
    await tab.focus();
    await page.keyboard.press('Enter');
    const panel = page.getByRole('tabpanel', { name: 'Diagnose', exact: true });
    // Inline (non-docked) navigation keeps keyboard focus in the tablist.
    await expect(panel).toBeVisible();
    await expect(tab).toBeFocused();
    await expect(tab).toHaveAttribute('aria-selected', 'true');
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth)
    ).toBe(390);
    await page.screenshot({
      path: info.outputPath(`short-viewport-keyboard-${scheme}.png`)
    });
  });

test('uplift dock clearance includes the complete safe-area border box', async ({
  page
}, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockExecutionWork(page, workScenario());
  await openExecutionPanel(page);
  await frameApp(page);
  const main = page.getByRole('main');
  await main.evaluate((node) =>
    node.style.setProperty('--work-safe-area-bottom', '34px')
  );
  const dock = page.getByTestId('work-panel-navigation');
  await expect(dock).toHaveCSS('padding-bottom', '34px');
  const height = (await dock.boundingBox())!.height;
  const clearance = await page
    .getByRole('tab', { name: 'Work', exact: true })
    .evaluate((node) =>
      Number.parseFloat(getComputedStyle(node).scrollMarginBottom)
    );
  expect(clearance).toBeGreaterThanOrEqual(height + 15);
  await info.attach('safe-area-geometry', {
    body: JSON.stringify({ height, clearance, syntheticInset: 34 }),
    contentType: 'application/json'
  });
});
