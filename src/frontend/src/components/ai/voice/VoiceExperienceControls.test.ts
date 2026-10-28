import { MantineProvider, Switch } from '@mantine/core';
import { Children, type ReactNode, createElement, isValidElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { beforeEach, expect, it, vi } from 'vitest';

const fixture = vi.hoisted(() => ({
  preferences: {
    voiceReviewEveryRequest: false,
    voiceEarcons: false,
    voiceSrAnnounceTranscripts: false,
    voiceSpeakerNoticeSeen: true,
    voiceProgressMode: 'brief',
    setVoicePreferences: vi.fn()
  },
  session: {
    session: { id: 'fixture-session' },
    mode: 'continuous',
    queued: 0,
    outputs: [],
    outputSelectionSupported: false,
    revisions: []
  }
}));
vi.mock('@lingui/core/macro', () => ({
  t: (parts: TemplateStringsArray, ...args: unknown[]) =>
    parts.reduce((text, part, i) => text + part + (args[i] ?? ''), '')
}));
vi.mock('../../../states/LocalState', () => ({
  useLocalState: () => fixture.preferences
}));
vi.mock('../../../states/VoiceSessionState', () => ({
  useVoiceSessionState: () => fixture.session,
  useVoiceSurfaceState: { getState: () => ({ openFullscreen: vi.fn() }) },
  voiceController: {}
}));
import { VoiceExperienceControls } from './VoiceExperienceControls';

beforeEach(() => {
  fixture.preferences.voiceReviewEveryRequest = false;
  fixture.preferences.setVoicePreferences.mockClear();
});

const render = () =>
  renderToStaticMarkup(
    createElement(
      MantineProvider,
      {},
      createElement(VoiceExperienceControls, { embedded: true })
    )
  );
function reviewInput(markup: string): string {
  const label = [...markup.matchAll(/<label\b[^>]*>[\s\S]*?<\/label>/g)].find(
    ([html]) => html.includes('Review every request before sending')
  )?.[0];
  const id = /\bfor="([^"]+)"/.exec(label ?? '')?.[1];
  expect(id).toBeTruthy();
  return (
    [...markup.matchAll(/<input\b[^>]*>/g)]
      .map(([input]) => input)
      .find((input) => input.includes(`id="${id}"`)) ?? ''
  );
}

it('renders a native labeled switch with an associated explanation and the default off state', () => {
  const markup = render();
  const input = reviewInput(markup);
  expect(input).toContain('type="checkbox"');
  expect(input).toContain('role="switch"');
  expect(input).not.toContain('checked=""');
  expect(input).not.toContain('tabindex="-1"');
  const description = /aria-describedby="([^"]+)"/.exec(input)?.[1];
  expect(description).toBeTruthy();
  expect(markup).toContain(`id="${description}"`);
  expect(markup).toContain('say Confirm to send or Discard to try again');
  expect(markup).toContain(
    'Action approvals still require their own confirmation'
  );
});

it('renders an opted-in checked state independently of screen-reader announcements', () => {
  fixture.preferences.voiceReviewEveryRequest = true;
  expect(reviewInput(render())).toContain('checked=""');
  expect(fixture.preferences.voiceSrAnnounceTranscripts).toBe(false);
});

it('connects the native input change to only the review preference', () => {
  const find = (
    node: ReactNode
  ): ((event: { currentTarget: { checked: boolean } }) => void) | undefined => {
    if (!isValidElement(node)) return;
    const props = node.props as {
      label?: string;
      children?: ReactNode;
      onChange?: (event: { currentTarget: { checked: boolean } }) => void;
    };
    if (
      node.type === Switch &&
      props.label === 'Review every request before sending'
    )
      return props.onChange;
    for (const child of Children.toArray(props.children)) {
      const result = find(child);
      if (result) return result;
    }
  };
  const change = find(VoiceExperienceControls({ embedded: true }));
  expect(change).toBeTypeOf('function');
  change?.({ currentTarget: { checked: true } });
  expect(
    fixture.preferences.setVoicePreferences
  ).toHaveBeenCalledExactlyOnceWith({ voiceReviewEveryRequest: true });
});
