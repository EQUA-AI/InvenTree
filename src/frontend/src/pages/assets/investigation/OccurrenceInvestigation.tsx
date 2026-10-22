import { t } from '@lingui/core/macro';
import { Anchor, Spoiler, Stack, Text } from '@mantine/core';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import type { FaultOccurrence } from '../faults/occurrenceModel';
import operations from '../operations.module.css';
import { recordedTelemetryAsLive } from '../telemetryPresentation';
import { InvestigationSlices } from './InvestigationSlices';
import {
  boundedWindow,
  investigationHref,
  sliceAt,
  validSpan
} from './windows';

/** Uses only the opened occurrence's re-authorized detail, never current values. */
export function OccurrenceInvestigation({
  machineId,
  occurrence
}: Readonly<{ machineId: number; occurrence: FaultOccurrence }>) {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  // Links retain plant instants even while O27 displays this occurrence as live.
  const shift =
    recordedTelemetryAsLive(occurrence) && occurrence.display_shifted
      ? occurrence.display_shift_seconds * 1000
      : 0;
  const from = Date.parse(occurrence.first_observed_at ?? '');
  const to = Date.parse(occurrence.last_observed_at ?? '');
  const sourceFrom = from - shift;
  const context = boundedWindow(
    sourceFrom - 30 * 60_000,
    sourceFrom + 30 * 60_000
  );
  return (
    <Stack gap='xs' data-testid='occurrence-investigation'>
      <Text fw={600} size='sm'>{t`Investigate source history`}</Text>
      <Spoiler
        classNames={{ control: operations.link }}
        maxHeight={22}
        showLabel={t`Read history limits`}
        hideLabel={t`Show less`}
        maw='70ch'
      >
        <Text size='xs' className={operations.meta}>
          {recordedTelemetryAsLive(occurrence)
            ? t`A new source read can differ from the retained evidence below. It does not replace, extend or recapture a snapshot. Source retention, resolution and permissions apply.`
            : t`A new historian read can differ from the retained evidence below. It does not replace, extend or recapture a snapshot. Source retention, resolution and permissions apply. Case and snapshot bounds retain their recorded instants; Performance converts them only with a known display-clock offset.`}
        </Text>
      </Spoiler>
      {context && (
        <Anchor
          className={operations.link}
          component={Link}
          size='sm'
          to={investigationHref(
            machineId,
            context,
            occurrence.id,
            undefined,
            'recorded',
            params
          )}
        >
          {t`Trend around first observation · 30 min before and after`}
        </Anchor>
      )}
      <Text
        size='xs'
        className={operations.meta}
      >{t`The context window is a navigation choice, not the event duration. Performance shows available source samples and gaps.`}</Text>
      {validSpan(from, to) ? (
        <InvestigationSlices
          initial={{ from, to }}
          onRead={(span) =>
            navigate(
              investigationHref(
                machineId,
                { from: span.from - shift, to: span.to - shift },
                occurrence.id,
                undefined,
                'recorded',
                params
              )
            )
          }
        />
      ) : (
        <Text size='xs'>{t`The occurrence does not record an increasing observation window. Choose a bounded source window in Performance; no event duration is inferred.`}</Text>
      )}
      {occurrence.snapshots.map((snapshot) => {
        const start = Date.parse(snapshot.window_start ?? '');
        const end = Date.parse(snapshot.window_end ?? '');
        const span =
          boundedWindow(start, end) ?? sliceAt({ from: start, to: end }, 0);
        return (
          span && (
            <Anchor
              className={operations.link}
              component={Link}
              key={snapshot.id}
              size='xs'
              to={investigationHref(
                machineId,
                span,
                occurrence.id,
                snapshot.id,
                'recorded',
                params
              )}
            >
              {t`Read source history for retained snapshot ${snapshot.id}`} ·{' '}
              {snapshot.source_name ?? t`Source not recorded`}
              {end - start > 86400_000 ? ` · ${t`first bounded slice`}` : ''}
            </Anchor>
          )
        );
      })}
    </Stack>
  );
}
