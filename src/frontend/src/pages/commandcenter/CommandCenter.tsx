import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Badge,
  Group,
  Loader,
  Paper,
  Select,
  SimpleGrid,
  Stack,
  Text
} from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useState } from 'react';

import { StylishText } from '@lib/components/StylishText';
import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import { PageDetail } from '../../components/nav/PageDetail';
import {
  type CommandCenterSummary,
  FlowTiles,
  LocalDateTime,
  type RiskSeverity,
  type RuleFreshness,
  parseCommandCenterSummary,
  severityColor,
  severityIcon,
  severityLabel
} from '../../components/riskradar/RiskRadarCommon';
import { useApi } from '../../contexts/ApiContext';
import { useRiskScope } from '../../hooks/UseRiskScope';
import FindingDrawer from './FindingDrawer';
import FindingQueue from './FindingQueue';
import RuleHealthPanel from './RuleHealthPanel';

const SEVERITY_ORDER: RiskSeverity[] = ['critical', 'high', 'medium', 'low'];

function freshnessChipLabel(row: RuleFreshness): string | null {
  if (row.source_disabled) {
    return `${row.rule}: ${t`source disabled`}`;
  }
  if (row.degraded) {
    return `${row.rule}: ${t`degraded`}`;
  }
  if (row.dormant) {
    return `${row.rule}: ${t`dormant`}`;
  }
  return null;
}

function FreshnessBar({
  summary
}: Readonly<{ summary: CommandCenterSummary }>) {
  const chips = summary.freshness
    .map((row) => ({ rule: row.rule, label: freshnessChipLabel(row) }))
    .filter((chip) => chip.label != null);
  const degradedSources = summary.source_freshness.filter(
    (source) => source.degraded
  );

  return (
    <Paper withBorder p='xs'>
      <Stack gap='xs'>
        <Group gap='xs' wrap='wrap'>
          <Text size='sm' c='dimmed'>
            {t`Data as of`} <LocalDateTime value={summary.as_of} />
          </Text>
          {chips.map((chip) => (
            <Badge key={chip.rule} color='orange' variant='light'>
              {chip.label}
            </Badge>
          ))}
          {degradedSources.map((source) => (
            <Badge key={source.source} color='orange' variant='light'>
              {source.source}: {t`degraded`}
            </Badge>
          ))}
        </Group>
        {summary.stale && (
          <Alert
            color='orange'
            icon={<IconAlertTriangle size={16} />}
            title={t`Stale data`}
            data-testid='stale-data-alert'
          >
            {t`Data is stale — recommendations withheld`}
          </Alert>
        )}
      </Stack>
    </Paper>
  );
}

function HeadlineTiles({
  headline
}: Readonly<{ headline: Record<RiskSeverity, number> }>) {
  return (
    <SimpleGrid cols={{ base: 2, md: 4 }}>
      {SEVERITY_ORDER.map((severity) => (
        <Paper
          key={severity}
          withBorder
          p='sm'
          data-testid={`headline-${severity}`}
        >
          <Group gap='xs' wrap='nowrap' c={severityColor(severity)}>
            {severityIcon(severity, 20)}
            <Stack gap={0}>
              <Text size='xs' c='dimmed'>
                {severityLabel(severity)}
              </Text>
              <Text size='xl' fw={700}>
                {headline[severity]}
              </Text>
            </Stack>
          </Group>
        </Paper>
      ))}
    </SimpleGrid>
  );
}

function AgingSection({
  aging
}: Readonly<{ aging: CommandCenterSummary['aging'] }>) {
  const approvals = aging?.approvals_in_review;
  const shortages = aging?.shortages_open;

  return (
    <Group gap='xl' wrap='wrap'>
      <Stack gap={2}>
        <Text size='xs' c='dimmed'>
          {t`Approvals in review`}
        </Text>
        {!approvals ? (
          <Text size='sm' c='dimmed' fs='italic'>
            {t`Data unavailable`}
          </Text>
        ) : 'source_disabled' in approvals ? (
          <Text size='sm' c='dimmed' fs='italic'>
            {t`Source disabled`}
          </Text>
        ) : (
          <Text size='sm'>
            {t`Median`} {approvals.p50_hours}h / {t`Max`} {approvals.max_hours}h
          </Text>
        )}
      </Stack>
      <Stack gap={2}>
        <Text size='xs' c='dimmed'>
          {t`Open shortages`}
        </Text>
        {!shortages ? (
          <Text size='sm' c='dimmed' fs='italic'>
            {t`Data unavailable`}
          </Text>
        ) : 'source_disabled' in shortages ? (
          <Text size='sm' c='dimmed' fs='italic'>
            {t`Source disabled`}
          </Text>
        ) : (
          <Text size='sm'>
            {t`Median`} {shortages.p50_days}d / {t`Max`} {shortages.max_days}d
          </Text>
        )}
      </Stack>
    </Group>
  );
}

/**
 * Command Center page: freshness, headline severity counts, flow lanes,
 * aging metrics, return-to-service blockers and the finding queue for a
 * single authorized scope.
 */
export default function CommandCenter() {
  const api = useApi();
  const {
    scopes,
    scope,
    authorizationFingerprint,
    setScope,
    unavailable,
    isLoading
  } = useRiskScope();

  const [openFindingId, setOpenFindingId] = useState<number | null>(null);
  const [drawerOpened, setDrawerOpened] = useState<boolean>(false);

  const openFinding = useCallback((findingId: number) => {
    setOpenFindingId(findingId);
    setDrawerOpened(true);
  }, []);

  useEffect(() => {
    setDrawerOpened(false);
    setOpenFindingId(null);
  }, [scope]);

  const summaryQuery = useQuery({
    queryKey: ['command-center-summary', scope, authorizationFingerprint],
    enabled: !!scope && !!authorizationFingerprint,
    retry: false,
    queryFn: () =>
      api
        .get(apiUrl(ApiEndpoints.command_center_summary), {
          params: { scope: scope }
        })
        .then((response) => parseCommandCenterSummary(response.data, scope))
  });

  const summary = summaryQuery.data;

  const categories: string[] = useMemo(
    () => Object.keys(summary?.by_category ?? {}),
    [summary]
  );

  if (isLoading) {
    return <Loader mt='xl' />;
  }

  if (unavailable) {
    return (
      <Alert
        color='red'
        icon={<IconAlertTriangle size={16} />}
        title={t`Command Center unavailable`}
        m='md'
      >
        {t`The Command Center is disabled, or you have no authorized scopes.`}
      </Alert>
    );
  }

  return (
    <Stack gap='md'>
      <PageDetail
        title={t`Command Center`}
        actions={[
          <Select
            key='scope-select'
            size='xs'
            aria-label='command-center-scope-select'
            data={scopes}
            value={scope}
            onChange={(value) => {
              if (value) {
                setScope(value);
              }
            }}
            allowDeselect={false}
          />
        ]}
      />
      <Stack gap='md' px='md' pb='md'>
        {summaryQuery.isLoading && <Loader />}
        {summaryQuery.isError && (
          <Alert
            color='red'
            icon={<IconAlertTriangle size={16} />}
            title={t`Summary unavailable`}
          >
            {t`The command center summary could not be loaded for this scope.`}
          </Alert>
        )}
        {summary && (
          <>
            <FreshnessBar summary={summary} />
            <HeadlineTiles headline={summary.headline} />
            <Group gap='xs' wrap='wrap'>
              {Object.entries(summary.by_category ?? {}).map(([cat, total]) => (
                <Badge key={cat} variant='light' color='gray'>
                  {cat}: {total}
                </Badge>
              ))}
            </Group>
            <Paper withBorder p='sm'>
              <Stack gap='sm'>
                <StylishText size='lg'>{t`Flow`}</StylishText>
                <Group gap='xl' wrap='wrap' align='flex-start'>
                  <FlowTiles
                    title={t`Repair Packets`}
                    counts={summary.flow?.packets}
                  />
                  <FlowTiles
                    title={t`Work Orders`}
                    counts={summary.flow?.work_orders}
                  />
                </Group>
                <AgingSection aging={summary.aging} />
              </Stack>
            </Paper>
            {(summary.return_to_service?.length ?? 0) > 0 && (
              <Paper withBorder p='sm'>
                <Stack gap='xs'>
                  <StylishText size='lg'>{t`Return to Service`}</StylishText>
                  {summary.return_to_service.map((entry) => (
                    <Group key={entry.finding_id} gap='xs' wrap='wrap'>
                      <Anchor
                        component='button'
                        type='button'
                        size='sm'
                        onClick={() => openFinding(entry.finding_id)}
                        data-testid={`rts-finding-${entry.finding_id}`}
                      >
                        {entry.packet}
                      </Anchor>
                      <Badge variant='light' color='gray'>
                        {entry.code}
                      </Badge>
                      <Text size='sm' c='dimmed'>
                        {entry.reason_snapshot}
                      </Text>
                    </Group>
                  ))}
                </Stack>
              </Paper>
            )}
          </>
        )}
        {!!scope && (
          <FindingQueue
            scope={scope}
            authorizationFingerprint={authorizationFingerprint}
            categories={categories}
            onSelectFinding={openFinding}
          />
        )}
        {!!scope && (
          <RuleHealthPanel
            scope={scope}
            authorizationFingerprint={authorizationFingerprint}
          />
        )}
      </Stack>
      <FindingDrawer
        findingId={openFindingId}
        scope={scope}
        authorizationFingerprint={authorizationFingerprint}
        opened={drawerOpened}
        onClose={() => setDrawerOpened(false)}
      />
    </Stack>
  );
}
