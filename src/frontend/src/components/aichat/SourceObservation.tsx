import { t } from '@lingui/core/macro';
import { Paper, Stack, Text } from '@mantine/core';
import { CitationRow } from './CitationList';
import {
  type SourceObservation as Observation,
  sourceCitationDisplay,
  sourceObservationLines
} from './sourceObservation';

/** Provenance in the existing assistant; recorded scope never implies live data. */
export function SourceObservation({
  observation,
  messageId
}: Readonly<{
  observation: Observation;
  messageId: string;
}>) {
  return (
    <Paper
      component='section'
      aria-label={t`Source observation`}
      withBorder
      p='sm'
      radius='sm'
      mt='sm'
      data-testid='source-observation'
      style={{ overflowWrap: 'anywhere' }}
    >
      <Stack gap={4}>
        <Text size='xs' fw={600}>{t`Source observation`}</Text>
        {sourceObservationLines(observation).map((line) => (
          <Text key={line} size='sm'>
            {line}
          </Text>
        ))}
        {!('state' in observation) &&
          observation.citations.map((entry) => (
            <CitationRow
              key={entry.ordinal}
              entry={sourceCitationDisplay(entry)}
              anchorPrefix={messageId}
              timeZone='UTC'
            />
          ))}
      </Stack>
    </Paper>
  );
}
