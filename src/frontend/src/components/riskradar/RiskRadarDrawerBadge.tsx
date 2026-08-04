import { t } from '@lingui/core/macro';
import { Badge, Tooltip } from '@mantine/core';
import { IconRadar2 } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import { useApi } from '../../contexts/ApiContext';
import { useRiskScope } from '../../hooks/UseRiskScope';
import { parseRiskFindingListResponse } from './RiskRadarCommon';

/**
 * Compact open-findings count for the chat drawer.
 *
 * Renders nothing when Risk Radar is off, the viewer has no scope, or the
 * count is zero — the drawer looks exactly as before in every one of those
 * states. Clicking navigates to the Maintenance Risk Radar panel.
 */
export default function RiskRadarDrawerBadge() {
  const api = useApi();
  const navigate = useNavigate();
  const { scope, authorizationFingerprint, unavailable } = useRiskScope();

  const countQuery = useQuery({
    queryKey: ['risk-findings', scope, authorizationFingerprint, 'badge'],
    enabled: !unavailable && !!scope && !!authorizationFingerprint,
    retry: false,
    staleTime: 60_000,
    queryFn: () =>
      api
        .get(apiUrl(ApiEndpoints.risk_finding_list), {
          params: { scope: scope, limit: 1, offset: 0 }
        })
        .then((response) => parseRiskFindingListResponse(response.data, scope))
  });

  const count = countQuery.data?.count ?? 0;
  if (unavailable || countQuery.isError || count <= 0) {
    return null;
  }

  return (
    <Tooltip label={t`Open risk findings - view in Maintenance`}>
      <Badge
        size='sm'
        variant='light'
        color='orange'
        leftSection={<IconRadar2 size={12} />}
        style={{ cursor: 'pointer' }}
        data-testid='risk-radar-drawer-badge'
        onClick={() => navigate('/maintenance/risk-radar/')}
      >
        {count}
      </Badge>
    </Tooltip>
  );
}
