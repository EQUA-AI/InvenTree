import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createWriteStream } from 'node:fs';
import { readFile, rename, writeFile } from 'node:fs/promises';
import { createServer, request as proxyRequest } from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../../..'
);
const output = process.env.AIMMS_BROWSER_OUTPUT;
const origin = 'http://127.0.0.1:56311';
const backendLog = createWriteStream(path.join(output, 'backend.log'));
function launchBackend(restore = false) {
  const child = spawn(
    process.env.AIMMS_NATIVE_PYTHON,
    [
      '-I',
      '-B',
      path.join(
        root,
        'src/backend/InvenTree/ai/core/tests/native_browser/native_browser_backend.py'
      )
    ],
    {
      env: {
        ...process.env,
        AIMMS_NATIVE_BROWSER_RUNTIME: output,
        AIMMS_NATIVE_BROWSER_RESTORE: restore ? '1' : '0'
      },
      stdio: ['ignore', 'pipe', 'pipe']
    }
  );
  child.stdout.pipe(backendLog, { end: false });
  child.stderr.pipe(backendLog, { end: false });
  return child;
}
let backend = launchBackend();
let server;
let browser;
let activePage;
let nativeRestartInProgress = false;
const results = {
  profile:
    'Full native frontend with isolated synthetic native backend; no API response mocking',
  cases: [],
  responses: [],
  restartProxyFaults: [],
  pageErrors: [],
  externalRequests: []
};

async function ready() {
  const deadline = Date.now() + 90_000;
  while (Date.now() < deadline) {
    if (backend.exitCode !== null || backend.signalCode !== null)
      throw new Error('Owned native backend exited before readiness');
    try {
      const response = await fetch('http://127.0.0.1:8000/api/');
      if (response.ok) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error('Owned native backend readiness deadline exceeded');
}

async function context(viewport) {
  const value = await browser.newContext({ viewport });
  await value.route('**/*', (route) => {
    const url = new URL(route.request().url());
    if (
      ['data:', 'blob:', 'about:'].includes(url.protocol) ||
      [origin, 'http://127.0.0.1:8000', 'http://localhost:8000'].includes(
        url.origin
      )
    )
      return route.continue();
    results.externalRequests.push({ origin: url.origin, path: url.pathname });
    return route.abort();
  });
  return value;
}

async function login(value, code, viewport = 'desktop', checkpoint = '') {
  const page = await value.newPage();
  activePage = page;
  page.on('pageerror', (error) => results.pageErrors.push(error.message));
  page.on('requestfailed', (request) => {
    results.failedResources ??= [];
    results.failedResources.push({
      path: new URL(request.url()).pathname,
      type: request.resourceType(),
      error: request.failure()?.errorText
    });
  });
  page.on('console', (message) => {
    if (message.type() === 'error') {
      results.consoleErrors ??= [];
      if (!results.responses.length) {
        results.bootErrors ??= [];
        results.bootErrors.push(
          message
            .text()
            .slice(0, 1600)
            .replace(/[A-Za-z0-9_-]{24,}/g, '[long-value]')
        );
      }
      results.consoleErrors.push({
        location: message.location().url?.split('?')[0],
        line: message.location().lineNumber,
        category: message.text().includes('MIME')
          ? 'module-mime'
          : (message.text().match(/net::[A-Z_]+/)?.[0] ??
            'browser-console-error')
      });
    }
  });
  page.on('response', (response) => {
    if (
      response.request().resourceType() === 'script' ||
      response.request().resourceType() === 'document'
    ) {
      results.entryResources ??= [];
      results.entryResources.push({
        path: new URL(response.url()).pathname,
        type: response.request().resourceType(),
        status: response.status(),
        contentType: response.headers()['content-type']
      });
    }
    const url = new URL(response.url());
    if (url.pathname.startsWith('/api/'))
      results.responses.push({
        method: response.request().method(),
        path: url.pathname,
        status: response.status(),
        fixtureFault: response.headers()['x-aimms-fixture-fault'] ?? null
      });
  });
  page.setDefaultTimeout(45_000);
  await page.goto(`${origin}/web/login`);
  results.entryState = await page.evaluate(() => ({
    readyState: document.readyState,
    rootPresent: !!document.querySelector('#root'),
    scripts: Array.from(document.scripts, (s) => ({
      path: new URL(s.src || location.href).pathname,
      type: s.type
    }))
  }));
  await page
    .getByRole('textbox', { name: 'login-username' })
    .fill(`native-browser-${code}`);
  await page
    .getByRole('textbox', { name: 'login-password' })
    .fill('synthetic-browser-only');
  await page.getByRole('button', { name: /^log in$/i }).click();
  await page.getByRole('button', { name: 'navigation-menu' }).waitFor();
  assert(
    !page.url().includes('/login'),
    'Native login must leave the login route'
  );
  results.cases.push({
    id: `native-login-${code}-${viewport}${checkpoint}`,
    outcome: 'passed'
  });
  return page;
}

async function openAssistant(page) {
  const trigger = page.getByTestId('open-ai-chat');
  // A native cache-context reset may close the drawer after list sync.
  // Reopen through the same user control once that state transition settles.
  for (let attempt = 0; attempt < 3; attempt += 1) {
    if ((await trigger.getAttribute('aria-expanded')) !== 'true')
      await trigger.click();
    try {
      await page
        .getByTestId('ai-chat-composer')
        .waitFor({ state: 'visible', timeout: 5000 });
      return;
    } catch (error) {
      if (attempt === 2) throw error;
    }
  }
}

async function own(page, route, text, capture) {
  await page.goto(origin + route);
  await page.getByText(text, { exact: false }).first().waitFor();
  await page.screenshot({
    path: path.join(output, `${capture}.png`),
    fullPage: true
  });
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1
    ),
    'Page must not overflow the viewport'
  );
  results.cases.push({ id: capture, outcome: 'passed' });
}

async function denied(page, route, apiPath, secret, id) {
  const response = page.waitForResponse(
    (value) => new URL(value.url()).pathname === apiPath
  );
  await page.goto(origin + route);
  assert(
    [403, 404].includes((await response).status()),
    'Foreign source must be denied by the native API'
  );
  assert(
    !(await page.locator('body').innerText()).includes(secret),
    'Foreign source name must be absent'
  );
  await page.screenshot({
    path: path.join(output, `${id}.png`),
    fullPage: true
  });
  results.cases.push({ id, outcome: 'passed' });
}

try {
  await ready();
  const fixture = JSON.parse(
    await readFile(path.join(output, 'fixture-info.json'), 'utf8')
  );
  const bundle = path.resolve(process.env.AIMMS_NATIVE_BUNDLE);
  const types = {
    '.js': 'text/javascript',
    '.css': 'text/css',
    '.html': 'text/html',
    '.svg': 'image/svg+xml',
    '.png': 'image/png',
    '.ico': 'image/x-icon',
    '.woff': 'font/woff',
    '.woff2': 'font/woff2',
    '.json': 'application/json',
    '.webmanifest': 'application/manifest+json'
  };
  server = createServer(async (req, res) => {
    if (req.url.startsWith('/api/') || req.url.startsWith('/media/')) {
      const upstream = proxyRequest(
        {
          hostname: '127.0.0.1',
          port: 8000,
          path: req.url,
          method: req.method,
          headers: req.headers
        },
        (response) => {
          res.writeHead(response.statusCode, response.headers);
          response.pipe(res);
        }
      );
      upstream.on('error', (error) => {
        const headers = {};
        if (nativeRestartInProgress && error.code === 'ECONNREFUSED') {
          const id = String(results.restartProxyFaults.length + 1);
          results.restartProxyFaults.push({
            id,
            method: req.method,
            path: new URL(req.url, origin).pathname,
            code: error.code,
            duringRestart: true
          });
          headers['X-Aimms-Fixture-Fault'] = id;
        }
        res.writeHead(502, headers);
        res.end();
      });
      req.pipe(upstream);
      return;
    }
    const requestPath = new URL(req.url, origin).pathname;
    if (
      requestPath === '/static/img/inventree_splash.jpg' ||
      /^\/static\/tabler-icons\/(?:tabler-icons\.(?:woff2?|ttf)|icons\.json)$/.test(
        requestPath
      )
    ) {
      const nativeFile = path.join(
        root,
        'src/backend/InvenTree/InvenTree',
        requestPath.slice(1)
      );
      try {
        res.writeHead(200, {
          'Content-Type': requestPath.endsWith('.jpg')
            ? 'image/jpeg'
            : types[path.extname(nativeFile)] || 'application/octet-stream'
        });
        res.end(await readFile(nativeFile));
      } catch {
        res.writeHead(404);
        res.end();
      }
      return;
    }
    const name = decodeURIComponent(new URL(req.url, origin).pathname).replace(
      /^\/(?:web|static\/web)\//,
      '/'
    );
    let file = path.resolve(bundle, `.${name}`);
    if (file !== bundle && !file.startsWith(`${bundle}${path.sep}`)) {
      res.writeHead(404);
      res.end();
      return;
    }
    if (!path.extname(file)) file = path.join(bundle, 'index.html');
    try {
      let body = await readFile(file);
      if (path.extname(file) === '.html')
        body = Buffer.from(
          body.toString().replace('<head>', '<head><base href="/web/">')
        );
      results.staticShell =
        'Unchanged production assets; HTML fixture adds /web/ base href for nested SPA routes';
      res.writeHead(200, {
        'Content-Type': types[path.extname(file)] || 'application/octet-stream'
      });
      res.end(body);
    } catch {
      res.writeHead(404);
      res.end();
    }
  });
  await new Promise((resolve) => server.listen(56311, '127.0.0.1', resolve));
  const html = await (await fetch(`${origin}/web/login`)).text();
  const scriptPath = html.match(/type="module"[^>]*src="([^"]+)"/)[1];
  const scriptUrl = new URL(scriptPath, `${origin}/web/login`);
  const scriptResponse = await fetch(scriptUrl);
  results.assetPreflight = {
    path: scriptUrl.pathname,
    status: scriptResponse.status,
    contentType: scriptResponse.headers.get('content-type')
  };
  assert.equal(scriptResponse.status, 200);
  assert.equal(scriptResponse.headers.get('content-type'), 'text/javascript');
  browser = await chromium.launch({
    executablePath: process.env.AIMMS_CHROMIUM,
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage']
  });
  const desktop = await context({ width: 1440, height: 1000 });
  const page = await login(desktop, 'a');
  await page.screenshot({
    path: path.join(output, 'native-home.png'),
    fullPage: true
  });
  await own(
    page,
    `/web/machines/machine/${fixture.machines[0]}/`,
    'Browser Fixture Machine A',
    'native-machine-a'
  );
  await own(
    page,
    `/web/maintenance/work-orders/${fixture.work[0]}/`,
    'Browser Fixture Work A',
    'native-work-a'
  );
  await denied(
    page,
    `/web/machines/machine/${fixture.machines[1]}/`,
    `/api/assets/machines/${fixture.machines[1]}/`,
    'Browser Fixture Machine B',
    'native-machine-b-denied'
  );
  await denied(
    page,
    `/web/maintenance/work-orders/${fixture.work[1]}/`,
    `/api/kanban/work-orders/${fixture.work[1]}/overview/`,
    'Browser Fixture Work B',
    'native-work-b-denied'
  );
  let commandSequence = 0;
  async function control(kind, details = {}) {
    commandSequence += 1;
    await writeFile(
      path.join(output, 'fixture-command.json'),
      JSON.stringify({ sequence: commandSequence, kind, ...details })
    );
    await rename(
      path.join(output, 'fixture-command.json'),
      path.join(output, 'fixture-command.ready.json')
    );
    const deadline = Date.now() + 15_000;
    while (Date.now() < deadline) {
      try {
        const result = JSON.parse(
          await readFile(
            path.join(output, 'fixture-command-result.json'),
            'utf8'
          )
        );
        if (result.sequence === commandSequence && result.failed)
          throw new Error(`Owned fixture command failed: ${result.error_type}`);
        if (result.sequence === commandSequence && result.applied)
          return result;
      } catch (error) {
        if (String(error).includes('Owned fixture command failed:'))
          throw error;
      }
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    throw new Error('Owned fixture command deadline exceeded');
  }
  async function api(target, method, route, data) {
    return target.evaluate(
      async ({ method, route, data }) => {
        const csrf = document.cookie
          .split('; ')
          .find((v) => v.startsWith('csrftoken='))
          ?.slice('csrftoken='.length);
        const response = await fetch(route, {
          method,
          credentials: 'same-origin',
          headers: {
            'Content-Type': 'application/json',
            ...(csrf ? { 'X-CSRFToken': decodeURIComponent(csrf) } : {})
          },
          ...(data ? { body: JSON.stringify(data) } : {})
        });
        const text = await response.text();
        let body;
        try {
          body = JSON.parse(text);
        } catch {
          body = null;
        }
        return { status: response.status, body, text };
      },
      { method, route, data }
    );
  }
  function passed(id) {
    results.cases.push({ id, outcome: 'passed' });
  }
  function sourceBody(result) {
    assert.equal(result.status, 200, result.text.slice(0, 300));
    assert(
      result.text.includes(String(fixture.source.source_value)),
      'Actual source value must survive'
    );
    assert(
      !result.text.includes('_aimms_source_ref') &&
        !result.text.includes('_source_events_sha256'),
      'Private source reference must not be public'
    );
  }
  const restKey = 'browser-rest-source-1';
  const restInput = {
    message: 'Read selected recorded source',
    idempotency_key: restKey
  };
  const rest = await api(page, 'POST', '/api/ai/chat', restInput);
  sourceBody(rest);
  assert.equal(rest.body.source_observation.kind, 'recorded_history');
  const sourceThread = rest.body.thread_id;
  passed('native-source-rest');
  const replayInput = { ...restInput, thread_id: sourceThread };
  sourceBody(await api(page, 'POST', '/api/ai/chat', replayInput));
  passed('native-source-rest-replay');
  const historyPath = `/api/ai/threads/${sourceThread}`;
  sourceBody(await api(page, 'GET', historyPath));
  passed('native-source-history');
  const search = await api(page, 'GET', '/api/ai/threads?q=37.125');
  assert.equal(search.status, 200);
  assert(search.body.threads.some((t) => t.thread_id === sourceThread));
  passed('native-source-search');
  sourceBody(await api(page, 'POST', '/api/ai/threads/export'));
  passed('native-source-export');
  const legacy = await api(page, 'POST', '/api/ai/chat/stream', {
    message: 'Read selected recorded source over legacy SSE',
    idempotency_key: 'browser-legacy-source-1'
  });
  sourceBody(legacy);
  assert(
    legacy.text.includes('RUN_FINISHED') && !legacy.text.includes('RUN_ERROR')
  );
  passed('native-source-legacy-sse');
  const agui = await api(page, 'POST', '/api/ai/agui', {
    threadId: 'browser-agui-source-1',
    runId: 'browser-agui-run-1',
    messages: [
      {
        id: 'browser-agui-message-1',
        role: 'user',
        content: 'Read selected recorded source over AG-UI'
      }
    ],
    tools: [],
    context: [],
    forwardedProps: { idempotency_key: 'browser-agui-source-1' }
  });
  sourceBody(agui);
  assert(
    agui.text.includes('RUN_FINISHED') &&
      agui.text.includes('aimms.sourceObservation') &&
      !agui.text.includes('RUN_ERROR')
  );
  passed('native-source-agui');
  // The real production drawer sends its own request through the advertised SDK rail.
  await openAssistant(page);
  await page.getByTestId('new-ai-chat-thread').click();
  await page
    .getByRole('textbox', { name: 'Message', exact: true })
    .fill('Read selected recorded source from native browser');
  const uiStream = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname === '/api/ai/agui' &&
      r.request().method() === 'POST'
  );
  await page.getByTestId('send-ai-chat-message').click();
  const uiResponse = await uiStream;
  assert.equal(uiResponse.status(), 200);
  await uiResponse.finished();
  const uiWire = await uiResponse.text();
  assert(uiWire.includes('RUN_FINISHED') && !uiWire.includes('RUN_ERROR'));
  assert(!uiWire.includes('_aimms_source_ref'));
  results.uiTransport = {
    path: '/api/ai/agui',
    completed: true,
    sourceObservation: uiWire.includes('aimms.sourceObservation')
  };
  await page.getByTestId('source-observation').last().waitFor();
  assert(
    (await page.getByTestId('source-observation').last().innerText()).includes(
      'Recorded'
    )
  );
  assert(
    (await page.locator('body').innerText()).includes(
      String(fixture.source.source_value)
    )
  );
  await page.screenshot({
    path: path.join(output, 'native-source-ai-live.png'),
    fullPage: true
  });
  passed('native-source-ai-rendered-live');
  await page.reload();
  await openAssistant(page);
  await page.getByTestId('source-observation').last().waitFor();
  await page.screenshot({
    path: path.join(output, 'native-source-ai-reloaded.png'),
    fullPage: true
  });
  passed('native-source-ai-rendered-reload');
  // Disable only the fixture's source capability: stored bodies must fail closed.
  await control('disable_adapter');
  const disabled = await api(page, 'GET', historyPath);
  assert.equal(disabled.status, 200);
  assert(!disabled.text.includes(String(fixture.source.source_value)));
  passed('native-source-capability-withheld');
  await control('enable_adapter');
  sourceBody(await api(page, 'GET', historyPath));
  passed('native-source-capability-restored');
  await control('revoke_a');
  const revoked = await api(page, 'GET', historyPath);
  assert.equal(revoked.status, 200);
  assert(!revoked.text.includes(String(fixture.source.source_value)));
  const revokedReplay = await api(page, 'POST', '/api/ai/chat', replayInput);
  assert.equal(revokedReplay.status, 409);
  assert.equal(revokedReplay.body.detail, 'AI turn is incomplete');
  results.revokedReplay = {
    status: revokedReplay.status,
    detail: revokedReplay.body.detail
  };
  // Current-source projection produces an incomplete response; the native
  // REST adapter refuses it without disclosing the stored source answer.
  assert(!revokedReplay.text.includes(String(fixture.source.source_value)));
  const revokedExport = await api(page, 'POST', '/api/ai/threads/export');
  assert.equal(revokedExport.status, 200);
  assert(!revokedExport.text.includes(String(fixture.source.source_value)));
  const revokedSync = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname === '/api/ai/threads' &&
      r.request().method() === 'GET'
  );
  await page.reload();
  const syncResponse = await revokedSync;
  assert.equal(syncResponse.status(), 200);
  const syncBody = await syncResponse.json();
  assert.notEqual(syncBody.cache_context, search.body.cache_context);
  await openAssistant(page);
  await page.getByTestId('ai-chat-composer').waitFor();
  assert(
    !(await page.locator('body').innerText()).includes(
      String(fixture.source.source_value)
    )
  );
  const unavailableCards = page.getByTestId('source-observation');
  if (await unavailableCards.count()) {
    assert(
      (await unavailableCards.last().innerText()).includes('Source unavailable')
    );
    results.revokedUi = 'source-unavailable';
  } else {
    results.revokedUi = 'cleared-after-native-cache-context-change';
  }
  await page.screenshot({
    path: path.join(output, 'native-source-ai-revoked.png'),
    fullPage: true
  });
  const revokedSearchCheck = await api(page, 'GET', '/api/ai/threads?q=37.125');
  assert.equal(revokedSearchCheck.status, 200);
  assert(
    !revokedSearchCheck.body.threads.some((t) => t.thread_id === sourceThread)
  );
  passed('native-source-revoked-history-replay-export-ui-search');
  await control('restore_a');
  sourceBody(await api(page, 'GET', historyPath));
  passed('native-source-regrant-restores-current-proof');
  await control('change_source');
  const changed = await api(page, 'GET', historyPath);
  assert.equal(changed.status, 200);
  assert(!changed.text.includes(String(fixture.source.source_value)));
  assert(
    !changed.text.includes(String(fixture.source.changed_value)),
    'Changed source cannot rewrite an old answer'
  );
  const fresh = await api(page, 'POST', '/api/ai/chat', {
    message: 'Read current changed source',
    idempotency_key: 'browser-changed-source-1'
  });
  assert.equal(fresh.status, 200);
  assert(fresh.text.includes(String(fixture.source.changed_value)));
  passed('native-source-change-withholds-old-and-allows-fresh');
  await page.getByTestId('close-ai-chat').click();

  const second = await context({ width: 1440, height: 1000 });
  const other = await login(second, 'b');
  const foreignThread = await api(other, 'GET', historyPath);
  assert.equal(foreignThread.status, 404);
  assert(!foreignThread.text.includes(String(fixture.source.source_value)));
  passed('native-source-foreign-thread-denied');
  await own(
    other,
    `/web/machines/machine/${fixture.machines[1]}/`,
    'Browser Fixture Machine B',
    'native-machine-b'
  );
  await denied(
    other,
    `/web/machines/machine/${fixture.machines[0]}/`,
    `/api/assets/machines/${fixture.machines[0]}/`,
    'Browser Fixture Machine A',
    'native-machine-a-denied'
  );
  const phone = await context({ width: 390, height: 844 });
  const mobile = await login(phone, 'a', 'phone');
  await mobile.goto(
    `${origin}/web/maintenance/work-orders/${fixture.work[0]}/`
  );
  await mobile.getByText(/Use a tablet or desktop for the full app/).waitFor();
  assert.equal(
    await mobile
      .getByText('Browser Fixture Work A', { exact: false })
      .first()
      .isVisible(),
    false,
    'Native full-app phone gate stays in force'
  );
  await mobile.getByRole('button', { name: 'Open AI Assistant' }).waitFor();
  await mobile.keyboard.press('Tab');
  await mobile.screenshot({
    path: path.join(output, 'native-phone-full-app-gate.png'),
    fullPage: true
  });
  assert(
    await mobile.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1
    ),
    'Phone gate must not overflow'
  );
  results.cases.push({ id: 'native-phone-full-app-gate', outcome: 'passed' });
  await mobile.getByRole('button', { name: 'Open AI Assistant' }).click();
  await mobile.getByTestId('new-ai-chat-thread').click();
  await mobile
    .getByRole('textbox', { name: 'Message', exact: true })
    .fill('Read current recorded source on phone');
  const phoneStream = mobile.waitForResponse(
    (r) =>
      ['/api/ai/agui', '/api/ai/chat/stream'].includes(
        new URL(r.url()).pathname
      ) && r.request().method() === 'POST'
  );
  await mobile.getByTestId('send-ai-chat-message').click();
  const phoneResponse = await phoneStream;
  assert.equal(phoneResponse.status(), 200);
  results.phoneTransport = new URL(phoneResponse.url()).pathname;
  await phoneResponse.finished();
  assert((await phoneResponse.text()).includes('RUN_FINISHED'));
  await mobile.getByTestId('source-observation').last().waitFor();
  assert(
    (await mobile.locator('body').innerText()).includes(
      String(fixture.source.changed_value)
    )
  );
  assert(
    await mobile.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1
    )
  );
  await mobile.screenshot({
    path: path.join(output, 'native-source-ai-phone.png'),
    fullPage: true
  });
  passed('native-source-ai-phone');
  const subjects = fixture.source.subjects;
  const unproven = await control('check_unproven');
  assert.equal(unproven.work_available, false);
  assert.equal(unproven.original_available, false);
  passed('native-future-source-unproven-withheld');
  const requestContext = await context({ width: 1440, height: 1000 });
  const requesterPage = await login(requestContext, 'requester');
  const reviewContext = await context({ width: 1440, height: 1000 });
  const reviewerPage = await login(reviewContext, 'reviewer');
  const authorityPath = '/api/assets/integration-authority/';
  const createClaim = (kind, pk, key) => ({
    purpose: 'attribution',
    kind,
    source_pk: pk,
    client_ids: [subjects.client],
    evidence_reference: `synthetic-native-browser-evidence:${pk}`,
    request_key: key
  });
  const readerRefusal = await api(
    page,
    'POST',
    authorityPath,
    createClaim('work', subjects.work, 'reader-must-not-attest')
  );
  assert.equal(readerRefusal.status, 404);
  passed('native-reader-cannot-request-source-authority');
  async function reviewed(kind, pk, key) {
    const created = await api(
      requesterPage,
      'POST',
      authorityPath,
      createClaim(kind, pk, key)
    );
    assert.equal(created.status, 201, created.text);
    const id = created.body.request_pk;
    const self = await api(
      requesterPage,
      'POST',
      `${authorityPath}${id}/review/`,
      {
        expected_request_digest: created.body.request_digest,
        expected_impact_digest: '0'.repeat(64),
        approved: true
      }
    );
    assert.equal(self.status, 404);
    const view = await api(
      reviewerPage,
      'POST',
      `${authorityPath}${id}/inspect/`,
      {}
    );
    assert.equal(view.status, 200, view.text);
    const decision = await api(
      reviewerPage,
      'POST',
      `${authorityPath}${id}/review/`,
      {
        expected_request_digest: view.body.request_digest,
        expected_impact_digest: view.body.impact_digest,
        approved: true
      }
    );
    assert.equal(decision.status, 200, decision.text);
    assert.equal(decision.body.erasure_authorized, false);
    return { id, digest: view.body.request_digest };
  }
  const workClaim = await reviewed(
    'work',
    subjects.work,
    'native-browser-future-work'
  );
  passed('native-two-session-source-request-review-no-self-approval');
  await control('select_work');
  const workAnswer = await api(page, 'POST', '/api/ai/chat', {
    message: 'Read approved future Work source',
    idempotency_key: 'future-work-browser'
  });
  assert.equal(workAnswer.status, 200, workAnswer.text);
  assert(workAnswer.body.message.includes('Future browser source Work'));
  assert.equal(workAnswer.body.source_observation.kind, 'record');
  const workThread = workAnswer.body.thread_id;
  passed('native-future-work-ai-current-source');
  await control('select_context');
  const contextAnswer = await api(page, 'POST', '/api/ai/chat', {
    message: 'Read approved Work with independently authorized machine context',
    idempotency_key: 'future-context-browser'
  });
  assert.equal(contextAnswer.status, 200, contextAnswer.text);
  assert(contextAnswer.body.message.includes('machine_state'));
  assert(contextAnswer.body.message.includes('Browser Fixture Machine A'));
  passed('native-work-context-source-consumer');
  await openAssistant(page);
  await page.getByTestId('new-ai-chat-thread').click();
  await page
    .getByRole('textbox', { name: 'Message', exact: true })
    .fill('Read independently authorized Work machine context');
  const contextUiStream = page.waitForResponse(
    (r) =>
      ['/api/ai/agui', '/api/ai/chat/stream'].includes(
        new URL(r.url()).pathname
      ) && r.request().method() === 'POST'
  );
  await page.getByTestId('send-ai-chat-message').click();
  const contextUi = await contextUiStream;
  assert.equal(contextUi.status(), 200);
  await contextUi.finished();
  assert((await contextUi.text()).includes('RUN_FINISHED'));
  await page.getByTestId('source-observation').last().waitFor();
  assert(
    (await page.locator('body').innerText()).includes(
      'Browser Fixture Machine A'
    )
  );
  await page.screenshot({
    path: path.join(output, 'native-work-context-ai.png'),
    fullPage: true
  });
  passed('native-work-context-rendered');
  await reviewed(
    'maintenance',
    subjects.maintenance,
    'native-browser-future-maintenance'
  );
  await control('select_maintenance');
  const maintenanceAnswer = await api(page, 'POST', '/api/ai/chat', {
    message: 'Read approved native maintenance source',
    idempotency_key: 'future-maintenance-browser'
  });
  assert.equal(maintenanceAnswer.status, 200, maintenanceAnswer.text);
  assert(
    maintenanceAnswer.body.message.includes('Future browser maintenance source')
  );
  passed('native-maintenance-source-consumer');
  const originalClaim = await reviewed(
    'attachment',
    subjects.attachment,
    'native-browser-future-original'
  );
  const originalMetadata = await api(
    page,
    'GET',
    `/api/attachment/${subjects.attachment}/`
  );
  assert.equal(originalMetadata.status, 200, originalMetadata.text);
  const foreignOriginalMetadata = await api(
    other,
    'GET',
    `/api/attachment/${subjects.attachment}/`
  );
  assert([403, 404].includes(foreignOriginalMetadata.status));
  passed('native-original-metadata-independent-scope');
  const downloadPath = `/api/assets/integration-content/${subjects.attachment}/`;
  const downloaded = await api(page, 'GET', downloadPath);
  assert.equal(downloaded.status, 200, downloaded.text);
  assert.equal(downloaded.text, 'Synthetic protected original bytes');
  const deniedDownload = await api(other, 'GET', downloadPath);
  assert([403, 404].includes(deniedDownload.status));
  assert(!deniedDownload.text.includes('Synthetic protected original bytes'));
  passed('native-original-http-download-independent-grant');
  await reviewed(
    'attachment',
    subjects.large_attachment,
    'native-browser-maximum-original'
  );
  async function largeDownload(target) {
    return target.evaluate(async (route) => {
      const started = performance.now();
      const response = await fetch(route, { credentials: 'same-origin' });
      const bytes = new Uint8Array(await response.arrayBuffer());
      return {
        status: response.status,
        byteSize: bytes.length,
        correct: bytes.every((value) => value === 88),
        elapsedMs: performance.now() - started,
        cacheControl: response.headers.get('cache-control')
      };
    }, `/api/assets/integration-content/${subjects.large_attachment}/`);
  }
  const largeRead = await largeDownload(page);
  assert.equal(largeRead.status, 200);
  assert.equal(largeRead.byteSize, 1024 * 1024);
  assert.equal(largeRead.correct, true);
  assert.equal(largeRead.cacheControl, 'private, no-store');
  passed('native-original-maximum-bounded-http-download');
  const concurrentOriginals = await Promise.all([
    largeDownload(page),
    largeDownload(requesterPage)
  ]);
  for (const value of concurrentOriginals) {
    assert.equal(value.status, 200);
    assert.equal(value.byteSize, 1024 * 1024);
    assert.equal(value.correct, true);
  }
  results.downloadPerformance = {
    maximumOriginal: largeRead,
    concurrentSessions: concurrentOriginals
  };
  passed('native-original-concurrent-http-downloads');

  await control('select_document');
  const original = await api(page, 'POST', '/api/ai/chat', {
    message: 'Read independently approved synthetic original bytes',
    idempotency_key: 'future-original-browser'
  });
  assert.equal(original.status, 200, original.text);
  assert(original.body.message.includes('Synthetic protected original bytes'));
  const originalThread = original.body.thread_id;
  passed('native-original-bytes-source-consumer');
  for (let repeat = 0; repeat < 3; repeat += 1) {
    const result = await api(page, 'POST', '/api/ai/chat', {
      message: 'Read the same currently approved original under a fresh turn',
      idempotency_key: `native-original-repeat-${repeat}`
    });
    assert.equal(result.status, 200, result.text);
    assert(result.body.message.includes('Synthetic protected original bytes'));
    passed(`native-original-fresh-turn-repeat-${repeat + 1}`);
  }

  await page.getByTestId('new-ai-chat-thread').click();
  await page
    .getByRole('textbox', { name: 'Message', exact: true })
    .fill('Read separately approved original content');
  const originalUiStream = page.waitForResponse(
    (r) =>
      ['/api/ai/agui', '/api/ai/chat/stream'].includes(
        new URL(r.url()).pathname
      ) && r.request().method() === 'POST'
  );
  await page.getByTestId('send-ai-chat-message').click();
  const originalUi = await originalUiStream;
  assert.equal(originalUi.status(), 200);
  await originalUi.finished();
  assert((await originalUi.text()).includes('RUN_FINISHED'));
  await page.getByTestId('source-observation').last().waitFor();
  assert(
    (await page.locator('body').innerText()).includes(
      'Synthetic protected original bytes'
    )
  );
  await page.screenshot({
    path: path.join(output, 'native-original-ai.png'),
    fullPage: true
  });
  passed('native-original-rendered');
  await requestContext.close();
  // A fresh native session remains distinct from the source evidence it created.
  const currentRequesterContext = await context({ width: 1440, height: 1000 });
  const currentRequester = await login(
    currentRequesterContext,
    'requester',
    'desktop',
    '-new-session'
  );
  const withdraw = await api(
    currentRequester,
    'POST',
    `${authorityPath}${originalClaim.id}/withdraw/`,
    {
      expected_request_digest: originalClaim.digest,
      reason_reference: 'synthetic-original-withdrawal'
    }
  );
  assert.equal(withdraw.status, 200, withdraw.text);
  const withdrawn = await api(page, 'GET', `/api/ai/threads/${originalThread}`);
  assert.equal(withdrawn.status, 200);
  assert(!withdrawn.text.includes('Synthetic protected original bytes'));
  passed('native-original-withdrawal-withholds-stored-source');
  const withdrawnDownload = await api(page, 'GET', downloadPath);
  assert.equal(withdrawnDownload.status, 404);
  assert(
    !withdrawnDownload.text.includes('Synthetic protected original bytes')
  );
  passed('native-original-http-download-withdrawal');
  const withdrawalSync = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname === '/api/ai/threads' &&
      r.request().method() === 'GET'
  );
  await page.reload();
  assert.equal((await withdrawalSync).status(), 200);
  await openAssistant(page);
  await page.getByTestId('ai-chat-composer').waitFor();
  assert(
    !(await page.locator('body').innerText()).includes(
      'Synthetic protected original bytes'
    )
  );
  const withdrawnCard = page.getByTestId('source-observation').last();
  if (await withdrawnCard.isVisible()) {
    assert((await withdrawnCard.innerText()).includes('Source unavailable'));
    results.originalWithdrawalUi = 'source-unavailable';
  } else {
    results.originalWithdrawalUi = 'cleared-after-native-cache-context-change';
  }
  await page.screenshot({
    path: path.join(output, 'native-original-withdrawn.png'),
    fullPage: true
  });
  passed('native-original-withdrawal-rendered');
  const readiness = await api(currentRequester, 'POST', authorityPath, {
    purpose: 'deletion_readiness',
    client_pk: subjects.client,
    client_request_reference: 'synthetic-client-readiness-reference',
    request_key: 'native-browser-readiness',
    scope: [
      { entity_kind: 'work', aspect: 'metadata', source_id: subjects.work }
    ]
  });
  assert.equal(readiness.status, 201, readiness.text);
  const readinessView = await api(
    reviewerPage,
    'POST',
    `${authorityPath}${readiness.body.request_pk}/inspect/`,
    {}
  );
  assert.equal(readinessView.status, 200, readinessView.text);
  assert.equal(readinessView.body.impact.coverage_complete, false);
  assert.equal(readinessView.body.impact.erasure_authorized, false);
  assert.equal(
    readinessView.body.impact.scope_coverage.domains[0].capture_state,
    'future_insert_markers_only'
  );
  assert(
    readinessView.body.impact.scope_coverage.domains[0].unqualified_writer_paths.includes(
      'bulk_insert'
    )
  );
  passed('native-readiness-specific-gaps-no-erasure');
  // Withdraw the parent claim through its own reviewed native record.
  const workWithdraw = await api(
    currentRequester,
    'POST',
    `${authorityPath}${workClaim.id}/withdraw/`,
    {
      expected_request_digest: workClaim.digest,
      reason_reference: 'synthetic-work-withdrawal'
    }
  );
  assert.equal(workWithdraw.status, 200, workWithdraw.text);
  const withdrawnWork = await api(page, 'GET', `/api/ai/threads/${workThread}`);
  assert.equal(withdrawnWork.status, 200);
  assert(!withdrawnWork.text.includes('Future browser source Work'));
  passed('native-work-withdrawal-withholds-stored-source');
  // Restart the actual mounted native process with its SQLite/session/source
  // state. Do not recreate subjects, grants, epochs or authority decisions.
  const placementConfig = await control('prepare_placement_restart');
  async function placementRow() {
    const result = await api(
      page,
      'GET',
      '/api/assets/locations/machines/?limit=100'
    );
    assert.equal(result.status, 200, result.text);
    return result.body.results.find((row) => row.pk === fixture.machines[0]);
  }
  const initialPlacement = await placementRow();
  assert.equal(initialPlacement.placement_sync.state, 'pending');
  await control('stage_placement', {
    row: initialPlacement,
    generation: 'native-before-transfer'
  });
  const transferInput = {
    machines: [
      {
        machine_id: fixture.machines[0],
        expected_placement_version: Number(
          initialPlacement.placement_sync.native_version
        )
      }
    ],
    destination_location_id: placementConfig.destination_pk,
    reason: 'Synthetic native restart transfer',
    idempotency_key: '00000000-0000-4000-8000-000000000701'
  };
  const moved = await api(
    currentRequester,
    'POST',
    '/api/assets/locations/transfers/',
    transferInput
  );
  assert.equal(moved.status, 200, moved.text);
  const replayedMove = await api(
    currentRequester,
    'POST',
    '/api/assets/locations/transfers/',
    transferInput
  );
  assert.equal(replayedMove.status, 200, replayedMove.text);
  assert.equal(moved.body.replayed, false);
  assert.equal(replayedMove.body.replayed, true);
  assert.deepEqual({ ...replayedMove.body, replayed: false }, moved.body);
  const pendingBeforeRestart = await placementRow();
  assert.equal(pendingBeforeRestart.placement_sync.state, 'pending');
  assert.notEqual(
    pendingBeforeRestart.placement_sync.native_version,
    initialPlacement.placement_sync.native_version
  );
  passed('native-placement-transfer-and-idempotent-replay-pending');
  const originalPid = backend.pid;
  nativeRestartInProgress = true;
  backend.kill('SIGINT');
  await once(backend, 'exit');
  const stopped = JSON.parse(
    await readFile(path.join(output, 'fixture-readback.json'), 'utf8')
  );
  assert.equal(stopped.selected_operational_rows_unchanged, true);
  assert.equal(stopped.authorized_placement_write_verified, true);
  backend = launchBackend(true);
  await ready();
  nativeRestartInProgress = false;
  const restoredInfo = JSON.parse(
    await readFile(path.join(output, 'fixture-info.json'), 'utf8')
  );
  assert.notEqual(backend.pid, originalPid);
  assert.equal(restoredInfo.restored, true);
  assert.deepEqual(restoredInfo.users, fixture.users);
  const restoredSession = await api(page, 'GET', '/api/user/me/');
  assert.equal(restoredSession.status, 200, restoredSession.text);
  passed('native-asgi-process-restart-retains-session');
  const recoveryPaths = [
    ...new Set([
      '/api/ai/voice/capability',
      '/api/notifications/',
      ...results.restartProxyFaults.map((fault) => {
        assert.equal(fault.method, 'GET');
        return fault.path;
      })
    ])
  ].sort();
  results.backgroundRecovery = [];
  for (const route of recoveryPaths) {
    const recovered = await api(page, 'GET', route);
    assert.equal(recovered.status, 200, recovered.text);
    results.backgroundRecovery.push({ path: route, status: recovered.status });
  }
  passed('native-asgi-restart-background-endpoints-recover');
  const pendingAfterRestart = await placementRow();
  assert.equal(pendingAfterRestart.placement_sync.state, 'pending');
  assert.deepEqual(
    pendingAfterRestart.placement_sync,
    pendingBeforeRestart.placement_sync
  );
  const staleProof = await control('ack_placement');
  assert.equal(staleProof.acknowledged, false);
  assert.equal(staleProof.proof_status, 'rejected');
  assert.equal(staleProof.error_type, 'PlacementProofError');
  assert.equal((await placementRow()).placement_sync.state, 'pending');
  passed('native-asgi-restart-retains-pending-and-refuses-stale-serving');
  await control('stage_placement', {
    row: pendingAfterRestart,
    generation: 'native-after-restart-latest'
  });
  assert.equal((await control('ack_placement')).acknowledged, true);
  assert.equal((await placementRow()).placement_sync.state, 'current');
  const secondSessionPlacement = await api(
    currentRequester,
    'GET',
    '/api/assets/locations/machines/?limit=100'
  );
  assert.equal(secondSessionPlacement.status, 200, secondSessionPlacement.text);
  assert.equal(
    secondSessionPlacement.body.results.find(
      (row) => row.pk === fixture.machines[0]
    ).placement_sync.state,
    'current'
  );
  results.placementRestart = {
    nativeVersion: pendingAfterRestart.placement_sync.native_version,
    pendingRetained: true,
    staleServingRejected: true,
    latestServingAcknowledged: true,
    secondSessionCurrent: true
  };
  passed('native-asgi-restart-independent-latest-serving-two-sessions');
  const afterRestartOriginal = await api(
    page,
    'GET',
    `/api/ai/threads/${originalThread}`
  );
  assert.equal(afterRestartOriginal.status, 200);
  assert(
    !afterRestartOriginal.text.includes('Synthetic protected original bytes')
  );
  const afterRestartDownload = await api(page, 'GET', downloadPath);
  assert.equal(afterRestartDownload.status, 404);
  passed('native-asgi-process-restart-retains-withdrawal');
  await control('select_history');
  const restoredSource = await api(page, 'POST', '/api/ai/chat', {
    message: 'Read current source after native process restart',
    idempotency_key: 'native-restored-source'
  });
  assert.equal(restoredSource.status, 200, restoredSource.text);
  assert(
    restoredSource.body.message.includes(String(fixture.source.changed_value))
  );
  assert.equal(restoredSource.body.source_observation.kind, 'recorded_history');
  results.processRestart = {
    oldPid: originalPid,
    newPid: backend.pid,
    sessionRetained: true,
    withdrawalRetained: true,
    freshSource: true
  };
  passed('native-asgi-process-restart-fresh-source-positive');
  await reviewContext.close();
  await currentRequesterContext.close();

  const certifiedRestartFault = (response) =>
    response.status === 502 &&
    results.restartProxyFaults.some(
      (fault) =>
        fault.id === response.fixtureFault &&
        fault.path === response.path &&
        fault.method === response.method &&
        fault.code === 'ECONNREFUSED' &&
        fault.duringRestart === true
    );
  results.expectedRestartServerErrors = results.responses.filter(
    certifiedRestartFault
  );
  results.unexpectedServerErrors = results.responses.filter(
    (response) => response.status >= 500 && !certifiedRestartFault(response)
  );
  assert.equal(
    results.unexpectedServerErrors.length,
    0,
    'No unexpected native server errors'
  );
  assert.equal(results.pageErrors.length, 0, 'No browser runtime errors');
  assert.equal(results.externalRequests.length, 0, 'No external requests');
  results.outcome = 'passed';
} catch (error) {
  results.outcome = 'failed';
  results.error = String(error);
  if (activePage) {
    await activePage
      .screenshot({ path: path.join(output, 'failure.png'), fullPage: true })
      .catch(() => {});
    results.failedPath = new URL(activePage.url()).pathname;
    results.failedBody = (
      await activePage
        .locator('body')
        .innerText()
        .catch(() => '')
    ).slice(0, 2500);
  }
  process.exitCode = 1;
} finally {
  await browser?.close();
  if (server) await new Promise((resolve) => server.close(resolve));
  if (backend.exitCode === null && backend.signalCode === null) {
    backend.kill('SIGINT');
    await once(backend, 'exit');
  }
  backendLog.end();
  try {
    const readback = JSON.parse(
      await readFile(path.join(output, 'fixture-readback.json'), 'utf8')
    );
    results.selectedOperationalRowsUnchanged =
      readback.selected_operational_rows_unchanged;
    results.authorizedPlacementWriteVerified =
      readback.authorized_placement_write_verified;
    assert.equal(results.authorizedPlacementWriteVerified, true);
    if (!results.selectedOperationalRowsUnchanged) {
      results.outcome = 'failed';
      results.error = 'Selected operational fixture rows changed';
      process.exitCode = 1;
    }
  } catch {
    results.outcome = 'failed';
    results.readback = 'unavailable';
    process.exitCode = 1;
  }
  results.backendExit = { code: backend.exitCode, signal: backend.signalCode };
  await writeFile(
    path.join(output, 'native-browser-results.json'),
    `${JSON.stringify(results, null, 2)}\n`
  );
}
