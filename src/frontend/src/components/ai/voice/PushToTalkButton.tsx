import { t } from '@lingui/core/macro';
import { Button } from '@mantine/core';
import { useEffect, useState } from 'react';
import { voiceController } from '../../../states/VoiceSessionState';

export function PushToTalkButton({ disabled = false }: { disabled?: boolean }) {
  const [pressed, setPressed] = useState(false);
  const release = () => {
    setPressed(false);
    voiceController.pushToTalk(false);
  };
  useEffect(() => {
    const keyup = (event: KeyboardEvent) => {
      if ([' ', 'Enter'].includes(event.key)) release();
    };
    const visibility = () => {
      if (document.hidden) release();
    };
    window.addEventListener('keyup', keyup);
    window.addEventListener('pointerup', release);
    window.addEventListener('blur', release);
    document.addEventListener('visibilitychange', visibility);
    return () => {
      window.removeEventListener('keyup', keyup);
      window.removeEventListener('pointerup', release);
      window.removeEventListener('blur', release);
      document.removeEventListener('visibilitychange', visibility);
      voiceController.pushToTalk(false);
    };
  }, []);
  const press = () => {
    if (!disabled) {
      setPressed(true);
      voiceController.pushToTalk(true);
    }
  };
  return (
    <Button
      mih={56}
      miw={160}
      disabled={disabled}
      aria-pressed={pressed}
      aria-keyshortcuts='Space Enter'
      onPointerDown={(event) => {
        event.currentTarget.setPointerCapture(event.pointerId);
        press();
      }}
      onPointerUp={release}
      onPointerCancel={release}
      onLostPointerCapture={release}
      onBlur={release}
      onKeyDown={(event) => {
        if ([' ', 'Enter'].includes(event.key)) {
          event.preventDefault();
          if (!event.repeat) press();
        }
      }}
      onKeyUp={(event) => {
        if ([' ', 'Enter'].includes(event.key)) {
          event.preventDefault();
          release();
        }
      }}
      data-testid='voice-ptt'
    >
      {pressed ? t`Listening — release to finish` : t`Hold to talk`}
    </Button>
  );
}
