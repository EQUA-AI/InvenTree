import type { UserTheme } from '@lib/types/Core';
import { msg } from '@lingui/core/macro';
import { Trans } from '@lingui/react';
import {
  type CSSVariablesResolver,
  MantineProvider,
  type MantineThemeOverride,
  createTheme,
  isLightColor
} from '@mantine/core';
import { ModalsProvider } from '@mantine/modals';
import { Notifications } from '@mantine/notifications';
import { ContextMenuProvider } from 'mantine-contextmenu';
import type { JSX } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { AboutInvenTreeModal } from '../components/modals/AboutInvenTreeModal';
import { HotkeyModal } from '../components/modals/HotkeyModal';
import { LicenseModal } from '../components/modals/LicenseModal';
import { QrModal } from '../components/modals/QrModal';
import { ServerInfoModal } from '../components/modals/ServerInfoModal';
import { useLocalState } from '../states/LocalState';
import { LanguageContext } from './LanguageContext';
import { colorSchema } from './colorSchema';

// Work tokens. They are defined app-wide but consumed only by Work surfaces
// (workCompanion.module.css scopes `--mantine-color-dimmed` to them), so other
// native pages keep Mantine's own secondary text and palette.
export const cssVariablesResolver: CSSVariablesResolver = (theme) => ({
  variables: {
    // Consumed only by Work surfaces; other native pages retain their palette.
    '--aimms-work-action-bg': theme.colors[theme.primaryColor][8],
    '--aimms-work-action-hover': theme.colors[theme.primaryColor][9],
    '--aimms-work-action-hover-text': isLightColor(
      theme.colors[theme.primaryColor][9]
    )
      ? '#000000'
      : '#ffffff',
    '--aimms-work-action-text': isLightColor(
      theme.colors[theme.primaryColor][8]
    )
      ? '#000000'
      : '#ffffff'
  },
  // AA secondary text on body, cards and contained panels.
  light: {
    '--aimms-work-dimmed': theme.colors.gray[7],
    '--aimms-work-warning-icon': theme.colors.orange[9]
  },
  dark: {
    '--aimms-work-dimmed': '#A6A7AB',
    '--aimms-work-warning-icon': theme.colors.orange[4]
  }
});

export function createInvenTreeTheme(
  userTheme: UserTheme
): MantineThemeOverride {
  return createTheme({
    primaryColor: userTheme.primaryColor,
    white: userTheme.whiteColor,
    black: userTheme.blackColor,
    defaultRadius: userTheme.radius,
    breakpoints: {
      xs: '30em',
      sm: '48em',
      md: '64em',
      lg: '74em',
      xl: '90em'
    }
  });
}

export function ThemeContext({
  children
}: Readonly<{ children: JSX.Element }>) {
  const [userTheme] = useLocalState(useShallow((state) => [state.userTheme]));

  let customUserTheme: MantineThemeOverride | undefined = undefined;

  // Theme
  try {
    customUserTheme = createInvenTreeTheme(userTheme);
  } catch (error) {
    console.error('Error creating theme with user settings:', error);
    // Fallback to default theme if there's an error
    customUserTheme = undefined;
  }

  return (
    <MantineProvider
      theme={customUserTheme}
      colorSchemeManager={colorSchema}
      cssVariablesResolver={cssVariablesResolver}
    >
      <ContextMenuProvider>
        <LanguageContext>
          <ModalsProvider
            labels={{
              confirm: <Trans id={msg`Submit`.id} />,
              cancel: <Trans id={msg`Cancel`.id} />
            }}
            modals={{
              info: ServerInfoModal,
              about: AboutInvenTreeModal,
              license: LicenseModal,
              qr: QrModal,
              hotkey: HotkeyModal
            }}
          >
            <Notifications />
            {children}
          </ModalsProvider>
        </LanguageContext>
      </ContextMenuProvider>
    </MantineProvider>
  );
}
