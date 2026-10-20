import { i18n } from '@lingui/core';
import { I18nProvider } from '@lingui/react';
import { Button, Group, MantineProvider, Stack, Text } from '@mantine/core';
import { useAIChatState } from '../src/states/AIChatState';
import '@mantine/core/styles.css';
import { QueryClient } from '@tanstack/react-query';
import axios from 'axios';
import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Link, MemoryRouter, useLocation } from 'react-router-dom';

import { ApiProvider } from '../src/contexts/ApiContext';

import { EquipmentScene } from '../src/pages/assets/equipment/EquipmentScene';
import { EquipmentViewPanel } from '../src/pages/assets/equipment/EquipmentViewPanel';
import type {
  TwinMode,
  TwinPartId
} from '../src/pages/assets/equipment/twinScene.mjs';

i18n.load('en', {});
i18n.activate('en');

const params = new URLSearchParams(window.location.search);
const client = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } }
});

/**
 * S2 fixture page for the reference 3D scene. The buttons are fixture
 * controls standing in for a parent's props, a telemetry refresh and
 * navigation; they are not product chrome.
 */
function Harness() {
  const focus = useAIChatState((state) => state.workFocusHint);
  const [selected, setSelected] = useState<TwinPartId>('machine');
  const [mode, setMode] = useState<TwinMode>('assembled');
  const [labels, setLabels] = useState(true);
  const [mounted, setMounted] = useState(true);
  const [modelKey, setModelKey] = useState('reference-pump:18');
  const [polls, setPolls] = useState(0);
  return (
    <Stack p='md' gap='sm'>
      <Text>S2 synthetic fixture — reference 3D scene</Text>
      <Group gap='xs'>
        <Button
          size='xs'
          variant='default'
          onClick={() => setSelected('bearing')}
        >
          Fixture select bearing
        </Button>
        <Button
          size='xs'
          variant='default'
          onClick={() => setSelected('impeller')}
        >
          Fixture select impeller
        </Button>
        <Button size='xs' variant='default' onClick={() => setMode('exploded')}>
          Fixture exploded
        </Button>
        <Button
          size='xs'
          variant='default'
          onClick={() => setMode('assembled')}
        >
          Fixture assembled
        </Button>
        <Button
          size='xs'
          variant='default'
          onClick={() => setLabels((v) => !v)}
        >
          Fixture toggle labels
        </Button>
        <Button
          size='xs'
          variant='default'
          onClick={() => setPolls((v) => v + 1)}
        >
          Fixture telemetry poll
        </Button>
        <Button
          size='xs'
          variant='default'
          onClick={() => setMounted((v) => !v)}
        >
          Fixture toggle mount
        </Button>
        <Button
          size='xs'
          variant='default'
          onClick={() => setModelKey((key) => `${key}+`)}
        >
          Fixture change model
        </Button>
      </Group>
      <Text data-testid='fixture-selected'>{selected}</Text>
      <Text data-testid='fixture-polls'>{polls}</Text>
      <FixtureLocation />
      <Link to='?position=bearing'>Fixture return to bearing</Link>
      <output hidden data-testid='fixture-reference-focus'>
        {JSON.stringify(focus?.component ?? null)}
      </output>
      {mounted &&
        (params.get('kind') === 'panel' ? (
          <EquipmentViewPanel
            machine={{ pk: 18, name: 'Synthetic pump' }}
            schematic={
              params.get('schematic') === 'yes' ? (
                <Text data-testid='fixture-schematic'>
                  Fixture process schematic
                </Text>
              ) : undefined
            }
          />
        ) : (
          <EquipmentScene
            modelKey={modelKey}
            selected={selected}
            mode={mode}
            labels={labels}
            onSelect={setSelected}
          />
        ))}
    </Stack>
  );
}

function FixtureLocation() {
  const location = useLocation();
  return (
    <Text data-testid='fixture-location'>
      {location.pathname}
      {location.search}
    </Text>
  );
}

createRoot(document.getElementById('root')!).render(
  <I18nProvider i18n={i18n}>
    <MantineProvider
      forceColorScheme={params.get('scheme') === 'dark' ? 'dark' : 'light'}
    >
      <ApiProvider api={axios.create()} client={client}>
        <MemoryRouter initialEntries={[params.get('entry') ?? '/fixture']}>
          <Harness />
        </MemoryRouter>
      </ApiProvider>
    </MantineProvider>
  </I18nProvider>
);
