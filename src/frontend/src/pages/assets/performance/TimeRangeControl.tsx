import { t } from '@lingui/core/macro';
import {
  ActionIcon,
  Button,
  Group,
  SegmentedControl,
  Stack,
  Switch,
  Text,
  Tooltip,
  useMantineTheme
} from '@mantine/core';
import { DateTimePicker } from '@mantine/dates';
import {
  IconPlayerPause,
  IconPlayerPlay,
  IconRefresh,
  IconZoomReset
} from '@tabler/icons-react';
import dayjs from 'dayjs';
import { useEffect, useMemo, useState } from 'react';
import { displayInstant } from '../displayClock';
import { StatusBadge as Badge } from '../health/StatusBadge';
import monitoring from '../monitoring.module.css';

import type { SeriesResponse } from '@lib/types/MachineHealth';

import {
  formatClock,
  formatClockOn,
  formatDuration,
  formatTick
} from './format';
import {
  type DataRange,
  type RangePreset,
  type WindowSpec,
  windowSeconds
} from './usePerformanceData';

const PRESETS: { value: RangePreset; label: () => string }[] = [
  { value: '15m', label: () => t`15 min` },
  { value: '1h', label: () => t`1 h` },
  { value: '6h', label: () => t`6 h` },
  { value: '12h', label: () => t`12 h` },
  { value: '24h', label: () => t`24 h` }
];

const CUSTOM = 'custom';
const MAX_CUSTOM_SECONDS = 24 * 3600;

export interface TimeRangeControlProps {
  preset: RangePreset | typeof CUSTOM;
  onPreset: (preset: RangePreset) => void;
  onCustom: (from: number, to: number) => void;
  /** A zoom narrows the window below the preset; clearing it goes back. */
  zoomed: WindowSpec | null;
  onClearZoom: () => void;
  live: boolean;
  onLive: (live: boolean) => void;
  onRefresh: () => void;
  refreshing: boolean;
  dataRange: DataRange | undefined;
}

/**
 * The one control every chart on the page obeys.
 *
 * Presets, a bounded custom range, live on/off and refresh. A zoom is shown as
 * a badge with its own clear action rather than as a sixth preset, because it
 * is a temporary narrowing of whatever the preset was, and the operator should
 * be able to see both at once.
 */
export function TimeRangeControl({
  preset,
  onPreset,
  onCustom,
  zoomed,
  onClearZoom,
  live,
  onLive,
  onRefresh,
  refreshing,
  dataRange
}: Readonly<TimeRangeControlProps>) {
  const theme = useMantineTheme();
  const [customFrom, setCustomFrom] = useState<string | null>(null);
  const [customTo, setCustomTo] = useState<string | null>(null);
  // Opening the custom form is local, but the window it belongs to is not:
  // re-enabling Live clears the custom range upstream, and a flag kept only
  // here went on highlighting "Custom" over abandoned dates while the charts
  // were back on a preset. The form closes whenever the window stops being
  // custom, so the highlighted segment is always the one being drawn.
  const [manualOpen, setManualOpen] = useState(false);
  const pickingCustom = preset === CUSTOM || manualOpen;
  useEffect(() => {
    if (preset !== CUSTOM) setManualOpen(false);
  }, [preset]);

  const customError = useMemo(() => {
    if (!customFrom || !customTo) return t`Pick both a start and an end.`;
    const from = dayjs(customFrom);
    const to = dayjs(customTo);
    if (!from.isValid() || !to.isValid())
      return t`That is not a readable date and time.`;
    if (!to.isAfter(from)) return t`The end must come after the start.`;
    if (to.diff(from, 'second') > MAX_CUSTOM_SECONDS) {
      return t`A window may not exceed 24 hours.`;
    }
    return null;
  }, [customFrom, customTo]);

  // An empty field is guidance; a filled pair the page refuses is a refusal,
  // and a refusal printed in the same dimmed grey as the help text beside it
  // read as one long sentence about history.
  const customInvalid = customError !== null && !!customFrom && !!customTo;

  const minDate =
    dataRange?.available && dataRange.from
      ? new Date(dataRange.from)
      : undefined;
  const maxDate =
    dataRange?.available && dataRange.to ? new Date(dataRange.to) : new Date();

  return (
    <Stack gap='xs'>
      <Group gap='sm' align='center' wrap='wrap'>
        <SegmentedControl
          color={`${theme.primaryColor}.7`}
          size='xs'
          value={pickingCustom ? CUSTOM : preset}
          onChange={(value) => {
            if (value === CUSTOM) {
              setManualOpen(true);
              return;
            }
            setManualOpen(false);
            onPreset(value as RangePreset);
          }}
          data={[
            ...PRESETS.map((p) => ({ value: p.value, label: p.label() })),
            { value: CUSTOM, label: t`Custom` }
          ]}
        />
        {zoomed && (
          <Badge
            variant='light'
            color='blue'
            rightSection={
              <ActionIcon
                size='xs'
                variant='transparent'
                color='blue'
                aria-label={t`Clear zoom`}
                onClick={onClearZoom}
              >
                <IconZoomReset size={12} />
              </ActionIcon>
            }
          >
            {zoomed.kind === 'absolute'
              ? t`Zoomed ${formatTick(zoomed.from, windowSeconds(zoomed))} – ${formatTick(zoomed.to, windowSeconds(zoomed))}`
              : t`Zoomed`}
          </Badge>
        )}
        <Group gap={6} ml='auto' wrap='nowrap'>
          <Tooltip
            label={
              live
                ? t`Live: the window follows the clock and re-reads as the source is polled.`
                : t`Paused: the window is frozen where you left it.`
            }
          >
            <Switch
              classNames={{ track: monitoring.track }}
              size='sm'
              checked={live}
              onChange={(event) => onLive(event.currentTarget.checked)}
              onLabel={<IconPlayerPlay size={12} />}
              offLabel={<IconPlayerPause size={12} />}
              label={
                <Text size='xs' fw={500}>
                  {t`Live`}
                </Text>
              }
              labelPosition='left'
            />
          </Tooltip>
          <Tooltip label={t`Re-read now`}>
            <ActionIcon
              variant='default'
              size='md'
              aria-label={t`Refresh`}
              loading={refreshing}
              onClick={onRefresh}
            >
              <IconRefresh size={16} />
            </ActionIcon>
          </Tooltip>
        </Group>
      </Group>

      {pickingCustom && (
        <>
          <Group gap='sm' align='flex-end' wrap='wrap'>
            <DateTimePicker
              classNames={{
                input: monitoring.input,
                placeholder: monitoring.meta
              }}
              label={t`From`}
              size='xs'
              value={customFrom}
              onChange={setCustomFrom}
              valueFormat='D MMM YYYY HH:mm:ss'
              withSeconds
              clearable
              minDate={minDate}
              maxDate={maxDate}
              placeholder={t`Start of window`}
              style={{ minWidth: 200 }}
            />
            <DateTimePicker
              classNames={{
                input: monitoring.input,
                placeholder: monitoring.meta
              }}
              label={t`To`}
              size='xs'
              value={customTo}
              onChange={setCustomTo}
              valueFormat='D MMM YYYY HH:mm:ss'
              withSeconds
              clearable
              minDate={customFrom ? new Date(customFrom) : minDate}
              maxDate={maxDate}
              placeholder={t`End of window`}
              // Every refusal is about the end relative to the start, so this
              // is the field the reader has to change.
              error={customInvalid || undefined}
              style={{ minWidth: 200 }}
            />
            <Button
              size='xs'
              disabled={customError !== null}
              onClick={() => {
                if (!customFrom || !customTo) return;
                onCustom(
                  dayjs(customFrom).valueOf(),
                  dayjs(customTo).valueOf()
                );
              }}
            >
              {t`Apply`}
            </Button>
            {customError && (
              <Text
                size='xs'
                c={
                  customInvalid
                    ? 'red'
                    : 'var(--aimms-monitoring-meta, var(--mantine-color-text))'
                }
                fw={customInvalid ? 500 : undefined}
                role={customInvalid ? 'alert' : undefined}
              >
                {customError}
              </Text>
            )}
          </Group>
          {/* Its own line: sharing a row with the refusal above made the two
              grey sentences read as one. */}
          {dataRange?.available && dataRange.from && dataRange.to && (
            <Text size='xs' className={monitoring.meta}>
              {t`History held from ${displayInstant(dataRange.from) ?? t`Time unavailable`} to ${displayInstant(dataRange.to) ?? t`Time unavailable`}.`}
            </Text>
          )}
        </>
      )}
    </Stack>
  );
}

export interface LiveIndicatorProps {
  live: boolean;
  fetching: boolean;
  failed?: boolean;
  /** Newest reading on the page, display clock. */
  newestAt: number | null;
  /** When the last successful read finished. */
  updatedAt: number | null;
  response: SeriesResponse | undefined;
  freshnessSeconds: number | null;
  now: number;
}

/**
 * Whether "live" is true, stated in the data's own terms.
 *
 * Green only when the page is following the clock *and* the newest reading is
 * inside the source's freshness budget. A live page whose newest reading is
 * old is stale and says so; a paused page says paused. Beneath, the cadence
 * facts a reader needs to judge any of it: how often the plant writes, how
 * often this deployment reads it, and whether the window on screen was read
 * whole or sampled.
 */
export function LiveIndicator({
  live,
  fetching,
  failed = false,
  newestAt,
  updatedAt,
  response,
  freshnessSeconds,
  now
}: Readonly<LiveIndicatorProps>) {
  const ageSeconds =
    newestAt === null ? null : Math.max(0, (now - newestAt) / 1000);
  const stale =
    ageSeconds !== null &&
    freshnessSeconds !== null &&
    ageSeconds > freshnessSeconds;
  const disabled = response ? !response.live.enabled : false;

  // A read that returned no reading at all has nothing to be fresh: green
  // "Live" over a source that served nothing read as a healthy feed.
  const empty = !!response && newestAt === null;

  let color = 'gray';
  let word = t`Paused`;
  if (failed) {
    word = t`Read failed`;
  } else if (!response) {
    word = fetching ? t`Reading` : t`Not read yet`;
  } else if (live && disabled) {
    word = t`Polling off`;
  } else if (live && empty) {
    word = t`No readings`;
  } else if (live && (freshnessSeconds === null || freshnessSeconds <= 0)) {
    word = t`Freshness unavailable`;
  } else if (live && stale) {
    color = 'gray';
    word = t`Stale`;
  } else if (live) {
    color = 'green';
    word = t`Live`;
  }

  const cadence = response?.limits.expected_sample_interval_seconds ?? null;
  const poll = response?.live.poll_interval_seconds ?? null;

  let modeText: string | null = null;
  if (response) {
    if (response.read_batches && response.read_batches.length > 1) {
      modeText = response.read_batches.some((batch) => batch.failed)
        ? t`Partial history: some bounded batches failed. See history coverage before drawing conclusions.`
        : t`${response.read_batches.length} bounded history batches (${response.mode}); resolution ${formatDuration(response.resolution_seconds)}. Document counts are per batch, not a flat-cost total.`;
    } else if (response.mode === 'complete') {
      modeText = t`Every reading in the window (${response.documents_read} snapshots, cadence ${formatDuration(response.cadence_seconds ?? cadence ?? 0)}).`;
    } else {
      modeText = t`Sampled: one reading every ${formatDuration(response.resolution_seconds)} — ${response.documents_read} of about ${response.expected_documents.toLocaleString()} snapshots.`;
    }
  }

  return (
    <Stack gap={2} align='flex-end'>
      <Group gap={6} wrap='nowrap'>
        <Badge
          color={color}
          variant={
            live && !!response && !failed && !stale && !disabled && !empty
              ? 'filled'
              : 'light'
          }
          size='sm'
          leftSection={
            <span
              aria-hidden
              style={{
                display: 'inline-block',
                width: 8,
                height: 8,
                borderRadius: 8,
                background: 'currentColor',
                opacity: fetching ? 0.4 : 1
              }}
            />
          }
        >
          {word}
        </Badge>
        <Text size='xs' className={monitoring.meta}>
          {updatedAt === null
            ? t`Not read yet`
            : t`Updated ${formatClock(updatedAt)}`}
          {/* The read happened now; the reading it found need not have. */}
          {newestAt !== null
            ? ` · ${t`newest reading ${formatClockOn(newestAt, now)}`}`
            : ''}
        </Text>
      </Group>
      <Text size='xs' className={monitoring.meta} ta='right'>
        {cadence !== null
          ? t`Expected sample interval ${formatDuration(cadence)} (server default)`
          : ''}
        {poll !== null
          ? ` · ${t`source poll interval ${formatDuration(poll)}`}`
          : ''}
        {response?.live.last_poll_at
          ? ` · ${t`last poll attempt ${displayInstant(response.live.last_poll_at) ?? t`Time unavailable`}`}`
          : ''}
      </Text>
      {modeText && (
        <Text size='xs' className={monitoring.meta} ta='right'>
          {modeText}
        </Text>
      )}
      {response?.display_shifted && (
        <Text size='xs' className={monitoring.meta} ta='right'>
          {t`Source history display shift: ${formatDuration(Math.abs(response.display_shift_seconds))} ${response.display_shift_seconds >= 0 ? t`later than recorded` : t`earlier than recorded`}.`}
        </Text>
      )}
    </Stack>
  );
}

export default TimeRangeControl;
