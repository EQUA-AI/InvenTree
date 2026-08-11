import { t } from '@lingui/core/macro';
import {
  Alert,
  Badge,
  Button,
  Group,
  Modal,
  Stack,
  Text,
  Textarea
} from '@mantine/core';
import { IconAlertTriangle, IconMicrophone } from '@tabler/icons-react';
import { useCallback, useEffect, useState } from 'react';

import { useVoiceCapture } from '../../../hooks/useVoiceCapture';

/**
 * B4 (S32b): dictate a closeout narrative through the governed voice-capture
 * contract — consent (create), transcript review (revise), hash-bound accept,
 * then commit, which hands the exact accepted revision to the closeout
 * wizard server-side (accept_voice_handoff). The transcript text itself
 * comes from the technician's device dictation or keyboard; this flow's job
 * is provenance, not audio: the narrative lands with source_type=voice and
 * a transcript_reference bound to the accepted revision's content hash.
 */
export default function DictateCloseoutModal({
  opened,
  onClose,
  workOrderId,
  workOrderVersion,
  onCommitted
}: Readonly<{
  opened: boolean;
  onClose: () => void;
  workOrderId: number;
  workOrderVersion: number;
  onCommitted: () => void;
}>) {
  const voice = useVoiceCapture();
  const [transcript, setTranscript] = useState('');

  useEffect(() => {
    if (!opened) {
      voice.reset();
      setTranscript('');
    }
    // voice.reset is stable per render of the hook; intentionally omitted.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened]);

  const state = voice.capture?.state ?? null;
  const latestRevision = voice.capture?.revisions?.length
    ? voice.capture.revisions[voice.capture.revisions.length - 1]
    : null;

  const begin = useCallback(() => {
    void voice.create('closeout', workOrderId, workOrderVersion);
  }, [voice, workOrderId, workOrderVersion]);

  const submitTranscript = useCallback(() => {
    void voice.addRevision(transcript);
  }, [voice, transcript]);

  const acceptAndHandOff = useCallback(async () => {
    if (!latestRevision) return;
    await voice.acceptRevision(latestRevision);
    await voice.commit();
  }, [voice, latestRevision]);

  useEffect(() => {
    if (state === 'committed') {
      onCommitted();
      onClose();
    }
  }, [state, onCommitted, onClose]);

  return (
    <Modal
      opened={opened}
      onClose={() => {
        if (state && state !== 'committed' && state !== 'canceled') {
          void voice.cancel();
        }
        onClose();
      }}
      title={t`Dictate closeout narrative`}
      size='lg'
    >
      <Stack gap='sm' data-testid='dictate-closeout-modal'>
        {voice.error && (
          <Alert color='red' icon={<IconAlertTriangle size={16} />}>
            {voice.error === 'CAPTURE_PURPOSE_UNSUPPORTED'
              ? t`Voice dictation is not enabled in this deployment.`
              : voice.error}
          </Alert>
        )}

        {!state && (
          <>
            <Text size='sm'>
              {t`Dictation goes on the record: the exact transcript you accept becomes the closeout narrative, with voice provenance attached. You will review it before anything is extracted.`}
            </Text>
            <Group>
              <Button
                leftSection={<IconMicrophone size={16} />}
                loading={voice.busy}
                data-testid='dictate-consent'
                onClick={begin}
              >
                {t`I consent — start dictation`}
              </Button>
            </Group>
          </>
        )}

        {(state === 'active' || state === 'review') && (
          <>
            <Group gap='xs'>
              <Badge color='grape'>{t`Voice capture`}</Badge>
              <Badge variant='light'>{state}</Badge>
            </Group>
            <Textarea
              label={t`Transcript`}
              description={t`Dictate with your device microphone (keyboard dictation) or type; review every word — this exact text is what gets accepted.`}
              value={transcript}
              onChange={(event) => setTranscript(event.currentTarget.value)}
              autosize
              minRows={5}
              data-testid='dictate-transcript'
            />
            <Group>
              <Button
                variant='light'
                loading={voice.busy}
                disabled={!transcript.trim()}
                data-testid='dictate-submit-revision'
                onClick={submitTranscript}
              >
                {t`Submit for review`}
              </Button>
              {state === 'review' && latestRevision && (
                <Button
                  color='green'
                  loading={voice.busy}
                  data-testid='dictate-accept'
                  onClick={() => void acceptAndHandOff()}
                >
                  {t`Accept and hand off`}
                </Button>
              )}
            </Group>
            {state === 'review' && latestRevision && (
              <Text size='xs' c='dimmed'>
                {t`Revision`} {latestRevision.revision} —{' '}
                {t`accepting binds this exact text by content hash.`}
              </Text>
            )}
          </>
        )}

        {state === 'accepted' && (
          <Group>
            <Badge color='green'>{t`Accepted`}</Badge>
            <Button loading={voice.busy} onClick={() => void voice.commit()}>
              {t`Hand off to closeout`}
            </Button>
          </Group>
        )}
      </Stack>
    </Modal>
  );
}
