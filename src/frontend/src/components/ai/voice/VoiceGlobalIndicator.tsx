import { useInvenTreeHotkeys } from '@lib/functions/Events';
import { getBaseUrl } from '@lib/functions/Navigation';
import { t } from '@lingui/core/macro';
import { Button, Group, Text, VisuallyHidden } from '@mantine/core';
import { useEffect } from 'react';
import { Link, useInRouterContext } from 'react-router-dom';
import { useThrottledAriaLive } from '../../../hooks/useThrottledAriaLive';
import { useAIChatState } from '../../../states/AIChatState';
import { useLocalState } from '../../../states/LocalState';
import { useVoiceDecisionState } from '../../../states/VoiceDecisionState';
import {
  useVoiceSessionState,
  useVoiceSurfaceState,
  voiceController
} from '../../../states/VoiceSessionState';
import { noticeText } from './VoiceExperienceControls';
import { isVoiceShortcut, voiceEscape } from './voiceShortcuts';

export function VoiceGlobalIndicator({
  embedded = false
}: { embedded?: boolean }) {
  const routed = useInRouterContext();
  const s = useVoiceSessionState();
  const decision = useVoiceDecisionState((state) => state.decision);
  const sr = useLocalState((state) => state.voiceSrAnnounceTranscripts);
  useInvenTreeHotkeys([
    [
      'mod+shift+v',
      t`Start or end voice`,
      (event) => {
        if (
          !isVoiceShortcut(event) ||
          !useVoiceSessionState.getState().capability?.enabled
        )
          return;
        event.preventDefault();
        if (useVoiceSessionState.getState().session) void voiceController.end();
        else useVoiceSurfaceState.getState().requestStart();
      }
    ]
  ]);
  useEffect(() => {
    const handle = (event: KeyboardEvent) => {
      const state = useVoiceSessionState.getState();
      const action = voiceEscape(
        event,
        ['pending', 'playing'].includes(state.playback)
      );
      if (!action) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      if (action === 'stop') void voiceController.cancel();
      else {
        useVoiceSurfaceState.getState().closeFullscreen();
        useAIChatState.getState().close();
        voiceController.minimize();
      }
    };
    window.addEventListener('keydown', handle, true);
    return () => window.removeEventListener('keydown', handle, true);
  }, []);
  const status =
    noticeText(s.notice) ||
    (s.mic === 'listening'
      ? t`Voice is listening`
      : s.session
        ? t`Voice microphone paused`
        : t`Voice ended`);
  const announced = useThrottledAriaLive(
    sr ? s.pendingConfirm?.text || s.partial?.text || status : status
  );
  if (!s.capability?.enabled) return null;
  return (
    <Group gap='xs' data-testid='voice-global-indicator'>
      {!embedded &&
        (routed ? (
          <Button
            component={Link}
            to='/voice'
            mih={44}
            variant='subtle'
          >{t`Back to voice`}</Button>
        ) : (
          <Button
            component='a'
            href={`${getBaseUrl().replace(/\/$/, '')}/voice`}
            mih={44}
            variant='subtle'
          >{t`Back to voice`}</Button>
        ))}
      <VisuallyHidden component='output' aria-live='polite' aria-atomic='true'>
        {announced}
      </VisuallyHidden>
      {s.session && !embedded && (
        <>
          <Button
            mih={44}
            variant='light'
            onClick={() => {
              useAIChatState.getState().close();
              useVoiceSurfaceState.getState().openFullscreen();
            }}
            aria-label={t`Open hands-free voice`}
            data-testid='voice-minimized-indicator'
          >
            {s.mic === 'listening' ? t`Voice listening` : t`Voice paused`}
            {decision?.state === 'presented' ? t` — confirmation waiting` : ''}
            {decision?.state === 'presented'
              ? `: ${decision.target_label}`
              : ''}
          </Button>
          <Button
            mih={44}
            variant='subtle'
            color='red'
            onClick={() => void voiceController.end()}
          >{t`End voice`}</Button>
        </>
      )}
      <Text size='xs'>{t`Voice: Ctrl/⌘+Shift+V · Chat: Ctrl/⌘+K`}</Text>
    </Group>
  );
}
