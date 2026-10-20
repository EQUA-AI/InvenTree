import { useEffect, useRef, useState } from 'react';

/** Capture original bytes only; transcript review remains the native voice flow. */
export function useOriginalRecording(
  onRecorded: (file: File, started: string, duration: number) => void
) {
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const generation = useRef(0);
  const callback = useRef(onRecorded);
  callback.current = onRecorded;
  const [recording, setRecording] = useState(false);
  const [failed, setFailed] = useState(false);
  const [starting, setStarting] = useState(false);
  const reset = () => {
    generation.current++;
    if (recorder.current?.state === 'recording') recorder.current.stop();
    stream.current?.getTracks().forEach((track) => track.stop());
    recorder.current = null;
    stream.current = null;
    setRecording(false);
    setStarting(false);
    setFailed(false);
  };
  useEffect(
    () => () => {
      generation.current++;
      if (recorder.current?.state === 'recording') recorder.current.stop();
      stream.current?.getTracks().forEach((track) => track.stop());
    },
    []
  );
  useEffect(() => {
    const stopWhenHidden = () => {
      if (document.hidden && recorder.current?.state === 'recording')
        recorder.current.stop();
    };
    document.addEventListener('visibilitychange', stopWhenHidden);
    return () =>
      document.removeEventListener('visibilitychange', stopWhenHidden);
  }, []);
  const start = async () => {
    if (recording || starting) return;
    const current = ++generation.current;
    setStarting(true);
    setFailed(false);
    try {
      const media = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (generation.current !== current || document.hidden) {
        media.getTracks().forEach((track) => track.stop());
        return;
      }
      stream.current = media;
      const capture = new MediaRecorder(media);
      recorder.current = capture;
      const chunks: Blob[] = [];
      const started = new Date().toISOString();
      const begin = performance.now();
      capture.ondataavailable = (event) => {
        if (event.data.size) chunks.push(event.data);
      };
      capture.onstop = () => {
        media.getTracks().forEach((track) => track.stop());
        if (generation.current !== current) return;
        setRecording(false);
        const blob = new Blob(chunks, { type: capture.mimeType });
        if (!blob.size) {
          setFailed(true);
          return;
        }
        callback.current(
          new File(
            [blob],
            capture.mimeType.includes('mp4')
              ? 'recording.mp4'
              : 'recording.webm',
            { type: capture.mimeType }
          ),
          started,
          Math.round(performance.now() - begin)
        );
      };
      capture.onerror = () => {
        reset();
        setFailed(true);
      };
      capture.start();
      setRecording(true);
    } catch {
      stream.current?.getTracks().forEach((track) => track.stop());
      setFailed(true);
    } finally {
      if (generation.current === current) setStarting(false);
    }
  };
  return {
    recording,
    failed,
    starting,
    start,
    stop: () => {
      if (recorder.current?.state === 'recording') recorder.current.stop();
    },
    reset
  };
}
