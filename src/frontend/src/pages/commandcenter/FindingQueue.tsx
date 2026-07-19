import { t } from '@lingui/core/macro';
import {
  Alert,
  Badge,
  Group,
  Loader,
  Pagination,
  Paper,
  Select,
  Stack,
  Table,
  Text
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';

import { StylishText } from '@lib/components/StylishText';
import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import {
  type RiskFinding,
  SeverityIndicator,
  formatAge,
  parseRiskFindingListResponse
} from '../../components/riskradar/RiskRadarCommon';
import { useApi } from '../../contexts/ApiContext';

const PAGE_SIZE = 25;

const SEVERITY_OPTIONS = ['critical', 'high', 'medium', 'low'];
const STATE_OPTIONS = [
  'open',
  'acknowledged',
  'snoozed',
  'resolved',
  'dismissed'
];

/**
 * Filterable, paginated table of risk findings for a scope.
 *
 * Findings are ranked server-side: rows are rendered strictly in response
 * order and are never re-sorted client-side. Filters are applied as query
 * parameters so the server performs the filtering.
 */
export default function FindingQueue({
  scope,
  authorizationFingerprint,
  categories,
  onSelectFinding
}: Readonly<{
  scope: string;
  authorizationFingerprint: string;
  categories?: string[];
  onSelectFinding: (findingId: number) => void;
}>) {
  const api = useApi();

  const [category, setCategory] = useState<string | null>(null);
  const [severity, setSeverity] = useState<string | null>(null);
  const [state, setState] = useState<string | null>(null);
  const [page, setPage] = useState<number>(1);

  // A scope switch must restart pagination: a stale offset against a
  // smaller scope would render a silently empty table.
  useEffect(() => {
    setPage(1);
  }, [scope]);

  const queryParams = useMemo(() => {
    const params: Record<string, any> = {
      scope: scope,
      limit: PAGE_SIZE,
      offset: (page - 1) * PAGE_SIZE
    };
    if (category) {
      params.category = category;
    }
    if (severity) {
      params.severity = severity;
    }
    if (state) {
      params.state = state;
    }
    return params;
  }, [scope, category, severity, state, page]);

  const findingsQuery = useQuery({
    queryKey: [
      'risk-findings',
      scope,
      authorizationFingerprint,
      { category, severity, state },
      page
    ],
    enabled: !!scope && !!authorizationFingerprint,
    retry: false,
    queryFn: () =>
      api
        .get(apiUrl(ApiEndpoints.risk_finding_list), { params: queryParams })
        .then((response) => parseRiskFindingListResponse(response.data, scope))
  });

  // Clamp a stranded offset: if invalidation shrinks the total below the
  // current page, snap back to the first page instead of showing an empty
  // table with the paginator hidden.
  useEffect(() => {
    const data = findingsQuery.data;
    if (data && page > 1 && data.results.length === 0 && data.count > 0) {
      setPage(1);
    }
  }, [findingsQuery.data, page]);

  const categoryOptions: string[] = useMemo(() => {
    if (categories && categories.length > 0) {
      return categories;
    }
    // Fall back to the categories present in the current result page
    const found = new Set<string>();
    for (const finding of findingsQuery.data?.results ?? []) {
      found.add(finding.category);
    }
    return Array.from(found);
  }, [categories, findingsQuery.data]);

  const count: number = findingsQuery.data?.count ?? 0;
  const totalPages: number = Math.max(1, Math.ceil(count / PAGE_SIZE));

  const rowKeyHandler = (event: React.KeyboardEvent, findingId: number) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      onSelectFinding(findingId);
    }
  };

  return (
    <Paper withBorder p='sm'>
      <Stack gap='sm'>
        <Group justify='space-between' wrap='nowrap'>
          <StylishText size='lg'>{t`Finding Queue`}</StylishText>
          <Group gap='xs' wrap='nowrap'>
            <Select
              size='xs'
              aria-label='finding-filter-category'
              placeholder={t`Category`}
              data={categoryOptions}
              value={category}
              onChange={(value) => {
                setCategory(value);
                setPage(1);
              }}
              clearable
            />
            <Select
              size='xs'
              aria-label='finding-filter-severity'
              placeholder={t`Severity`}
              data={SEVERITY_OPTIONS}
              value={severity}
              onChange={(value) => {
                setSeverity(value);
                setPage(1);
              }}
              clearable
            />
            <Select
              size='xs'
              aria-label='finding-filter-state'
              placeholder={t`State`}
              data={STATE_OPTIONS}
              value={state}
              onChange={(value) => {
                setState(value);
                setPage(1);
              }}
              clearable
            />
          </Group>
        </Group>
        {findingsQuery.isLoading && <Loader size='sm' />}
        {findingsQuery.isError && (
          <Alert color='gray' title={t`Findings unavailable`}>
            <Text size='sm'>{t`Risk findings could not be loaded for this scope`}</Text>
          </Alert>
        )}
        {findingsQuery.isSuccess && (
          <>
            <Table highlightOnHover verticalSpacing={6}>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>{t`Severity`}</Table.Th>
                  <Table.Th>{t`Title`}</Table.Th>
                  <Table.Th>{t`Category`}</Table.Th>
                  <Table.Th>{t`Rule`}</Table.Th>
                  <Table.Th>{t`Age`}</Table.Th>
                  <Table.Th>{t`Due`}</Table.Th>
                  <Table.Th>{t`State`}</Table.Th>
                  <Table.Th>{t`Owner`}</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {(findingsQuery.data?.results ?? []).map(
                  (finding: RiskFinding) => (
                    <Table.Tr
                      key={finding.pk}
                      tabIndex={0}
                      data-testid={`finding-row-${finding.pk}`}
                      aria-label={`finding-row-${finding.pk}`}
                      style={{ cursor: 'pointer' }}
                      onClick={() => onSelectFinding(finding.pk)}
                      onKeyDown={(event) => rowKeyHandler(event, finding.pk)}
                    >
                      <Table.Td>
                        <SeverityIndicator severity={finding.severity} />
                      </Table.Td>
                      <Table.Td>
                        <Text size='sm' lineClamp={1}>
                          {finding.title}
                        </Text>
                      </Table.Td>
                      <Table.Td>
                        <Badge variant='light' color='gray'>
                          {finding.category}
                        </Badge>
                      </Table.Td>
                      <Table.Td>
                        <Text size='sm'>{finding.rule_code}</Text>
                      </Table.Td>
                      <Table.Td>
                        <Text size='sm'>{formatAge(finding.age_hours)}</Text>
                      </Table.Td>
                      <Table.Td>
                        {finding.due_breached ? (
                          <Badge color='red' variant='light'>
                            {t`Due breached`}
                          </Badge>
                        ) : (
                          <Text size='sm' c='dimmed'>
                            -
                          </Text>
                        )}
                      </Table.Td>
                      <Table.Td>
                        <Badge variant='light'>{finding.state}</Badge>
                      </Table.Td>
                      <Table.Td>
                        <Text size='sm'>{finding.owner_username ?? '-'}</Text>
                      </Table.Td>
                    </Table.Tr>
                  )
                )}
              </Table.Tbody>
            </Table>
            {count === 0 && (
              <Text size='sm' c='dimmed'>
                {t`No findings match the current filters`}
              </Text>
            )}
            {totalPages > 1 && (
              <Pagination
                total={totalPages}
                value={page}
                onChange={setPage}
                size='sm'
              />
            )}
          </>
        )}
      </Stack>
    </Paper>
  );
}
