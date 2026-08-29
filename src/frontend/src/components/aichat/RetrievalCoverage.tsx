/**
 * S11: the per-answer retrieval-coverage block (§8.8).
 *
 * Three DISTINCT states with distinct language:
 * - complete            → neutral status line ("602/602 evaluated · 24 shown")
 * - display truncation  → neutral ("All N evaluated; showing M…") — NOT a warning
 * - incomplete          → a real Alert ("25 of 403 records evaluated")
 *
 * Every sentence comes from the shared formatters, so the displayed text and
 * the copied/exported text can never drift.
 */

import type { RetrievalCoveragePayload } from '@lib/types/AimmsWire.generated';
import { t } from '@lingui/core/macro';
import { Alert, Group, Text } from '@mantine/core';
import { IconAlertTriangle, IconCheck } from '@tabler/icons-react';
import {
  excludedLabel,
  formatCoverageLine,
  formatCoverageWarning
} from './evidenceFormat';

export function RetrievalCoverage({
  coverage
}: Readonly<{ coverage: RetrievalCoveragePayload }>) {
  const warning = formatCoverageWarning(coverage);
  const asOf = coverage.as_of ? new Date(coverage.as_of).toLocaleString() : '';
  const detailParts: string[] = [];
  if (coverage.date_field) detailParts.push(coverage.date_field);
  if (asOf) detailParts.push(t`as of ${asOf}`);
  detailParts.push(...coverage.filters);
  const detail = detailParts.join(' · ');

  if (warning) {
    return (
      <Alert
        color='yellow'
        variant='light'
        p='xs'
        role='alert'
        icon={<IconAlertTriangle size={14} />}
        data-testid='coverage-incomplete'
      >
        <Text size='xs'>{warning}</Text>
        {detail && (
          <Text size='xs' c='dimmed'>
            {detail}
          </Text>
        )}
      </Alert>
    );
  }

  return (
    <Group
      gap={6}
      wrap='nowrap'
      aria-live='polite'
      data-testid='retrieval-coverage'
    >
      <IconCheck size={12} aria-hidden />
      <Text size='xs' c='dimmed'>
        {formatCoverageLine(coverage)}
        {detail ? ` · ${detail}` : ''}
        {(coverage.excluded_null_date_count ?? 0) > 0
          ? ` · ${excludedLabel(coverage.excluded_null_date_count ?? 0, coverage.date_field)}`
          : ''}
      </Text>
    </Group>
  );
}
