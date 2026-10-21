import { MantineProvider } from '@mantine/core';
import '@mantine/notifications/styles.css';
import 'mantine-contextmenu/styles.css';
import type { JSX } from 'react';
import { api } from '../src/App';
import {
  ThemeContext,
  createInvenTreeTheme,
  cssVariablesResolver
} from '../src/contexts/ThemeContext';
import { useLocalState } from '../src/states/LocalState';

/** Native providers by default; monitoring fixtures can opt into a lightweight theme. */
export function FixtureTheme({
  children,
  nativeTheme = true,
  fallbackUserTheme = true
}: Readonly<{
  children: JSX.Element;
  nativeTheme?: boolean;
  fallbackUserTheme?: boolean;
}>) {
  const params = new URLSearchParams(location.search);
  if (!nativeTheme)
    return (
      <MantineProvider
        cssVariablesResolver={
          fallbackUserTheme ? cssVariablesResolver : undefined
        }
        theme={
          fallbackUserTheme
            ? createInvenTreeTheme(useLocalState.getState().userTheme)
            : undefined
        }
        forceColorScheme={params.get('scheme') === 'dark' ? 'dark' : 'light'}
      >
        {children}
      </MantineProvider>
    );
  return <ThemeContext>{children}</ThemeContext>;
}

export function prepareFixtureTheme(params: URLSearchParams) {
  localStorage.setItem(
    'scheme',
    params.get('scheme') === 'dark' ? 'dark' : 'light'
  );
  useLocalState.setState({ language: 'en' });
  const primary = params.get('primary');
  if (primary === 'indigo' || primary === 'blue') {
    useLocalState.setState({
      userTheme: {
        ...useLocalState.getState().userTheme,
        primaryColor: primary
      }
    });
  }
  // LanguageContext's normal header is already initialized; no global-state
  // refresh or real backend is needed by this isolated fixture.
  api.defaults.headers.common['Accept-Language'] = 'en,en-us';
}
