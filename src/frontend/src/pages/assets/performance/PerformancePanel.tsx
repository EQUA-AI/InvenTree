import { t } from '@lingui/core/macro';
import {
  Alert,
  Badge,
  Button,
  Center,
  Group,
  Loader,
  LoadingOverlay,
  Paper,
  Stack,
  Text,
  Title
} from '@mantine/core';
import { IconAlertTriangle, IconInfoCircle } from '@tabler/icons-react';
import { useCallback, useEffect, useMemo, useState } from 'react';

import type { SeriesEntry } from '@lib/types/MachineHealth';

import { KpiStrip } from './KpiStrip';
import type { WindowInfo } from './SensorGroupCard';
import { StationPerformance } from './StationPerformance';
import { LiveIndicator, TimeRangeControl } from './TimeRangeControl';
import { buildKpiTiles } from './kpis';
import { categoryLabel } from './parameters';
import { inCategory, resolveParameters } from './resolve';
import { ElectricalSection } from './sections/ElectricalSection';
import { OperationSection } from './sections/OperationSection';
import { OtherSection } from './sections/OtherSection';
import {
  BearingSection,
  CoolingSection,
  VibrationSection,
  WindingSection
} from './sections/ThermalSections';
import { indexSeries, newestInstant } from './series';
import {
  PRESET_SECONDS,
  type RangePreset,
  type WindowSpec,
  resolveWindow,
  useDataRange,
  useMachineSeries,
  useMachineSignals,
  windowSeconds
} from './usePerformanceData';

export interface PerformanceMachine {
  pk: number;
  name?: string;
  asset_type?: string;
}

/** Station-level context a pump page reads alongside its own tags. */
const STATION_CONTEXT_KEYS = ['/dex/COMMAN_FORBAY_LEVEL', '/pc'];

/**
 * Performance tab for a pump or a pump station.
 *
 * Reads two things and draws everything from them: the machine's current
 * signals, and one series read for every chartable parameter over the selected
 * window. The page is arranged by what an operator asks, in order - what is
 * happening now, whether anything is unusual, what changed, where, and how the
 * parameters relate - and every section is only as large as the machine's own
 * instrumentation makes it.
 */
export function PerformancePanel({
  machine
}: Readonly<{ machine: PerformanceMachine }>) {
  if (machine.asset_type === 'pumphouse') {
    return <StationPerformance station={machine} />;
  }
  return <PumpPerformance machine={machine} />;
}

/** Window selection shared by the pump and station views. */
export function useWindowState() {
  const [preset, setPreset] = useState<RangePreset>('1h');
  const [custom, setCustom] = useState<WindowSpec | null>(null);
  const [zoom, setZoom] = useState<WindowSpec | null>(null);
  const [live, setLive] = useState(true);

  const base: WindowSpec = custom ?? {
    kind: 'relative',
    seconds: PRESET_SECONDS[preset]
  };
  const window: WindowSpec = zoom ?? base;

  const onPreset = useCallback((next: RangePreset) => {
    setPreset(next);
    setCustom(null);
    setZoom(null);
    setLive(true);
  }, []);
  const onCustom = useCallback((from: number, to: number) => {
    setCustom({ kind: 'absolute', from, to });
    setZoom(null);
    // A fixed span cannot follow the clock.
    setLive(false);
  }, []);
  const onZoom = useCallback((from: number, to: number) => {
    setZoom({ kind: 'absolute', from, to });
    setLive(false);
  }, []);
  const onClearZoom = useCallback(() => {
    setZoom(null);
    setLive((current) => current || !custom);
  }, [custom]);
  const onLive = useCallback((next: boolean) => {
    if (next) {
      setZoom(null);
      setCustom(null);
    }
    setLive(next);
  }, []);

  return {
    preset: custom ? ('custom' as const) : preset,
    window,
    zoom,
    live,
    onPreset,
    onCustom,
    onZoom,
    onClearZoom,
    onLive
  };
}

/** A ticking "now" so ages and the live badge stay honest without refetching. */
export function useClock(intervalMs = 5000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}

export function windowInfoFrom(
  response:
    | { window_start: string; window_end: string; resolution_seconds: number }
    | undefined,
  spec: WindowSpec,
  now: number
): WindowInfo {
  if (response) {
    const start = new Date(response.window_start).getTime();
    const end = new Date(response.window_end).getTime();
    return {
      start,
      end,
      seconds: Math.max(1, Math.round((end - start) / 1000)),
      resolutionSeconds: response.resolution_seconds
    };
  }
  const { from, to } = resolveWindow(spec, now);
  return {
    start: from,
    end: to,
    seconds: windowSeconds(spec),
    resolutionSeconds: 5
  };
}

function PumpPerformance({
  machine
}: Readonly<{ machine: PerformanceMachine }>) {
  const now = useClock();
  const range = useWindowState();
  const signalsQuery = useMachineSignals(machine.pk, range.live);
  const dataRange = useDataRange(machine.pk);

  const parameters = useMemo(
    () => resolveParameters(signalsQuery.data ?? []),
    [signalsQuery.data]
  );
  const bindings = useMemo(
    () => parameters.map((p) => p.signal.binding_id),
    [parameters]
  );
  // Not before the signals arrive: a keys-only request would read the
  // station context on its own and then be repeated with the bindings.
  const targets = useMemo(
    () => (bindings.length > 0 ? { bindings, keys: STATION_CONTEXT_KEYS } : {}),
    [bindings]
  );

  const seriesQuery = useMachineSeries(
    machine.pk,
    targets,
    range.window,
    range.live
  );
  const response = seriesQuery.data;

  // Station context arrives as series entries for bindings the pump's own
  // signal list does not carry; they are resolved into parameters here so the
  // sections can draw them like any other.
  const allParameters = useMemo(() => {
    if (!response) return parameters;
    const known = new Set(parameters.map((p) => p.signal.binding_id));
    const extra = response.series
      .filter((entry) => !known.has(entry.binding_id))
      .map((entry) => contextSignal(entry));
    return extra.length > 0
      ? resolveParameters([...parameters.map((p) => p.signal), ...extra])
      : parameters;
  }, [parameters, response]);

  const series = useMemo(() => indexSeries(response), [response]);
  const window = useMemo(
    () => windowInfoFrom(response, range.window, now),
    [response, range.window, now]
  );
  const tiles = useMemo(
    () => buildKpiTiles(allParameters, series),
    [allParameters, series]
  );
  const newestAt = useMemo(() => newestInstant([...series.values()]), [series]);
  const syncId = `performance-${machine.pk}`;

  const freshness = signalsQuery.data?.[0]?.freshness_threshold_seconds ?? null;

  if (signalsQuery.isLoading) {
    return (
      <Center p='xl'>
        <Loader />
      </Center>
    );
  }
  if (signalsQuery.isError) {
    return (
      <Alert
        color='red'
        variant='light'
        title={t`Performance data unavailable`}
      >
        {t`The health service could not be reached. Nothing on this tab is cached or estimated.`}
        <Group mt='xs'>
          <Button
            size='xs'
            variant='light'
            onClick={() => signalsQuery.refetch()}
          >
            {t`Retry`}
          </Button>
        </Group>
      </Alert>
    );
  }
  if (parameters.length === 0) {
    return (
      <Alert
        color='blue'
        variant='light'
        icon={<IconInfoCircle size={16} />}
        title={t`No signals are mapped for this machine`}
      >
        {t`Map this machine's tags to a health source to see its performance here. Until then there is nothing to draw.`}
      </Alert>
    );
  }

  const unavailable =
    response?.series.filter((entry) => !entry.available) ?? [];
  const failedAll = response
    ? unavailable.length === response.series.length &&
      response.series.length > 0
    : false;

  return (
    <Stack gap='lg'>
      <Group justify='space-between' align='flex-start' wrap='wrap' gap='md'>
        <Stack gap={4} style={{ flex: 1, minWidth: 320 }}>
          <TimeRangeControl
            preset={range.preset}
            onPreset={range.onPreset}
            onCustom={range.onCustom}
            zoomed={range.zoom}
            onClearZoom={range.onClearZoom}
            live={range.live}
            onLive={range.onLive}
            onRefresh={() => {
              signalsQuery.refetch();
              seriesQuery.refetch();
            }}
            refreshing={seriesQuery.isFetching}
            dataRange={dataRange.data}
          />
        </Stack>
        <LiveIndicator
          live={range.live}
          fetching={seriesQuery.isFetching}
          newestAt={newestAt}
          updatedAt={seriesQuery.dataUpdatedAt || null}
          response={response}
          freshnessSeconds={freshness}
          now={now}
        />
      </Group>

      <KpiStrip tiles={tiles} now={now} />

      <UnusualStrip parameters={allParameters} response={response} />

      {seriesQuery.isError && (
        <Alert
          color='red'
          variant='light'
          title={t`Could not read history for this window`}
        >
          {t`The request failed. Current values above are still live; the charts below show nothing rather than a partial line.`}
        </Alert>
      )}
      {failedAll && !seriesQuery.isError && (
        <Alert
          color='yellow'
          variant='light'
          icon={<IconAlertTriangle size={16} />}
          title={t`The source could not serve this window`}
        >
          {unavailable[0]?.detail ??
            t`No history is available for this window.`}
        </Alert>
      )}

      <div style={{ position: 'relative' }}>
        <LoadingOverlay
          visible={
            seriesQuery.isLoading || (seriesQuery.isFetching && !response)
          }
          zIndex={5}
          overlayProps={{ blur: 1 }}
        />
        <Stack gap='xl'>
          <Section title={categoryLabel('electrical')}>
            <ElectricalSection
              parameters={allParameters}
              series={series}
              window={window}
              syncId={syncId}
              onZoom={range.onZoom}
            />
          </Section>
          <Section title={t`Speed, operation and hydraulics`}>
            <OperationSection
              parameters={allParameters}
              series={series}
              window={window}
              syncId={syncId}
              onZoom={range.onZoom}
            />
          </Section>
          <Section title={categoryLabel('winding')}>
            <WindingSection
              parameters={allParameters}
              series={series}
              window={window}
              syncId={syncId}
              onZoom={range.onZoom}
            />
          </Section>
          <Section title={categoryLabel('vibration')}>
            <VibrationSection
              parameters={allParameters}
              series={series}
              window={window}
              syncId={syncId}
              onZoom={range.onZoom}
            />
          </Section>
          <Section title={categoryLabel('cooling')}>
            <CoolingSection
              parameters={allParameters}
              series={series}
              window={window}
              syncId={syncId}
              onZoom={range.onZoom}
            />
          </Section>
          <Section title={categoryLabel('bearing')}>
            <BearingSection
              parameters={allParameters}
              series={series}
              window={window}
              syncId={syncId}
              onZoom={range.onZoom}
            />
          </Section>
          {inCategory(allParameters, 'other').length > 0 && (
            <Section title={categoryLabel('other')}>
              <OtherSection
                parameters={inCategory(allParameters, 'other')}
                series={series}
              />
            </Section>
          )}
        </Stack>
      </div>

      {response && (
        <Text size='xs' c='dimmed'>
          {t`Read live from ${response.series[0]?.source_name ?? t`the source`}. AIMMS stores current values only; every chart here is a bounded read of the source's own history.`}
        </Text>
      )}
    </Stack>
  );
}

/** A section heading that disappears with its content. */
function Section({
  title,
  children
}: Readonly<{ title: string; children: React.ReactNode }>) {
  // A section component returns null when the machine has none of its
  // parameters; the heading must go with it, so both are rendered together and
  // the child decides.
  return (
    <Stack gap='sm' data-section={title}>
      <SectionBody title={title}>{children}</SectionBody>
    </Stack>
  );
}

function SectionBody({
  title,
  children
}: Readonly<{ title: string; children: React.ReactNode }>) {
  return <HideIfEmpty title={title}>{children}</HideIfEmpty>;
}

/**
 * Render the heading only when the child produced something. Sections return
 * null for machines without their parameters; this keeps an empty heading from
 * standing over nothing.
 */
function HideIfEmpty({
  title,
  children
}: Readonly<{ title: string; children: React.ReactNode }>) {
  const [empty, setEmpty] = useState(false);
  const ref = useCallback((node: HTMLDivElement | null) => {
    if (node) setEmpty(node.childElementCount === 0);
  }, []);
  return (
    <>
      {!empty && <Title order={5}>{title}</Title>}
      <div ref={ref}>{children}</div>
    </>
  );
}

/**
 * Level 2: is anything unusual? Stale readings, poor quality, configured
 * limits crossed, and how much of the machine has limits at all.
 */
function UnusualStrip({
  parameters,
  response
}: Readonly<{
  parameters: ReturnType<typeof resolveParameters>;
  response: ReturnType<typeof useMachineSeries>['data'];
}>) {
  const stale = parameters.filter((p) => p.signal.stale);
  const poor = parameters.filter((p) => p.signal.quality !== 'good');
  const configured = parameters.filter((p) => {
    const l = p.signal.limits;
    return (
      l.warn_min !== null ||
      l.warn_max !== null ||
      l.critical_min !== null ||
      l.critical_max !== null ||
      l.normal_min !== null ||
      l.normal_max !== null
    );
  });
  const critical = parameters.filter((p) => p.signal.state === 'critical');
  const warning = parameters.filter((p) => p.signal.state === 'warning');
  const unavailable =
    response?.series.filter((entry) => !entry.available) ?? [];

  const calm =
    critical.length === 0 &&
    warning.length === 0 &&
    stale.length === 0 &&
    poor.length === 0;

  return (
    <Paper withBorder radius='md' p='sm'>
      <Group gap='xs' wrap='wrap' align='center'>
        <Text size='sm' fw={600}>
          {t`Anything unusual?`}
        </Text>
        {critical.length > 0 && (
          <Badge color='red' variant='filled'>
            {t`${critical.length} critical: ${critical
              .map((p) => p.label)
              .slice(0, 3)
              .join(', ')}${critical.length > 3 ? '…' : ''}`}
          </Badge>
        )}
        {warning.length > 0 && (
          <Badge color='yellow' variant='light'>
            {t`${warning.length} warning: ${warning
              .map((p) => p.label)
              .slice(0, 3)
              .join(', ')}${warning.length > 3 ? '…' : ''}`}
          </Badge>
        )}
        {stale.length > 0 && (
          <Badge color='yellow' variant='light'>
            {t`${stale.length} of ${parameters.length} signals stale`}
          </Badge>
        )}
        {poor.length > 0 && (
          <Badge color='gray' variant='light'>
            {t`${poor.length} with poor quality`}
          </Badge>
        )}
        {unavailable.length > 0 && (
          <Badge color='gray' variant='light'>
            {t`${unavailable.length} without history in this window`}
          </Badge>
        )}
        {calm && (
          <Badge color='green' variant='light'>
            {t`Nothing outside configured limits`}
          </Badge>
        )}
        <Text size='xs' c='dimmed'>
          {t`${configured.length} of ${parameters.length} signals have limits configured; the rest are drawn without a verdict.`}
        </Text>
      </Group>
    </Paper>
  );
}

/** A series entry standing in for a signal the pump's own list does not carry. */
function contextSignal(entry: SeriesEntry) {
  const last = entry.samples[entry.samples.length - 1];
  return {
    binding_id: entry.binding_id,
    source_id: entry.source_id,
    source_name: entry.source_name,
    source_type: 'scada' as const,
    external_key: entry.external_key,
    display_name: entry.display_name,
    signal_kind: entry.signal_kind,
    unit: entry.unit,
    value: last?.v ?? null,
    observed_at: last ? new Date(last.t).toISOString() : null,
    received_at: null,
    quality: last?.q ?? ('unknown' as const),
    stale: false,
    freshness_threshold_seconds: 0,
    state: 'unknown' as const,
    limits: entry.limits
  };
}

export default PerformancePanel;
