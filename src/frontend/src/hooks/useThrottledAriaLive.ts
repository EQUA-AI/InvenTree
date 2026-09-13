import { useEffect, useRef, useState } from 'react';

/** Coalesce transcript/status updates; never announce every recognition delta. */
export function useThrottledAriaLive(
  text: string,
  enabled = true,
  delay = 1000
) {
  const [announced, setAnnounced] = useState('');
  const latest = useRef(text);
  latest.current = text;
  useEffect(() => {
    if (!enabled) {
      setAnnounced('');
      return;
    }
    const timer = window.setInterval(() => setAnnounced(latest.current), delay);
    return () => window.clearInterval(timer);
  }, [enabled, delay]);
  return announced;
}
