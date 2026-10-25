import { defineConfig } from '@playwright/test';

/**
 * AI chat drawer acceptance (mocked API + wholly mocked auth; no backend,
 * no credentials). There is deliberately NO webServer and NO globalSetup:
 * it runs against the already
 * running Vite dev server on :5173 (mocked component/app harness pages).
 */
export default defineConfig({
  testDir: './tests/pages',
  testMatch: [
    'pui_ai_chat_drawer_layout.spec.ts',
    'pui_voice_decisions.spec.ts',
    'pui_ai_chat_drafts.spec.ts',
    'pui_ai_chat_modal_focus.spec.ts',
    'pui_ai_chat_modal_pointer_focus.spec.ts',
    'pui_ai_chat_send_admission.spec.ts',
    'pui_ai_chat_identity_cleanup.spec.ts',
    'pui_ai_chat_upload_queue.spec.ts',
    'pui_ai_chat_submission.spec.ts',
    'pui_ai_chat_picker_recovery.spec.ts',
    'pui_ai_chat_presentation.spec.ts',
    'pui_ai_chat_visualization.spec.ts',
    'pui_ai_chat_workspace.spec.ts',
    'pui_ai_chat_overlay_matrix.spec.ts'
  ],
  workers: 1,
  retries: 0,
  timeout: 60_000,
  reporter: 'list',
  outputDir: 'test-results/chat-drawer',
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:5173',
    browserName: 'chromium',
    headless: true,
    contextOptions: {
      reducedMotion: 'reduce'
    }
  }
});
