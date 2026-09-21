import { ModelType } from '@lib/enums/ModelType';
import { t } from '@lingui/core/macro';

/** Versioned definitions; unsupported cards never issue a pretend count query. */
export function managementMetrics() {
  return [
    {
      id: 'material-shortages',
      model: ModelType.part,
      title: t`Material shortages`,
      unit: t`part/site pairs`,
      readiness: 'aggregation',
      description: t`Needs a validated demand and allocation calculation. Minimum-stock alerts are a different metric.`
    },
    {
      id: 'material-blocked-builds',
      model: ModelType.build,
      title: t`Material-blocked builds`,
      unit: t`builds`,
      readiness: 'aggregation',
      description: t`Needs distinct open builds with material shortages under the same allocation policy.`
    },
    {
      id: 'overdue-purchase',
      model: ModelType.purchaseorder,
      title: t`Overdue purchase orders`,
      unit: t`orders`,
      readiness: 'supported',
      description: t`Open orders with unreceived quantities due before today. Uses each line's expected date, falling back to the order date. Each order counts once.`
    },
    {
      id: 'overdue-sales',
      model: ModelType.salesorder,
      title: t`Overdue sales commitments`,
      unit: t`orders`,
      readiness: 'integration',
      description: t`Requires a verified ship-commitment field. The current order target is a delivery date.`
    }
  ] as const;
}

export type ManagementMetric = ReturnType<typeof managementMetrics>[number];
