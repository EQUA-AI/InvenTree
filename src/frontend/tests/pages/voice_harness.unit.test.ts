/** RV14 source-bound attribution regression for tests/pages/voice_harness.ts
 * (the REAL installVoiceMocks/observe seam, exercised with a minimal fake
 * Page/Route surface — this is mocked-harness evidence, never provider
 * evidence). Deliberately overlapped same-kind observations must each keep
 * their OWN request body and their own stable index in the turn callback. */
import { describe, expect, it } from 'vitest';

import { installVoiceMocks } from './voice_harness';

interface FakeRouteHolder {
  pattern: string;
  handler: (route: unknown) => Promise<void> | void;
}

function fakePage() {
  const routes: FakeRouteHolder[] = [];
  const page = {
    addInitScript: async () => undefined,
    route: async (pattern: string, handler: FakeRouteHolder['handler']) => {
      routes.push({ pattern, handler });
    }
  };
  return { page: page as never, routes };
}

function fakeTurnRoute(body: Record<string, unknown>) {
  const fulfills: { status?: number; json?: unknown }[] = [];
  const request = {
    method: () => 'POST',
    url: () => 'http://127.0.0.1/api/ai/voice/sessions/s1/turns',
    headers: () => ({ 'content-type': 'application/json' }),
    postData: () => JSON.stringify(body),
    postDataJSON: () => body
  };
  const route = {
    request: () => request as never,
    fulfill: async (options: { status?: number; json?: unknown }) => {
      fulfills.push(options);
    }
  };
  return { fulfills, route: route as never };
}

describe('RV14: overlapped same-kind observations keep their own body and index', () => {
  it('answers each turn request with its own body and stable own index', async () => {
    const { page, routes } = fakePage();
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const seen: { body: Record<string, unknown>; index: number }[] = [];
    const observations = await installVoiceMocks(page, {
      // Hold BOTH turn observations, then release them together so the two
      // records interleave: the deliberately overlapped/reordered phase.
      slowObservation: (kind) => (kind === 'turns' ? gate : undefined),
      onTurn: (body, index) => {
        seen.push({ body, index });
        return { echoed: body.n, index };
      }
    });
    const turns = routes.find(
      (route) => route.pattern === '**/api/ai/voice/sessions/*/turns'
    );
    expect(turns).toBeDefined();
    const first = fakeTurnRoute({ n: 1 });
    const second = fakeTurnRoute({ n: 2 });
    const firstRun = turns?.handler(first.route);
    const secondRun = turns?.handler(second.route);
    release();
    await Promise.all([firstRun, secondRun]);
    // Each response reflects its OWN request body and its own stable index.
    expect(second.fulfills[0]?.json).toEqual({ echoed: 2, index: 1 });
    expect(first.fulfills[0]?.json).toEqual({ echoed: 1, index: 0 });
    expect(seen).toContainEqual({ body: { n: 1 }, index: 0 });
    expect(seen).toContainEqual({ body: { n: 2 }, index: 1 });
    // Readiness/counts stay truthful: two real records, no fabricated ones.
    await observations.ready();
    await observations.whenObserved('turns', 2);
    expect(observations.turns).toHaveLength(2);
    expect(observations.turns.map((turn) => turn.body)).toEqual([
      { n: 1 },
      { n: 2 }
    ]);
    expect(observations.sessionEnded).toBe(false);
  });
});
