import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Badge,
  Group,
  Loader,
  Select,
  Stack,
  Table,
  Text
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';

import { StylishText } from '@lib/components/StylishText';
import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import { navigateToLink } from '@lib/functions/Navigation';
import { useApi } from '../../../contexts/ApiContext';
import { useRiskScope } from '../../../hooks/UseRiskScope';
import {
  LocalDateTime,
  type RiskFinding,
  SeverityIndicator,
  formatAge,
  parseRiskFindingListResponse
} from '../../riskradar/RiskRadarCommon';

const WIDGET_LIMIT = 8;

/**
 * Dashboard widget showing the top ranked risk findings for the selected
 * scope. Ranking is server-side: results are rendered in response order.
 */
export default function RiskRadarWidget() {
  const api = useApi();
  const navigate = useNavigate();
  const {
    scopes,
    scope,
    authorizationFingerprint,
    setScope,
    unavailable,
    isLoading
  } = useRiskScope();

  const findingsQuery = useQuery({
    queryKey: ['risk-findings', scope, authorizationFingerprint, 'widget'],
    enabled: !!scope && !!authorizationFingerprint,
    retry: false,
    queryFn: () =>
      api
        .get(apiUrl(ApiEndpoints.risk_finding_list), {
          params: {
            scope: scope,
            limit: WIDGET_LIMIT,
            offset: 0
          }
        })
        .then((response) => parseRiskFindingListResponse(response.data, scope))
  });

  if (isLoading || (!!scope && findingsQuery.isLoading)) {
    return <Loader size='sm' />;
  }

  if (unavailable || findingsQuery.isError) {
    return (
      <Stack gap='xs'>
        <StylishText size='md'>{t`Risk Radar`}</StylishText>
        <Text size='sm' c='dimmed'>
          {t`Risk radar is unavailable, or you have no authorized scopes.`}
        </Text>
      </Stack>
    );
  }

  const findings: RiskFinding[] = findingsQuery.data?.results ?? [];
  const degradedSources = (findingsQuery.data?.source_freshness ?? [])
    .filter((source) => source.degraded)
    .map((source) => source.source);

  return (
    <Stack gap='xs'>
      <Group justify='space-between' wrap='nowrap'>
        <StylishText size='md'>{t`Risk Radar`}</StylishText>
        <Select
          size='xs'
          aria-label='risk-radar-scope-select'
          data={scopes}
          value={scope}
          onChange={(value) => {
            if (value) {
              setScope(value);
            }
          }}
          allowDeselect={false}
        />
      </Group>
      {findings.length === 0 && degradedSources.length > 0 ? (
        <Alert color='orange' title={t`Risk data degraded`}>
          <Text size='sm'>
            {t`Unavailable sources`}: {degradedSources.join(', ')}
          </Text>
        </Alert>
      ) : findings.length === 0 ? (
        <Alert color='green' title={t`No open findings`}>
          <Text size='sm'>{t`There are no risk findings for this scope`}</Text>
        </Alert>
      ) : (
        <Table verticalSpacing={4} horizontalSpacing='xs'>
          <Table.Tbody>
            {findings.map((finding) => (
              <Table.Tr key={finding.pk}>
                <Table.Td>
                  <SeverityIndicator severity={finding.severity} />
                </Table.Td>
                <Table.Td>
                  <Text size='sm' lineClamp={1}>
                    {finding.title}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Text size='sm' c='dimmed'>
                    {formatAge(finding.age_hours)}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Badge variant='light' color='gray'>
                    {finding.category}
                  </Badge>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
      <Group justify='space-between' wrap='nowrap'>
        <Stack gap={2}>
          <Text size='xs' c='dimmed'>
            {t`Data as of`} <LocalDateTime value={findingsQuery.data?.as_of} />
          </Text>
          {degradedSources.length > 0 && (
            <Text size='xs' c='orange'>
              {t`Degraded`}: {degradedSources.join(', ')}
            </Text>
          )}
        </Stack>
        <Anchor
          href='#'
          size='xs'
          onClick={(event: any) =>
            navigateToLink('/command-center/', navigate, event)
          }
        >
          {t`View all`}
        </Anchor>
      </Group>
    </Stack>
  );
}
