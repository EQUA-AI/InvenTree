import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import {
  accepted,
  closeoutWork,
  containment,
  openCloseout,
  story
} from './closeoutFixtures';

nativeBaseline();
// Local entries such as 10:00 are asserted as 07:00Z; pin the browser's zone.
test.use({ timezoneId: 'Asia/Riyadh' });

async function syntheticRecorder(page: Parameters<typeof closeoutWork>[0]) {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'mediaDevices', {
      value: {
        getUserMedia: async () => ({ getTracks: () => [{ stop() {} }] })
      }
    });
    class SyntheticRecorder {
      state = 'inactive';
      mimeType = 'audio/webm';
      ondataavailable: ((event: { data: Blob }) => void) | null = null;
      onstop: (() => void) | null = null;
      start() {
        this.state = 'recording';
      }
      stop() {
        this.state = 'inactive';
        this.ondataavailable?.({
          data: new Blob(['Synthetic microphone original'], {
            type: this.mimeType
          })
        });
        this.onstop?.();
      }
    }
    Object.defineProperty(window, 'MediaRecorder', {
      value: SyntheticRecorder
    });
  });
}

test('O15 microphone bytes stay local until save and recording stops when hidden', async ({
  page
}) => {
  await syntheticRecorder(page);
  await closeoutWork(page, { captures: [] });
  await page.route('**/api/voice/captures/**', (route) =>
    route.fulfill({
      json: { id: 'synthetic-voice', state: 'active', revisions: [] }
    })
  );
  const uploads: string[] = [];
  await page.route('**/capture-media/', (route) => {
    uploads.push(route.request().postData()!);
    return route.fulfill({ status: 201, json: original });
  });
  await openCloseout(page);
  await page.getByText('Edit or add to the capture', { exact: true }).click();
  await page.getByTestId('dictate-closeout').click();
  await page.getByTestId('dictate-consent').click();
  const modal = page.getByTestId('dictate-closeout-modal');
  await modal
    .getByRole('button', { name: 'Record microphone', exact: true })
    .click();
  await expect(
    modal.getByRole('button', { name: 'Stop recording', exact: true })
  ).toBeVisible();
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', {
      configurable: true,
      value: true
    });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect(
    modal.getByRole('button', { name: 'Record microphone', exact: true })
  ).toBeVisible();
  expect(uploads).toEqual([]);
  await modal
    .getByRole('button', { name: 'Save original media', exact: true })
    .click();
  await expect.poll(() => uploads.length).toBe(1);
  expect(uploads[0]).toContain('Synthetic microphone original');
  expect(uploads[0]).toContain('name="started_at"');
  expect(uploads[0]).toContain('name="duration_ms"');
});

for (const origin of ['chosen', 'recorded'] as const) {
  test(`O15 ${origin} original can be discarded and never carries into a new session`, async ({
    page
  }, info) => {
    await syntheticRecorder(page);
    await closeoutWork(page, { captures: [] });
    let session = 0;
    let payload: any;
    await page.route('**/api/voice/captures/**', (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/api/voice/captures/') {
        session++;
        payload = {
          id: `local-session-${session}`,
          state: 'active',
          revisions: []
        };
      } else if (path.endsWith('/revise/')) {
        payload = {
          ...payload,
          state: 'review',
          revisions: [
            {
              id: `revision-${session}`,
              revision: 1,
              full_text: 'Synthetic reviewed story',
              content_hash: 'c'.repeat(64)
            }
          ]
        };
      } else if (path.endsWith('/cancel/'))
        payload = { ...payload, state: 'canceled' };
      return route.fulfill({ json: payload });
    });
    const uploads: string[] = [];
    await page.route('**/capture-media/', (route) => {
      uploads.push(route.request().postData()!);
      return uploads.length === 1
        ? route.fulfill({
            status: 400,
            json: { detail: 'Synthetic refused original' }
          })
        : route.fulfill({
            status: 201,
            json: { ...original, started_at: null, duration_ms: null }
          });
    });
    await openCloseout(page, origin === 'chosen' ? 'light' : 'dark');
    await page.getByText('Edit or add to the capture', { exact: true }).click();
    const modal = page.getByTestId('dictate-closeout-modal');
    const begin = async (retainedDraft = false) => {
      await page.getByTestId('dictate-closeout').click();
      if (retainedDraft)
        await expect(modal).toContainText('Your transcript draft is retained.');
      await page.getByTestId('dictate-consent').click();
    };
    const localMedia = async () => {
      if (origin === 'chosen') {
        await modal.locator('input[type=file]').setInputFiles({
          name: 'old-local.webm',
          mimeType: 'audio/webm',
          buffer: Buffer.from('Old local recording')
        });
      } else {
        await modal
          .getByRole('button', { name: 'Record microphone', exact: true })
          .click();
        await modal
          .getByRole('button', { name: 'Stop recording', exact: true })
          .click();
      }
      await expect(
        modal.getByRole('button', {
          name: 'Discard local recording',
          exact: true
        })
      ).toBeVisible();
      await modal
        .getByLabel('Recording start time (local)', { exact: true })
        .fill('2026-10-20T10:00');
      await modal
        .getByLabel('Recording duration (ms)', { exact: true })
        .fill('1200');
    };
    const review = async () => {
      await modal
        .getByRole('textbox', { name: 'Transcript', exact: true })
        .fill('Synthetic reviewed story');
      await modal.getByTestId('dictate-submit-revision').click();
    };
    await begin();
    await localMedia();
    await review();
    await expect(modal.getByTestId('dictate-accept')).toBeDisabled();
    await modal
      .getByRole('button', {
        name:
          origin === 'chosen' ? 'Clear local media' : 'Discard local recording',
        exact: true
      })
      .click();
    await expect(
      modal.getByLabel('Recording start time (local)', { exact: true })
    ).toHaveValue('');
    await expect(
      modal.getByLabel('Recording duration (ms)', { exact: true })
    ).toHaveValue('');
    await expect(modal.getByTestId('dictate-accept')).toBeEnabled();
    expect(uploads).toEqual([]);
    await page.screenshot({
      animations: 'disabled',
      path: info.outputPath(`discard-${origin}.png`)
    });
    await localMedia();
    await modal
      .getByRole('button', { name: 'Save original media', exact: true })
      .click();
    await expect(modal).toContainText('Original media was not confirmed saved');
    await page.keyboard.press('Escape');
    await expect(modal).not.toBeVisible();
    await begin(true);
    await expect(modal).not.toContainText(
      'Original media was not confirmed saved'
    );
    await expect(
      modal.getByLabel('Recording start time (local)', { exact: true })
    ).toHaveValue('');
    await expect(
      modal.getByLabel('Recording duration (ms)', { exact: true })
    ).toHaveValue('');
    await expect(
      modal.getByRole('button', { name: 'Save original media', exact: true })
    ).toBeDisabled();
    await expect(
      modal.getByRole('textbox', { name: 'Transcript', exact: true })
    ).toHaveValue('Synthetic reviewed story');
    await review();
    await expect(modal.getByTestId('dictate-accept')).toBeEnabled();
    await modal.locator('input[type=file]').setInputFiles({
      name: 'new-session.webm',
      mimeType: 'audio/webm',
      buffer: Buffer.from('New local original')
    });
    await modal
      .getByRole('button', { name: 'Save original media', exact: true })
      .click();
    await expect(modal).toContainText('Download original:');
    expect(uploads).toHaveLength(2);
    expect(uploads[1]).toContain('local-session-2');
    expect(uploads[1]).toContain('New local original');
    expect(uploads[1]).not.toContain('Old local recording');
    expect(uploads[1]).not.toContain('name="started_at"');
    expect(uploads[1]).not.toContain('name="duration_ms"');
    await page.screenshot({
      animations: 'disabled',
      path: info.outputPath(`new-session-${origin}.png`)
    });
  });
}

const original = {
  id: 91,
  filename: 'Synthetic recording.webm',
  content_type: 'audio/webm',
  content_hash: 'd'.repeat(64),
  byte_size: 8,
  started_at: '2026-10-20T07:00:00Z',
  duration_ms: 1200,
  url: '/api/tasks/work-orders/73/capture-media/91/'
};
const text = (revision: number) =>
  `Synthetic frozen report ${revision}\nOriginal observed result\nMaterials at completion: original seal\nOperating authority: Not recorded · 測定`;
const reportDocument = (revision: number) => ({ revision, synthetic: true });
const canonical = (revision: number) =>
  JSON.stringify({ document: reportDocument(revision), text: text(revision) });
const reports = [1, 2].map((revision) => ({
  id: 600 + revision,
  revision,
  url: `/api/tasks/work-orders/73/closeout/reports/${600 + revision}/`,
  content_hash: createHash('sha256')
    .update(canonical(revision), 'utf8')
    .digest('hex'),
  completed_by: 3,
  completed_by_name: 'Synthetic Technician',
  completed_at: accepted!.completed_at,
  frozen_by: revision === 2 ? 4 : 3,
  frozen_by_name:
    revision === 2 ? 'Synthetic Supervisor' : 'Synthetic Technician',
  frozen_at: revision === 2 ? '2026-10-19T10:05:00Z' : accepted!.completed_at,
  verified_by: null,
  verified_by_name: null,
  verified_at: null,
  amendment_id: revision === 2 ? 40 : null
}));

async function setup(
  page: Parameters<typeof closeoutWork>[0],
  verified = false
) {
  const { setOverview, commands } = await closeoutWork(page, {
    completed: true,
    captures: [
      {
        ...story,
        originals: [{ ...original, started_at: '2026-10-19T07:00:00Z' }],
        source_spans: [
          {
            original_id: 91,
            transcript_revision: 'synthetic-revision',
            narrative_code_points: [0, 20],
            segments: []
          }
        ],
        status: 'consumed',
        completed_closeout: 301
      }
    ]
  });
  // Preserve the fixture's native account, adding only report navigation.
  setOverview({
    structured_closeout: {
      ...accepted!,
      reports,
      ...(verified
        ? {
            verified_by: 8,
            verified_by_name: 'Synthetic Native Verifier',
            verified_at: '2026-10-20T10:00:00Z'
          }
        : {})
    }
  });
  await page.route('**/closeout/reports/*/', (route) => {
    const id = Number(
      new URL(route.request().url()).pathname.split('/').at(-2)
    );
    const report = reports.find((row) => row.id === id)!;
    return route.fulfill({
      json: {
        ...report,
        ...(verified
          ? {
              verified_by: 8,
              verified_by_name: 'Synthetic Native Verifier',
              verified_at: '2026-10-20T10:00:00Z'
            }
          : {}),
        document: reportDocument(report.revision),
        text: text(report.revision),
        canonical_export: canonical(report.revision)
      }
    });
  });
  return commands;
}

for (const scheme of ['light', 'dark'] as const) {
  test(`O15 dictation retains original upload before acceptance (${scheme})`, async ({
    page
  }, info) => {
    await page.addInitScript(() => {
      Object.defineProperty(navigator, 'mediaDevices', {
        value: {
          getUserMedia: async () => {
            throw new DOMException(
              'Synthetic microphone denial',
              'NotAllowedError'
            );
          }
        }
      });
    });
    await closeoutWork(page, { captures: [] });
    let payload: any = {
      id: 'synthetic-voice',
      state: 'active',
      revisions: []
    };
    await page.route('**/api/voice/captures/**', async (route) => {
      if (route.request().url().endsWith('/api/voice/captures/'))
        expect(route.request().postDataJSON().consent_version).toBe(
          'closeout-originals-v1'
        );
      if (route.request().url().endsWith('/revise/'))
        payload = {
          ...payload,
          state: 'review',
          revisions: [
            {
              id: 'r1',
              revision: 1,
              full_text: 'Synthetic reviewed story',
              content_hash: 'c'.repeat(64)
            }
          ]
        };
      return route.fulfill({ json: payload });
    });
    let body = '';
    await page.route('**/capture-media/', async (route) => {
      body = route.request().postData()!;
      expect(route.request().headers()['content-type']).toContain(
        'multipart/form-data; boundary='
      );
      return route.fulfill({ status: 201, json: original });
    });
    await openCloseout(page, scheme);
    await page.getByText('Edit or add to the capture', { exact: true }).click();
    await page.getByTestId('dictate-closeout').click();
    await expect(page.getByTestId('dictate-closeout-modal')).toContainText(
      'Saved originals are retained'
    );
    await expect(page.getByTestId('dictate-closeout-modal')).not.toContainText(
      'never keeps a recording'
    );
    await page.screenshot({
      path: info.outputPath(`original-consent-${scheme}.png`),
      animations: 'disabled',
      fullPage: false
    });
    await page.getByTestId('dictate-consent').click();
    const modal = page.getByTestId('dictate-closeout-modal');
    await modal
      .getByRole('button', { name: 'Record microphone', exact: true })
      .click();
    await expect(modal).toContainText('Microphone recording is unavailable');
    await modal.locator('input[type=file]').setInputFiles({
      name: 'recording.webm',
      mimeType: 'audio/webm',
      buffer: Buffer.from('original')
    });
    await modal
      .getByRole('textbox', { name: 'Transcript', exact: true })
      .fill('Synthetic reviewed story');
    await modal.getByTestId('dictate-submit-revision').click();
    await expect(modal.getByTestId('dictate-accept')).toBeDisabled();
    await modal
      .getByLabel('Recording start time (local)', { exact: true })
      .fill('2026-10-20T10:00');
    await modal
      .getByLabel('Recording duration (ms)', { exact: true })
      .fill('1200');
    await modal.locator('input[type=file]').setInputFiles({
      name: 'replacement-recording.webm',
      mimeType: 'audio/webm',
      buffer: Buffer.from('original')
    });
    await expect(
      modal.getByLabel('Recording start time (local)', { exact: true })
    ).toHaveValue('');
    await expect(
      modal.getByLabel('Recording duration (ms)', { exact: true })
    ).toHaveValue('');
    await modal
      .getByLabel('Recording start time (local)', { exact: true })
      .fill('2026-10-20T10:00');
    await modal
      .getByLabel('Recording duration (ms)', { exact: true })
      .fill('1200');
    await page.screenshot({
      path: info.outputPath(`original-upload-pending-${scheme}.png`),
      animations: 'disabled',
      fullPage: false
    });
    await modal
      .getByRole('button', { name: 'Save original media', exact: true })
      .click();
    await expect(modal.getByTestId('dictate-accept')).toBeEnabled();
    expect(body).toContain('original');
    expect(body).toContain('2026-10-20T07:00:00.000Z');
    expect(body).toContain('1200');
    await page.screenshot({
      path: info.outputPath(`original-upload-${scheme}.png`),
      animations: 'disabled',
      fullPage: false
    });
  });
  test(`O15 frozen report and original sources (${scheme})`, async ({
    page
  }, info) => {
    const commands = await setup(page);
    await openCloseout(page, scheme);
    const frozen = page.getByTestId('accepted-report');
    await frozen.getByText('Read frozen report', { exact: true }).click();
    await expect(frozen).toContainText(text(1));
    await expect(frozen).toContainText(
      'Completed by Synthetic Technician · 19 Oct 2026, 12:00 +03:00'
    );
    await expect(frozen).toContainText('Acceptance: Not recorded');
    await expect(frozen).not.toContainText('Accepted by');
    await expect(page.getByTestId('prepared-closeout')).not.toContainText(
      'Accepted by'
    );
    await expect(frozen).toContainText(
      'SHA-256 of verifiable snapshot (.json)'
    );
    await expect(frozen).toContainText(
      'exact UTF-8 canonical JSON download containing both document and text'
    );
    await page.getByText('Original source spans', { exact: true }).click();
    await expect(page.getByTestId('prepared-closeout')).toContainText(
      'synthetic-revision'
    );
    await expect(page.getByTestId('prepared-closeout')).toContainText(
      'Duration (ms): 1200'
    );
    await expect(page.getByTestId('prepared-closeout')).toContainText(
      'Recording started: 19 Oct 2026, 10:00 +03:00'
    );
    await expect(page.getByTestId('prepared-closeout')).toContainText(
      'Audio segment timing was not supplied.'
    );
    await expect(page.getByTestId('prepared-closeout')).not.toContainText(
      '"original_id"'
    );
    await containment(page);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: info.outputPath(`accepted-original-${scheme}.png`),
      animations: 'disabled',
      fullPage: true
    });
    await frozen.evaluate((element) =>
      window.scrollTo(
        0,
        element.getBoundingClientRect().top + window.scrollY - 180
      )
    );
    await page.screenshot({
      animations: 'disabled',
      path: info.outputPath(`frozen-report-detail-${scheme}.png`)
    });
    const download = page.waitForEvent('download');
    await frozen
      .getByRole('button', {
        name: 'Download report text (.txt)',
        exact: true
      })
      .click();
    const file = await download;
    expect(file.suggestedFilename()).toBe('work-73-frozen-report-601.txt');
    const exported = info.outputPath('accepted-report.txt');
    await file.saveAs(exported);
    expect(await readFile(exported, 'utf8')).toBe(text(1));
    const jsonDownload = page.waitForEvent('download');
    const jsonButton = frozen.getByRole('button', {
      name: 'Download verifiable JSON',
      exact: true
    });
    expect(
      await jsonButton
        .locator('.mantine-Button-label')
        .evaluate((label) => label.scrollWidth <= label.clientWidth + 1),
      'Verifiable download label fits its button at both widths'
    ).toBe(true);
    await jsonButton.click();
    const jsonPath = info.outputPath('accepted-snapshot.json');
    const jsonFile = await jsonDownload;
    expect(jsonFile.suggestedFilename()).toBe('work-73-frozen-report-601.json');
    await jsonFile.saveAs(jsonPath);
    const bytes = await readFile(jsonPath);
    expect(JSON.parse(bytes.toString('utf8'))).toEqual({
      document: reportDocument(1),
      text: text(1)
    });
    expect(bytes.toString('utf8')).toBe(canonical(1));
    expect(createHash('sha256').update(bytes).digest('hex')).toBe(
      reports[0].content_hash
    );
    await page.getByRole('tab', { name: 'History', exact: true }).click();
    const history = page.getByRole('tabpanel', {
      name: 'History',
      exact: true
    });
    const links = history.getByTestId('accepted-report-links');
    await expect(links).toContainText('Frozen closeout reports');
    await containment(page);
    await links.evaluate((element) =>
      window.scrollTo(
        0,
        element.getBoundingClientRect().top + window.scrollY - 180
      )
    );
    await page.screenshot({
      animations: 'disabled',
      path: info.outputPath(`history-report-links-${scheme}.png`)
    });
    await links
      .getByRole('link', { name: 'Frozen report · Revision 2', exact: true })
      .click();
    await expect(page.getByTestId('destination')).toContainText('report=602');
    await expect(frozen).toContainText(text(2));
    await expect(frozen).toContainText('Completed by Synthetic Technician');
    await expect(frozen).toContainText(
      'Revision recorded by Synthetic Supervisor'
    );
    expect(commands).toEqual([]);
  });
}

test('O15 refused snapshot read hides the report and disables exports', async ({
  page
}) => {
  await setup(page);
  await page.route('**/closeout/reports/*/', (route) =>
    route.fulfill({ status: 403, json: { detail: 'Synthetic denied report' } })
  );
  await openCloseout(page);
  const frozen = page.getByTestId('accepted-report');
  await expect(frozen).toContainText('No cached report is shown');
  await expect(
    frozen.getByRole('button', {
      name: 'Download report text (.txt)',
      exact: true
    })
  ).toBeDisabled();
  await expect(frozen).not.toContainText('Materials at completion');
  await expect(
    frozen.getByRole('button', {
      name: 'Download verifiable JSON',
      exact: true
    })
  ).toBeDisabled();
  await expect(frozen).toContainText('Acceptance information unavailable');
});

test('O15 native verification is separately attributed without implying closure acceptance', async ({
  page
}, info) => {
  await setup(page, true);
  await openCloseout(page);
  const frozen = page.getByTestId('accepted-report');
  await expect(frozen).toContainText('Completed by Synthetic Technician');
  await expect(frozen).toContainText(
    'Verified by Synthetic Native Verifier · 20 Oct 2026, 13:00 +03:00'
  );
  await expect(frozen).not.toContainText('Accepted by');
  await expect(frozen).not.toContainText('Acceptance: Not recorded');
  await expect(page.getByTestId('prepared-closeout')).toContainText(
    'Verified by Synthetic Native Verifier'
  );
  await frozen.evaluate((element) =>
    window.scrollTo(
      0,
      element.getBoundingClientRect().top + window.scrollY - 180
    )
  );
  await page.screenshot({
    animations: 'disabled',
    path: info.outputPath('native-verification.png')
  });
});

test('O15 absent original timing remains unrecorded and original bytes download', async ({
  page
}, info) => {
  await closeoutWork(page, {
    captures: [
      {
        ...story,
        originals: [{ ...original, duration_ms: null, started_at: null }]
      }
    ]
  });
  await page.route('**/capture-media/91/', (route) =>
    route.fulfill({ body: 'original', contentType: 'audio/webm' })
  );
  await openCloseout(page);
  await expect(page.getByTestId('prepared-closeout')).toContainText(
    'Recording started: Not recorded · Duration (ms): Not recorded'
  );
  const download = page.waitForEvent('download');
  await page
    .getByRole('button', {
      name: 'Download original: Synthetic recording.webm',
      exact: true
    })
    .click();
  const exported = info.outputPath('original.webm');
  await (await download).saveAs(exported);
  expect(await readFile(exported, 'utf8')).toBe('original');
});
