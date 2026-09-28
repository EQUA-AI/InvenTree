/**
 * REAL demo-metrics acceptance: PostgreSQL -> Django -> browser.
 *
 * This is the real local E2E the mocked suites (pui_demo_metrics.spec.ts) say
 * they are not: a real Django server on a dedicated loopback port backed by
 * the dedicated PostgreSQL database `inventree_dm_e2e_v3`, with the EQUA demo
 * dataset applied ONLY through the governed apply command
 * (`manage.py apply_demo_metrics`, approved SHA-256 plan hash — an
 * approval/integrity hash over the canonical plan body, NOT a cryptographic
 * signature) and real identity/scope mapping (`ClientScopeGrant` rows +
 * `granted_client_scope_resolver`).
 *
 * Honesty rules pinned here:
 * - No page.route / route.fulfill anywhere: every demo-metrics, work-order and
 *   history response comes from the real backend over the network. The spec
 *   records the real API responses it observed as evidence.
 * - Real auth only: the disposable users are created by the harness with
 *   runtime-generated passwords (never in source), and every page performs the
 *   real Django session login flow against the real server.
 * - History must stay honest: values only where coverage was imported, and
 *   `null` (never zero) where nothing was measured; a session without imported
 *   history is "unavailable", never zeroed.
 * - API denial must be an explicit real 403 from the scope control, not a UI
 *   pretense.
 * - Viewport honesty: the `desktop` project asserts the desktop UI panels;
 *   the `Pixel 7` project asserts the real MOBILE SHELL plus wire-level
 *   scoped API data only — it is not a desktop-UI check.
 *
 * Run: contrib/container/demo-metrics-e2e.sh
 *   (npx playwright test --config=playwright/tests/playwright.demo-metrics-e2e.config.ts)
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { type Page, expect, test } from '@playwright/test';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const STATE_DIR = process.env.DM_E2E_STATE_DIR ?? '';
const RESULTS_DIR = path.join(HERE, 'demo-metrics-e2e-results');

interface RunState {
  database: string;
  sessions: {
    hist: { id: string; session_key: string; anchor_at: string };
    nohist: { id: string; session_key: string; anchor_at: string };
  };
  locations_hist: Record<string, number>;
  machines_hist: Record<string, number>;
  locations_nohist: Record<string, number>;
  machines_nohist: Record<string, number>;
  users: { operator: string; denied: string };
}

interface RunCreds {
  operator: { username: string; password: string };
  denied: { username: string; password: string };
}

function loadJson<T>(name: string): T {
  const file = path.join(STATE_DIR, name);
  if (!STATE_DIR || !fs.existsSync(file)) {
    throw new Error(
      `Missing ${file}. Run contrib/container/demo-metrics-e2e.sh (setup writes state + credentials).`
    );
  }
  return JSON.parse(fs.readFileSync(file, 'utf8')) as T;
}

const state = loadJson<RunState>('state.json');
const creds = loadJson<RunCreds>('credentials.json');

test.beforeAll(() => {
  fs.mkdirSync(RESULTS_DIR, { recursive: true });
});

/**
 * Sanitized auth diagnostics: response paths, statuses and cookie-change
 * booleans only. Cookie / password / token VALUES are compared internally and
 * never logged or written to disk.
 */
interface DiagEvent {
  seq: number;
  t: number;
  event: string;
  path?: string;
  status?: number;
  setsSessionCookie?: boolean;
  sessionCookiePresent?: boolean;
  sessionCookieChanged?: boolean;
}

function pathOnly(url: string): string {
  try {
    return new URL(url).pathname;
  } catch {
    return '<unparseable>'; // codespell:ignore unparseable
  }
}

function setsSessionId(setCookieHeader: string | undefined): boolean {
  return /(?:^|[;,\s])sessionid=/.test(setCookieHeader ?? '');
}

/**
 * Real auth against the real server: the harness establishes a real Django
 * session through the app's own login endpoint (allauth headless) using the
 * disposable credentials, then the browser rides that session. The password
 * is never typed into the DOM, so it can never end up in a failure artifact.
 */
async function realLogin(page: Page, who: 'operator' | 'denied') {
  const { username, password } = creds[who];

  const events: DiagEvent[] = [];
  const t0 = Date.now();
  let seq = 0;
  let sessionValue: string | undefined; // compared internally; never logged
  let guardArmed = false;
  let clobbers = 0;

  const note = (event: string, extra: Partial<DiagEvent> = {}) => {
    events.push({ seq: seq++, t: Date.now() - t0, event, ...extra });
  };
  const snapshotCookies = async (label: string) => {
    const cookies = await page.context().cookies();
    const sid = cookies.find((cookie) => cookie.name === 'sessionid')?.value;
    const changed = sid !== sessionValue;
    if (changed && guardArmed) {
      clobbers += 1;
      note('session-clobber', {
        sessionCookiePresent: sid !== undefined,
        sessionCookieChanged: true
      });
    }
    sessionValue = sid;
    note(label, {
      sessionCookiePresent: sid !== undefined,
      sessionCookieChanged: changed
    });
    return changed;
  };
  const onResponse = async (response: any) => {
    const setCookie = (await response.headersArray())
      .filter((h) => h.name.toLowerCase() === 'set-cookie')
      .map((h) => h.value)
      .join('\n');
    const sets = setsSessionId(setCookie);
    note('response', {
      path: pathOnly(response.url()),
      status: response.status(),
      setsSessionCookie: sets
    });
    if (sets) await snapshotCookies('cookie-after-set');
  };
  page.on('response', onResponse);
  const onRequest = (request: any) => {
    note('request', { path: pathOnly(request.url()) });
  };
  const onRequestFinished = (request: any) => {
    note('requestfinished', { path: pathOnly(request.url()) });
  };
  const onRequestFailed = (request: any) => {
    note('requestfailed', { path: pathOnly(request.url()) });
  };
  page.on('request', onRequest);
  page.on('requestfinished', onRequestFinished);
  page.on('requestfailed', onRequestFailed);
  const poll = setInterval(() => {
    page
      .context()
      .cookies()
      .then((cookies) => {
        const sid = cookies.find(
          (cookie) => cookie.name === 'sessionid'
        )?.value;
        if (sid === sessionValue) return;
        if (guardArmed) {
          clobbers += 1;
          note('session-clobber', {
            sessionCookiePresent: sid !== undefined,
            sessionCookieChanged: true
          });
        }
        sessionValue = sid;
        note('cookie-change', {
          sessionCookiePresent: sid !== undefined,
          sessionCookieChanged: true
        });
      })
      .catch(() => {});
  }, 50);
  const dump = (suffix: string) => {
    dumped = true;
    fs.writeFileSync(
      path.join(RESULTS_DIR, `auth-diag-${who}-${suffix}.json`),
      JSON.stringify(events, null, 2)
    );
  };
  let dumped = false;

  try {
    // The SPA page load issues the CSRF cookie the login POST must echo.
    await page.goto('/web/login', { waitUntil: 'domcontentloaded' });
    await snapshotCookies('after-login-page-load');
    const csrf = (await page.context().cookies()).find(
      (cookie) => cookie.name === 'csrftoken'
    )?.value;
    expect(csrf).toBeTruthy();

    const login = await page.request.post('/api/auth/v1/auth/login', {
      headers: {
        'X-Csrftoken': csrf ?? '',
        Origin: new URL(page.url()).origin
      },
      data: { username, password }
    });
    {
      const headers = login.headers();
      note('login-post', {
        path: '/api/auth/v1/auth/login',
        status: login.status(),
        setsSessionCookie: setsSessionId(headers['set-cookie'])
      });
      await snapshotCookies('cookie-after-login');
    }
    expect(login.status()).toBe(200);
    // Record cookie changes after confirmed login. A change (or its absence)
    // is diagnostic evidence, not proof of the cause of a later auth failure.
    guardArmed = true;

    // The real session is now live for the browser: prove it with the real
    // user endpoint, then enter the authenticated app shell. The app's startup
    // session check has a bounded budget, so a cold/slow backend can land on the
    // login page even though the session cookie is live — reload while the real
    // session still answers 200 (a genuine lost session fails immediately).
    const me = await page.request.get('/api/user/me/');
    note('me-after-login', {
      path: '/api/user/me/',
      status: me.status(),
      setsSessionCookie: setsSessionId(me.headers()['set-cookie'])
    });
    expect(me.status()).toBe(200);

    const appReady = page
      .getByRole('link', { name: 'Dashboard' })
      .or(page.getByText('Mobile viewport detected'));
    for (let attempt = 0; attempt < 10; attempt++) {
      await page.goto('/web/home', { waitUntil: 'networkidle' });
      if (await appReady.isVisible().catch(() => false)) break;
      const alive = await page.request.get('/api/user/me/');
      note('alive-check', {
        path: '/api/user/me/',
        status: alive.status(),
        setsSessionCookie: setsSessionId(alive.headers()['set-cookie'])
      });
      expect(
        alive.status(),
        clobbers > 0
          ? 'authentication failed after an observed session-cookie change (see auth-diag)'
          : 'authentication failed with no session-cookie change observed; cause undetermined (see auth-diag)'
      ).toBe(200); // bounce, not a lost session
      await page.waitForTimeout(3_000);
    }
    await appReady.waitFor({ timeout: 30_000 });
    // Unexpected cookie rotation during this login-only flow is a separate
    // diagnostic failure; it does not identify which component caused it.
    expect(
      clobbers,
      'unexpected session-cookie change after confirmed login (see auth-diag)'
    ).toBe(0);
  } catch (error) {
    dump(`fail-${Date.now()}`);
    throw error;
  } finally {
    if (!dumped) dump(`ok-${Date.now()}`);
    clearInterval(poll);
    page.off('response', onResponse);
    page.off('request', onRequest);
    page.off('requestfinished', onRequestFinished);
    page.off('requestfailed', onRequestFailed);
  }
}

function panelUrl(sessionId: string, locationId?: number): string {
  const params = new URLSearchParams({ demo_session: sessionId });
  if (locationId !== undefined) params.set('location', String(locationId));
  return `/web/machines/index/sites/?${params.toString()}`;
}

/**
 * Navigate to an app route with a bounded retry for the app's own known
 * cold-route behavior: the startup session check has a limited budget and a
 * cold backend can land the SPA on the login page once even though the real
 * session cookie is live. The retry only triggers while `/api/user/me/`
 * still answers 200 for that session — a genuine lost session or denial
 * fails immediately and is never retried away.
 */
async function gotoAppRoute(page: Page, url: string): Promise<void> {
  for (let attempt = 0; attempt < 3; attempt++) {
    await page.goto(url, { waitUntil: 'networkidle' });
    const onLogin = await page
      .getByRole('button', { name: 'Log In' })
      .isVisible()
      .catch(() => false);
    if (!onLogin) return;
    const me = await page.request.get('/api/user/me/');
    expect(me.status()).toBe(200); // session is real: bounce, not a denial
    await page.waitForTimeout(2_000);
  }
  await page.goto(url, { waitUntil: 'networkidle' });
  await expect(page.getByRole('button', { name: 'Log In' })).toBeHidden();
}

/** GET the real API from inside the real authenticated page session. */
async function apiGet(
  page: Page,
  url: string
): Promise<{ status: number; body: any }> {
  return page.evaluate(async (target) => {
    const response = await fetch(target, { credentials: 'same-origin' });
    return { status: response.status, body: await response.json() };
  }, url);
}

function historyUrl(
  sessionId: string,
  locationId: number,
  start: string,
  end: string
): string {
  return (
    `/api/assets/demo-metrics/sessions/${sessionId}/history/` +
    `?location=${locationId}&include_descendants=true` +
    `&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`
  );
}

test('current scope renders the real shell and serves scoped data', async ({
  page
}, testInfo) => {
  const observed: { url: string; status: number }[] = [];
  page.on('response', (response) => {
    if (response.url().includes('/api/assets/demo-metrics/')) {
      observed.push({ url: response.url(), status: response.status() });
    }
  });

  await realLogin(page, 'operator');
  const locationId = state.locations_hist['SITE-A'];
  await gotoAppRoute(page, panelUrl(state.sessions.hist.id, locationId));

  const isMobile = testInfo.project.name === 'Pixel 7';

  if (isMobile) {
    // The real app serves the phone experience on a phone profile — assert
    // the real mobile shell (never desktop UI here), and prove the scoped
    // session data still arrives from the real backend over the wire.
    await expect(page.getByText('Mobile viewport detected')).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Open AI Assistant' })
    ).toBeVisible();
  } else {
    // Real scoped current metrics, rendered from the real API.
    await expect(
      page.getByText('Synthetic demo', { exact: true })
    ).toBeVisible();
    await expect(page.getByText('Session machines')).toBeVisible();
    await expect(page.getByText('Contributing session work')).toBeVisible();
    await expect(page.getByText('Scoped history')).toBeVisible();
    await expect(page.getByText(/\d+ open of \d+/).first()).toBeVisible();
  }

  // The scoped cohort is exactly SITE-A + descendants: A01, A02, A03.
  const metrics = await apiGet(
    page,
    `/api/assets/demo-metrics/sessions/${state.sessions.hist.id}/metrics/` +
      `?location=${locationId}&include_descendants=true`
  );
  expect(metrics.status).toBe(200);
  expect(metrics.body.session.id).toBe(state.sessions.hist.id);
  expect(metrics.body.cohort_size).toBe(3);
  const aliases = metrics.body.machines.map((row: any) => row.alias).sort();
  expect(aliases).toEqual(['A01', 'A02', 'A03']);

  // The work list is the real contributing set for that scope.
  const work = await apiGet(
    page,
    `/api/assets/demo-metrics/sessions/${state.sessions.hist.id}/work-orders/` +
      `?location=${locationId}&include_descendants=true`
  );
  expect(work.status).toBe(200);
  expect(work.body.count).toBeGreaterThan(0);

  // Evidence: only real server responses, none mocked/failed.
  expect(observed.length).toBeGreaterThan(0);
  for (const item of observed) {
    expect(new URL(item.url).origin).toBe(new URL(page.url()).origin);
    expect(item.status).toBe(200);
  }

  await page.screenshot({
    path: path.join(RESULTS_DIR, `${testInfo.project.name}-current-scope.png`),
    fullPage: true
  });
});

test('history reports measured values and honest nulls (real API)', async ({
  page
}, testInfo) => {
  test.skip(testInfo.project.name === 'Pixel 7', 'desktop-only detail check');

  await realLogin(page, 'operator');
  await page.goto(
    panelUrl(state.sessions.hist.id, state.locations_hist['SITE-A']),
    {
      waitUntil: 'networkidle'
    }
  );
  await expect(page.getByText(/Measured \d+ of \d+ machines/)).toBeVisible();
  await expect(page.getByLabel('Synthetic history chart')).toBeVisible();
  await expect(
    page.getByText(/Days without observation coverage are gaps/)
  ).toBeVisible();

  const anchor = new Date(state.sessions.hist.anchor_at).getTime();
  const full = await apiGet(
    page,
    historyUrl(
      state.sessions.hist.id,
      state.locations_hist['SITE-A'],
      new Date(anchor - 20 * 86400_000).toISOString(),
      new Date(anchor + 2 * 86400_000).toISOString()
    )
  );
  expect(full.status).toBe(200);
  expect(full.body.available).toBe(true);
  expect(full.body.daily.length).toBeGreaterThan(0);
  expect(typeof full.body.downtime_machine_minutes).toBe('number');
  expect(typeof full.body.measured_cohort_availability).toBe('number');
  expect(full.body.measured_cohort_availability).toBeGreaterThan(0);
  expect(full.body.measured_cohort_availability).toBeLessThanOrEqual(1);

  // Honest nulls: a window with no planned coverage must report null
  // downtime/availability — never a zero that implies "no losses".
  const empty = await apiGet(
    page,
    historyUrl(
      state.sessions.hist.id,
      state.locations_hist['SITE-A'],
      '2030-01-01T00:00:00Z',
      '2030-01-02T00:00:00Z'
    )
  );
  expect(empty.status).toBe(200);
  expect(empty.body.available).toBe(true);
  // Every unobserved bucket must stay null — a zero would claim "no losses".
  for (const bucket of empty.body.daily) {
    expect(bucket.planned_machine_minutes).toBe(0);
    expect(bucket.downtime_machine_minutes).toBeNull();
  }
  expect(empty.body.downtime_machine_minutes).toBeNull();
  expect(empty.body.measured_cohort_availability).toBeNull();

  // A session without imported history stays honestly unavailable: explicit
  // `available: false` with a reason — never a zeroed history.
  const nohist = await apiGet(
    page,
    historyUrl(
      state.sessions.nohist.id,
      state.locations_nohist['SITE-A'],
      new Date(anchor - 20 * 86400_000).toISOString(),
      new Date(anchor + 2 * 86400_000).toISOString()
    )
  );
  expect(nohist.status).toBe(200);
  expect(nohist.body.available).toBe(false);
  expect(nohist.body.reason).toBe('no_imported_history_for_session');
  expect(nohist.body.downtime_machine_minutes).toBeUndefined();

  await page.goto(
    panelUrl(state.sessions.nohist.id, state.locations_nohist['SITE-A']),
    {
      waitUntil: 'networkidle'
    }
  );
  await expect(
    page.getByText(
      'This session has no imported historical coverage, so no chart is shown.'
    )
  ).toBeVisible();
  await expect(page.getByLabel('Synthetic history chart')).toHaveCount(0);

  await page.screenshot({
    path: path.join(RESULTS_DIR, `${testInfo.project.name}-history.png`),
    fullPage: true
  });
});

test('contributing navigation keeps the real demo scope', async ({
  page
}, testInfo) => {
  test.skip(testInfo.project.name === 'Pixel 7', 'desktop-only detail check');

  await realLogin(page, 'operator');
  const locationId = state.locations_hist['SITE-A'];
  await gotoAppRoute(page, panelUrl(state.sessions.hist.id, locationId));

  // Contributing row -> machine detail through the real router, scope carried.
  const machineLink = page
    .locator('table a[href*="/machines/machine/"]')
    .first();
  await expect(machineLink).toBeVisible();
  await machineLink.click();
  await page.waitForURL(/\/web\/machines\/machine\/\d+\//);
  expect(page.url()).toContain(`demo_session=${state.sessions.hist.id}`);
  expect(page.url()).toContain(`location=${locationId}`);
  await page.screenshot({
    path: path.join(RESULTS_DIR, `${testInfo.project.name}-machine-detail.png`),
    fullPage: true
  });

  // Panel -> maintenance board keeps the same scoped drilldown.
  await page.goto(panelUrl(state.sessions.hist.id, locationId), {
    waitUntil: 'networkidle'
  });
  await page.getByRole('link', { name: 'View in Maintenance' }).click();
  await page.waitForURL(/\/web\/maintenance\/board\//);
  expect(page.url()).toContain(`demo_session=${state.sessions.hist.id}`);
  expect(page.url()).toContain(`location=${locationId}`);
  await page.screenshot({
    path: path.join(
      RESULTS_DIR,
      `${testInfo.project.name}-maintenance-board.png`
    ),
    fullPage: true
  });
});

test('API denial is an explicit real 403 from the scope control', async ({
  page,
  request
}, testInfo) => {
  test.skip(testInfo.project.name === 'Pixel 7', 'desktop-only detail check');

  // Anonymous: the real server refuses.
  const anon = await request.get('/api/assets/demo-metrics/sessions/');
  expect(anon.status()).toBe(401);

  // Authenticated with full role authority but scoped to a client that owns
  // no session machine: the scope control is the only difference from the
  // operator, and it must deny explicitly — never a zeroed dashboard.
  await realLogin(page, 'denied');
  const deniedList = await apiGet(page, '/api/assets/demo-metrics/sessions/');
  expect(deniedList.status).toBe(200);
  expect(deniedList.body.count).toBe(0);
  expect(deniedList.body.results).toEqual([]);

  const denied = await apiGet(
    page,
    `/api/assets/demo-metrics/sessions/${state.sessions.hist.id}/metrics/`
  );
  expect(denied.status).toBe(403);
  expect(denied.body.error).toBeTruthy();

  const deniedHistory = await apiGet(
    page,
    historyUrl(
      state.sessions.hist.id,
      state.locations_hist['SITE-A'],
      '2026-01-01T00:00:00Z',
      '2026-01-02T00:00:00Z'
    )
  );
  expect([403, 404]).toContain(deniedHistory.status);

  await page.goto(
    panelUrl(state.sessions.hist.id, state.locations_hist['SITE-A']),
    {
      waitUntil: 'networkidle'
    }
  );
  // The UI must show the honest scoped truth: an empty location tree (their
  // scope owns no locations) and NO scoped demo data — never another actor's
  // numbers, and never a zeroed dashboard that implies "nothing wrong".
  await expect(
    page.getByText('Create your first site to organize machines.')
  ).toBeVisible();
  await expect(page.getByText('Session machines')).toHaveCount(0);
  await expect(page.getByText('Synthetic demo', { exact: true })).toHaveCount(
    0
  );
  await page.screenshot({
    path: path.join(RESULTS_DIR, `${testInfo.project.name}-denied.png`),
    fullPage: true
  });
});
