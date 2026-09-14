import { t } from '@lingui/core/macro';
import { Button } from '@mantine/core';
import type { ReactNode } from 'react';
import { Link, Navigate, useLocation } from 'react-router-dom';
import { useLocalState } from '../../../states/LocalState';
import { isVoicePhone } from './phoneRouting';

/** Inside the shared router: view navigation never owns the live session. */
export function PhoneVoiceRouting({ children }: { children: ReactNode }) {
  const location = useLocation();
  const allowMobile = useLocalState((state) => state.allowMobile);
  const phone = isVoicePhone();
  const reserved =
    /^\/(?:voice|login|logged-in|logout|mfa|mfa-setup|register|provider-signup|change-password|verify-email|reset-password|set-password)(?:\/|$)/.test(
      location.pathname
    );
  if (phone && !allowMobile && !reserved)
    return <Navigate to='/voice' replace />;
  return (
    <>
      {children}
      {phone && allowMobile && !reserved && (
        <Button
          component={Link}
          to='/voice'
          mih={44}
          style={{
            position: 'fixed',
            bottom: 'max(12px, env(safe-area-inset-bottom))',
            left: 12,
            zIndex: 201
          }}
          onClick={() => useLocalState.getState().setAllowMobile(false)}
        >
          {t`Back to voice`}
        </Button>
      )}
    </>
  );
}
