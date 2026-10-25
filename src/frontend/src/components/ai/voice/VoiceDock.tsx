import { t } from '@lingui/core/macro';
import { Button, Group, Stack, Text } from '@mantine/core';
import { useState } from 'react';
import { useVoiceSessionState } from '../../../states/VoiceSessionState';
import { AssistantModal } from '../../aichat/AssistantModal';
import workspaceStyles from '../../aichat/AssistantWorkspace.module.css';
import { VoiceTranscript } from '../VoiceTranscript';
import { PushToTalkButton } from './PushToTalkButton';
import {
  VoiceActiveStrip,
  type VoiceComposerControlProps
} from './VoiceComposerControl';
import { VoiceExperienceControls, noticeText } from './VoiceExperienceControls';

/** Essential safety controls stay visible; secondary settings have one dialog. */
export function VoiceDock(props: VoiceComposerControlProps) {
  const s = useVoiceSessionState();
  const [options, setOptions] = useState(false);
  return (
    <Stack
      gap={4}
      mt={4}
      data-testid='voice-dock'
      className={workspaceStyles.voiceDock}
    >
      <VoiceActiveStrip {...props} />
      <VoiceTranscript
        partial={s.partial}
        listening={s.state === 'listening'}
        pendingConfirm={s.pendingConfirm}
        holdPrompt={s.holdPrompt}
      />
      {s.mode === 'push_to_talk' && (
        <PushToTalkButton
          disabled={s.muted || s.hidden || s.transport !== 'connected'}
        />
      )}
      <Group gap={4} justify='space-between'>
        <Button
          size='compact-xs'
          variant='subtle'
          onClick={() => setOptions(true)}
        >{t`Voice options`}</Button>
        {s.queued > 0 && <Text size='xs'>{t`${s.queued} waiting`}</Text>}
      </Group>
      {s.notice && (
        <Text component='output' aria-live='polite' size='xs'>
          {noticeText(s.notice)}
        </Text>
      )}
      <AssistantModal
        opened={options && !!s.session}
        onClose={() => setOptions(false)}
        title={t`Voice options`}
      >
        <Stack>
          <Group>
            <Button variant='default' onClick={props.onToggleMute}>
              {props.muted ? t`Unmute microphone` : t`Mute microphone`}
            </Button>
            <Button
              variant='default'
              onClick={props.onCancel}
            >{t`Stop speaking`}</Button>
            <Button
              color='red'
              onClick={() => {
                setOptions(false);
                props.onEnd();
              }}
            >{t`End voice`}</Button>
          </Group>
          <VoiceExperienceControls />
        </Stack>
      </AssistantModal>
    </Stack>
  );
}
