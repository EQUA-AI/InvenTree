import { t } from '@lingui/core/macro';
import { Accordion, Badge, Table, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import {
  LocalDateTime,
  type RiskRuleHealthRow,
  parseRiskRuleHealthResponse
} from '../../components/riskradar/RiskRadarCommon';
import { useApi } from '../../contexts/ApiContext';

/**
 * Collapsible panel listing risk rule health for a scope.
 *
 * The backing endpoint requires an extra permission; the panel is hidden
 * entirely when the query errors (e.g. HTTP 403/404).
 */
export default function RuleHealthPanel({
  scope,
  authorizationFingerprint
}: Readonly<{ scope: string; authorizationFingerprint: string }>) {
  const api = useApi();

  const healthQuery = useQuery({
    queryKey: ['risk-rule-health', scope, authorizationFingerprint],
    enabled: !!scope && !!authorizationFingerprint,
    retry: false,
    queryFn: () =>
      api
        .get(apiUrl(ApiEndpoints.risk_rule_health), {
          params: { scope: scope }
        })
        .then((response) => parseRiskRuleHealthResponse(response.data, scope))
  });

  // Hidden entirely unless the viewer is authorized and data is available
  if (!scope || !healthQuery.isSuccess || !healthQuery.data) {
    return null;
  }

  const rules: RiskRuleHealthRow[] = healthQuery.data.rules ?? [];

  return (
    <Accordion variant='contained'>
      <Accordion.Item value='rule-health'>
        <Accordion.Control>{t`Rule Health`}</Accordion.Control>
        <Accordion.Panel>
          <Table verticalSpacing={4}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t`Rule`}</Table.Th>
                <Table.Th>{t`Enabled`}</Table.Th>
                <Table.Th>{t`Gate`}</Table.Th>
                <Table.Th>{t`Last Complete`}</Table.Th>
                <Table.Th>{t`Last Status`}</Table.Th>
                <Table.Th>{t`Degraded`}</Table.Th>
                <Table.Th>{t`Source Disabled`}</Table.Th>
                <Table.Th>{t`Failure Streak`}</Table.Th>
                <Table.Th>{t`Dormant Reason`}</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rules.map((rule) => (
                <Table.Tr key={rule.rule}>
                  <Table.Td>
                    <Text size='sm'>{rule.rule}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size='sm'>{rule.enabled ? t`Yes` : t`No`}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size='sm'>{rule.gate ?? '-'}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size='sm'>
                      <LocalDateTime value={rule.last_complete} />
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size='sm'>{rule.last_status ?? '-'}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size='sm'>{rule.degraded ? t`Yes` : t`No`}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size='sm'>
                      {rule.source_disabled ? t`Yes` : t`No`}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size='sm'>{rule.failure_streak}</Text>
                  </Table.Td>
                  <Table.Td>
                    {rule.dormant ? (
                      <Badge color='gray' variant='light'>
                        {rule.dormant_reason ?? t`Dormant`}
                      </Badge>
                    ) : (
                      <Text size='sm' c='dimmed'>
                        -
                      </Text>
                    )}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Accordion.Panel>
      </Accordion.Item>
    </Accordion>
  );
}
