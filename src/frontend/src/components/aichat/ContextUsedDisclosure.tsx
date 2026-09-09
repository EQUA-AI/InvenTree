/**
 * Collapsed "Context used" disclosure under an assistant answer (M2 PR 9;
 * plan 9.11 / GR-16). Ids and counts only: which prompt sections were
 * present and how many items each carried, the per-corpus retrieval
 * ledger, the summary watermark, and the closed-vocabulary plan codes.
 * The record it renders has already been through the allow-list
 * normalizer, so no text field can reach this component.
 */

import { t } from '@lingui/core/macro';
import {
  Badge,
  Collapse,
  Group,
  Stack,
  Text,
  UnstyledButton
} from '@mantine/core';
import { IconChevronDown, IconChevronRight } from '@tabler/icons-react';
import { useState } from 'react';

import type { ContextUsedRecord } from './contextUsed';

/**
 * The per-corpus ledger vocabulary is the server's closed `LedgerState`
 * enum: not_consulted / consulted_none / used (plan 9.11). Only `used`
 * carries a hit count; GR-16: authorization filtering and an empty match
 * both read as "consulted - nothing usable".
 */
function corpusStateLabel(state: string): string {
  switch (state) {
    case 'not_consulted':
      return t`not consulted`;
    case 'consulted_none':
      return t`consulted - nothing usable`;
    case 'used':
      return t`used`;
    default:
      return state;
  }
}

function corpusStateCarriesCount(state: string): boolean {
  return state !== 'not_consulted' && state !== 'consulted_none';
}

export function ContextUsedDisclosure({
  record
}: Readonly<{ record: ContextUsedRecord }>) {
  const [open, setOpen] = useState(false);

  const rows: Array<{ key: string; label: string; value: string }> = [];

  if (record.recentTurns) {
    rows.push({
      key: 'recent_turns',
      label: t`Recent turns`,
      value: `${record.recentTurns.used} / ${record.recentTurns.available}`
    });
  }
  if (record.summaryProjected) {
    rows.push({
      key: 'summary',
      label: t`Summary`,
      value: record.summary
        ? t`through message ${record.summary.throughSequence}`
        : t`no summary`
    });
  }
  if (record.preferencesUsed !== null) {
    rows.push({
      key: 'preferences',
      label: t`Preferences`,
      value: String(record.preferencesUsed)
    });
  }
  if (record.factsUsed !== null) {
    rows.push({
      key: 'facts',
      label: t`Facts`,
      value: String(record.factsUsed)
    });
  }
  for (const corpus of record.corpora) {
    rows.push({
      key: `corpus_${corpus.corpus}`,
      label: corpus.corpus,
      value: corpusStateCarriesCount(corpus.state)
        ? `${corpusStateLabel(corpus.state)} · ${corpus.n}`
        : corpusStateLabel(corpus.state)
    });
  }
  if (record.retrievalEnvelopes !== null) {
    rows.push({
      key: 'envelopes',
      label: t`Retrieval envelopes`,
      value: String(record.retrievalEnvelopes)
    });
  }
  if (record.topology) {
    rows.push({ key: 'topology', label: t`Topology`, value: record.topology });
  }
  for (const cut of record.truncation) {
    rows.push({
      key: `truncation_${cut.slot}`,
      label: t`Truncated`,
      value: `${cut.slot} · ${cut.dropped}`
    });
  }
  if (record.taskIntent) {
    rows.push({
      key: 'task_intent',
      label: t`Task intent`,
      value: record.taskIntent
    });
  }
  if (record.memoryTypes.length > 0) {
    rows.push({
      key: 'memory_types',
      label: t`Memory types`,
      value: record.memoryTypes.join(', ')
    });
  }
  if (record.degradeReason) {
    rows.push({
      key: 'degrade',
      label: t`Degraded`,
      value: record.degradeReason
    });
  }

  return (
    <Stack gap={2} mt={6} data-testid='context-used-disclosure'>
      <UnstyledButton
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        aria-label={t`Toggle context used`}
      >
        <Group gap={4} wrap='nowrap'>
          {open ? (
            <IconChevronDown size={12} aria-hidden />
          ) : (
            <IconChevronRight size={12} aria-hidden />
          )}
          <Text size='xs' fw={600} c='dimmed'>
            {t`Context used`}
          </Text>
        </Group>
      </UnstyledButton>
      <Collapse expanded={open}>
        <Stack gap={2} data-testid='context-used-rows'>
          {rows.length === 0 ? (
            <Text size='xs' c='dimmed'>
              {t`Nothing was used`}
            </Text>
          ) : (
            rows.map((row) => (
              <Group key={row.key} gap={6} wrap='nowrap'>
                <Badge size='xs' variant='light' color='gray'>
                  {row.label}
                </Badge>
                <Text
                  size='xs'
                  c='dimmed'
                  data-testid={`context-used-${row.key}`}
                >
                  {row.value}
                </Text>
              </Group>
            ))
          )}
        </Stack>
      </Collapse>
    </Stack>
  );
}
