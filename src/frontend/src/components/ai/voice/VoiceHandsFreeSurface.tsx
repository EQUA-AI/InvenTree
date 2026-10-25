import { t } from '@lingui/core/macro';
import { Button, Group, Stack, Text } from '@mantine/core';
import {
  useVoiceSessionState,
  useVoiceSurfaceState,
  voiceController
} from '../../../states/VoiceSessionState';
import { AssistantModal } from '../../aichat/AssistantModal';
import { ConversationFocusControl } from '../../aichat/ConversationFocusControl';
import { VoiceDecisionCard } from '../VoiceDecisionCard';
import { VoiceSessionControl } from '../VoiceSessionControl';
import { VoiceTranscript } from '../VoiceTranscript';
import { VoiceExperienceControls } from './VoiceExperienceControls';
import { VoiceProcedureControls } from './VoiceProcedureControls';
import './voiceSurface.css';

export function VoiceHandsFreeSurface() {
  const surface = useVoiceSurfaceState();
  return (
    <AssistantModal
      opened={surface.fullscreen}
      onClose={surface.closeFullscreen}
      fullScreen
      title={t`Hands-free voice`}
      closeOnEscape={false}
      closeButtonProps={{ size: 44, 'aria-label': t`Minimize voice` }}
    >
      <VoiceHandsFreeContent />
    </AssistantModal>
  );
}

/** Route and modal share one control/card implementation and one controller. */
export function VoiceHandsFreeContent({
  embedded = false
}: { embedded?: boolean }) {
  const s = useVoiceSessionState();
  const surface = useVoiceSurfaceState();
  return (
    <Stack
      className='voice-hands-free'
      data-voice-surface
      data-testid='voice-hands-free'
    >
      <Group>
        <Text
          fw={700}
        >{t`Voice stays active only while this tab is visible.`}</Text>
        {!embedded && (
          <Button
            mih={44}
            onClick={surface.closeFullscreen}
          >{t`Minimize`}</Button>
        )}
      </Group>
      <VoiceSessionControl
        {...s}
        webrtcPreview={s.session?.webrtc_preview}
        onStart={surface.requestStart}
        onEnd={() => void voiceController.end()}
        onCancel={() => void voiceController.cancel()}
        onToggleMute={voiceController.toggleMute}
        onConfirmTranscript={() => void voiceController.confirmPending()}
        onDiscardTranscript={voiceController.discardPending}
      />
      {/* Plan C: the same Talking about chip as the drawer (server-confirmed
        focus only) — the hands-free surface may change or clear it. */}
      <ConversationFocusControl />
      <VoiceTranscript
        partial={s.partial}
        listening={s.mic === 'listening'}
        pendingConfirm={s.pendingConfirm}
        holdPrompt={s.holdPrompt}
      />
      {(embedded || surface.fullscreen) && <VoiceDecisionCard />}
      <VoiceExperienceControls embedded={embedded} />
      <VoiceProcedureControls />
    </Stack>
  );
}
