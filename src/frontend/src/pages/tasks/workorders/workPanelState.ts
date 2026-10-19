export const WORK_PANELS = [
  'work',
  'diagnose',
  'plan',
  'parts',
  'verify',
  'closeout',
  'history'
] as const;
export type WorkPanel = (typeof WORK_PANELS)[number];

export function workPanelState(params: URLSearchParams): WorkPanel | null {
  const panel = params.get('work_panel') ?? 'work';
  return WORK_PANELS.some((name) => name === panel)
    ? (panel as WorkPanel)
    : null;
}

export function workPanelParams(
  params: URLSearchParams,
  panel: WorkPanel
): URLSearchParams {
  const next = new URLSearchParams(params);
  if (panel === 'work') next.delete('work_panel');
  else next.set('work_panel', panel);
  return next;
}
