import { t } from '@lingui/core/macro';
import { Button, Group, Modal, Stack, Text } from '@mantine/core';
import {
  useVoiceSessionState,
  useVoiceSurfaceState,
  voiceController
} from '../../../states/VoiceSessionState';
import { VoiceDecisionCard } from '../VoiceDecisionCard';
import { VoiceSessionControl } from '../VoiceSessionControl';
import { VoiceTranscript } from '../VoiceTranscript';
import { VoiceExperienceControls } from './VoiceExperienceControls';
import { VoiceProcedureControls } from './VoiceProcedureControls';
import './voiceSurface.css';

export function VoiceHandsFreeSurface() {
  const surface = useVoiceSurfaceState();
  return (
    <Modal
      opened={surface.fullscreen}
      onClose={surface.closeFullscreen}
      fullScreen
      title={t`Hands-free voice`}
      closeOnEscape={false}
      closeButtonProps={{ size: 44, 'aria-label': t`Minimize voice` }}
    >
      <VoiceHandsFreeContent />
    </Modal>
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
