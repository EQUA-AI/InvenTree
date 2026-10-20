import { expect, it, vi } from 'vitest';

import type {
  ActiveScopeSummary,
  ThreadScopePayload
} from '@lib/types/AimmsWire.generated';
import {
  createScopeHydrationTracker,
  summaryDerivedScope
} from './threadScopeHydration';

const summary: ActiveScopeSummary = {
  mode: 'explicit_assets',
  version: 4,
  display_label: 'Fixture 12'
};

const payload = (version: number, ids: number[]): ThreadScopePayload => ({
  thread_id: 'fixture-thread',
  version,
  hash: `fixture-${version}`,
  display_label: `Fixture ${ids[0] ?? 0}`,
  editable: true,
  scope: {
    schema_version: 1,
    mode: 'explicit_assets',
    machine_ids: ids,
    date_window: { from: null, to: null },
    source_classes: [],
    display_label: `Fixture ${ids[0] ?? 0}`
  }
});

const project = (p: ThreadScopePayload) => ({
  mode: p.scope.mode,
  version: p.version,
  displayLabel: p.display_label,
  editable: p.editable,
  machineCount: p.scope.machine_ids.length,
  machineIds: p.scope.machine_ids
});

it('summary-derived scope carries no machine ids and never label-derived authority', () => {
  const scope = summaryDerivedScope(summary, false);
  expect(scope.mode).toBe('explicit_assets');
  expect(scope.version).toBe(4);
  expect(scope.displayLabel).toBe('Fixture 12');
  expect(scope.editable).toBe(true);
  expect(scope.machineIds).toBeUndefined();
  expect(scope.machineCount).toBeUndefined();
  expect('machineIds' in scope).toBe(false);
});

it('hydrates canonical machine ids through the full authorized scope endpoint', async () => {
  const tracker = createScopeHydrationTracker();
  const fetchScope = vi.fn().mockResolvedValue(payload(4, [12, 15]));
  const result = await tracker.hydrate({
    threadId: 'fixture-thread',
    shared: false,
    summary,
    isCurrent: () => true,
    fetchScope,
    project
  });
  expect(result.status).toBe('applied');
  expect(result.scope?.machineIds).toEqual([12, 15]);
  expect(result.scope?.machineCount).toBe(2);
  expect(fetchScope).toHaveBeenCalledTimes(1);
});

it('failed/revoked/null scope response keeps the summary display without ids', async () => {
  const tracker = createScopeHydrationTracker();
  const result = await tracker.hydrate({
    threadId: 'fixture-thread',
    shared: false,
    summary,
    isCurrent: () => true,
    fetchScope: async () => null,
    project
  });
  expect(result.status).toBe('applied');
  expect(result.scope?.displayLabel).toBe('Fixture 12');
  expect(result.scope?.machineIds).toBeUndefined();
});

it('a newer scope observation (PUT ack / newer summary / denial) drops the stale read', async () => {
  const tracker = createScopeHydrationTracker();
  let release!: (value: ThreadScopePayload | null) => void;
  const pending = new Promise<ThreadScopePayload | null>((resolve) => {
    release = resolve;
  });
  const hydration = tracker.hydrate({
    threadId: 'fixture-thread',
    shared: false,
    summary,
    isCurrent: () => true,
    fetchScope: () => pending,
    project
  });
  // A newer scope write acknowledgement lands while the read is in flight.
  tracker.fence();
  release(payload(3, [99]));
  const result = await hydration;
  expect(result.status).toBe('stale');
  expect(result.scope).toBeNull();
});

it('rapid thread / selection / account switch drops the stale read (not thread id alone)', async () => {
  const tracker = createScopeHydrationTracker();
  const result = await tracker.hydrate({
    threadId: 'fixture-thread',
    shared: false,
    summary,
    // Selection, session or account moved under the awaited read.
    isCurrent: () => false,
    fetchScope: async () => payload(4, [12]),
    project
  });
  expect(result.status).toBe('stale');
  expect(result.scope).toBeNull();
});

it('an older pending read can never overwrite a newer applied scope', async () => {
  const tracker = createScopeHydrationTracker();
  const slow = tracker.hydrate({
    threadId: 'fixture-thread',
    shared: false,
    summary,
    isCurrent: () => true,
    fetchScope: () => Promise.resolve(payload(4, [12])),
    project
  });
  tracker.fence();
  const first = await slow;
  expect(first.status).toBe('stale');
  // The newer observation is what remains authoritative.
  const second = await tracker.hydrate({
    threadId: 'fixture-thread',
    shared: false,
    summary: { ...summary, version: 5, display_label: 'Fixture 13' },
    isCurrent: () => true,
    fetchScope: async () => payload(5, [13]),
    project
  });
  expect(second.status).toBe('applied');
  expect(second.scope?.machineIds).toEqual([13]);
  expect(second.scope?.version).toBe(5);
});

it('shared read-only threads still hydrate ids for display', async () => {
  const tracker = createScopeHydrationTracker();
  const result = await tracker.hydrate({
    threadId: 'fixture-thread',
    shared: true,
    summary,
    isCurrent: () => true,
    fetchScope: async () => payload(4, [12]),
    project
  });
  expect(result.status).toBe('applied');
  expect(summaryDerivedScope(summary, true).editable).toBe(false);
});
