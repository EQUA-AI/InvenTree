import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import {
  accepted,
  closeoutWork,
  containment,
  openCloseout,
  proposal,
  story,
  values
} from './closeoutFixtures';

nativeBaseline();
for (const scheme of ['light', 'dark'] as const) {
  test(`prepared report is direct, sourced and read-only (${scheme})`, async ({
    page
  }, info) => {
    const { commands, reads } = await closeoutWork(page);
    await openCloseout(page, scheme);
    await expect(
      page.getByRole('heading', {
        name: 'Prepared maintenance account',
        exact: true
      })
    ).toBeVisible();
    await expect(page.getByTestId('prepared-closeout')).toContainText(
      values.action
    );
    await expect(
      page.getByRole('button', {
        name: 'Update maintenance record',
        exact: true
      })
    ).toBeEnabled();
    await expect(page.getByTestId('prepared-closeout')).toContainText(
      'Not established'
    );
    await expect(page.getByTestId('prepared-closeout')).toContainText(
      'Initial test: 6 mm/s · Failed reading'
    );
    await expect(page.getByTestId('prepared-closeout')).toContainText(
      'Repeat test: 0 mm/s · Value parsed · acceptance separate'
    );
    await expect(
      page.getByRole('button', { name: 'Add reading', exact: true })
    ).toHaveCount(0);
    await expect(page.getByTestId('prepared-closeout')).toContainText(
      'Recorded by Synthetic Technician'
    );
    await expect(page.getByTestId('prepared-closeout')).not.toContainText(
      'Recorded by user'
    );
    await expect(
      page.getByTestId('closeout-capture-disclosure')
    ).not.toHaveAttribute('open');
    await expect(
      page.getByText('Narrative', { exact: true })
    ).not.toBeVisible();
    await page.getByText('Retained story revisions', { exact: true }).click();
    await expect(
      page.getByText('Original transcript: adjusted seal.', { exact: true })
    ).toBeVisible();
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Download account (.txt)' }).click();
    expect((await download).suggestedFilename()).toContain('work-73-account');
    expect(commands).toEqual([]);
    expect(reads.filter((p) => p.endsWith('/readings/'))).toHaveLength(1);
    await containment(page);
    const order = await page.evaluate(() => {
      const update = document.querySelector('[data-testid=work-record-update]');
      const editor = document.querySelector(
        '[data-testid=closeout-capture-disclosure]'
      );
      return (
        !!update &&
        !!editor &&
        !!(
          update.compareDocumentPosition(editor) &
          Node.DOCUMENT_POSITION_FOLLOWING
        )
      );
    });
    expect(order).toBe(true);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: info.outputPath(`prepared-report-${scheme}.png`),
      fullPage: true
    });
  });
}
test('missing story does not prefill the mutually exclusive typed fallback or start a capture', async ({
  page
}, info) => {
  const { commands } = await closeoutWork(page, { captures: [] });
  await openCloseout(page);
  await page.getByText('Edit or add to the capture', { exact: true }).click();
  await expect(
    page.getByRole('textbox', { name: 'What did you do?', exact: true })
  ).toHaveValue('');
  await expect(page.getByTestId('dictate-closeout')).toBeEnabled();
  await expect(
    page.getByText('No technician story recorded', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Update maintenance record', exact: true })
  ).toBeDisabled();
  expect(commands).toEqual([]);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath('missing-story.png'),
    fullPage: true
  });
});
for (const status of ['open', 'extracting', 'proposed']) {
  test(`an ${status} capture opens recovery and keeps its lock visible`, async ({
    page
  }, info) => {
    const { commands } = await closeoutWork(page, {
      captures: [{ ...story, status }],
      ready: false
    });
    await openCloseout(page);
    await expect(
      page.getByRole('button', {
        name: 'Update maintenance record',
        exact: true
      })
    ).toBeDisabled();
    await expect(page.getByTestId('work-record-update')).toContainText(
      'Finish the active capture review'
    );
    const editor = page.getByTestId('closeout-capture-disclosure');
    await expect(editor).toHaveAttribute('open');
    await expect(page.getByTestId('closeout-capture-lock')).toBeVisible();
    // Deliberate collapse cannot hide the capture lock or discard its source.
    await editor
      .getByText('Edit or add to the capture', { exact: true })
      .click();
    await expect(editor).not.toHaveAttribute('open');
    await expect(page.getByTestId('closeout-capture-lock')).toBeVisible();
    await editor
      .getByText('Edit or add to the capture', { exact: true })
      .click();
    await expect(page.getByTestId('dictate-closeout')).toHaveCount(0);
    await expect(
      page.getByTestId('closeout-capture-disclosure').getByRole('textbox')
    ).toHaveValue(story.narrative);
    expect(commands).toEqual([]);
    if (status === 'extracting') {
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({
        path: info.outputPath('active-capture-open.png'),
        fullPage: true
      });
    }
  });
}

for (const scheme of ['light', 'dark'] as const) {
  test(`dictation locks a ready manual account and preserves its draft (${scheme})`, async ({
    page
  }, info) => {
    const { commands } = await closeoutWork(page, { captures: [] });
    await openCloseout(page, scheme);
    const editor = page.getByTestId('closeout-capture-disclosure');
    await editor
      .getByText('Edit or add to the capture', { exact: true })
      .click();
    await editor.getByText('Review', { exact: true }).click();
    for (const [label, value] of [
      ['Action taken', values.action],
      ['Result', values.result],
      ['Verification summary', values.verification_summary]
    ]) {
      await editor
        .getByRole('textbox', { name: label, exact: true })
        .fill(value);
    }
    const update = page.getByRole('button', {
      name: 'Update maintenance record',
      exact: true
    });
    await expect(update).toBeEnabled();
    await editor.getByText('Narrative', { exact: true }).click();
    await page.getByTestId('dictate-closeout').click();
    const dialog = page.getByRole('dialog', {
      name: 'Dictate closeout narrative'
    });
    await expect(dialog).toBeVisible();
    await expect(editor).toHaveAttribute('open');
    await expect(page.getByTestId('closeout-capture-lock')).toContainText(
      'Finish or cancel dictation'
    );
    await expect(update).toBeDisabled();
    // No consent, capture creation or update is submitted by opening the modal.
    expect(commands).toEqual([]);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: info.outputPath(`dictation-lock-${scheme}.png`),
      fullPage: true
    });
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    await expect(editor).toHaveAttribute('open');
    await expect(update).toBeEnabled();
    await editor.getByText('Review', { exact: true }).click();
    await expect(
      editor.getByRole('textbox', { name: 'Result', exact: true })
    ).toHaveValue(values.result);
    expect(commands).toEqual([]);
  });
}
test('unchanged extraction is accepted and a technician correction is edited', async ({
  page
}) => {
  const { commands } = await closeoutWork(page);
  await page.route('**/closeout/captures/12/decisions/', async (route) => {
    commands.push({
      path: 'decisions',
      body: route.request().postDataJSON(),
      method: 'POST'
    });
    await route.fulfill({ json: {} });
  });
  await openCloseout(page);
  await page.getByText('Edit or add to the capture', { exact: true }).click();
  await page.getByText('Review', { exact: true }).click();
  await page
    .getByRole('textbox', { name: 'Result', exact: true })
    .fill('Corrected observation');
  const tabs = page.getByRole('tablist', {
    name: 'Work workflow',
    exact: true
  });
  await tabs.getByRole('tab', { name: 'History', exact: true }).click();
  await tabs.getByRole('tab', { name: 'Closeout', exact: true }).click();
  await expect(page.getByTestId('closeout-capture-disclosure')).toHaveAttribute(
    'open'
  );
  await expect(
    page.getByRole('textbox', { name: 'Result', exact: true })
  ).toHaveValue('Corrected observation');
  await page
    .getByRole('button', { name: 'Record decisions', exact: true })
    .click();
  await expect.poll(() => commands.length).toBe(1);
  expect(commands[0].body.decisions).toContainEqual({
    field_path: 'action',
    decision: 'accepted'
  });
  expect(commands[0].body.decisions).toContainEqual({
    field_path: 'result',
    decision: 'edited',
    final_value: 'Corrected observation'
  });
});
test('accepted report retains original snapshot and attributed amendment; History remains chronological', async ({
  page
}, info) => {
  const { commands, setOverview } = await closeoutWork(page, {
    completed: true,
    captures: [{ ...story, status: 'consumed', completed_closeout: 301 }]
  });
  setOverview({
    events: [
      {
        id: 2,
        event_type: 'AMENDED',
        actor: 4,
        from_status: 'completed',
        to_status: 'completed',
        reason: 'Corrected observation',
        created_at: '2026-10-19T10:05:00Z'
      },
      {
        id: 1,
        event_type: 'COMPLETED',
        actor: 3,
        from_status: 'verifying',
        to_status: 'completed',
        reason: 'Original accepted record',
        created_at: '2026-10-19T09:00:00Z'
      }
    ]
  });
  await openCloseout(page, 'dark');
  await page.getByText('Original completed account', { exact: true }).click();
  await expect(page.getByTestId('prepared-closeout')).toContainText(
    values.result
  );
  await expect(page.getByTestId('prepared-closeout')).toContainText(
    'Corrected the recorded observation'
  );
  await expect(page.getByTestId('prepared-closeout')).toContainText(
    'Requested by Synthetic Technician'
  );
  await expect(
    page.getByRole('button', { name: 'Update maintenance record', exact: true })
  ).toHaveCount(0);
  await containment(page);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath('accepted-report-dark.png'),
    fullPage: true
  });
  await page.getByRole('tab', { name: 'History', exact: true }).click();
  const history = page.getByRole('tabpanel', { name: 'History', exact: true });
  expect(await history.innerText()).toMatch(
    /Original accepted record[\s\S]*Corrected observation/
  );
  expect(commands).toEqual([]);
});
test('source failure never looks like an empty or ready account', async ({
  page
}) => {
  await closeoutWork(page, { sourceError: true });
  await openCloseout(page);
  await expect(
    page.getByText(/Some closeout sources could not be read/)
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Download account (.txt)' })
  ).toBeDisabled();
  await expect(
    page.getByRole('button', { name: 'Update maintenance record', exact: true })
  ).toBeDisabled();
});
test('Verify owns native reading entry and shares the retained query with Closeout', async ({
  page
}) => {
  const { reads, commands } = await closeoutWork(page);
  await openCloseout(page);
  await page.getByRole('tab', { name: 'Verify & Return', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Add reading', exact: true })
  ).toBeVisible();
  await page.getByRole('tab', { name: 'Closeout', exact: true }).click();
  await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
  // The same key is shared. A second observer may revalidate stale data once;
  // returning to the kept-mounted Closeout must not create another reader.
  expect(
    reads.filter((p) => p.endsWith('/readings/')).length
  ).toBeLessThanOrEqual(2);
  expect(commands).toEqual([]);
});

test('missing recorder names remain Not recorded in the story, revisions and saved decisions', async ({
  page
}) => {
  await closeoutWork(page, {
    captures: [
      {
        ...story,
        created_by_name: null,
        revisions: story.revisions?.map((row) => ({
          ...row,
          created_by_name: null
        }))
      }
    ]
  });
  await page.route('**/captures/12/proposal/', (route) =>
    route.fulfill({
      json: {
        ...proposal,
        decisions: proposal.decisions.map((row) => ({
          ...row,
          decided_by_name: null
        }))
      }
    })
  );
  await openCloseout(page);
  await page.getByText('Retained story revisions', { exact: true }).click();
  const account = page.getByTestId('prepared-closeout');
  await expect(
    account.locator('p').filter({ hasText: /Recorded by Not recorded/ })
  ).toHaveCount(3);
  await expect(
    account.getByRole('button', {
      name: /^Information: accepted · Not recorded ·/
    })
  ).toHaveCount(3);
  await expect(account).not.toContainText('Recorded by user');
});

test('review regression: accepted consumed capture retains field recorders and completer', async ({
  page
}) => {
  await closeoutWork(page, {
    completed: true,
    captures: [{ ...story, status: 'consumed', completed_closeout: 301 }]
  });
  await openCloseout(page);
  const account = page.getByTestId('prepared-closeout');
  await expect(
    account
      .getByTestId('closeout-account')
      .getByText(/ · Synthetic Technician$/)
  ).toBeVisible();
  await expect(account).toContainText('Acceptance: Not recorded');
  await expect(account).not.toContainText('Accepted by');
  await expect(
    account.getByRole('button', {
      name: /^Information: accepted · Synthetic Technician ·/
    })
  ).toHaveCount(3);
  await page.getByText('Read report', { exact: true }).click();
  await expect(account.locator('pre')).toContainText(
    'Completed by Synthetic Technician'
  );
  await expect(account.locator('pre')).toContainText(
    'Saved decision: Action taken'
  );
});

test('review regression: unticked follow-up matches preview and payload', async ({
  page
}) => {
  const { commands } = await closeoutWork(page);
  await openCloseout(page);
  await page
    .getByRole('checkbox', { name: 'Follow-up required', exact: true })
    .check();
  await page
    .getByRole('textbox', { name: 'Follow-up', exact: true })
    .fill('Replace belt next week');
  await page
    .getByRole('checkbox', { name: 'Follow-up required', exact: true })
    .uncheck();
  await page
    .getByRole('button', { name: 'Update maintenance record', exact: true })
    .click();
  await expect.poll(() => commands.length).toBe(1);
  expect(commands[0].body.follow_up).toBe('');
});

test('review regression: pending proposal and materials block export without absent facts', async ({
  page
}) => {
  await closeoutWork(page);
  let release: (() => void) | undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(
    '**/api/tasks/work-orders/73/closeout/captures/12/proposal/',
    async (route) => {
      await gate;
      await route.fulfill({ json: proposal });
    }
  );
  await page.goto(
    '/playwright/s0-native.html?surface=maintenance-shell&sprint=s1&work_access=reader&entry=/maintenance/work-orders/73/?work_panel=closeout'
  );
  await expect(
    page.getByRole('button', { name: 'Download account (.txt)', exact: true })
  ).toBeDisabled();
  await expect(page.getByTestId('prepared-closeout')).toContainText(
    'Loading account decisions'
  );
  release!();
  await expect(
    page.getByRole('button', { name: 'Download account (.txt)', exact: true })
  ).toBeEnabled();
});

test('report waits for material and amendment reads, shows pending decisions and orders revisions', async ({
  page
}) => {
  await closeoutWork(page, {
    completed: true,
    captures: [
      {
        ...story,
        status: 'consumed',
        completed_closeout: 301,
        revisions: [...story.revisions!].reverse()
      }
    ]
  });
  let releaseMaterials: (() => void) | undefined;
  let releaseAmendments: (() => void) | undefined;
  const materialsGate = new Promise<void>((resolve) => {
    releaseMaterials = resolve;
  });
  const amendmentsGate = new Promise<void>((resolve) => {
    releaseAmendments = resolve;
  });
  await page.route(
    '**/api/tasks/work-orders/73/closeout/part-usage/',
    async (route) => {
      await materialsGate;
      await route.fulfill({ json: [] });
    }
  );
  await page.route(
    '**/api/tasks/work-orders/73/closeout/amendments/',
    async (route) => {
      await amendmentsGate;
      await route.fulfill({
        json: [
          {
            id: 41,
            closeout: 301,
            status: 'proposed',
            reason: 'Pending correction',
            changes: {},
            requested_by: 3,
            requested_by_name: 'Synthetic Technician',
            created_at: '2026-10-19T10:00:00Z',
            decided_by: null,
            decided_by_name: null,
            applied_at: null
          }
        ]
      });
    }
  );
  await page.goto(
    '/playwright/s0-native.html?surface=maintenance-shell&sprint=s1&work_access=reader&entry=/maintenance/work-orders/73/?work_panel=closeout'
  );
  const account = page.getByTestId('prepared-closeout');
  await expect(account).toContainText(story.narrative);
  const download = page.getByRole('button', {
    name: 'Download account (.txt)',
    exact: true
  });
  await expect(download).toBeDisabled();
  releaseMaterials!();
  await expect(account).toContainText('No material usage rows recorded');
  await expect(download).toBeDisabled();
  releaseAmendments!();
  await expect(download).toBeEnabled();
  await expect(account).toContainText('Not yet decided');
  await expect(account).not.toContainText('Decided by Not recorded');
  await page.getByText('Read report', { exact: true }).click();
  const report = await account.locator('pre').innerText();
  expect(report.indexOf('\n\nRevision 1 ·')).toBeLessThan(
    report.indexOf('\n\nRevision 2 ·')
  );
});

test('accepted export waits for the fresh consumed-capture link instead of cached manual attribution', async ({
  page
}) => {
  const { setOverview } = await closeoutWork(page);
  let completing = false;
  let releaseCapture: (() => void) | undefined;
  const gate = new Promise<void>((resolve) => {
    releaseCapture = resolve;
  });
  await page.route(
    '**/api/tasks/work-orders/73/closeout/captures/',
    async (route) => {
      if (completing) await gate;
      await route.fulfill({
        json: [
          {
            ...story,
            ...(completing
              ? { status: 'consumed', completed_closeout: 301 }
              : {})
          }
        ]
      });
    }
  );
  await openCloseout(page);
  completing = true;
  setOverview({
    lifecycle_status: 'completed',
    lifecycle_version: 8,
    structured_closeout: accepted
  });
  await page
    .getByRole('button', { name: 'Refresh fixture', exact: true })
    .click();
  const account = page.getByTestId('prepared-closeout');
  await expect(
    account
      .getByTestId('closeout-account')
      .getByText(/ · Synthetic Technician$/)
  ).toBeVisible();
  await expect(account).toContainText('Corrected the recorded observation');
  await expect(page.getByTestId('fixture-fetching')).toHaveText('1');
  await expect(
    page.getByRole('button', { name: 'Download account (.txt)', exact: true })
  ).toBeDisabled();
  await expect(account).not.toContainText(
    'Per-field decisions are not supplied for this manual account.'
  );
  releaseCapture!();
  await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
  await expect(
    account.getByRole('button', {
      name: /^Information: accepted · Synthetic Technician ·/
    })
  ).toHaveCount(3);
  await expect(
    page.getByRole('button', { name: 'Download account (.txt)', exact: true })
  ).toBeEnabled();
});
