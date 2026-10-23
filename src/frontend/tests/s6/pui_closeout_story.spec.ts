import { createHash } from 'node:crypto';
import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import {
  closeoutWork,
  containment,
  openCloseout,
  proposal,
  story,
  values
} from './closeoutFixtures';

nativeBaseline();
for (const scheme of ['light', 'dark'] as const) {
  test(`P45 reuses saved answers and exposes character sources (${scheme})`, async ({
    page
  }, info) => {
    const { commands } = await closeoutWork(page);
    const start = story.narrative.indexOf('Replaced');
    await page.route(
      '**/api/tasks/work-orders/73/closeout/captures/12/proposal/',
      (route) =>
        route.fulfill({
          json: {
            ...proposal,
            fields: {
              ...proposal.fields,
              action: {
                value: 'Replaced',
                confidence: 0.8,
                spans: [[start, start + 8]],
                warnings: []
              }
            },
            decisions: proposal.decisions.map((row) =>
              row.field_path === 'action' ? { ...row, decision: 'edited' } : row
            )
          }
        })
    );
    await openCloseout(page, scheme);
    await expect(page.getByTestId('closeout-questions')).toHaveCount(0);
    await expect(page.getByTestId('prepared-closeout')).toContainText(
      values.action
    );
    const sourceInfo = page
      .getByTestId('prepared-closeout')
      .getByRole('button', { name: /Information: Capture 12.*Replaced/ });
    await sourceInfo.focus();
    await expect(page.getByRole('tooltip')).toContainText(
      `[${start}, ${start + 8}) · Replaced`
    );
    await expect(
      page.getByTestId('prepared-closeout').getByRole('button', {
        name: /Information: Extracted text needs review/
      })
    ).toBeVisible();
    await expect(
      page.getByRole('textbox', { name: 'What did you do?', exact: true })
    ).toBeHidden();
    await expect(
      page.getByRole('button', {
        name: 'Update maintenance record',
        exact: true
      })
    ).toBeEnabled();
    expect(commands).toEqual([]);
    await containment(page);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: info.outputPath(`story-sources-${scheme}.png`),
      fullPage: true
    });
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: info.outputPath(`account-sources-${scheme}.png`),
      fullPage: true
    });
  });
}

test('P45 only asks the missing native required fact; rejected value stays missing', async ({
  page
}, info) => {
  await closeoutWork(page, {
    captures: [{ ...story, status: 'proposed' }],
    ready: false
  });
  await page.route(
    '**/api/tasks/work-orders/73/closeout/captures/12/proposal/',
    (route) =>
      route.fulfill({
        json: {
          ...proposal,
          decisions: proposal.decisions.map((row) =>
            row.field_path === 'result'
              ? { ...row, decision: 'rejected', final_value: null }
              : row
          )
        }
      })
  );
  await openCloseout(page);
  const questions = page.getByTestId('closeout-questions');
  await expect(questions.getByRole('button', { name: /^What / })).toHaveCount(
    1
  );
  await expect(
    questions.getByRole('button', {
      name: 'What was the observed result?',
      exact: true
    })
  ).toBeVisible();
  await questions
    .getByRole('button', { name: 'What was the observed result?', exact: true })
    .click();
  await expect(
    page.getByRole('textbox', { name: 'Action taken', exact: true })
  ).toHaveValue(values.action);
  await expect(
    page.getByRole('textbox', { name: 'Result', exact: true })
  ).toHaveValue('');
  await expect(
    page.getByRole('button', { name: 'Update maintenance record', exact: true })
  ).toBeDisabled();
  await page.evaluate(() => window.scrollTo(0, 0));
  await containment(page);
  await page.screenshot({
    path: info.outputPath('targeted-question.png'),
    fullPage: true
  });
});

test('P45 typed fallback records its reason with the attributed native capture', async ({
  page
}, info) => {
  const { commands } = await closeoutWork(page, { captures: [] });
  let saved = false;
  await page.route(
    '**/api/tasks/work-orders/73/closeout/captures/',
    async (route) => {
      if (route.request().method() === 'POST') {
        commands.push({
          path: '/captures/',
          method: 'POST',
          body: route.request().postDataJSON()
        });
        saved = true;
        const body = route.request().postDataJSON();
        return route.fulfill({
          status: 201,
          json: {
            work_order_id: 73,
            command: 'closeout_capture',
            event_id: 112,
            lifecycle_version: body.expected_version,
            metadata: { capture_id: 13 },
            idempotency_key: body.idempotency_key
          }
        });
      }
      return route.fulfill({
        json: saved
          ? [
              {
                ...story,
                id: 13,
                source_type: 'typed',
                transcript_reference: null,
                voice_unavailable_reason:
                  'Microphone cannot be used in this area',
                narrative:
                  'Found damaged seal; replaced it. Cause remains uncertain.',
                status: 'open',
                revision: 1,
                created_by: 11,
                created_by_name: 'S0 synthetic fixture',
                revisions: [
                  {
                    id: 130,
                    revision: 1,
                    narrative:
                      'Found damaged seal; replaced it. Cause remains uncertain.',
                    source_content_hash: createHash('sha256')
                      .update(
                        'Found damaged seal; replaced it. Cause remains uncertain.'
                      )
                      .digest('hex'),
                    work_order_version: 7,
                    supersedes: null,
                    created_by: 11,
                    created_by_name: 'S0 synthetic fixture',
                    created_at: story.created_at!
                  }
                ]
              }
            ]
          : []
      });
    }
  );
  await openCloseout(page);
  await page.getByText('Edit or add to the capture', { exact: true }).click();
  await page
    .getByRole('textbox', { name: 'What did you do?', exact: true })
    .fill('Found damaged seal; replaced it. Cause remains uncertain.');
  const capture = page.getByRole('button', {
    name: 'Capture narrative',
    exact: true
  });
  await expect(capture).toBeDisabled();
  await page
    .getByRole('textbox', {
      name: 'Why voice is unavailable or unusable',
      exact: true
    })
    .fill('Microphone cannot be used in this area');
  await capture.click();
  await expect(page.getByTestId('prepared-closeout')).toContainText(
    'Microphone cannot be used in this area'
  );
  await expect(page.getByTestId('prepared-closeout')).toContainText(
    'Capture 13 · open · Revision 1'
  );
  await expect(page.getByTestId('prepared-closeout')).toContainText(
    'Recorded by S0 synthetic fixture'
  );
  expect(commands).toHaveLength(1);
  expect(commands[0].body).toMatchObject({
    voice_unavailable_reason: 'Microphone cannot be used in this area',
    narrative: 'Found damaged seal; replaced it. Cause remains uncertain.'
  });
  await expect(page.getByTestId('dictate-closeout')).toHaveCount(0);
  await page.evaluate(() => window.scrollTo(0, 0));
  await containment(page);
  await page.screenshot({
    path: info.outputPath('typed-fallback.png'),
    fullPage: true
  });
});
