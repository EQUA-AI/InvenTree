import { Trans } from '@lingui/react/macro';
import { Button, Center, Container, Stack, Text, Title } from '@mantine/core';

import { useShallow } from 'zustand/react/shallow';
import { ThemeContext } from '../contexts/ThemeContext';
import { IS_DEV } from '../main';
import { useAIChatState } from '../states/AIChatState';
import { useLocalState } from '../states/LocalState';

export default function MobileAppView() {
  const [setAllowMobile] = useLocalState(
    useShallow((state) => [state.setAllowMobile])
  );

  function ignore() {
    setAllowMobile(true);
  }
  return (
    <ThemeContext>
      <Center h='100vh'>
        <Container>
          <Stack>
            <Title c='red'>
              <Trans>Mobile viewport detected</Trans>
            </Title>
            <Text>
              <Trans>
                The full app is optimized for tablets and desktops. Voice is
                available in this browser when enabled by your administrator.
              </Trans>
            </Text>
            <Button
              onClick={() => {
                setAllowMobile(true);
                useAIChatState.getState().open();
              }}
            >
              <Trans>Open AI Assistant</Trans>
            </Button>
            {(IS_DEV ||
              window.INVENTREE_SETTINGS.mobile_mode === 'allow-ignore') && (
              <Text
                onClick={ignore}
                style={{ cursor: 'pointer', textDecoration: 'underline' }}
              >
                <Trans>Open full app (not optimized for phones)</Trans>
              </Text>
            )}
          </Stack>
        </Container>
      </Center>
    </ThemeContext>
  );
}
