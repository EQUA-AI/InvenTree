import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Badge,
  Button,
  Group,
  Modal,
  Stack,
  Table,
  Text,
  Textarea
} from '@mantine/core';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import { api } from '../../../../App';
import { WorkOrderCreateModal } from '../../../maintenance/components/WorkOrderCreateModal';
import { type Display, conditionText } from './MimicReadings';
import { reasonLabel, valueText } from './format';
import type { MimicAlarm } from './types';

/**
 * The alarms a station's readings raise, and what can be done about them here.
 *
 * A reading outside its limits and an alarm are two different things. The
 * reading is what the page shows; the alarm is what the detector raised from
 * it, once the vote it needs agreed, and it is the alarm that is acknowledged.
 * A row carrying no alarm yet says so rather than offering a button that
 * would have nothing to act on.
 *
 * Acknowledging records that somebody has seen the condition. It does not
 * resolve it - only the reading coming back inside its limits does that - and
 * the note is the one thing the acknowledgement carries, so it is asked for.
 */
export function AlarmTable({
  alarms,
  display = valueText,
  onAcknowledged
}: Readonly<{
  alarms: MimicAlarm[];
  display?: Display;
  /** Called once an acknowledgement is stored, so the page re-reads. */
  onAcknowledged?: () => void;
}>) {
  const [acknowledging, setAcknowledging] = useState<MimicAlarm | null>(null);
  const [repairing, setRepairing] = useState<MimicAlarm | null>(null);

  return (
    <>
      <Table.ScrollContainer minWidth={1040}>
        <Table striped>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t`Signal`}</Table.Th>
              <Table.Th>{t`Value`}</Table.Th>
              <Table.Th>{t`Observed at`}</Table.Th>
              <Table.Th>{t`Condition`}</Table.Th>
              <Table.Th w={170}>{t`Alarm`}</Table.Th>
              <Table.Th w={320} />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {alarms.map((alarm) => (
              <Table.Tr
                key={`${alarm.machine ?? ''}:${alarm.pointer}`}
                data-point={alarm.pointer}
                data-anomaly={alarm.anomaly ?? undefined}
              >
                <Table.Td>
                  {alarm.label}
                  <Text size='xs' c='dimmed'>
                    {alarm.pointer}
                  </Text>
                </Table.Td>
                <Table.Td>
                  {display(alarm)}
                  {alarm.reason && (
                    <Text size='xs'>{reasonLabel(alarm.reason)}</Text>
                  )}
                </Table.Td>
                <Table.Td style={{ whiteSpace: 'nowrap' }}>
                  {alarm.observed_at
                    ? new Date(alarm.observed_at).toLocaleString()
                    : t`No reading`}
                </Table.Td>
                <Table.Td>{conditionText(alarm)}</Table.Td>
                <Table.Td>
                  <AlarmStatus alarm={alarm} />
                </Table.Td>
                <Table.Td style={{ whiteSpace: 'nowrap' }}>
                  <Group
                    gap='sm'
                    wrap='nowrap'
                    justify='flex-end'
                    preventGrowOverflow={false}
                  >
                    {alarm.anomaly !== null && alarm.machine && (
                      <Anchor
                        component={Link}
                        to={`/machines/machine/${alarm.machine}/health`}
                        size='sm'
                      >
                        {t`Open in Health`}
                      </Anchor>
                    )}
                    {alarm.anomaly !== null &&
                      alarm.anomaly_status === 'open' && (
                        <Button
                          size='compact-sm'
                          variant='light'
                          onClick={() => setAcknowledging(alarm)}
                        >
                          {t`Acknowledge`}
                        </Button>
                      )}
                    {alarm.anomaly !== null && alarm.machine && (
                      <Button
                        size='compact-sm'
                        variant='light'
                        color='orange'
                        onClick={() => setRepairing(alarm)}
                      >
                        {t`Create repair`}
                      </Button>
                    )}
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      <AcknowledgeModal
        alarm={acknowledging}
        onClose={() => setAcknowledging(null)}
        onAcknowledged={() => {
          setAcknowledging(null);
          onAcknowledged?.();
        }}
      />
      {/* The same intake the Health tab offers, from the row the alarm is
          seen on: the work order is raised against the anomaly, so the two
          stay linked. Planned, not started. */}
      {repairing && repairing.anomaly !== null && repairing.machine && (
        <WorkOrderCreateModal
          opened
          onClose={() => setRepairing(null)}
          machineId={repairing.machine}
          origin='anomaly'
          anomalyId={repairing.anomaly}
          initialTitle={`${repairing.label} outside configured limits`}
          initialFaultSummary={`${repairing.label} read ${valueText(repairing)}`}
          initialCriticality={
            repairing.severity === 'critical' ? 'critical' : 'high'
          }
          onCreated={() => {
            setRepairing(null);
            onAcknowledged?.();
          }}
        />
      )}
    </>
  );
}

/** Whether the detector has raised this reading into an alarm, and its state. */
function AlarmStatus({ alarm }: Readonly<{ alarm: MimicAlarm }>) {
  if (alarm.anomaly === null) {
    // Outside its limits on this reading, but not an alarm until the detector
    // has seen enough to raise one: the next evaluation, or a second detector
    // in its vote group agreeing.
    return (
      <Text size='sm' c='dimmed'>
        {t`Not raised yet`}
      </Text>
    );
  }
  const severity =
    alarm.severity === 'critical'
      ? t`critical`
      : alarm.severity === 'warning'
        ? t`warning`
        : alarm.severity;
  return alarm.anomaly_status === 'acknowledged' ? (
    <Badge variant='light' color='gray'>
      {t`Acknowledged`} · {severity}
    </Badge>
  ) : (
    <Badge variant='light' color='red'>
      {t`Open`} · {severity}
    </Badge>
  );
}

function AcknowledgeModal({
  alarm,
  onClose,
  onAcknowledged
}: Readonly<{
  alarm: MimicAlarm | null;
  onClose: () => void;
  onAcknowledged: () => void;
}>) {
  const [note, setNote] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const close = () => {
    setNote('');
    setError(null);
    onClose();
  };

  const submit = async () => {
    if (!alarm || alarm.anomaly === null || !alarm.machine) return;
    setBusy(true);
    setError(null);
    try {
      await api.post(
        apiUrl(ApiEndpoints.machine_health_anomaly_acknowledge, alarm.machine, {
          anomalyId: alarm.anomaly
        }),
        { note }
      );
      setNote('');
      onAcknowledged();
    } catch (failure: any) {
      // The server's own words where it gave any: a permission refused, an
      // alarm that closed while the note was being written.
      setError(
        failure?.response?.data?.detail ??
          failure?.response?.data?.error ??
          t`The acknowledgement was not stored.`
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      opened={alarm !== null}
      onClose={close}
      title={t`Acknowledge alarm`}
      centered
    >
      {alarm && (
        <Stack gap='sm'>
          <Text size='sm'>
            {alarm.label}: {valueText(alarm)}
          </Text>
          <Text size='xs' c='dimmed'>
            {t`Acknowledging records that you have seen this condition. It closes when the reading returns inside its limits, not before; acknowledging does not make a repair ready.`}
          </Text>
          <Textarea
            label={t`Note`}
            placeholder={t`What was seen, and what is being done`}
            value={note}
            onChange={(event) => setNote(event.currentTarget.value)}
            autosize
            minRows={3}
            maxLength={2000}
            data-autofocus
          />
          {error && (
            <Alert color='red' variant='light'>
              {error}
            </Alert>
          )}
          <Group justify='flex-end'>
            <Button variant='subtle' onClick={close} disabled={busy}>
              {t`Cancel`}
            </Button>
            <Button onClick={submit} loading={busy}>
              {t`Acknowledge`}
            </Button>
          </Group>
        </Stack>
      )}
    </Modal>
  );
}
