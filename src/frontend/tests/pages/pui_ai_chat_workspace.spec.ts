/** Geometry, keyboard and compact voice checks using the localhost fixture. */
import { expect, test } from '@playwright/test';
import { loadDrawerFixture, prepareDrawerFixture } from './ai_drawer_harness';
import { startVoice } from './voice_harness';

test('desktop width clamps to the viewport and keyboard resizing preserves the saved preference across fullscreen', async ({
  page
}) => {
  await page.setViewportSize({ width: 1100, height: 800 });
  await page.addInitScript(() =>
    localStorage.setItem('ai-chat-drawer-width', JSON.stringify(900))
  );
  await prepareDrawerFixture(page, { voice: false });
  await loadDrawerFixture(page);
  const drawer = page.getByTestId('ai-chat-drawer');
  const separator = page.getByRole('separator', { name: 'Resize chat panel' });
  expect((await drawer.boundingBox())!.width).toBe(780);
  await expect(separator).toHaveAttribute('aria-valuenow', '780');
  await separator.focus();
  await page.keyboard.press('Home');
  await expect(separator).toHaveAttribute('aria-valuenow', '340');
  await page.keyboard.press('ArrowLeft');
  await expect(separator).toHaveAttribute('aria-valuenow', '364');
  await page.getByRole('button', { name: 'Expand chat', exact: true }).click();
  await expect(drawer).toHaveAttribute('aria-modal', 'true');
  await expect(separator).toHaveCount(0);
  expect((await drawer.boundingBox())!.width).toBe(1100);
  await page.getByRole('button', { name: 'Restore chat width' }).click();
  await expect(separator).toHaveAttribute('aria-valuenow', '364');
  await expect(drawer).toHaveAttribute('role', 'complementary');
});
test('closing during a pointer resize restores the original body cursor and selection styles', async ({
  page
}) => {
  await page.setViewportSize({ width: 1440, height: 800 });
  await prepareDrawerFixture(page, { voice: false });
  await loadDrawerFixture(page);
  await page.evaluate(() => {
    document.body.style.cursor = 'crosshair';
    document.body.style.userSelect = 'text';
  });
  const box = (await page.getByRole('separator').boundingBox())!;
  await page.getByRole('separator').focus();
  await page.mouse.move(box.x + 2, box.y + 30);
  await page.mouse.down();
  await page.mouse.move(box.x - 40, box.y + 30);
  await expect
    .poll(() => page.evaluate(() => document.body.style.cursor))
    .toBe('col-resize');
  await page.keyboard.press('Escape');
  await expect
    .poll(() =>
      page.evaluate(() => [
        document.body.style.cursor,
        document.body.style.userSelect
      ])
    )
    .toEqual(['crosshair', 'text']);
  await page.mouse.up();
});
test.describe('touch controls', () => {
  test.use({ hasTouch: true });
  for (const width of [320, 390, 768]) {
    test(`short ${width}px modal keeps text, send, voice controls and options reachable`, async ({
      page
    }, info) => {
      await page.setViewportSize({ width, height: 480 });
      await page.emulateMedia({
        colorScheme: width === 320 ? 'dark' : 'light',
        reducedMotion: 'reduce'
      });
      await prepareDrawerFixture(page);
      await loadDrawerFixture(page);
      const drawer = page.getByTestId('ai-chat-drawer');
      await expect(drawer).toHaveAttribute('aria-modal', 'true');
      await expect(page.getByRole('separator')).toHaveCount(0);
      const editor = page.getByRole('textbox', {
        name: 'Message',
        exact: true
      });
      await editor.fill('Keep this typed draft separate from voice.');
      await startVoice(page);
      await expect(page.getByTestId('voice-end')).toBeVisible();
      await page.getByTestId('voice-mute').click();
      await expect(page.getByTestId('voice-mute')).toHaveAccessibleName(
        'Unmute microphone'
      );
      await page
        .getByRole('button', { name: 'Voice options', exact: true })
        .click();
      const options = page.getByRole('dialog', { name: 'Voice options' });
      await expect(options).toBeVisible();
      expect(
        await options.evaluate((element) => !!element.closest('[inert]'))
      ).toBe(false);
      await page.keyboard.press('Escape');
      await expect(options).toHaveCount(0);
      await expect(drawer).toHaveAttribute('aria-modal', 'true');
      await expect(editor).toHaveValue(
        'Keep this typed draft separate from voice.'
      );
      await editor.focus();
      await expect(editor).toBeInViewport();
      expect(
        await editor.evaluate(
          (element) => element.scrollHeight - element.clientHeight
        )
      ).toBeLessThanOrEqual(2);
      expect(
        await page
          .getByTestId('voice-end')
          .evaluate((element) => element.getBoundingClientRect().height)
      ).toBeGreaterThanOrEqual(44);
      await page.getByTestId('voice-end').focus();
      await expect(page.getByTestId('voice-end')).toBeInViewport();
      const overflow = await drawer.evaluate(
        (element) => element.scrollWidth > element.clientWidth
      );
      expect(overflow).toBe(false);
      await page.screenshot({
        path: info.outputPath(`workspace-${width}.png`)
      });
    });
  }
});

test('visual viewport resize and pan keep the composer inside the visible keyboard viewport', async ({
  page
}) => {
  await page.setViewportSize({ width: 390, height: 740 });
  await page.addInitScript(() => {
    const viewport = Object.assign(new EventTarget(), {
      width: 390,
      height: 740,
      offsetTop: 0,
      offsetLeft: 0
    });
    Object.defineProperty(window, 'visualViewport', {
      configurable: true,
      value: viewport
    });
  });
  await prepareDrawerFixture(page, { voice: false });
  await loadDrawerFixture(page);
  await page.evaluate(() => {
    Object.assign(window.visualViewport!, {
      width: 280,
      height: 320,
      offsetTop: 60,
      offsetLeft: 40
    });
    window.visualViewport!.dispatchEvent(new Event('resize'));
    window.visualViewport!.dispatchEvent(new Event('scroll'));
  });
  const drawer = page.getByTestId('ai-chat-drawer');
  await expect
    .poll(async () => {
      const box = (await drawer.boundingBox())!;
      return [box.x, box.y, box.width, box.height];
    })
    .toEqual([40, 60, 280, 320]);
  const editor = page.getByRole('textbox', { name: 'Message', exact: true });
  await editor.fill('Keep the keyboard composer usable.');
  await editor.focus();
  const send = (await page.getByTestId('send-ai-chat-message').boundingBox())!;
  expect(send.y + send.height).toBeLessThanOrEqual(380);
  await expect(editor).toBeEditable();
});

test('RTL with a long confirmed target keeps the compact viewport and picker usable', async ({
  page
}) => {
  await page.setViewportSize({ width: 640, height: 480 });
  await page.addInitScript(() => {
    document.documentElement.dir = 'rtl';
  });
  const label = 'مضخة محطة معالجة المياه '.repeat(12);
  await prepareDrawerFixture(page, {
    voice: false,
    foundation: {
      focus: {
        focus: {
          kind: 'machine',
          id: 7,
          label,
          machine_id: 7,
          work_order_id: null
        }
      }
    }
  });
  await loadDrawerFixture(page);
  await expect(page.getByTestId('conversation-focus-chip')).toContainText(
    label.trim()
  );
  const drawer = page.getByTestId('ai-chat-drawer');
  expect(
    await drawer.evaluate(
      (element) => element.scrollWidth > element.clientWidth
    )
  ).toBe(false);
  await page.getByTestId('conversation-focus-change').click();
  const picker = page.getByRole('dialog', {
    name: 'What are we talking about?'
  });
  await expect(picker).toBeVisible();
  await page.getByTestId('conversation-focus-search').focus();
  await expect(page.getByTestId('conversation-focus-search')).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(picker).toHaveCount(0);
  await page
    .getByRole('textbox', { name: 'Message', exact: true })
    .fill('Keep the compact composer usable.');
  await expect(page.getByTestId('send-ai-chat-message')).toBeEnabled();
});
