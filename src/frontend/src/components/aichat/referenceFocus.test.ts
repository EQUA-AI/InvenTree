import {
  displayAction,
  displayArtifact,
  displayAuthority,
  displayCloseout,
  displayComponent,
  displayFocus,
  displayReading,
  displayRequest,
  displayUpdate,
  displayWork
} from '@lib/types/CanonicalDisplay';
import { describe, expect, it } from 'vitest';
import {
  focusReturnPath,
  parseWorkFocusAcknowledgement,
  parseWorkFocusIdentity,
  reloadedWorkFocus
} from './workFocus';
const component = {
  machine_id: 18,
  rule: 'reference_positions.v1' as const,
  position_id: 'bearing',
  slot_code: null,
  revision: 'a'.repeat(64)
};
const focus = {
  schema_version: 3,
  kind: 'reference_position',
  work_order_id: null,
  lifecycle_version: null,
  machine_id: 18,
  component
};
const evidence = {
  kind: 'closeout_reading' as const,
  id: 42,
  revision: 'b'.repeat(64)
};
describe('canonical reference focus', () => {
  it('accepts exact standalone and composed Work/request identities', () => {
    expect(parseWorkFocusIdentity(focus)).toEqual(focus);
    expect(displayFocus(parseWorkFocusIdentity(focus)!).source).toEqual({
      record: 'reference_position',
      id: 'bearing',
      machine_id: 18,
      revision: component.revision,
      provider: 'reference_positions.v1'
    });
    expect(
      parseWorkFocusIdentity({
        ...focus,
        kind: 'work_order',
        work_order_id: 73,
        lifecycle_version: 7,
        request_id: 5,
        request_version: 2,
        evidence
      })?.evidence
    ).toEqual(evidence);
  });
  it.each([
    { component: { ...component, rule: 'guessed' } },
    { component: { ...component, machine_id: 19 } },
    { component: { ...component, position_id: 'made-up' } },
    { component: { ...component, revision: '' } },
    { work_order_id: 0 },
    { request_id: 5 },
    { kind: 'work_order' },
    { extra: true },
    { schema_version: 4 }
  ])('refuses incomplete or unknown identity %o', (change) =>
    expect(parseWorkFocusIdentity({ ...focus, ...change })).toBeNull()
  );
  it('refuses a mismatched confirmation even on a 200', () => {
    const ack = {
      schema_version: 1,
      thread_id: 't',
      focus,
      receipt: 'opaque',
      expires_in: 300
    };
    expect(
      parseWorkFocusAcknowledgement(
        ack,
        't',
        null,
        null,
        undefined,
        undefined,
        component
      )
    ).not.toBeNull();
    expect(
      parseWorkFocusAcknowledgement(
        ack,
        't',
        null,
        null,
        undefined,
        undefined,
        { ...component, position_id: 'motor' }
      )
    ).toBeNull();
    expect(parseWorkFocusAcknowledgement(ack, 't', null, null)).toBeNull();
  });
  it('compares semantic coordinates independent of JSON key order', () => {
    const ack = {
      schema_version: 1,
      thread_id: 't',
      focus,
      receipt: 'opaque',
      expires_in: 300
    };
    const reversed = Object.fromEntries(
      Object.entries(component).reverse()
    ) as typeof component;
    expect(
      parseWorkFocusAcknowledgement(
        ack,
        't',
        null,
        null,
        undefined,
        undefined,
        reversed
      )
    ).not.toBeNull();
  });
  it('returns to exact position and preserves only the supported schematic mode', () => {
    const identity = parseWorkFocusIdentity(focus)!;
    expect(
      focusReturnPath(identity, 'https://evil.invalid/?view=schematic')
    ).toBe('/machines/machine/18/equipment/?position=bearing&view=schematic');
    expect(focusReturnPath(identity)).toBe(
      '/machines/machine/18/equipment/?position=bearing'
    );
  });
  it('absent authority and missing source revisions remain explicit', () => {
    expect(displayAuthority()).toEqual({
      state: 'not_recorded',
      record: null,
      source: null
    });
    expect(
      displayComponent(18, 'reference_positions.v1', { id: 'bearing' })
    ).toBeNull();
    const row = {
      id: 42,
      label: 'Zero reading',
      raw_text: '0',
      value: '0',
      unit: 'degC',
      required: false,
      verification_state: 'pending',
      warnings: []
    };
    expect(displayReading(row).record).toBe(row);
    expect(displayReading(row).source.revision).toBeNull();
    expect(displayReading(row).focus).toBeNull();
    expect(
      displayReading({ ...row, focus_revision: evidence.revision }).focus
    ).toEqual(evidence);
  });
});

it('reload preserves independent reference states and drops unknown vocabulary', () => {
  const saved = reloadedWorkFocus(
    { ...focus, evidence: { kind: 'media', id: 5, revision: 'b'.repeat(64) } },
    'changed',
    { component: 'current', evidence: 'changed', extra: 'current' }
  );
  expect(saved?.references).toEqual({
    component: 'current',
    evidence: 'changed'
  });
  expect(
    reloadedWorkFocus(focus, 'healthy', { component: 'healthy' })?.state
  ).toBeUndefined();
});
it('all display seams retain canonical IDs, enums and unmodified source objects', () => {
  const work = {
    id: 73,
    reference: 'WO-73',
    title: 'Synthetic',
    lifecycle_status: 'verifying',
    lifecycle_version: 7
  };
  expect(displayWork(work).record).toBe(work);
  expect(displayWork(work).source).toEqual({
    record: 'work_order',
    id: 73,
    revision: 7,
    provider: null
  });
  const request = { id: 5, version: 2, status: 'pending_external' } as any;
  expect(displayRequest(request).state).toBe('pending_external');
  expect(displayRequest(request).source.provider).toBeNull();
  expect(displayRequest(request).record).toBe(request);
  const artifact = { id: 9, supersedes: 8, source_turn_id: 'turn' } as any;
  expect(displayArtifact(artifact).record).toBe(artifact);
  const action = {
    id: 'native:9',
    source: { record: 'closeout_effect', id: 9, provider: null },
    state: 'unknown',
    status: 'dispatching'
  } as any;
  expect(displayAction(action).state).toBe('unknown');
  expect(displayAction(action).source.id).toBe(9);
  const update = {
    id: 9,
    effect_type: 'notification',
    status: 'dispatching',
    attempts: 1,
    result_reference: ''
  };
  expect(displayUpdate(update).record).toBe(update);
  expect(displayUpdate(update).state).toBe('dispatching');
  const capture = { id: 3, revision: 2, status: 'reviewed' } as any;
  expect(displayCloseout(capture).source.revision).toBe(2);
  expect(displayCloseout(capture).record).toBe(capture);
  const authority = { id: 6, status: null, version: 3 };
  expect(displayAuthority(authority).state).toBeNull();
  expect(displayAuthority(authority).record).toBe(authority);
  expect(displayAuthority(authority).source?.record).toBeNull();
  const attributed = {
    ...authority,
    source: { record: 'provider_supplied_kind', provider: 'fixture-provider' }
  };
  expect(displayAuthority(attributed).source).toEqual({
    record: 'provider_supplied_kind',
    id: 6,
    revision: 3,
    provider: 'fixture-provider'
  });
});

describe('independent reference availability', () => {
  it('keeps the canonical Work link when evidence or the slot is unavailable', () => {
    const work = {
      ...focus,
      kind: 'work_order',
      work_order_id: 73,
      lifecycle_version: 7,
      request_id: 5,
      request_version: 2,
      evidence
    };
    const parsed = reloadedWorkFocus(work, 'current', {
      component: 'unavailable',
      evidence: 'unavailable'
    });
    expect(parsed?.state).toBe('current');
    expect(parsed?.references).toEqual({
      component: 'unavailable',
      evidence: 'unavailable'
    });
    expect(parsed?.returnPath).toBe(
      '/maintenance/work-orders/73?work_panel=diagnose&investigation_request=5'
    );
  });
});
