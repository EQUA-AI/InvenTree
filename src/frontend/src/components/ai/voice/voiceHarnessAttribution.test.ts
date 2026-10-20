/**
 * RV14 — mocked-voice harness attribution regression.
 *
 * Mocked HARNESS/CONTROLLER fixture only: no browser is launched, no
 * microphone, no provider, nothing mounted. It drives the REAL route
 * handlers installed by `installVoiceMocks` (tests/pages/voice_harness.ts)
 * with a fake Page/Route whose Request implements the exact Playwright
 * request APIs the shared `observeRequest` metadata reader consumes
 * (`postData`, `postDataJSON`, `url`, `method`, async `allHeaders`).
 *
 * Contract under test: when same-kind observations overlap and their
 * metadata continuations complete in a deliberately reordered way, each
 * request must keep its OWN body and its OWN stable per-kind index
 * (registration order), while counts/readiness stay truthful and rejected
 * metadata propagates without fabricating entries.
 */
import type { Page, Route } from '@playwright/test';
import { describe, expect, it } from 'vitest';

import { installVoiceMocks } from '../../../../tests/pages/voice_harness';

interface FakeTurnSpec {
  n: number;
  /** When set, the simulated metadata fetch (allHeaders) rejects. */
  rejectMetadata?: Error;
}

interface FakeFulfill {
  status?: number;
  json?: unknown;
}

function fakeRequest(spec: FakeTurnSpec) {
  return {
    method: () => 'POST',
    url: () => 'http://127.0.0.1/api/ai/voice/sessions/s1/turns',
    postData: () => JSON.stringify({ n: spec.n }),
    postDataJSON: () => ({ n: spec.n }),
    // observeRequest awaits request.allHeaders() — the async metadata read
    // whose continuations this test deliberately reorders.
    allHeaders: async () => {
      if (spec.rejectMetadata) throw spec.rejectMetadata;
      return { 'content-type': 'application/json' };
    }
  };
}

function fakeRoute(spec: FakeTurnSpec) {
  const fulfills: FakeFulfill[] = [];
  const route = {
    request: () => fakeRequest(spec),
    fulfill: async (options: FakeFulfill) => {
      fulfills.push(options);
    }
  };
  return { fulfills, route: route as unknown as Route };
}

function fakePage() {
  const routes: {
    pattern: string;
    handler: (route: Route) => Promise<void>;
  }[] = [];
  const page = {
    addInitScript: async () => undefined,
    route: async (
      pattern: string,
      handler: (route: Route) => Promise<void>
    ): Promise<void> => {
      routes.push({ pattern, handler });
    }
  };
  const turnHandler = (): ((route: Route) => Promise<void>) => {
    const entry = routes.find(
      (route) => route.pattern === '**/api/ai/voice/sessions/*/turns'
    );
    if (!entry) throw new Error('turns route handler was not installed');
    return entry.handler;
  };
  return { page: page as unknown as Page, turnHandler };
}

describe('RV14: overlapped mocked-voice observations keep their own body and index', () => {
  it('completion-order reversal: each overlapped turn keeps its own body and stable index', async () => {
    const { page, turnHandler } = fakePage();
    const seen: { body: Record<string, unknown>; index: number }[] = [];
    // Sanctioned seam: slowObservation simulates artificially slow metadata
    // fetches BEFORE recording. First 'turns' call belongs to request #1,
    // second call to request #2.
    const gates: (() => void)[] = [];
    const observations = await installVoiceMocks(page, {
      slowObservation: (kind) => {
        if (kind !== 'turns') return undefined;
        return new Promise<void>((resolve) => {
          gates.push(resolve);
        });
      },
      onTurn: (body, index) => {
        seen.push({ body, index });
        return { echoed: body.n, index };
      }
    });
    const handler = turnHandler();
    const first = fakeRoute({ n: 1 });
    const second = fakeRoute({ n: 2 });
    const firstRun = handler(first.route);
    const secondRun = handler(second.route);
    await Promise.resolve();
    expect(gates).toHaveLength(2);
    // Deliberate completion-order reversal: release request #2's simulated
    // metadata first, then #1's, in the same tick so the two observation
    // continuations interleave and #2 records before #1.
    gates[1]();
    gates[0]();
    await Promise.all([firstRun, secondRun]);

    // Attribution: each response must echo its OWN request body and its own
    // stable per-kind index (registration order: request #1 -> 0, #2 -> 1).
    // String compare so the failure diff prints BOTH requests' full body/index
    // pairs instead of eliding array elements.
    expect(
      JSON.stringify([first.fulfills[0]?.json, second.fulfills[0]?.json])
    ).toBe(
      JSON.stringify([
        { echoed: 1, index: 0 },
        { echoed: 2, index: 1 }
      ])
    );
    expect(seen).toContainEqual({ body: { n: 1 }, index: 0 });
    expect(seen).toContainEqual({ body: { n: 2 }, index: 1 });
    expect(observations.turns[0]?.body).toEqual({ n: 1 });
    expect(observations.turns[1]?.body).toEqual({ n: 2 });

    // Count/readiness control: two REAL records, no fabricated entries.
    await observations.ready();
    await observations.whenObserved('turns', 2);
    expect(observations.turns).toHaveLength(2);
    expect(observations.turns.map((turn) => turn.body)).toEqual(
      expect.arrayContaining([{ n: 1 }, { n: 2 }])
    );
    expect(observations.sessionEnded).toBe(false);
  });

  it('ordinary ordered requests are answered with their own body and index', async () => {
    const { page, turnHandler } = fakePage();
    const seen: { body: Record<string, unknown>; index: number }[] = [];
    const observations = await installVoiceMocks(page, {
      onTurn: (body, index) => {
        seen.push({ body, index });
        return { echoed: body.n, index };
      }
    });
    const handler = turnHandler();
    const first = fakeRoute({ n: 1 });
    const second = fakeRoute({ n: 2 });
    await handler(first.route);
    await handler(second.route);

    expect(first.fulfills).toHaveLength(1);
    expect(first.fulfills[0]?.json).toEqual({ echoed: 1, index: 0 });
    expect(second.fulfills).toHaveLength(1);
    expect(second.fulfills[0]?.json).toEqual({ echoed: 2, index: 1 });
    expect(seen).toEqual([
      { body: { n: 1 }, index: 0 },
      { body: { n: 2 }, index: 1 }
    ]);

    await observations.ready();
    await observations.whenObserved('turns', 2);
    expect(observations.turns).toHaveLength(2);
    expect(observations.turns.map((turn) => turn.body)).toEqual([
      { n: 1 },
      { n: 2 }
    ]);
  });

  it('rejected metadata propagates the failure without fabricating entries', async () => {
    const { page, turnHandler } = fakePage();
    const seen: { body: Record<string, unknown>; index: number }[] = [];
    const observations = await installVoiceMocks(page, {
      onTurn: (body, index) => {
        seen.push({ body, index });
        return { echoed: body.n, index };
      }
    });
    const handler = turnHandler();
    const good = fakeRoute({ n: 1 });
    await handler(good.route);

    const bad = fakeRoute({
      n: 2,
      rejectMetadata: new Error('metadata fetch rejected')
    });
    await expect(handler(bad.route)).rejects.toThrow('metadata fetch rejected');

    // Failure propagated above. Counts stay genuine: only the real record is
    // present and no entry is fabricated for the rejected observation.
    await observations.ready();
    expect(observations.turns).toHaveLength(1);
    expect(observations.turns.map((turn) => turn.body)).toEqual([{ n: 1 }]);
    expect(seen).toEqual([{ body: { n: 1 }, index: 0 }]);
    // whenObserved must NOT fabricate its way to the requested count.
    const overCountResolved = await Promise.race([
      observations.whenObserved('turns', 2).then(() => true),
      Promise.resolve(false)
    ]);
    expect(overCountResolved).toBe(false);
  });
});
