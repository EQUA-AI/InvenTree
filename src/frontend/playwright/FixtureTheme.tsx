import { MantineProvider } from '@mantine/core';
import type { JSX } from 'react';
import { api } from '../src/App';
import { ThemeContext } from '../src/contexts/ThemeContext';

/** Use the production theme and colour-scheme manager, with explicit fixture-backed global reads. */
export function FixtureTheme({
  children
}: Readonly<{ children: JSX.Element }>) {
  const params = new URLSearchParams(location.search);
  if (params.get('nativeTheme') !== 'true')
    return (
      <MantineProvider
        forceColorScheme={params.get('scheme') === 'dark' ? 'dark' : 'light'}
      >
        {children}
      </MantineProvider>
    );
  api.defaults.headers.common['Accept-Language'] = 'en,en-us';
  localStorage.setItem(
    'scheme',
    new URLSearchParams(location.search).get('scheme') === 'dark'
      ? 'dark'
      : 'light'
  );
  return <ThemeContext>{children}</ThemeContext>;
}
