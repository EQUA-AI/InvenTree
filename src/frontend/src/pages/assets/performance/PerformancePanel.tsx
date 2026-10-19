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
import { useSearchParams } from 'react-router-dom';
import { HistoryProvenance } from '../investigation/HistoryProvenance';
import { InvestigationSlices } from '../investigation/InvestigationSlices';
import { LinkedEvidence } from '../investigation/LinkedEvidence';
import { linkedWindow } from '../investigation/windows';

import type { SeriesEntry } from '@lib/types/MachineHealth';

import { useLocalState } from '../../../states/LocalState';
import { useUserState } from '../../../states/UserState';
import { ChannelNavigator } from './ChannelNavigator';
import { ComparisonWorkbench } from './ComparisonWorkbench';
import { KpiStrip } from './KpiStrip';
import type { WindowInfo } from './SensorGroupCard';
import { StationPerformance } from './StationPerformance';
import { LiveIndicator, TimeRangeControl } from './TimeRangeControl';
import { WindowStatus } from './WindowStatus';
import {
  type PerformanceSection,
  matchesChannel,
  sectionOf
} from './channelNavigation';
import { evaluatePerformance } from './evaluation';
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
 * signals, and bounded aggregate reads for chartable parameters over the selected
 * window. The page is arranged by what an operator asks, in order - what is
 * happening now, whether anything is unusual, what changed, where, and how the
 * parameters relate - and every section is only as large as the machine's own
 * instrumentation makes it.
 */
export function PerformancePanel({
  machine
}: Readonly<{ machine: PerformanceMachine }>) {
  const host = useLocalState((state) => state.hostKey);
  const actor = useUserState((state) => state.user?.pk);
  const generation = useUserState((state) => state.authGeneration);
  if (machine.asset_type === 'pumphouse') {
    return (
      <StationPerformance
        key={JSON.stringify([host, actor, generation, machine.pk])}
        station={machine}
      />
    );
  }
  return (
    <PumpPerformance
      key={JSON.stringify([host, actor, generation, machine.pk])}
      machine={machine}
    />
  );
}

/** Window selection shared by the pump and station views. */
export function useWindowState(
  initial?: Extract<WindowSpec, { kind: 'absolute' }>
) {
  const [, setParams] = useSearchParams();
  const clearLinked = useCallback(
    () =>
      setParams(
        (previous) => {
          if (!previous.has('history_from') && !previous.has('history_to'))
            return previous;
          const next = new URLSearchParams(previous);
          next.delete('history_from');
          next.delete('history_to');
          return next;
        },
        { replace: true }
      ),
    [setParams]
  );
  const [preset, setPreset] = useState<RangePreset>('1h');
  const [custom, setCustom] = useState<WindowSpec | null>(initial ?? null);
  const [zoom, setZoom] = useState<WindowSpec | null>(null);
  const [live, setLive] = useState(!initial);
  const initialFrom = initial?.from;
  const initialTo = initial?.to;
  useEffect(() => {
    if (initialFrom !== undefined && initialTo !== undefined) {
      setCustom({ kind: 'absolute', from: initialFrom, to: initialTo });
      setZoom(null);
      setLive(false);
    }
  }, [initialFrom, initialTo]);

  const base: WindowSpec = custom ?? {
    kind: 'relative',
    seconds: PRESET_SECONDS[preset]
  };
  const window: WindowSpec = zoom ?? base;

  const onPreset = useCallback(
    (next: RangePreset) => {
      clearLinked();
      setPreset(next);
      setCustom(null);
      setZoom(null);
      setLive(true);
    },
    [clearLinked]
  );
  const onCustom = useCallback(
    (from: number, to: number) => {
      clearLinked();
      setCustom({ kind: 'absolute', from, to });
      setZoom(null);
      // A fixed span cannot follow the clock.
      setLive(false);
    },
    [clearLinked]
  );
  const onZoom = useCallback(
    (from: number, to: number) => {
      clearLinked();
      setZoom({ kind: 'absolute', from, to });
      setLive(false);
    },
    [clearLinked]
  );
  const onClearZoom = useCallback(() => {
    setZoom(null);
    setLive((current) => current || !custom);
  }, [custom]);
  const onLive = useCallback(
    (next: boolean) => {
      if (next) {
        clearLinked();
        setZoom(null);
        setCustom(null);
      }
      setLive(next);
    },
    [clearLinked]
  );

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
  const [params, setParams] = useSearchParams();
  const linked = linkedWindow(params);
  const invalidLink = linked.present && !linked.window;
  const range = useWindowState(
    linked.window ? { kind: 'absolute', ...linked.window } : undefined
  );
  const signalsQuery = useMachineSignals(machine.pk, range.live);
  const dataRange = useDataRange(machine.pk);
  const [investigationOpen, setInvestigationOpen] = useState(false);
  const [comparisonOpen, setComparisonOpen] = useState(false);
  const [comparisonMounted, setComparisonMounted] = useState(false);
  const [search, setSearch] = useState('');
  const [section, setSection] = useState('all');
  const [expanded, setExpanded] = useState<Set<string> | null>(null);

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
    () =>
      bindings.length > 0 && !invalidLink
        ? { bindings, keys: STATION_CONTEXT_KEYS }
        : {},
    [bindings, invalidLink]
  );

  const seriesQuery = useMachineSeries(
    machine.pk,
    targets,
    range.window,
    range.live
  );
  const response =
    seriesQuery.isError || invalidLink ? undefined : seriesQuery.data;

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
  const large = parameters.length > 100;
  const isOpen = (key: string) => expanded?.has(key) ?? !large;
  const visible = (key: PerformanceSection) =>
    (section === 'all' || section === key) &&
    allParameters.some(
      (p) => sectionOf(p) === key && matchesChannel(p, search)
    );
  const toggleSection = (key: string) =>
    setExpanded((previous) => {
      const next = new Set(
        previous ??
          (large
            ? []
            : [
                'electrical',
                'operation',
                'winding',
                'vibration',
                'cooling',
                'bearing',
                'other'
              ])
      );
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const selectSection = (key: string) => {
    setSection(key);
    if (key !== 'all' && !isOpen(key)) toggleSection(key);
  };

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
        <Stack gap={4} style={{ flex: 1, minWidth: 0 }}>
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
              if (!invalidLink) seriesQuery.refetch();
            }}
            refreshing={seriesQuery.isFetching}
            dataRange={dataRange.data}
          />
          <WindowStatus
            response={response}
            updating={seriesQuery.isPlaceholderData}
            now={now}
          />
        </Stack>
        <LiveIndicator
          live={range.live}
          fetching={seriesQuery.isFetching}
          failed={seriesQuery.isError}
          newestAt={newestAt}
          updatedAt={seriesQuery.dataUpdatedAt || null}
          response={response}
          freshnessSeconds={freshness}
          now={now}
        />
      </Group>

      {invalidLink && (
        <Alert color='yellow' title={t`Invalid investigation window`}>
          {t`The linked window must have valid increasing timestamps and be no longer than 24 h. No history has been requested from this link.`}
          <Button
            size='xs'
            variant='subtle'
            onClick={() =>
              setParams(
                (previous) => {
                  const next = new URLSearchParams(previous);
                  next.delete('history_from');
                  next.delete('history_to');
                  return next;
                },
                { replace: true }
              )
            }
          >{t`Use current range controls`}</Button>
        </Alert>
      )}
      <LinkedEvidence machineId={machine.pk} />
      <HistoryProvenance
        response={response}
        loading={seriesQuery.isFetching && !response}
        updating={seriesQuery.isPlaceholderData}
        failed={seriesQuery.isError}
        extent={dataRange.data}
        onRetry={() => seriesQuery.refetch()}
      />
      <Paper withBorder radius='md' p='sm'>
        <Button
          variant='subtle'
          size='xs'
          aria-expanded={investigationOpen}
          onClick={() => setInvestigationOpen((value) => !value)}
        >{t`Case span investigation`}</Button>
        {investigationOpen && (
          <InvestigationSlices
            initial={resolveWindow(range.window, now)}
            onRead={(span) => {
              range.onCustom(span.from, span.to);
            }}
          />
        )}
      </Paper>
      <ChannelNavigator
        parameters={parameters}
        search={search}
        onSearch={setSearch}
        section={section}
        onSection={selectSection}
        onShowAll={() => {
          setSearch('');
          setSection('all');
          setExpanded(
            new Set([
              'electrical',
              'operation',
              'winding',
              'vibration',
              'cooling',
              'bearing',
              'other'
            ])
          );
        }}
      />
      <Paper withBorder radius='md' p='sm'>
        <Button
          variant='subtle'
          size='xs'
          aria-expanded={comparisonOpen}
          onClick={() => {
            setComparisonOpen((value) => !value);
            setComparisonMounted(true);
          }}
        >{t`Comparisons and display views`}</Button>
        <div style={{ display: comparisonOpen ? undefined : 'none' }}>
          {comparisonMounted && (
            <ComparisonWorkbench
              machineId={machine.pk}
              assetType={machine.asset_type}
              parameters={parameters}
              response={seriesQuery.isError ? undefined : response}
              window={range.window}
              search={search}
              section={section}
              onView={(view) => {
                setSearch(view.search);
                selectSection(view.section);
              }}
            />
          )}
        </div>
      </Paper>
      <KpiStrip tiles={tiles} now={now} />

      {/* Counts are of this machine's own signals; the station context read
          alongside them is not something this pump has limits on. */}
      <UnusualStrip parameters={parameters} response={response} />

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
          <Section
            title={categoryLabel('electrical')}
            visible={visible('electrical')}
            open={isOpen('electrical')}
            onToggle={() => toggleSection('electrical')}
          >
            <ElectricalSection
              parameters={allParameters}
              series={series}
              window={window}
              syncId={syncId}
              onZoom={range.onZoom}
            />
          </Section>
          <Section
            title={t`Speed, operation and hydraulics`}
            visible={visible('operation')}
            open={isOpen('operation')}
            onToggle={() => toggleSection('operation')}
          >
            <OperationSection
              parameters={allParameters}
              series={series}
              window={window}
              syncId={syncId}
              onZoom={range.onZoom}
            />
          </Section>
          <Section
            title={categoryLabel('winding')}
            visible={visible('winding')}
            open={isOpen('winding')}
            onToggle={() => toggleSection('winding')}
          >
            <WindingSection
              parameters={allParameters}
              series={series}
              window={window}
              syncId={syncId}
              onZoom={range.onZoom}
            />
          </Section>
          <Section
            title={categoryLabel('vibration')}
            visible={visible('vibration')}
            open={isOpen('vibration')}
            onToggle={() => toggleSection('vibration')}
          >
            <VibrationSection
              parameters={allParameters}
              series={series}
              window={window}
              syncId={syncId}
              onZoom={range.onZoom}
            />
          </Section>
          <Section
            title={categoryLabel('cooling')}
            visible={visible('cooling')}
            open={isOpen('cooling')}
            onToggle={() => toggleSection('cooling')}
          >
            <CoolingSection
              parameters={allParameters}
              series={series}
              window={window}
              syncId={syncId}
              onZoom={range.onZoom}
            />
          </Section>
          <Section
            title={categoryLabel('bearing')}
            visible={visible('bearing')}
            open={isOpen('bearing')}
            onToggle={() => toggleSection('bearing')}
          >
            <BearingSection
              parameters={allParameters}
              series={series}
              window={window}
              syncId={syncId}
              onZoom={range.onZoom}
            />
          </Section>
          {inCategory(allParameters, 'other').length > 0 && (
            <Section
              title={categoryLabel('other')}
              visible={visible('other')}
              open={isOpen('other')}
              onToggle={() => toggleSection('other')}
            >
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
          {t`Read live from ${response.series[0]?.source_name ?? t`the source`}. Every window chart here is a bounded read of the source's own history; retained evidence is identified separately.`}
        </Text>
      )}
    </Stack>
  );
}

/** Mount on first expansion, then retain chart controls through collapse/filter. */
function Section({
  title,
  children,
  visible,
  open,
  onToggle
}: Readonly<{
  title: string;
  children: React.ReactNode;
  visible: boolean;
  open: boolean;
  onToggle: () => void;
}>) {
  const [mounted, setMounted] = useState(open);
  useEffect(() => {
    if (open) setMounted(true);
  }, [open]);
  return (
    <Stack
      gap='sm'
      data-section={title}
      style={{ display: visible ? undefined : 'none' }}
    >
      <Group justify='space-between'>
        <Title order={5}>{title}</Title>
        <Button
          variant='subtle'
          size='compact-xs'
          aria-expanded={open}
          onClick={onToggle}
        >
          {open ? t`Collapse ${title}` : t`Open ${title}`}
        </Button>
      </Group>
      <div style={{ display: open ? undefined : 'none' }}>
        {(mounted || open) && children}
      </div>
    </Stack>
  );
}

/**
 * Level 2: is anything unusual? Stale readings, poor quality, configured
 * limits crossed, and how much of the machine was actually judged. Calm is
 * claimed only from readings the server evaluated against their limits
 * (``evaluatePerformance``); no limits or nothing evaluable says so instead.
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
  const evaluation = evaluatePerformance(parameters.map((p) => p.signal));
  const critical = parameters.filter((p) => p.signal.state === 'critical');
  const warning = parameters.filter((p) => p.signal.state === 'warning');
  const unavailable =
    response?.series.filter((entry) => !entry.available) ?? [];

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
        {evaluation.verdict.kind === 'calm' && (
          <Badge color='green' variant='light'>
            {t`Nothing outside configured limits`}
          </Badge>
        )}
        {evaluation.verdict.kind === 'no-limits' && (
          <Badge color='gray' variant='light'>
            {t`No configured limits to evaluate`}
          </Badge>
        )}
        {evaluation.verdict.kind === 'unavailable' && (
          <Badge color='gray' variant='light'>
            {t`Evaluation unavailable`}
          </Badge>
        )}
        <Text size='xs' c='dimmed' data-evaluated={evaluation.evaluated}>
          {t`${evaluation.evaluated} of ${evaluation.total} evaluated against their limits now.`}{' '}
          {t`${evaluation.configured} of ${evaluation.total} signals have limits configured; the rest are drawn without a verdict.`}
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
