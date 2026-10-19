import type {
  HandledAction,
  HandledActionLink,
  HandledActionState,
  HandledActions
} from '@lib/types/WorkExecution';

/** Rows shown before "Show all activity" (blueprint 15.4: four to eight). */
export const HANDLED_PREVIEW_ROWS = 6;

const STATES: HandledActionState[] = [
  'completed',
  'active',
  'failed',
  'unknown'
];

export type HandledReading =
  | { kind: 'unsupported' }
  | {
      kind: 'ready';
      projection: HandledActions;
      /** Active work first, then everything else newest first. */
      handling: HandledAction[];
      recorded: HandledAction[];
      notEnabled: string[];
    };

function isAction(value: unknown): value is HandledAction {
  if (!value || typeof value !== 'object') return false;
  const row = value as Partial<HandledAction>;
  return (
    typeof row.id === 'string' &&
    typeof row.kind === 'string' &&
    typeof row.status === 'string' &&
    STATES.includes(row.state as HandledActionState)
  );
}

/**
 * Accept only the projection this page knows (`handled_actions.v1`).
 *
 * Anything else, including an absent field from an older server, is
 * unsupported: the page says the records are not supplied rather than
 * claiming AIMMS did nothing. Counts are recomputed from the rows so a badge
 * can never disagree with the list beneath it.
 */
export function readHandledActions(value: unknown): HandledReading {
  if (!value || typeof value !== 'object') return { kind: 'unsupported' };
  const projection = value as Partial<HandledActions>;
  if (
    projection.rule !== 'handled_actions.v1' ||
    !Array.isArray(projection.actions) ||
    !Array.isArray(projection.sources) ||
    !projection.actions.every(isAction)
  )
    return { kind: 'unsupported' };
  const actions = projection.actions;
  const counts = Object.fromEntries(
    STATES.map((state) => [
      state,
      actions.filter((row) => row.state === state).length
    ])
  ) as Record<HandledActionState, number>;
  const served = projection.counts ?? ({} as Record<string, number>);
  if (STATES.some((state) => served[state] !== counts[state]))
    return { kind: 'unsupported' };
  return {
    kind: 'ready',
    projection: { ...(projection as HandledActions), counts },
    handling: actions.filter((row) => row.state === 'active'),
    recorded: actions.filter((row) => row.state !== 'active'),
    notEnabled: projection.sources
      .filter((source) => source.state === 'not_enabled')
      .map((source) => source.key)
  };
}

/** Where a row's saved result lives, as a route relative to the Work page. */
export function handledLinkTarget(
  link: HandledActionLink | null,
  params: URLSearchParams
): string | null {
  if (!link) return null;
  if (link.kind === 'repair_packet')
    return Number.isSafeInteger(link.id) && link.id > 0
      ? `/repair/packets/${link.id}/`
      : null;
  if (link.kind === 'work_panel') {
    const next = new URLSearchParams(params);
    if (link.panel === 'work') next.delete('work_panel');
    else next.set('work_panel', link.panel);
    const query = next.toString();
    return query ? `?${query}` : '?';
  }
  return null;
}
