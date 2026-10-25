import { displayComponent } from '@lib/types/CanonicalDisplay';
import { t } from '@lingui/core/macro';
import {
  ActionIcon,
  Button,
  Group,
  Menu,
  NavLink,
  Paper,
  SegmentedControl,
  Stack,
  Switch,
  Text,
  Title,
  Tooltip,
  VisuallyHidden,
  useMantineTheme
} from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import {
  IconFocusCentered,
  IconRefresh,
  IconZoomIn,
  IconZoomOut
} from '@tabler/icons-react';
import { type UseQueryResult, useQuery } from '@tanstack/react-query';
import { type ReactNode, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { openGlobalAIChatAboutWork } from '../../../states/AIChatState';
import { StatusBadge as Badge } from '../health/common';
import { partStates } from '../health/mimic/MimicParts';
import { TestAlarmNotice } from '../health/mimic/TestAlarmNotice';
import { valueText } from '../health/mimic/format';
import { useDisplayClock } from '../health/mimic/observedTime';
import { useStationMimic } from '../health/useStationMimic';
import { EquipmentViewControls } from './ViewControls';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import { useApi } from '../../../contexts/ApiContext';

import operations from '../operations.module.css';
import { EquipmentScene, type EquipmentSceneHandle } from './EquipmentScene';
import { PositionInspector } from './PositionInspector';
import classes from './equipment.module.css';
import type { MachinePositions } from './machinePositions';
import {
  REFERENCE_PARTS,
  referenceListOrder,
  referenceModeLabel,
  referencePartLabel
} from './referenceParts';
import type {
  TwinMode,
  TwinMotorAlarm,
  TwinPartId,
  TwinView
} from './twinScene.mjs';

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
    <Paper withBorder p='xs' className={classes.components}>
      <Text size='xs' fw={600} className={operations.meta} px='xs' pb={4}>
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
                classNames={{ label: operations.meta }}
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
                  paddingLeft: 'var(--mantine-spacing-xs)',
                  borderRadius: 'var(--mantine-radius-default)'
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

type ModelProps = Readonly<
  {
    machine: { pk: number; name: string };
    motorAlarm?: TwinMotorAlarm | null;
  } & ModelState
>;

/** The model and native schematic share the exact motor verdict and alarm. */
function MimicModelView({
  source,
  ...props
}: ModelProps & { source: { stationId: number; unit: string } }) {
  const { ref, query } = useStationMimic(source.stationId, source.unit);
  const motor =
    !query.isError && query.data
      ? partStates(query.data, valueText).find(
          (state) => state.part.code === 'PS-MOTOR'
        )
      : undefined;
  const motorAlarm =
    motor && (motor.verdict === 'critical' || motor.verdict === 'warning')
      ? {
          severity: motor.verdict,
          attention: motor.attention,
          label: motor.verdict === 'critical' ? t`Critical` : t`Warning`
        }
      : null;
  return (
    <div ref={ref}>
      {!query.isError && query.data && (
        <TestAlarmNotice
          alarms={query.data.alarms.filter(
            (alarm) =>
              alarm.machine ===
              query.data?.bays.find((bay) => bay.key === source.unit)?.machine
          )}
        />
      )}
      <ModelView {...props} motorAlarm={motorAlarm} />
    </div>
  );
}

/**
 * The reference 3D model with reversible exploration controls and a
 * keyboard component list. Camera moves are one-shot requests to the scene.
 *
 * The model is the authored conceptual pump train, not this machine's
 * verified geometry, and says so. Selection identifies a reference position;
 * its inspector uses this machine's records. The motor can carry the native
 * mimic's alarm emphasis, without any motion implying the pump is running.
 */
function ModelView({
  machine,
  selected,
  setSelected,
  mode,
  setMode,
  labels,
  setLabels,
  positions,
  motorAlarm
}: ModelProps) {
  const scene = useRef<EquipmentSceneHandle>(null);
  const theme = useMantineTheme();
  const phone = useMediaQuery(`(max-width: ${theme.breakpoints.sm})`);
  const [rotate, setRotate] = useState(false);
  const [hiddenParts, setHiddenParts] = useState<
    { id: TwinPartId; number: number; label: string }[]
  >([]);

  return (
    <Stack
      gap='sm'
      className={operations.phoneStack}
      data-operational-panel='equipment'
    >
      <Group justify='space-between' gap='sm' wrap='wrap'>
        <SegmentedControl
          aria-label={t`Model view mode`}
          classNames={{ label: classes.viewModeLabel }}
          transitionDuration={0}
          color={`${theme.primaryColor}.7`}
          fullWidth={phone}
          value={mode}
          onChange={(value) => setMode(value as TwinMode)}
          data={MODES.map((value) => ({
            value,
            label: referenceModeLabel(value)
          }))}
        />
        <Group gap='xs' wrap='wrap' visibleFrom='lg'>
          <Text size='xs' className={operations.meta}>
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
            classNames={{ track: operations.input }}
            size='sm'
            label={t`Labels`}
            checked={labels}
            onChange={(event) => setLabels(event.currentTarget.checked)}
          />
        </Group>
        <Group hiddenFrom='lg' gap='xs'>
          <Menu>
            <Menu.Target>
              <Button size='xs' variant='default'>{t`View`}</Button>
            </Menu.Target>
            <Menu.Dropdown>
              {(['iso', 'front', 'side', 'top'] as const).map((view) => (
                <Menu.Item
                  key={view}
                  onClick={() => scene.current?.setView(view)}
                >
                  {viewLabel(view)}
                </Menu.Item>
              ))}
              <Menu.Item
                onClick={() => scene.current?.zoom(0.82)}
              >{t`Zoom in`}</Menu.Item>
              <Menu.Item
                onClick={() => scene.current?.zoom(1.22)}
              >{t`Zoom out`}</Menu.Item>
              <Menu.Item
                onClick={() => scene.current?.focus(selected)}
              >{t`Focus selection`}</Menu.Item>
              <Menu.Item
                onClick={() => scene.current?.reset()}
              >{t`Reset view`}</Menu.Item>
              <Menu.Item onClick={() => setLabels(!labels)}>
                {labels ? t`Hide labels` : t`Show labels`}
              </Menu.Item>
            </Menu.Dropdown>
          </Menu>
          <Button
            hiddenFrom='sm'
            size='xs'
            variant='default'
            aria-pressed={rotate}
            onClick={() => setRotate((value) => !value)}
          >{t`Rotate model`}</Button>
        </Group>
      </Group>
      <VisuallyHidden component='output'>
        {t`Selected`}: {referencePartLabel(selected)}
      </VisuallyHidden>

      <div className={classes.modelLayout} data-model-layout>
        <div className={classes.modelColumns}>
          <div>
            <Stack gap={6}>
              <EquipmentScene
                fitViewport
                ref={scene}
                modelKey={`reference-pump:${machine.pk}`}
                selected={selected}
                mode={mode}
                labels={labels}
                motorAlarm={motorAlarm}
                onSelect={setSelected}
                rotate={!phone || rotate}
                onOccluded={(parts) =>
                  setHiddenParts((current) =>
                    current.map((p) => p.id).join() ===
                    parts.map((p) => p.id).join()
                      ? current
                      : parts
                  )
                }
              />
            </Stack>
          </div>
          <div>
            <PositionInspector
              machineId={machine.pk}
              selected={selected}
              query={positions}
            />
            <ComponentList selected={selected} onSelect={setSelected} />
          </div>
        </div>
      </div>

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
      </Group>
    </Stack>
  );
}

export type EquipmentView = 'model' | 'schematic';

/**
 * R6 UI03 Equipment view for a pump: one panel, the process schematic first and an
 * explicitly selected reference 3D view
 * (R6 P08). The choice lives in the link (`?view=schematic`), so the old
 * mimic route can land here. Selection, mode and labels outlast a switch.
 */
export function EquipmentViewPanel({
  machine,
  schematic,
  mimicSource
}: Readonly<{
  machine: { pk: number; name: string };
  mimicSource?: { stationId: number; unit: string };
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
  const requestedView = params.get('view');
  const view: EquipmentView =
    requestedView === 'model' || requestedView === 'schematic'
      ? requestedView
      : schematic
        ? 'schematic'
        : 'model';
  // Parts and saved-answer links select the same position even when this
  // panel stays mounted. Replace selection in the URL without making every
  // model click a browser-history entry; view/mode switches retain it.
  const asked = params.get('position');
  const selected: TwinPartId = REFERENCE_PARTS.some((part) => part.id === asked)
    ? (asked as TwinPartId)
    : 'machine';
  const setSelected = (id: TwinPartId) =>
    setParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        if (id === 'machine') next.delete('position');
        else next.set('position', id);
        return next;
      },
      { replace: true }
    );
  const [mode, setMode] = useState<TwinMode>('assembled');
  const [labels, setLabels] = useState(true);
  const choose = (next: EquipmentView) =>
    setParams(
      (previous) => {
        const nextParams = new URLSearchParams(previous);
        nextParams.set('view', next);
        return nextParams;
      },
      { replace: true }
    );

  const position = positions.data?.positions.find((p) => p.id === selected);
  const component =
    !positions.isError && position && positions.data?.machine === machine.pk
      ? displayComponent(machine.pk, positions.data.rule, position)
      : null;
  const theme = useMantineTheme();
  const clock = useDisplayClock();
  const controls = (
    <SegmentedControl
      size='xs'
      aria-label={t`Equipment view`}
      classNames={{ label: classes.viewModeLabel }}
      transitionDuration={0}
      color={`${theme.primaryColor}.7`}
      value={view}
      onChange={(value) => choose(value as EquipmentView)}
      data={[
        { value: 'model', label: t`3D model` },
        { value: 'schematic', label: t`Process schematic` }
      ]}
    />
  );
  return (
    <EquipmentViewControls value={controls}>
      <Stack
        gap='sm'
        className={operations.phoneStack}
        data-operational-panel='equipment'
      >
        {view === 'model' && (
          <Paper
            className={operations.band}
            component='section'
            aria-labelledby='equipment-title'
          >
            <Group justify='space-between' gap={4}>
              <Stack gap={2}>
                <Title order={4} id='equipment-title'>
                  {view === 'model'
                    ? t`Inspect equipment positions`
                    : t`Process readings`}
                </Title>
              </Stack>
              <Group gap={4}>
                {controls}
                <Button
                  size='xs'
                  variant='default'
                  loading={positions.isFetching}
                  onClick={() => positions.refetch()}
                >{t`Refresh`}</Button>
              </Group>
            </Group>
          </Paper>
        )}
        {view === 'schematic' ? (
          <>
            <div data-equipment-view='schematic'>
              {schematic ?? (
                <Paper withBorder p='sm'>
                  <Group justify='space-between'>
                    {controls}
                    <Text size='sm'>{t`Process schematic unavailable`}</Text>
                  </Group>
                </Paper>
              )}
            </div>
          </>
        ) : mimicSource ? (
          <MimicModelView
            source={mimicSource}
            machine={machine}
            selected={selected}
            setSelected={setSelected}
            mode={mode}
            setMode={setMode}
            labels={labels}
            setLabels={setLabels}
            positions={positions}
          />
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
        {view === 'model' && (
          <Group justify='space-between' wrap='wrap'>
            <Stack gap={2}>
              <Text size='sm' fw={600}>
                {t`Selected reference position`}: {referencePartLabel(selected)}
              </Text>
            </Stack>
            <Button
              size='xs'
              variant='light'
              disabled={!component}
              onClick={() => {
                if (!component) return;
                openGlobalAIChatAboutWork({
                  workOrderId: null,
                  lifecycleVersion: null,
                  component,
                  label: `${machine.name} · ${referencePartLabel(selected)}`,
                  returnPath: `/machines/machine/${machine.pk}/equipment/?position=${selected}&view=model`
                });
              }}
            >{t`Ask about this position`}</Button>
          </Group>
        )}
      </Stack>
    </EquipmentViewControls>
  );
}
