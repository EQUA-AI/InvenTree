import '@mantine/core/styles.css';
import { useViewportSize } from '@mantine/hooks';
import { type ComponentType, useEffect, useState } from 'react';
import { useShallow } from 'zustand/react/shallow';

import { setApiDefaults } from '../App';
import { phoneRoutingEnabled } from '../components/ai/voice/phoneRouting';
import { useAIChatState } from '../states/AIChatState';
import { useLocalState } from '../states/LocalState';

function checkMobile() {
  const { height, width } = useViewportSize();
  if (width < 425 || height < 425) return true;
  return false;
}

// Import both views eagerly (outside React.lazy/Suspense): a lazy component
// always suspends on its first render, and React's Suspense commit-delay
// heuristic can then hold that first commit - and every effect beneath it,
// including locale and layout loading - back by several hundred ms, even
// though the underlying chunk is already cached by the time it's needed.
const desktopViewPromise = import('./DesktopAppView').then((m) => m.default);
const mobileViewPromise = import('./MobileAppView').then((m) => m.default);

// Main App
export default function MainView() {
  const assistantOpen = useAIChatState((state) => state.isOpen);
  const [allowMobile] = useLocalState(
    useShallow((state) => [state.allowMobile])
  );
  const [DesktopView, setDesktopView] = useState<ComponentType | null>(null);
  const [MobileView, setMobileView] = useState<ComponentType | null>(null);

  // Set initial login status
  useEffect(() => {
    try {
      // Local state initialization
      setApiDefaults();
    } catch (e) {
      console.error(e);
    }
  }, []);

  useEffect(() => {
    desktopViewPromise.then((Component) => setDesktopView(() => Component));
    mobileViewPromise.then((Component) => setMobileView(() => Component));
  }, []);

  const legacySmallViewport = checkMobile();
  // The pilot uses a sticky screen/touch classifier, not viewport height.
  // Authentication stays in the shared router.
  const isMobile =
    !phoneRoutingEnabled(
      window.INVENTREE_SETTINGS?.voice_phone_short_edge_px
    ) &&
    !/\/(login|logged-in|logout|mfa|mfa-setup)\/?$/.test(
      window.location.pathname
    ) &&
    !allowMobile &&
    !assistantOpen &&
    window.INVENTREE_SETTINGS.mobile_mode !== 'allow-always' &&
    legacySmallViewport;

  // Resizing or opening the keyboard must not destroy the router, chat draft,
  // conversation, or an in-flight response. The mobile notice is presentation
  // only; the authenticated application keeps its stable owner underneath it.
  return (
    <>
      <div style={{ display: isMobile ? 'none' : 'contents' }}>
        {DesktopView && <DesktopView />}
      </div>
      {isMobile && MobileView && <MobileView />}
    </>
  );
}
