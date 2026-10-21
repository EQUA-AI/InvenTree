import '@mantine/notifications/styles.css';
import 'mantine-contextmenu/styles.css';
import type { JSX } from 'react';
import { api } from '../src/App';
import { ThemeContext } from '../src/contexts/ThemeContext';
import { useLocalState } from '../src/states/LocalState';

/** The app's real theme, locale and modal providers, with local fixture state. */
export function FixtureTheme({ children }: { children: JSX.Element }) {
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
