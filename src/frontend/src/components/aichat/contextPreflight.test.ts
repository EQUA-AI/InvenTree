/**
 * Shared context preflight decisions (plan packages A + C).
 *
 * Typed send and every voice entry path must commit the same explicit target
 * before any turn or session creation. A machine hint commits BOTH the
 * thread's explicit-assets analysis scope (explicit machine-context behavior)
 * and the conversation focus; a task/work-order target commits ONLY the focus
 * (a focus change never widens an established analysis scope). These pure
 * decisions keep both paths identical:
 *
 * - machine scope reuse requires canonical machine IDS — a display label is
 *   never a binding (equal-name machines cannot skip confirmation);
 * - an explicit selection that cannot be confirmed (no scope/focus
 *   capability, denied candidate, version conflict) BLOCKS the entry —
 *   never a display-only chip followed by an unscoped turn;
 * - the focus PUT is the last step, so its exact server ack is what the
 *   entry submits against.
 */
import { describe, expect, it, vi } from 'vitest';
import {
  type ContextTargetHint,
  canAdoptSessionThread,
  isFreshSessionTurn,
  machineScopeCoversHint,
  planContextPreflight,
  runContextPreflight
} from './contextPreflight';

const pump: ContextTargetHint = { kind: 'machine', id: 7, label: 'Pump A' };
const sealCard: ContextTargetHint = {
  kind: 'kanban_card',
  id: 11,
  label: 'Replace seal'
};
const job: ContextTargetHint = {
  kind: 'work_order',
  id: 5,
  label: 'WO-000005 Pump service'
};
const confirmed = {
  mode: 'explicit_assets',
  displayLabel: 'Pump A',
  machineIds: [7],
  machineCount: 1
};
const focusAck = { ok: true, version: 4 };

describe('planContextPreflight', () => {
  it('proceeds without context work when no target hint is pending', () => {
    expect(
      planContextPreflight({
        hint: null,
        scopeCapable: true,
        focusCapable: true,
        threadShared: false,
        confirmed: null
      })
    ).toEqual({ action: 'proceed', reason: 'no-hint' });
  });

  it('blocks an explicit selection on a shared (read-only) thread', () => {
    expect(
      planContextPreflight({
        hint: pump,
        scopeCapable: true,
        focusCapable: true,
        threadShared: true,
        confirmed: null
      })
    ).toEqual({ action: 'blocked', code: 'shared-thread' });
  });

  it('blocks while another scope application is in flight', () => {
    expect(
      planContextPreflight({
        hint: pump,
        scopeCapable: true,
        focusCapable: true,
        threadShared: false,
        busy: true,
        confirmed: null
      })
    ).toEqual({ action: 'blocked', code: 'in-flight' });
  });

  it('blocks an explicit selection when the backend cannot confirm focus', () => {
    for (const hint of [pump, sealCard, job]) {
      expect(
        planContextPreflight({
          hint,
          scopeCapable: true,
          focusCapable: false,
          threadShared: false,
          confirmed: null
        })
      ).toEqual({ action: 'blocked', code: 'focus-unsupported' });
    }
  });

  it('blocks a machine selection instead of degrading to a display-only chip', () => {
    expect(
      planContextPreflight({
        hint: pump,
        scopeCapable: false,
        focusCapable: true,
        threadShared: false,
        confirmed: null
      })
    ).toEqual({ action: 'blocked', code: 'scope-unsupported' });
  });

  it('commits focus without a scope PUT when the confirmed scope covers the machine', () => {
    expect(
      planContextPreflight({
        hint: pump,
        scopeCapable: true,
        focusCapable: true,
        threadShared: false,
        confirmed
      })
    ).toEqual({ action: 'apply', focus: { kind: 'machine', id: 7 } });
  });

  it('requests an explicit-assets scope when the confirmed scope differs', () => {
    expect(
      planContextPreflight({
        hint: pump,
        scopeCapable: true,
        focusCapable: true,
        threadShared: false,
        confirmed: { ...confirmed, machineIds: [8], displayLabel: 'Pump B' }
      })
    ).toEqual({
      action: 'apply',
      scope: {
        mode: 'explicit_assets',
        machine_ids: [7],
        display_label: 'Pump A'
      },
      focus: { kind: 'machine', id: 7 }
    });
  });

  it('never trusts a display label: unknown scope ids require the full scope', () => {
    // Same display label, ids never observed: a label match must NOT skip
    // canonical confirmation (equal-name machines reuse trap).
    expect(
      planContextPreflight({
        hint: pump,
        scopeCapable: true,
        focusCapable: true,
        threadShared: false,
        confirmed: {
          mode: 'explicit_assets',
          displayLabel: 'Pump A',
          machineCount: 1
        }
      })
    ).toEqual({ action: 'resolve-scope' });
  });

  it('commits only focus for task/work-order targets — never a scope', () => {
    for (const hint of [sealCard, job]) {
      expect(
        planContextPreflight({
          hint,
          scopeCapable: true,
          focusCapable: true,
          threadShared: false,
          confirmed: null
        })
      ).toEqual({
        action: 'apply',
        focus: { kind: hint.kind, id: hint.id }
      });
    }
  });
});

describe('machineScopeCoversHint', () => {
  it('matches on the canonical machine id when the confirmed scope carries ids', () => {
    expect(machineScopeCoversHint(confirmed, pump)).toBe(true);
    expect(
      machineScopeCoversHint({ ...confirmed, machineIds: [8] }, pump)
    ).toBe(false);
    expect(machineScopeCoversHint({ ...confirmed, machineIds: [] }, pump)).toBe(
      false
    );
    expect(
      machineScopeCoversHint({ ...confirmed, machineIds: [7, 8] }, pump)
    ).toBe(false);
  });

  it('never accepts a display label as a binding when ids are not observed', () => {
    expect(
      machineScopeCoversHint(
        { mode: 'explicit_assets', displayLabel: 'Pump A', machineCount: 1 },
        pump
      )
    ).toBe(false);
    expect(
      machineScopeCoversHint(
        { mode: 'explicit_assets', displayLabel: 'Pump A', machineCount: 2 },
        pump
      )
    ).toBe(false);
  });

  it('never treats a fleet or unconfirmed scope as covering a machine hint', () => {
    expect(
      machineScopeCoversHint(
        {
          mode: 'all_authorized_assets',
          machineIds: [7],
          displayLabel: 'Pump A'
        },
        pump
      )
    ).toBe(false);
    expect(machineScopeCoversHint(null, pump)).toBe(false);
    expect(machineScopeCoversHint(confirmed, null)).toBe(false);
    expect(machineScopeCoversHint(confirmed, sealCard)).toBe(false);
  });
});

describe('stale turn guard: late A can never navigate B', () => {
  const base = {
    turnSessionId: 'session-A',
    turnThreadId: 'thread-A',
    currentSessionId: 'session-A',
    createdThreadId: 'thread-A',
    visibleThreadId: 'thread-A'
  };

  it('accepts a turn of the current session on its own thread', () => {
    expect(isFreshSessionTurn(base)).toBe(true);
    expect(canAdoptSessionThread(base)).toBe(false);
  });

  it('supports initial server-assigned thread adoption', () => {
    const adoption = { ...base, turnThreadId: 'thread-server' };
    expect(isFreshSessionTurn(adoption)).toBe(true);
    expect(canAdoptSessionThread(adoption)).toBe(true);
  });

  it('adopts the initial server-created thread only while nothing is selected', () => {
    const unbound = {
      turnSessionId: 'session-A',
      turnThreadId: 'thread-server',
      currentSessionId: 'session-A',
      createdThreadId: null,
      visibleThreadId: null
    };
    expect(isFreshSessionTurn(unbound)).toBe(true);
    expect(canAdoptSessionThread(unbound)).toBe(true);
  });

  it('keeps later same-session results on the adopted thread fresh', () => {
    const adopted = {
      turnSessionId: 'session-A',
      turnThreadId: 'thread-server',
      currentSessionId: 'session-A',
      createdThreadId: null,
      visibleThreadId: 'thread-server'
    };
    expect(isFreshSessionTurn(adopted)).toBe(true);
    expect(canAdoptSessionThread(adopted)).toBe(false);
  });

  it('never lets a late unbound-session result adopt after a deliberate switch', () => {
    // RV15: the session was created unbound but the user has since selected
    // thread-X on purpose — a late result must not navigate it anywhere.
    const lateUnbound = {
      turnSessionId: 'session-A',
      turnThreadId: 'thread-server',
      currentSessionId: 'session-A',
      createdThreadId: null,
      visibleThreadId: 'thread-X'
    };
    expect(isFreshSessionTurn(lateUnbound)).toBe(false);
    expect(canAdoptSessionThread(lateUnbound)).toBe(false);
  });

  it('drops late results of an old session entirely', () => {
    const late = {
      ...base,
      currentSessionId: 'session-B',
      createdThreadId: 'thread-B',
      visibleThreadId: 'thread-B'
    };
    expect(isFreshSessionTurn(late)).toBe(false);
    expect(canAdoptSessionThread(late)).toBe(false);
  });

  it('never navigates back after a deliberate thread switch', () => {
    // Session A was created on thread-A but the visible thread already moved
    // to thread-B before the controller teardown landed.
    const switched = { ...base, visibleThreadId: 'thread-B' };
    expect(isFreshSessionTurn(switched)).toBe(false);
    expect(canAdoptSessionThread(switched)).toBe(false);
  });

  it('drops results with no live session and turns with no thread', () => {
    expect(isFreshSessionTurn({ ...base, currentSessionId: null })).toBe(false);
    expect(canAdoptSessionThread({ ...base, turnThreadId: null })).toBe(false);
    expect(canAdoptSessionThread({ ...base, turnSessionId: null })).toBe(false);
  });
});

describe('runContextPreflight: shared typed/voice entry', () => {
  const deps = (
    over: Partial<Parameters<typeof runContextPreflight>[0]> = {}
  ) => {
    const applyScope = vi.fn(async () => ({ ok: true }));
    const applyFocus = vi.fn(async () => focusAck);
    return {
      applyScope,
      applyFocus,
      contextUnchanged: () => true,
      ...over,
      hint: over.hint === undefined ? pump : over.hint,
      scopeCapable: over.scopeCapable ?? true,
      focusCapable: over.focusCapable ?? true,
      threadShared: over.threadShared ?? false,
      confirmed: over.confirmed ?? null,
      threadId: over.threadId ?? 'thread-1'
    } as Parameters<typeof runContextPreflight>[0] & {
      applyScope: ReturnType<typeof vi.fn>;
      applyFocus: ReturnType<typeof vi.fn>;
    };
  };

  it('commits the machine hint (scope then focus) before the send/session', async () => {
    const d = deps();
    await expect(runContextPreflight(d)).resolves.toEqual({
      ok: true,
      threadId: 'thread-1',
      focusVersion: 4
    });
    expect(d.applyScope).toHaveBeenCalledExactlyOnceWith({
      mode: 'explicit_assets',
      machine_ids: [7],
      display_label: 'Pump A'
    });
    // The focus PUT is the last step: its exact ack is the entry's binding.
    expect(d.applyFocus).toHaveBeenCalledExactlyOnceWith({
      kind: 'machine',
      id: 7
    });
    expect(d.applyScope.mock.invocationCallOrder[0]).toBeLessThan(
      d.applyFocus.mock.invocationCallOrder[0]
    );
  });

  it('reuses a confirmed matching scope with no redundant scope PUT', async () => {
    const d = deps({ confirmed });
    await expect(runContextPreflight(d)).resolves.toEqual({
      ok: true,
      threadId: 'thread-1',
      focusVersion: 4
    });
    expect(d.applyScope).not.toHaveBeenCalled();
    expect(d.applyFocus).toHaveBeenCalledExactlyOnceWith({
      kind: 'machine',
      id: 7
    });
  });

  it('fetches the full scope when ids are unknown and still commits canonical ids', async () => {
    // The label says "Pump A" but the server's ids name a DIFFERENT machine:
    // the fetch (not the label) decides, and the hint id is what is applied.
    const fetchScope = vi.fn(async () => ({
      mode: 'explicit_assets',
      displayLabel: 'Pump A',
      machineIds: [8],
      machineCount: 1
    }));
    const d = deps({
      confirmed: {
        mode: 'explicit_assets',
        displayLabel: 'Pump A',
        machineCount: 1
      },
      fetchScope
    });
    await expect(runContextPreflight(d)).resolves.toEqual({
      ok: true,
      threadId: 'thread-1',
      focusVersion: 4
    });
    expect(fetchScope).toHaveBeenCalledOnce();
    expect(d.applyScope).toHaveBeenCalledExactlyOnceWith({
      mode: 'explicit_assets',
      machine_ids: [7],
      display_label: 'Pump A'
    });
  });

  it('falls back to an explicit scope commit when the fetch yields no ids', async () => {
    // No canonical ids can be confirmed: a label match is never an option,
    // so the entry commits the explicit scope itself (server re-authorizes).
    const d = deps({
      confirmed: {
        mode: 'explicit_assets',
        displayLabel: 'Pump A',
        machineCount: 1
      },
      fetchScope: vi.fn(async () => null)
    });
    await expect(runContextPreflight(d)).resolves.toEqual({
      ok: true,
      threadId: 'thread-1',
      focusVersion: 4
    });
    expect(d.applyScope).toHaveBeenCalledExactlyOnceWith({
      mode: 'explicit_assets',
      machine_ids: [7],
      display_label: 'Pump A'
    });
  });

  it('blocks the entry when the explicit scope selection fails (never sends unscoped)', async () => {
    const d = deps({
      applyScope: vi.fn(async () => ({
        ok: false,
        code: 'scope_update_rejected'
      }))
    });
    await expect(runContextPreflight(d)).resolves.toEqual({
      ok: false,
      code: 'scope_update_rejected'
    });
    expect(d.applyFocus).not.toHaveBeenCalled();
  });

  it('blocks with the conflict code when the scope update is rejected', async () => {
    const d = deps({
      applyScope: vi.fn(async () => ({
        ok: false,
        code: 'scope_version_conflict'
      }))
    });
    await expect(runContextPreflight(d)).resolves.toEqual({
      ok: false,
      code: 'scope_version_conflict'
    });
  });

  it('blocks the entry when the focus candidate cannot be confirmed', async () => {
    for (const code of ['focus_update_rejected', 'focus_version_conflict']) {
      const d = deps({
        confirmed,
        applyFocus: vi.fn(async () => ({ ok: false, code }))
      });
      await expect(runContextPreflight(d)).resolves.toEqual({
        ok: false,
        code
      });
    }
    // A denied/conflicting focus never proceeds display-only.
    const denied = deps({
      confirmed,
      applyFocus: vi.fn(async () => ({
        ok: false,
        code: 'focus_update_rejected'
      }))
    });
    await expect(runContextPreflight(denied)).resolves.toEqual({
      ok: false,
      code: 'focus_update_rejected'
    });
    expect(denied.applyScope).not.toHaveBeenCalled();
  });

  it('fails closed when the context changed while a commit was in flight', async () => {
    let changed = false;
    const d = deps({
      applyScope: vi.fn(async () => {
        changed = true;
        return { ok: true };
      }),
      applyFocus: vi.fn(async () => ({ ok: true, version: 4 })),
      contextUnchanged: () => !changed
    });
    await expect(runContextPreflight(d)).resolves.toEqual({
      ok: false,
      code: 'context_changed'
    });
  });

  it('fails closed when the context changes during the focus ack', async () => {
    let changed = false;
    const d = deps({
      confirmed,
      applyFocus: vi.fn(async () => {
        changed = true;
        return { ok: true, version: 4 };
      }),
      contextUnchanged: () => !changed
    });
    await expect(runContextPreflight(d)).resolves.toEqual({
      ok: false,
      code: 'context_changed'
    });
  });

  it('blocks on a shared thread and while a scope application is in flight', async () => {
    const shared = deps({ threadShared: true });
    await expect(runContextPreflight(shared)).resolves.toEqual({
      ok: false,
      code: 'shared-thread'
    });
    const busy = deps({ busy: true });
    await expect(runContextPreflight(busy)).resolves.toEqual({
      ok: false,
      code: 'in-flight'
    });
    expect(shared.applyScope).not.toHaveBeenCalled();
    expect(shared.applyFocus).not.toHaveBeenCalled();
    expect(busy.applyScope).not.toHaveBeenCalled();
    expect(busy.applyFocus).not.toHaveBeenCalled();
  });

  it('blocks explicit selections the backend cannot confirm', async () => {
    const noScope = deps({ scopeCapable: false });
    await expect(runContextPreflight(noScope)).resolves.toEqual({
      ok: false,
      code: 'scope-unsupported'
    });
    const noFocus = deps({ focusCapable: false });
    await expect(runContextPreflight(noFocus)).resolves.toEqual({
      ok: false,
      code: 'focus-unsupported'
    });
    expect(noScope.applyScope).not.toHaveBeenCalled();
    expect(noFocus.applyFocus).not.toHaveBeenCalled();
  });

  it('commits task/work-order targets as focus only, with no scope work', async () => {
    for (const hint of [sealCard, job]) {
      const d = deps({ hint, confirmed: null });
      await expect(runContextPreflight(d)).resolves.toEqual({
        ok: true,
        threadId: 'thread-1',
        focusVersion: 4
      });
      expect(d.applyScope).not.toHaveBeenCalled();
      expect(d.applyFocus).toHaveBeenCalledExactlyOnceWith({
        kind: hint.kind,
        id: hint.id
      });
    }
  });

  it('proceeds without any work when there is no pending target', async () => {
    const noHint = deps({ hint: null });
    await expect(runContextPreflight(noHint)).resolves.toEqual({
      ok: true,
      threadId: 'thread-1'
    });
    expect(noHint.applyScope).not.toHaveBeenCalled();
    expect(noHint.applyFocus).not.toHaveBeenCalled();
  });
});
