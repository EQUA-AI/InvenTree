import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type { SeriesResponse } from '@lib/types/MachineHealth';
import { t } from '@lingui/core/macro';
import {
  Alert,
  Button,
  Group,
  Loader,
  MultiSelect,
  Paper,
  Select,
  SimpleGrid,
  Stack,
  Text,
  TextInput
} from '@mantine/core';
import { DateTimePicker } from '@mantine/dates';
import { useQuery } from '@tanstack/react-query';
import dayjs from 'dayjs';
import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useApi } from '../../../contexts/ApiContext';
import { useLocalState } from '../../../states/LocalState';
import { useUserState } from '../../../states/UserState';
import { MetaText } from '../health/common';
import { HistoryProvenance } from '../investigation/HistoryProvenance';
import { pickerInstant } from '../investigation/windows';
import monitoring from '../monitoring.module.css';
import {
  LIVE_TELEMETRY_WINDOW_SECONDS,
  recordedTelemetryAsLive
} from '../telemetryPresentation';
import { useNow } from '../useNow';
import { DisplayPreferences } from './DisplayPreferences';
import { HistoryReadStack } from './HistoryReadContext';
import { windowInfoFrom } from './PerformancePanel';
import type { WindowInfo } from './SensorGroupCard';
import { TrendChart } from './TrendChart';
import { WindowStatus } from './WindowStatus';
import { type DisplayView, comparisonGroups } from './comparisonViews';
import { unitLabel } from './format';
import { type ResolvedParameter, resolveParameters } from './resolve';
import { lineFor, rowsFor } from './sections/common';
import { indexSeries } from './series';
import { sameScale } from './units';
import { useComparisonDropdown } from './useComparisonDropdown';
import {
  type WindowSpec,
  resolveWindow,
  useMachineSeries,
  useMachineSignals
} from './usePerformanceData';

export function ComparisonWorkbench({
  machineId,
  assetType,
  parameters,
  response,
  window,
  search,
  section,
  onView
}: Readonly<{
  machineId: number;
  assetType: string | undefined;
  parameters: ResolvedParameter[];
  response: SeriesResponse | undefined;
  window: WindowSpec;
  search: string;
  section: string;
  onView: (view: DisplayView) => void;
}>) {
  const now = useNow();
  const [params] = useSearchParams();
  const [selected, setSelected] = useState<string[]>(
    () =>
      params
        .get('compare')
        ?.split(',')
        .filter((id) => /^\d+$/.test(id)) ?? []
  );
  const [missing, setMissing] = useState(false);
  const [mode, setMode] = useState('within');
  const modeDropdown = useComparisonDropdown();
  const channelsDropdown = useComparisonDropdown();
  const chosen = parameters.filter((p) =>
    selected.includes(String(p.signal.binding_id))
  );
  const [anchor, setAnchor] = useState<WindowSpec>(() => ({
    kind: 'absolute',
    ...toAbsolute(window)
  }));
  const restore = (view: DisplayView) => {
    const allowed = new Set(parameters.map((p) => p.signal.binding_id));
    setSelected(view.bindings.filter((id) => allowed.has(id)).map(String));
    setMissing(view.bindings.some((id) => !allowed.has(id)));
    onView(view);
  };
  return (
    <Stack gap='md' data-testid='comparison-workbench'>
      {(missing ||
        selected.some(
          (id) => !parameters.some((p) => String(p.signal.binding_id) === id)
        )) && (
        <Alert color='yellow'>{t`Some selected or saved signals are no longer mapped or permitted. They were not substituted.`}</Alert>
      )}
      <SimpleGrid cols={{ base: 1, lg: 2 }} spacing='md'>
        <Stack gap='xs'>
          <Text fw={600} size='sm'>{t`Investigation comparison`}</Text>
          <SimpleGrid cols={{ base: 1, sm: 2 }}>
            <Select
              ref={modeDropdown.ref}
              onDropdownOpen={modeDropdown.reveal}
              onDropdownClose={modeDropdown.cancel}
              classNames={{ input: monitoring.input }}
              label={t`Comparison mode`}
              value={mode}
              allowDeselect={false}
              onChange={(value) => {
                setMode(value ?? 'within');
                setAnchor({ kind: 'absolute', ...toAbsolute(window) });
              }}
              data={[
                { value: 'within', label: t`Within this machine` },
                { value: 'machine', label: t`Another permitted machine` },
                { value: 'before-after', label: t`Before / after windows` }
              ]}
            />
            <MultiSelect
              ref={channelsDropdown.ref}
              onDropdownOpen={channelsDropdown.reveal}
              onDropdownClose={channelsDropdown.cancel}
              classNames={{ input: monitoring.input }}
              label={t`Comparison channels`}
              searchable
              value={selected}
              onChange={(value) => {
                setSelected(value);
                setMissing(false);
                channelsDropdown.reveal();
              }}
              maxValues={12}
              filter={({ options, search }) =>
                options.filter(
                  (option) =>
                    'value' in option &&
                    `${option.label} ${parameters.find((p) => String(p.signal.binding_id) === option.value)?.signal.external_key ?? ''}`
                      .toLowerCase()
                      .includes(search.toLowerCase())
                )
              }
              renderOption={({ option }) => (
                <Stack gap={0}>
                  <Text size='sm'>{option.label}</Text>
                  <MetaText size='xs'>
                    {
                      parameters.find(
                        (p) => String(p.signal.binding_id) === option.value
                      )?.signal.external_key
                    }
                  </MetaText>
                </Stack>
              )}
              data={parameters.map((p) => ({
                value: String(p.signal.binding_id),
                label: `${p.label} · ${unitLabel(p.signal.unit)}`
              }))}
            />
          </SimpleGrid>
          <MetaText size='sm'>{t`Up to 12 channels. Only matching engineering roles and compatible units share a chart. Other channels use separate synchronized charts. Operating duty, load and instrument configuration are not verified by unit compatibility.`}</MetaText>
          {chosen.length === 0 ? (
            <Text size='sm'>{t`Select channels to compare.`}</Text>
          ) : mode === 'within' ? (
            <ComparisonPlots
              parameters={chosen}
              response={response}
              window={windowInfoFrom(response, window, now)}
              syncId={`compare-${machineId}`}
            />
          ) : mode === 'before-after' ? (
            <BeforeAfter
              machineId={machineId}
              parameters={chosen}
              anchor={anchor}
              asLive={recordedTelemetryAsLive(response)}
            />
          ) : (
            <MachineComparison
              key={machineId}
              machineId={machineId}
              assetType={assetType}
              parameters={chosen}
              window={anchor}
              asLive={recordedTelemetryAsLive(response)}
            />
          )}
        </Stack>
        <Stack gap='sm'>
          <DisplayPreferences
            presentation={response}
            machineId={machineId}
            current={{ search, section, bindings: selected.map(Number) }}
            onRestore={restore}
          />
        </Stack>
      </SimpleGrid>
    </Stack>
  );
}
function toAbsolute(window: WindowSpec) {
  const { from, to } = resolveWindow(window, Date.now());
  return { from, to };
}

/** Uses the same native chart and gap adapter; separate scales also synchronize. */
export function ComparisonPlots({
  parameters,
  response,
  window,
  syncId
}: Readonly<{
  parameters: ResolvedParameter[];
  response: SeriesResponse | undefined;
  window: WindowInfo;
  syncId: string;
}>) {
  const series = useMemo(() => indexSeries(response), [response]);
  const groups = useMemo(() => comparisonGroups(parameters), [parameters]);
  return (
    <Stack gap='sm'>
      {groups.length > 1 && (
        <Stack gap={2} data-testid='comparison-refusal'>
          <Text fw={600} size='sm'>{t`Not compared on one axis`}</Text>
          {groups.slice(1).map((group) => {
            const first = groups[0].items[0];
            const own = group.items[0];
            const mismatch = !sameScale(first.signal.unit, own.signal.unit)
              ? first.definition.role !== own.definition.role
                ? t`unit and role`
                : t`unit`
              : t`role`;
            return (
              <Text
                size='sm'
                maw='70ch'
                key={group.key}
              >{t`${group.items.map((p) => p.label).join(', ')} (${unitLabel(own.signal.unit)}, ${own.definition.role}) can't share an axis with ${groups[0].items.map((p) => p.label).join(', ')} (${unitLabel(first.signal.unit)}, ${first.definition.role}): different ${mismatch}.`}</Text>
            );
          })}
          <Text size='sm'>{t`Shown on separate synchronized charts`}</Text>
        </Stack>
      )}
      {groups.map((group) => (
        <Paper
          key={group.key}
          withBorder
          p='sm'
          data-comparison-group={group.key}
        >
          <Text fw={600} size='sm'>
            {group.items.map((p) => p.label).join(' / ')} ·{' '}
            {unitLabel(group.items[0].signal.unit)}
          </Text>
          <TrendChart
            rows={rowsFor(group.items, series, window)}
            series={group.items.map((p, i) =>
              lineFor(
                p,
                ['blue.6', 'indigo.6', 'cyan.7', 'grape.6'][i % 4],
                'left',
                p.definition.chart === 'step'
              )
            )}
            windowStart={window.start}
            windowEnd={window.end}
            windowSeconds={window.seconds}
            unit={group.items[0].signal.unit}
            syncId={syncId}
            toggleable
          />
        </Paper>
      ))}
    </Stack>
  );
}

function HistoryComparison({
  machineId,
  parameters,
  window,
  label,
  syncId,
  onShift
}: Readonly<{
  machineId: number;
  parameters: ResolvedParameter[];
  window: WindowSpec;
  label: string;
  syncId: string;
  onShift?: (shift: number | undefined) => void;
}>) {
  const now = useNow();
  const bindings = useMemo(
    () => parameters.map((p) => p.signal.binding_id),
    [parameters]
  );
  const query = useMachineSeries(machineId, { bindings }, window, false);
  const shift =
    query.isError || query.isFetching
      ? undefined
      : query.data?.display_shift_seconds;
  useEffect(() => {
    onShift?.(shift);
  }, [onShift, shift]);
  return (
    <HistoryReadStack
      response={query.isError ? undefined : query.data}
      gap='xs'
    >
      <Text fw={600} size='sm'>
        {label}
      </Text>
      {query.isFetching && (
        <Group gap='xs'>
          <Loader size='sm' />
          <Text size='sm'>{t`Reading bounded history…`}</Text>
        </Group>
      )}
      {query.isError ? (
        <Alert color='red'>
          {t`Comparison history read failed. No current reading is substituted.`}
          <Button
            className={monitoring.link}
            size='compact-xs'
            variant='subtle'
            onClick={() => query.refetch()}
          >{t`Retry comparison`}</Button>
        </Alert>
      ) : (
        <>
          <WindowStatus
            showClock={false}
            announce={false}
            response={query.data}
            updating={query.isPlaceholderData}
            now={now}
          />
          <HistoryProvenance
            response={query.data}
            loading={query.isFetching && !query.data}
            updating={query.isPlaceholderData}
            failed={false}
            extent={undefined}
            onRetry={() => query.refetch()}
            currentDescription={t`Comparison charts use the returned source window only. No current values are substituted.`}
          />
          {query.data && (
            <>
              <MetaText size='sm'>
                {query.data.series
                  .map((entry) => entry.source_name)
                  .filter((name, i, all) => all.indexOf(name) === i)
                  .join(', ')}{' '}
                · {query.data.resolution_seconds} s · {query.data.mode}
              </MetaText>
              <ComparisonPlots
                parameters={parameters}
                response={query.data}
                window={windowInfoFrom(query.data, window, now)}
                syncId={
                  onShift
                    ? `${syncId}-${Number.isFinite(shift) ? shift : `unknown-${machineId}`}`
                    : syncId
                }
              />
            </>
          )}
        </>
      )}
    </HistoryReadStack>
  );
}

function BeforeAfter({
  machineId,
  parameters,
  anchor,
  asLive
}: Readonly<{
  machineId: number;
  parameters: ResolvedParameter[];
  anchor: WindowSpec;
  asLive: boolean;
}>) {
  const [from, setFrom] = useState<string | null>(null);
  const [to, setTo] = useState<string | null>(null);
  const [after, setAfter] = useState<WindowSpec | null>(null);
  const start = pickerInstant(from);
  const end = pickerInstant(to);
  const maxWindowMs = (asLive ? LIVE_TELEMETRY_WINDOW_SECONDS : 86400) * 1000;
  const valid =
    Number.isFinite(start) &&
    Number.isFinite(end) &&
    end > start &&
    end - start <= maxWindowMs;
  return (
    <Stack gap='sm'>
      <Button
        size='sm'
        variant='light'
        w='fit-content'
        onClick={() => {
          const { from: originFrom, to: originTo } = resolveWindow(
            anchor,
            Date.now()
          );
          const span = originTo - originFrom;
          if (span <= 0 || span > maxWindowMs) return;
          const previous = {
            kind: 'absolute' as const,
            from: originFrom - span,
            to: originFrom
          };
          setFrom(dayjs(previous.from).format('YYYY-MM-DD HH:mm:ss'));
          setTo(dayjs(previous.to).format('YYYY-MM-DD HH:mm:ss'));
          setAfter(previous);
        }}
      >{t`Read preceding equal window as B`}</Button>
      <MetaText size='sm'>
        {asLive
          ? t`A is the selected window frozen when this mode opens. B is an independent source read, up to 30 days. Timestamps use the browser timezone. The preceding-window shortcut does not infer a repair boundary. Select the actual repair boundaries and check duty/load yourself.`
          : t`A is the selected window frozen when this mode opens. B is an independent source read, up to 24 h. Timestamps remain absolute in the browser timezone; charts do not shift events to imply a repair effect. The preceding-window shortcut does not infer a repair boundary. Select the actual repair boundaries and check duty/load yourself.`}
      </MetaText>
      <SimpleGrid
        cols={{ base: 1, sm: 2, lg: 3 }}
        style={{ alignItems: 'end' }}
      >
        <DateTimePicker
          classNames={{ input: monitoring.input }}
          label={t`Window B from`}
          value={from}
          onChange={setFrom}
        />
        <DateTimePicker label={t`Window B to`} value={to} onChange={setTo} />
        <Button
          disabled={!valid}
          onClick={() => setAfter({ kind: 'absolute', from: start, to: end })}
        >{t`Read window B`}</Button>
      </SimpleGrid>
      {(from || to) && !valid && (
        <Text size='sm' className={monitoring.error}>
          {asLive
            ? t`Choose an increasing window no longer than 30 days.`
            : t`Choose an increasing window no longer than 24 h.`}
        </Text>
      )}
      <HistoryComparison
        machineId={machineId}
        parameters={parameters}
        window={anchor}
        label={t`Window A — selected origin`}
        syncId={`before-${machineId}`}
      />
      {after && (
        <HistoryComparison
          machineId={machineId}
          parameters={parameters}
          window={after}
          label={t`Window B — independent origin`}
          syncId={`after-${machineId}`}
        />
      )}
    </Stack>
  );
}

function MachineComparison({
  machineId,
  assetType,
  parameters,
  window,
  asLive
}: Readonly<{
  machineId: number;
  assetType: string | undefined;
  parameters: ResolvedParameter[];
  window: WindowSpec;
  asLive: boolean;
}>) {
  const api = useApi();
  const host = useLocalState((state) => state.hostKey);
  const actor = useUserState((state) => state.user?.pk);
  const generation = useUserState((state) => state.authGeneration);
  const [search, setSearch] = useState('');
  const [lookup, setLookup] = useState('');
  const [candidate, setCandidate] = useState<string | null>(null);
  const machineDropdown = useComparisonDropdown();
  const [primaryShift, setPrimaryShift] = useState<number | undefined>();
  const [candidateShift, setCandidateShift] = useState<number | undefined>();
  const machines = useQuery<{
    results: { pk: number; name: string; asset_type: string }[];
  }>({
    queryKey: [
      'performance-comparison-machines',
      host,
      actor,
      generation,
      lookup
    ],
    retry: false,
    queryFn: async () =>
      (
        await api.get(apiUrl(ApiEndpoints.asset_machine_list), {
          params: { search: lookup, limit: 25 }
        })
      ).data
  });
  const candidates = (
    machines.isError ? [] : (machines.data?.results ?? [])
  ).filter(
    (item) =>
      item.pk !== machineId && !!assetType && item.asset_type === assetType
  );
  const permitted =
    !machines.isFetching &&
    candidates.find((item) => String(item.pk) === candidate);
  return (
    <Stack gap='sm'>
      <MetaText size='sm'>
        {asLive
          ? t`Candidates come from your permitted machine list, with the same asset type. This does not establish equivalent duty or load. No operational ranking is made. Both machines use the same time bounds. Select comparable duty and load yourself.`
          : t`Candidates come from your permitted machine list, with the same recorded asset type. This does not establish equivalent duty or load. No operational ranking is made. Both machines use the same absolute display-time bounds. Crosshairs synchronize only when their recorded-to-display clock offsets are known and equal.`}
      </MetaText>
      <Group align='flex-end' grow>
        <TextInput
          classNames={{ input: monitoring.input }}
          label={t`Find comparison machine`}
          value={search}
          onChange={(event) => setSearch(event.currentTarget.value)}
        />
        <Button
          variant='default'
          onClick={() => {
            setLookup(search);
            setCandidate(null);
          }}
        >{t`Search machines`}</Button>
      </Group>
      {machines.isFetching && <Loader size='sm' />}
      {machines.isError ? (
        <Alert color='red'>{t`Permitted machines could not be read.`}</Alert>
      ) : (
        <Select
          ref={machineDropdown.ref}
          onDropdownOpen={machineDropdown.reveal}
          onDropdownClose={machineDropdown.cancel}
          classNames={{ input: monitoring.input }}
          label={t`Comparison machine`}
          data={candidates.map((item) => ({
            value: String(item.pk),
            label: item.name
          }))}
          value={candidate}
          onChange={(value) => {
            setCandidateShift(undefined);
            setCandidate(value);
          }}
          searchable
        />
      )}
      {!machines.isFetching && !machines.isError && candidates.length === 0 && (
        <Text size='sm'>{t`No same-type candidate in these 25 results. Refine the search. Duty eligibility is not recorded.`}</Text>
      )}
      {permitted &&
        Number.isFinite(primaryShift) &&
        Number.isFinite(candidateShift) &&
        primaryShift !== candidateShift && (
          <Alert color='yellow'>{t`Different plant periods: these machines have different display-clock shifts (${primaryShift!} s and ${candidateShift!} s). Matching display times do not mean simultaneous plant observations. Crosshairs are not synchronized between machines.`}</Alert>
        )}
      {permitted && (
        <HistoryComparison
          machineId={machineId}
          parameters={parameters}
          window={window}
          label={t`This machine — selected origin`}
          syncId='machine-comparison'
          onShift={setPrimaryShift}
        />
      )}
      {permitted && (
        <MatchedMachine
          key={permitted.pk}
          machineId={permitted.pk}
          onShift={setCandidateShift}
          parameters={parameters}
          window={window}
        />
      )}
    </Stack>
  );
}
function MatchedMachine({
  machineId,
  parameters,
  window,
  onShift
}: Readonly<{
  machineId: number;
  parameters: ResolvedParameter[];
  window: WindowSpec;
  onShift: (shift: number | undefined) => void;
}>) {
  const signals = useMachineSignals(machineId, false);
  const mapped = useMemo(
    () => resolveParameters(signals.isError ? [] : (signals.data ?? [])),
    [signals.data, signals.isError]
  );
  // A known role and exact dictionary scale qualify a technical comparison;
  // uncatalogued tags have no cross-machine meaning and are never guessed.
  const selected = new Set(
    parameters
      .filter((p) => p.definition.role !== 'other')
      .map((p) => `${p.definition.role}/${p.signal.unit}`)
  );
  const compatible = mapped.filter((p) =>
    selected.has(`${p.definition.role}/${p.signal.unit}`)
  );
  const matched = compatible.slice(0, 12);
  return (
    <Stack gap='sm'>
      {signals.isFetching && <Loader size='sm' />}
      {signals.isError && (
        <Alert color='red'>{t`Comparison signals unavailable or no longer permitted.`}</Alert>
      )}
      {!signals.isPending && !signals.isError && matched.length === 0 && (
        <Text>{t`No compatible mapped engineering roles and units on this machine.`}</Text>
      )}
      {!signals.isError && matched.length > 0 && (
        <MetaText size='sm'>{t`Only the first 12 available role/unit matches below are compared; unmatched channels are omitted. Duty, load and configuration remain unverified.`}</MetaText>
      )}
      {!signals.isError && matched.length > 0 && (
        <HistoryComparison
          machineId={machineId}
          parameters={matched}
          window={window}
          label={t`Machine ${machineId} — matching roles and units`}
          onShift={onShift}
          syncId='machine-comparison'
        />
      )}
    </Stack>
  );
}
