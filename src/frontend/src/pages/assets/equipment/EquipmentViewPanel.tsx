import { t } from '@lingui/core/macro';
import {
  ActionIcon,
  Badge,
  Button,
  Grid,
  Group,
  NavLink,
  Paper,
  SegmentedControl,
  Stack,
  Switch,
  Text,
  Tooltip
} from '@mantine/core';
import {
  IconFocusCentered,
  IconRefresh,
  IconZoomIn,
  IconZoomOut
} from '@tabler/icons-react';
import { type UseQueryResult, useQuery } from '@tanstack/react-query';
import { type ReactNode, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import { useApi } from '../../../contexts/ApiContext';

import { EquipmentScene, type EquipmentSceneHandle } from './EquipmentScene';
import { PositionInspector } from './PositionInspector';
import classes from './equipment.module.css';
import type { MachinePositions } from './machinePositions';
import {
  REFERENCE_PARTS,
  referenceDepth,
  referenceListOrder,
  referenceModeLabel,
  referencePartLabel
} from './referenceParts';
import type { TwinMode, TwinPartId, TwinView } from './twinScene.mjs';

const MODES: TwinMode[] = ['assembled', 'cutaway', 'exploded'];

function viewLabel(view: TwinView): string {
  switch (view) {
    case 'iso':
      return t`Overview`;
    case 'front':
      return t`Front`;
    case 'side':
      return t`Side`;
    case 'top':
      return t`Top`;
  }
}

/**
 * The positions as a keyboard list: the same selection a click on the model
 * makes, and the way to reach parts the casing hides in the default view.
 */
function ComponentList({
  selected,
  onSelect
}: Readonly<{
  selected: TwinPartId;
  onSelect: (id: TwinPartId) => void;
}>) {
  return (
    <Paper withBorder radius='md' p='xs'>
      <Text size='xs' fw={600} c='dimmed' px='xs' pb={4}>
        {t`Components`}
      </Text>
      <ul aria-label={t`Model components`} className={classes.list}>
        {referenceListOrder().map((id) => {
          const number = REFERENCE_PARTS.find((part) => part.id === id)!.number;
          return (
            <li key={id}>
              <NavLink
                component='button'
                type='button'
                active={selected === id}
                aria-pressed={selected === id}
                data-position={id}
                onClick={() => onSelect(id)}
                label={referencePartLabel(id)}
                leftSection={
                  <Badge
                    size='sm'
                    circle
                    variant={selected === id ? 'filled' : 'light'}
                    color='gray'
                  >
                    {number}
                  </Badge>
                }
                style={{
                  paddingLeft: `calc(var(--mantine-spacing-xs) + ${referenceDepth(id) * 14}px)`,
                  borderRadius: 'var(--mantine-radius-sm)'
                }}
              />
            </li>
          );
        })}
      </ul>
    </Paper>
  );
}

type ModelState = {
  selected: TwinPartId;
  setSelected: (id: TwinPartId) => void;
  mode: TwinMode;
  setMode: (mode: TwinMode) => void;
  labels: boolean;
  setLabels: (labels: boolean) => void;
  positions: UseQueryResult<MachinePositions>;
};

/**
 * The reference 3D model with reversible exploration controls and a
 * keyboard component list. Camera moves are one-shot requests to the scene.
 *
 * The model is the authored conceptual pump train, not this machine's
 * verified geometry, and says so. No component records are bound yet (P07):
 * a selected position is a place in the model, not a claim about this
 * pump's parts or condition. Nothing animates, so nothing implies the
 * machine is running.
 */
function ModelView({
  machine,
  selected,
  setSelected,
  mode,
  setMode,
  labels,
  setLabels,
  positions
}: Readonly<{ machine: { pk: number; name: string } } & ModelState>) {
  const scene = useRef<EquipmentSceneHandle>(null);

  return (
    <Stack gap='sm'>
      <Group justify='space-between' gap='sm' wrap='wrap'>
        <SegmentedControl
          aria-label={t`Model view mode`}
          value={mode}
          onChange={(value) => setMode(value as TwinMode)}
          data={MODES.map((value) => ({
            value,
            label: referenceModeLabel(value)
          }))}
        />
        <Group gap='xs' wrap='wrap'>
          <Text size='xs' c='dimmed'>
            {t`Camera`}
          </Text>
          <Button.Group>
            {(['iso', 'front', 'side', 'top'] as const).map((view) => (
              <Button
                key={view}
                size='xs'
                variant='default'
                onClick={() => scene.current?.setView(view)}
              >
                {viewLabel(view)}
              </Button>
            ))}
          </Button.Group>
          <ActionIcon.Group>
            <Tooltip label={t`Zoom in`}>
              <ActionIcon
                variant='default'
                size='md'
                aria-label={t`Zoom in`}
                onClick={() => scene.current?.zoom(0.82)}
              >
                <IconZoomIn size={16} />
              </ActionIcon>
            </Tooltip>
            <Tooltip label={t`Zoom out`}>
              <ActionIcon
                variant='default'
                size='md'
                aria-label={t`Zoom out`}
                onClick={() => scene.current?.zoom(1.22)}
              >
                <IconZoomOut size={16} />
              </ActionIcon>
            </Tooltip>
          </ActionIcon.Group>
          <Button
            size='xs'
            variant='default'
            leftSection={<IconFocusCentered size={14} />}
            onClick={() => scene.current?.focus(selected)}
          >
            {t`Focus selection`}
          </Button>
          <Button
            size='xs'
            variant='default'
            leftSection={<IconRefresh size={14} />}
            onClick={() => scene.current?.reset()}
          >
            {t`Reset view`}
          </Button>
          <Switch
            size='sm'
            label={t`Labels`}
            checked={labels}
            onChange={(event) => setLabels(event.currentTarget.checked)}
          />
        </Group>
      </Group>

      <Grid gap='sm'>
        <Grid.Col span={{ base: 12, lg: 8 }}>
          <Stack gap={6}>
            <EquipmentScene
              ref={scene}
              modelKey={`reference-pump:${machine.pk}`}
              selected={selected}
              mode={mode}
              labels={labels}
              onSelect={setSelected}
            />
            <Text size='xs' c='dimmed'>
              {t`Drag to rotate. Scroll zooms once the model has focus, or hold Ctrl. Arrow keys rotate; + and − zoom.`}
            </Text>
            {mode === 'cutaway' && (
              <Text size='xs' c='dimmed' data-mode-note='cutaway'>
                {t`Cutaway shows reference internals, not this machine's verified design.`}
              </Text>
            )}
            {mode === 'exploded' && (
              <Text size='xs' c='dimmed' data-mode-note='exploded'>
                {t`Exploded for orientation only; not a disassembly procedure.`}
              </Text>
            )}
            <ComponentList selected={selected} onSelect={setSelected} />
          </Stack>
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 4 }}>
          <PositionInspector
            machineId={machine.pk}
            selected={selected}
            query={positions}
          />
        </Grid.Col>
      </Grid>

      <Group justify='space-between' gap='xs' wrap='wrap'>
        <Group gap='xs'>
          <Text size='sm' data-selected-position={selected}>
            {t`Selected`}: {referencePartLabel(selected)}
          </Text>
          {selected !== 'machine' && (
            <Button
              size='compact-xs'
              variant='subtle'
              onClick={() => setSelected('machine')}
            >
              {t`Clear selection`}
            </Button>
          )}
        </Group>
        <Text size='xs' c='dimmed'>
          {t`Reference model: conceptual geometry, not this machine's verified design.`}
        </Text>
      </Group>
    </Stack>
  );
}

export type EquipmentView = 'model' | 'schematic';

/**
 * R6 UI03 Equipment view for a pump: one panel, the 3D model first and the
 * native process schematic as the secondary view where the pump has one
 * (R6 P08). The choice lives in the link (`?view=schematic`), so the old
 * mimic route can land here. Selection, mode and labels outlast a switch.
 */
export function EquipmentViewPanel({
  machine,
  schematic
}: Readonly<{
  machine: { pk: number; name: string };
  /** The native schematic for this pump, unchanged; absent when it has none. */
  schematic?: ReactNode;
}>) {
  const api = useApi();
  // One read per machine: choosing another position never refetches. It is
  // re-read every 30 s while the page is visible, so readings do not sit
  // unrefreshed; each shows its own observation time regardless.
  const positions = useQuery<MachinePositions>({
    queryKey: ['equipment-positions', machine.pk],
    retry: false,
    refetchInterval: 30_000,
    queryFn: async ({ signal }) =>
      (
        await api.get(
          apiUrl(ApiEndpoints.asset_machine_positions, machine.pk),
          {
            signal
          }
        )
      ).data
  });
  const [params, setParams] = useSearchParams();
  const view: EquipmentView =
    schematic && params.get('view') === 'schematic' ? 'schematic' : 'model';
  // R6 P13: a Parts slot opens here with its position (`?position=bearing`).
  const [selected, setSelected] = useState<TwinPartId>(() => {
    const asked = params.get('position');
    return REFERENCE_PARTS.some((part) => part.id === asked)
      ? (asked as TwinPartId)
      : 'machine';
  });
  const [mode, setMode] = useState<TwinMode>('assembled');
  const [labels, setLabels] = useState(true);
  const choose = (next: EquipmentView) =>
    setParams(
      (previous) => {
        const nextParams = new URLSearchParams(previous);
        if (next === 'schematic') nextParams.set('view', 'schematic');
        else nextParams.delete('view');
        return nextParams;
      },
      { replace: true }
    );

  return (
    <Stack gap='sm'>
      {schematic && (
        <SegmentedControl
          aria-label={t`Equipment view`}
          w='fit-content'
          value={view}
          onChange={(value) => choose(value as EquipmentView)}
          data={[
            { value: 'model', label: t`3D model` },
            { value: 'schematic', label: t`Process schematic` }
          ]}
        />
      )}
      {view === 'schematic' ? (
        <div data-equipment-view='schematic'>{schematic}</div>
      ) : (
        <ModelView
          machine={machine}
          selected={selected}
          setSelected={setSelected}
          mode={mode}
          setMode={setMode}
          labels={labels}
          setLabels={setLabels}
          positions={positions}
        />
      )}
    </Stack>
  );
}
