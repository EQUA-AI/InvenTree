import { t } from '@lingui/core/macro';
import {
  Alert,
  Badge,
  Button,
  FileInput,
  Group,
  Modal,
  NumberInput,
  Stack,
  Text,
  TextInput,
  Textarea
} from '@mantine/core';
import { IconAlertTriangle, IconMicrophone } from '@tabler/icons-react';
import { useCallback, useEffect, useRef, useState } from 'react';

import type { CaptureOriginal } from '@lib/types/Closeout';
import { useApi } from '../../../contexts/ApiContext';
import { useVoiceCapture } from '../../../hooks/useVoiceCapture';
import { OriginalMedia } from './OriginalMedia';
import { useOriginalRecording } from './useOriginalRecording';
import workClasses from './workCompanion.module.css';

/**
 * B4 (S32b): dictate a closeout narrative through the governed voice-capture
 * contract — consent (create), transcript review (revise), hash-bound accept,
 * then commit, which hands the exact accepted revision to the closeout
 * wizard server-side (accept_voice_handoff). The transcript text itself
 * comes from the technician's device dictation or keyboard; this flow's job
 * is provenance and original media: the narrative lands with source_type=voice and
 * a transcript_reference bound to the accepted revision's content hash.
 */
export default function DictateCloseoutModal({
  opened,
  locked = false,
  onClose,
  workOrderId,
  workOrderVersion,
  onCommitted
}: Readonly<{
  opened: boolean;
  locked?: boolean;
  onClose: () => void;
  workOrderId: number;
  workOrderVersion: number;
  onCommitted: () => void;
}>) {
  const voice = useVoiceCapture();
  const api = useApi();
  const lockedRef = useRef(locked);
  lockedRef.current = locked;
  const [original, setOriginal] = useState<File | null>(null);
  const [started, setStarted] = useState('');
  const [duration, setDuration] = useState<string | number>('');
  const [savedOriginal, setSavedOriginal] = useState<CaptureOriginal | null>(
    null
  );
  const [mediaError, setMediaError] = useState('');
  const [uploading, setUploading] = useState(false);
  const [recordedStart, setRecordedStart] = useState('');
  const localGeneration = useRef(0);
  const recording = useOriginalRecording((file, began, elapsed) => {
    setOriginal(file);
    setStarted('');
    setRecordedStart(began);
    setDuration(elapsed);
    setSavedOriginal(null);
  });
  const discardLocal = () => {
    localGeneration.current++;
    recording.reset();
    setOriginal(null);
    setStarted('');
    setRecordedStart('');
    setDuration('');
    setMediaError('');
    setSavedOriginal(null);
    setUploading(false);
  };
  const upload = async () => {
    if (lockedRef.current || !original || !voice.capture) return;
    const generation = localGeneration.current;
    setUploading(true);
    setMediaError('');
    const body = new FormData();
    body.append('file', original);
    body.append('voice_capture', voice.capture.id);
    body.append('expected_version', String(workOrderVersion));
    if (started || recordedStart)
      body.append(
        'started_at',
        started ? new Date(started).toISOString() : recordedStart
      );
    if (duration !== '') body.append('duration_ms', String(duration));
    try {
      const { data } = await api.post(
        `/api/tasks/work-orders/${workOrderId}/capture-media/`,
        body,
        { headers: { 'Content-Type': 'multipart/form-data' } }
      );
      if (!Number.isInteger(data.id) || !data.content_hash || !data.url)
        throw new Error('Invalid media receipt');
      if (localGeneration.current === generation) setSavedOriginal(data);
    } catch {
      if (localGeneration.current === generation)
        setMediaError(
          t`Original media was not confirmed saved. Keep the file and retry before accepting.`
        );
    } finally {
      if (localGeneration.current === generation) setUploading(false);
    }
  };
  const [transcript, setTranscript] = useState('');

  useEffect(() => {
    discardLocal();
    // Dismissing dictation discards local media, but keeps the transcript draft
    // for the existing failed-review recovery flow.
    if (!opened) {
      voice.reset();
    }
    // voice.reset is stable per render of the hook; intentionally omitted.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened, workOrderId]);

  const state = voice.capture?.state ?? null;
  const latestRevision = voice.capture?.revisions?.length
    ? voice.capture.revisions[voice.capture.revisions.length - 1]
    : null;

  const begin = useCallback(() => {
    if (lockedRef.current) return;
    discardLocal();
    void voice.create('closeout', workOrderId, workOrderVersion);
  }, [voice, workOrderId, workOrderVersion]);

  const submitTranscript = useCallback(() => {
    if (lockedRef.current) return;
    void voice.addRevision(transcript);
  }, [voice, transcript]);

  const acceptAndHandOff = useCallback(async () => {
    if (lockedRef.current || !latestRevision || (original && !savedOriginal))
      return;
    await voice.acceptRevision(latestRevision);
    if (lockedRef.current) return;
    await voice.commit();
  }, [voice, latestRevision, original, savedOriginal]);

  useEffect(() => {
    // Permission/source loss stops local recording without discarding the draft.
    if (locked && recording.recording) recording.stop();
  }, [locked, recording.recording]);

  useEffect(() => {
    if (state === 'committed') {
      setTranscript('');
      onCommitted();
      onClose();
    }
  }, [state, onCommitted, onClose]);

  return (
    <Modal
      opened={opened}
      classNames={{ content: workClasses.workTokens }}
      onClose={async () => {
        if (voice.busy || uploading) return;
        recording.reset();
        if (
          !lockedRef.current &&
          state &&
          state !== 'committed' &&
          state !== 'canceled'
        ) {
          await voice.cancel();
        }
        onClose();
      }}
      closeButtonProps={{ 'aria-label': t`Close dictation` }}
      title={t`Dictate closeout narrative`}
      size='lg'
    >
      <Stack
        renderRoot={(props) => <fieldset {...props} disabled={locked} />}
        gap='sm'
        style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}
        data-testid='dictate-closeout-modal'
      >
        {locked && <Alert color='orange'>{t`Capture locked`}</Alert>}
        {!state && transcript && (
          <Alert color='orange'>{t`Your transcript draft is retained. Start a governed session to review and save it.`}</Alert>
        )}
        {voice.error && (
          <Alert color='red' icon={<IconAlertTriangle size={16} />}>
            {voice.error === 'CAPTURE_PURPOSE_UNSUPPORTED'
              ? t`Voice dictation is not enabled in this deployment.`
              : voice.error}
          </Alert>
        )}

        {!state && (
          <>
            <Text size='sm'>{t`Recording starts only when you choose Record microphone. The recording stays on this device until you choose Save original media. Recording stops when you stop it, close this screen or hide this tab.`}</Text>
            <Text size='sm'>{t`Saving keeps the original media on the server with its content hash and any supplied recording time and duration. Saved originals are retained, including if you later cancel transcript review.`}</Text>
            <Text size='sm'>{t`Accept and hand off shares the reviewed transcript and its saved original with people allowed to read this Work. This screen does not automatically transcribe audio or enable memory.`}</Text>
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
            <Text size='sm'>{t`Keep the original recording on the server before accepting. Keyboard dictation supplies text only; attach the recording when available. Upload does not transcribe audio.`}</Text>
            {!savedOriginal && (
              <>
                <Group>
                  <Button
                    variant='light'
                    disabled={uploading || recording.starting}
                    onClick={() =>
                      recording.recording
                        ? recording.stop()
                        : void recording.start()
                    }
                  >
                    {recording.recording
                      ? t`Stop recording`
                      : t`Record microphone`}
                  </Button>
                </Group>
                {recording.failed && (
                  <Alert color='orange'>{t`Microphone recording is unavailable. Attach an existing recording, or use the typed fallback with a reason.`}</Alert>
                )}
                <FileInput
                  label={t`Original recording or capture media`}
                  value={original}
                  clearable
                  clearButtonProps={{ 'aria-label': t`Clear local media` }}
                  onChange={(file) => {
                    // A chosen file never inherits an earlier recording's timing.
                    discardLocal();
                    setOriginal(file);
                  }}
                  disabled={uploading}
                />
                {(original || recording.recording || recording.starting) && (
                  <Button
                    variant='subtle'
                    disabled={uploading}
                    onClick={discardLocal}
                  >{t`Discard local recording`}</Button>
                )}
                <TextInput
                  type='datetime-local'
                  label={t`Recording start time (local)`}
                  value={started}
                  onChange={(event) => setStarted(event.currentTarget.value)}
                  disabled={uploading}
                />
                <NumberInput
                  label={t`Recording duration (ms)`}
                  min={0}
                  allowDecimal={false}
                  value={duration}
                  onChange={setDuration}
                  disabled={uploading}
                />
                <Button
                  variant='light'
                  onClick={() => void upload()}
                  loading={uploading}
                  disabled={
                    !original || recording.recording || recording.starting
                  }
                >{t`Save original media`}</Button>
              </>
            )}
            {mediaError && <Alert color='red'>{mediaError}</Alert>}
            {savedOriginal && <OriginalMedia originals={[savedOriginal]} />}
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
                  disabled={
                    uploading ||
                    recording.recording ||
                    recording.starting ||
                    (!!original && !savedOriginal)
                  }
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
            <Button
              loading={voice.busy}
              onClick={() => {
                if (!lockedRef.current) void voice.commit();
              }}
            >
              {t`Hand off to closeout`}
            </Button>
          </Group>
        )}
      </Stack>
    </Modal>
  );
}
