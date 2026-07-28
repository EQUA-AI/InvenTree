/**
 * Renderers for the machine context's scoped-chat tool results.
 *
 * These are deliberately separate from the work-order renderers rather than
 * reused: the shapes only look similar. Feeding machine maintenance rows to the
 * work-order event renderer, for instance, would read every field as undefined
 * and collide every React key on `undefined`.
 *
 * Stored free text arrives wrapped in untrusted-content markers. That fence is
 * an annotation for the model — it tells it to treat operator- and
 * machine-authored text as data, never instructions. A human reading the same
 * envelope has no use for it, so it is stripped here for display only; nothing
 * strips it from what a model is given.
 */

import { t } from '@lingui/core/macro';
import { Badge, Group, Stack, Text } from '@mantine/core';

const UNTRUSTED_BEGIN = '[UNTRUSTED-CONTENT-BEGIN]';
const UNTRUSTED_END = '[UNTRUSTED-CONTENT-END]';

/** Strip the model-facing untrusted-content fence for human display. */
export function unfence(value: unknown): string {
  if (typeof value !== 'string' || value.length === 0) {
    return '';
  }
  if (!value.startsWith(UNTRUSTED_BEGIN)) {
    return value;
  }
  return value
    .slice(UNTRUSTED_BEGIN.length, value.lastIndexOf(UNTRUSTED_END))
    .trim();
}

function Field({
  label,
  value
}: Readonly<{ label: string; value: React.ReactNode }>) {
  return (
    <Group gap={6} wrap='nowrap' align='flex-start'>
      <Text size='xs' c='dimmed' style={{ minWidth: 130 }}>
        {label}
      </Text>
      <Text size='xs' style={{ whiteSpace: 'pre-wrap' }}>
        {value === '' || value == null ? '—' : value}
      </Text>
    </Group>
  );
}

const HEALTH_COLOUR: Record<string, string> = {
  normal: 'teal',
  warning: 'yellow',
  critical: 'red',
  offline: 'gray',
  unknown: 'gray'
};

export function MachineSummaryResult({
  result
}: Readonly<{ result: Record<string, any> }>) {
  const summary: Record<string, any> = result.summary ?? {};
  return (
    <Stack gap={2}>
      <Field label={t`Name`} value={unfence(summary.name)} />
      <Field label={t`Active`} value={summary.active ? t`Yes` : t`No`} />
      <Field label={t`Location`} value={unfence(summary.location)} />
      <Field label={t`Manufacturer`} value={unfence(summary.manufacturer)} />
      <Field label={t`Model`} value={unfence(summary.model)} />
      <Field label={t`Serial Number`} value={unfence(summary.serial)} />
      <Field label={t`Customer`} value={unfence(summary.customer_name)} />
      <Field label={t`Client`} value={unfence(summary.client_name)} />
      <Field label={t`Description`} value={unfence(summary.description)} />
    </Stack>
  );
}

export function MachineHealthResult({
  result
}: Readonly<{ result: Record<string, any> }>) {
  const sources: any[] = result.sources ?? [];
  return (
    <Stack gap={4}>
      <Group gap='xs'>
        <Badge color={HEALTH_COLOUR[result.state] ?? 'gray'} size='sm'>
          {result.state}
        </Badge>
        {result.degraded_data && (
          <Badge color='orange' size='xs' variant='light'>
            {t`Degraded data`}
          </Badge>
        )}
        <Text size='xs' c='dimmed'>
          {result.signal_count} {t`signals`} · {result.stale_signal_count}{' '}
          {t`stale`} · {result.active_anomaly_count} {t`active alarms`}
        </Text>
      </Group>
      {!result.configured && (
        <Text size='xs' c='dimmed'>
          {t`No monitoring source is mapped to this machine.`}
        </Text>
      )}
      {sources.map((source) => (
        <Group key={source.source_id} gap={6} wrap='nowrap'>
          <Badge
            size='xs'
            color={source.healthy ? 'teal' : 'red'}
            variant='light'
          >
            {source.healthy ? t`up` : t`down`}
          </Badge>
          <Text size='xs'>
            {unfence(source.name)} · {source.mapped_tag_count} {t`tags`}
            {source.last_success_at
              ? ` · ${t`last ok`} ${new Date(source.last_success_at).toLocaleString()}`
              : ''}
            {source.last_error_code ? ` · ${source.last_error_code}` : ''}
          </Text>
        </Group>
      ))}
    </Stack>
  );
}

export function MachineSignalsResult({
  result
}: Readonly<{ result: Record<string, any> }>) {
  const signals: any[] = result.signals ?? [];
  if (signals.length === 0) {
    return <Text size='xs'>{t`No signals are mapped to this machine.`}</Text>;
  }
  return (
    <Stack gap={2}>
      {signals.map((signal) => (
        <Group key={signal.binding_id} gap={6} wrap='nowrap'>
          <Badge
            size='xs'
            color={HEALTH_COLOUR[signal.state] ?? 'gray'}
            variant='light'
          >
            {signal.state}
          </Badge>
          <Text size='xs'>
            {unfence(signal.display_name)}:{' '}
            {signal.value == null ? '—' : unfence(String(signal.value))}
            {unfence(signal.unit) ? ` ${unfence(signal.unit)}` : ''}
            {signal.stale ? ` · ${t`stale`}` : ''}
          </Text>
        </Group>
      ))}
    </Stack>
  );
}

export function MachineTrendResult({
  result
}: Readonly<{ result: Record<string, any> }>) {
  if (result.available === false) {
    return (
      <Text size='xs'>
        {t`No history available`}
        {result.detail ? `: ${result.detail}` : ''}
      </Text>
    );
  }
  const unit = unfence(result.unit);
  return (
    <Stack gap={2}>
      <Text size='xs'>
        {unfence(result.display_name)} · {result.sample_count} {t`samples`}
      </Text>
      <Text size='xs'>
        {t`first`} {result.first_value ?? '—'} → {t`last`}{' '}
        {result.last_value ?? '—'} {unit} ({t`min`} {result.min_value ?? '—'},{' '}
        {t`max`} {result.max_value ?? '—'})
      </Text>
    </Stack>
  );
}

export function MachineAnomaliesResult({
  result
}: Readonly<{ result: Record<string, any> }>) {
  const anomalies: any[] = result.anomalies ?? [];
  if (anomalies.length === 0) {
    return <Text size='xs'>{t`No anomalies recorded.`}</Text>;
  }
  return (
    <Stack gap={4}>
      {anomalies.map((anomaly) => (
        <Stack key={anomaly.anomaly_id} gap={2}>
          <Group gap={6} wrap='nowrap'>
            <Badge
              size='xs'
              color={anomaly.severity === 'critical' ? 'red' : 'yellow'}
            >
              {anomaly.severity}
            </Badge>
            <Badge size='xs' variant='light'>
              {anomaly.status}
            </Badge>
            <Text size='xs'>{unfence(anomaly.title)}</Text>
          </Group>
          {anomaly.work_order_reference && (
            <Text size='xs' c='dimmed'>
              {t`Covered by`} {anomaly.work_order_reference}
            </Text>
          )}
          <Text size='xs' c='dimmed'>
            {t`last seen`} {new Date(anomaly.last_observed_at).toLocaleString()}
            {anomaly.resolved_at
              ? ` · ${t`resolved`} ${new Date(anomaly.resolved_at).toLocaleString()}`
              : ''}
          </Text>
        </Stack>
      ))}
    </Stack>
  );
}

export function MachinePartsResult({
  result
}: Readonly<{ result: Record<string, any> }>) {
  const parts: any[] = result.parts ?? [];
  if (parts.length === 0) {
    return (
      <Text size='xs'>{t`No parts are recorded against this machine.`}</Text>
    );
  }
  return (
    <Stack gap={2}>
      {parts.map((part) => (
        <Text size='xs' key={part.part_id}>
          {unfence(part.part_name)}
          {unfence(part.ipn) ? ` (${unfence(part.ipn)})` : ''} × {part.quantity}
        </Text>
      ))}
      {result.truncated && (
        <Text size='xs' c='dimmed'>
          {t`Showing`} {parts.length} / {result.total}
        </Text>
      )}
    </Stack>
  );
}

export function MachineMaintenanceResult({
  result
}: Readonly<{ result: Record<string, any> }>) {
  const records: any[] = result.records ?? [];
  if (records.length === 0) {
    return <Text size='xs'>{t`No maintenance has been recorded.`}</Text>;
  }
  return (
    <Stack gap={2}>
      {records.map((record, index) => (
        <Text
          // Maintenance rows carry no id of their own in this projection, so
          // the date alone is not unique — two services can share a day.
          key={`${record.date}-${index}`}
          size='xs'
        >
          {record.date} · {unfence(record.summary)}
          {unfence(record.performed_by)
            ? ` — ${unfence(record.performed_by)}`
            : ''}
          {record.work_order_reference
            ? ` (${record.work_order_reference})`
            : ''}
        </Text>
      ))}
      {result.truncated && (
        <Text size='xs' c='dimmed'>
          {t`Showing`} {records.length} / {result.total}
        </Text>
      )}
    </Stack>
  );
}

export function MachineAttachmentsResult({
  result
}: Readonly<{ result: Record<string, any> }>) {
  const attachments: any[] = result.attachments ?? [];
  if (attachments.length === 0) {
    return <Text size='xs'>{t`No documents are attached.`}</Text>;
  }
  return (
    <Stack gap={2}>
      {attachments.map((item, index) => (
        <Group key={`${item.name ?? 'link'}-${index}`} gap={6} wrap='nowrap'>
          <Badge size='xs' variant='light'>
            {item.kind}
          </Badge>
          <Text size='xs'>
            {unfence(item.name) || t`(unnamed)`}
            {unfence(item.comment) ? ` — ${unfence(item.comment)}` : ''}
          </Text>
        </Group>
      ))}
      {result.truncated && (
        <Text size='xs' c='dimmed'>
          {t`Showing`} {attachments.length} / {result.total}
        </Text>
      )}
    </Stack>
  );
}
