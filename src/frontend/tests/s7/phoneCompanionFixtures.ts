import type { Page } from '@playwright/test';
import { sseBody } from '../pages/aichat_harness';
import { mockAssistantOpen } from '../s4/workExecutionFixtures';

/** Existing Work/request acknowledgement and stream, with no live service. */
export async function phoneAssistant(page: Page) {
  await mockAssistantOpen(page);
  const acknowledgements: Record<string, unknown>[] = [];
  const sends: Record<string, unknown>[] = [];
  let confirmed = false;
  await page.route(/\/api\/ai\/threads(?:\?|$)/, (route) =>
    route.fulfill({
      json: {
        threads: [],
        shared_threads: [],
        has_more: false,
        capabilities: {
          thread_scope: true,
          work_focus: true,
          investigation_request_focus: true
        }
      }
    })
  );
  await page.route('**/api/ai/threads/*/work-focus', async (route) => {
    const body = route.request().postDataJSON();
    acknowledgements.push(body);
    if (!confirmed)
      return route.fulfill({
        status: 400,
        json: { detail: 'work_focus_rejected' }
      });
    const thread = decodeURIComponent(
      new URL(route.request().url()).pathname.split('/').at(-2)!
    );
    await route.fulfill({
      json: {
        schema_version: 1,
        thread_id: thread,
        focus: {
          schema_version: body.request_id ? 2 : 1,
          kind: 'work_order',
          work_order_id: 73,
          lifecycle_version: 7,
          machine_id: 18,
          ...(body.request_id
            ? {
                request_id: body.request_id,
                request_version: body.request_version
              }
            : {})
        },
        receipt: 'synthetic-phone-signed-receipt',
        expires_in: 300
      }
    });
  });
  await page.route('**/api/ai/chat/stream', (route) => {
    const body = route.request().postDataJSON();
    sends.push(body);
    return route.fulfill({
      contentType: 'text/event-stream',
      body: sseBody([
        { type: 'RUN_STARTED', threadId: body.thread_id, runId: 'phone-run' },
        {
          type: 'TEXT_MESSAGE_START',
          messageId: `phone-answer-${sends.length}`,
          role: 'assistant'
        },
        {
          type: 'TEXT_MESSAGE_CONTENT',
          messageId: `phone-answer-${sends.length}`,
          delta: 'Synthetic phone answer; no evidence published.'
        },
        { type: 'TEXT_MESSAGE_END', messageId: `phone-answer-${sends.length}` },
        { type: 'RUN_FINISHED', threadId: body.thread_id, runId: 'phone-run' }
      ])
    });
  });
  return {
    acknowledgements,
    sends,
    confirm: () => {
      confirmed = true;
    }
  };
}
