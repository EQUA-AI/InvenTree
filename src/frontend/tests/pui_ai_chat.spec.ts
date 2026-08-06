import { test } from './baseFixtures';
import { navigate } from './helpers';
import { doCachedLogin } from './login';

/**
 * S14 B5: the machine page opens the main chat drawer with a visible
 * routing hint — the only machine-scoped chat entry point after the
 * scoped-chat rail was deleted.
 */
test('AI Chat - Ask about this machine', async ({ browser }) => {
  const page = await doCachedLogin(browser, {
    username: 'admin',
    password: 'inventree'
  });

  await navigate(page, 'machines/machine/1/');
  await page.getByRole('button', { name: 'Ask about this machine' }).click();

  // Drawer opens with the machine preloaded as a dismissible hint chip.
  await page.getByTestId('ai-chat-routing-hint').waitFor();
  await page.getByLabel('dismiss-routing-hint').click();
  await page.getByTestId('ai-chat-routing-hint').waitFor({ state: 'detached' });
});

/**
 * S20 A8: the history tab searches the durable conversation ledger.
 */
test('AI Chat - History search', async ({ browser }) => {
  const page = await doCachedLogin(browser, {
    username: 'admin',
    password: 'inventree'
  });

  await page.getByLabel('open-ai-chat').click();
  await page.getByRole('tab', { name: 'History' }).click();

  const search = page.getByLabel('search-ai-chat-history');
  await search.waitFor();
  await search.fill('zz-no-thread-matches-this-zz');
  await page.getByText('No conversations match').waitFor();
});
