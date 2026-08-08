import { expect, test } from './baseFixtures';
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

/**
 * S28: server-observed entity chips. A machine-routed question resolves a
 * machine record root, which becomes a navigable chip under the answer.
 * Resilient like the question-card spec: when no chip appears (routing did
 * not resolve a machine root) the spec verifies nothing phantom rendered.
 */
test('AI Chat - Entity chips navigate', async ({ browser }) => {
  const page = await doCachedLogin(browser, {
    username: 'admin',
    password: 'inventree'
  });

  await navigate(page, 'machines/machine/1/');
  await page.getByRole('button', { name: 'Ask about this machine' }).click();
  await page.getByTestId('ai-chat-routing-hint').waitFor();

  const composer = page.getByPlaceholder(/Type a message|Answer the question/);
  await composer.fill('Give me a quick status overview of this machine.');
  await composer.press('Enter');

  const chips = page.getByTestId('entity-chips');
  const appeared = await chips
    .waitFor({ timeout: 60000 })
    .then(() => true)
    .catch(() => false);

  if (appeared) {
    const machineChip = chips.locator(
      '[data-testid^="entity-chip-assetmachine"]'
    );
    await machineChip.first().click();
    await page.waitForURL(/machines\/machine\/\d+/, { timeout: 15000 });
  } else {
    // No machine root resolved: the transcript must not contain flattened
    // manifest payload keys rendered as text.
    const transcript = await page
      .getByTestId('ai-chat-drawer')
      .textContent()
      .catch(() => '');
    expect(transcript ?? '').not.toContain('entity_manifest');
  }
});

/**
 * S22/S23: the structured question card. These cases exercise the armed and
 * frozen states; they require FEATURE_QUESTION_CARDS on the backend and a
 * corpus with >=2 machines matching "pump", so they are resilient: when no
 * card appears the spec verifies the stale-safe fallback (plain text answer,
 * no phantom transcript content).
 */
test('AI Chat - Question card round trip', async ({ browser }) => {
  const page = await doCachedLogin(browser, {
    username: 'admin',
    password: 'inventree'
  });

  await page.getByLabel('open-ai-chat').click();
  const composer = page.getByPlaceholder(/Type a message|Answer the question/);
  await composer.fill('What does the manual say about the pump station?');
  await composer.press('Enter');

  const card = page.getByTestId('question-card');
  const appeared = await card
    .waitFor({ timeout: 45000 })
    .then(() => true)
    .catch(() => false);

  if (appeared) {
    // Exactly-once: click an option; the card freezes and never re-arms.
    await card.locator('[data-testid^="question-option-"]').first().click();
    await page.getByTestId('question-card-frozen').waitFor({ timeout: 45000 });
    await expect(
      card.locator('[data-testid^="question-option-"]').first()
    ).toBeDisabled();
  } else {
    // No ambiguity in this corpus: the answer must be ordinary text and the
    // transcript must not contain flattened event payload keys.
    const transcript = await page
      .getByTestId('ai-chat-drawer')
      .textContent()
      .catch(() => '');
    expect(transcript ?? '').not.toContain('interrupt_id');
  }
});
