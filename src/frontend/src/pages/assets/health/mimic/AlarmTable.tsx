import { t } from '@lingui/core/macro';
import {
  ActionIcon,
  Badge,
  Button,
  Group,
  Table,
  Text,
  Tooltip
} from '@mantine/core';
import { IconExternalLink } from '@tabler/icons-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { WorkOrderCreateModal } from '../../../maintenance/components/WorkOrderCreateModal';
import { type AlarmAction, AlarmNoteModal } from './AlarmNoteModal';
import { type Display, conditionText } from './MimicReadings';
import { reasonLabel, valueText } from './format';
import type { MimicAlarm } from './types';

/**
 * The alarms a station's readings raise, and what can be done about them here.
 *
 * A reading outside its limits and an alarm are two different things. The
 * reading is what the page shows; the alarm is what the detector raised from
 * it, once the vote it needs agreed, and it is the alarm that is acted on.
 * A row carrying no alarm yet says so rather than offering buttons that would
 * have nothing to act on.
 *
 * Three things can be done: acknowledge it (somebody has seen it), raise a
 * repair against it, or dismiss it as wrong, with a reason. None of them
 * clears it - only the reading coming back inside its limits does that.
 */
export function AlarmTable({
  alarms,
  display = valueText,
  onAcknowledged
}: Readonly<{
  alarms: MimicAlarm[];
  display?: Display;
  /** Called once an alarm's state has changed, so the page re-reads. */
  onAcknowledged?: () => void;
}>) {
  const [noting, setNoting] = useState<{
    action: AlarmAction;
    alarm: MimicAlarm;
  } | null>(null);
  const [repairing, setRepairing] = useState<MimicAlarm | null>(null);

  return (
    <>
      <Table.ScrollContainer minWidth={960}>
        <Table striped>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t`Signal`}</Table.Th>
              <Table.Th>{t`Value`}</Table.Th>
              <Table.Th>{t`Observed at`}</Table.Th>
              <Table.Th>{t`Condition`}</Table.Th>
              <Table.Th>{t`Alarm`}</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {alarms.map((alarm) => {
              const raised = alarm.anomaly !== null && !!alarm.machine;
              const dismissed = alarm.anomaly_status === 'suppressed';
              return (
                <Table.Tr
                  key={`${alarm.machine ?? ''}:${alarm.pointer}`}
                  data-point={alarm.pointer}
                  data-anomaly={alarm.anomaly ?? undefined}
                >
                  <Table.Td>
                    {alarm.label}
                    <Text
                      size='xs'
                      c='dimmed'
                      style={{ wordBreak: 'break-all' }}
                    >
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
                      {raised && (
                        <Tooltip label={t`Open in Health`}>
                          <ActionIcon
                            component={Link}
                            to={`/machines/machine/${alarm.machine}/health`}
                            variant='subtle'
                            aria-label={t`Open in Health`}
                          >
                            <IconExternalLink size={16} />
                          </ActionIcon>
                        </Tooltip>
                      )}
                      {raised && alarm.anomaly_status === 'open' && (
                        <Button
                          size='compact-sm'
                          variant='light'
                          onClick={() =>
                            setNoting({ action: 'acknowledge', alarm })
                          }
                        >
                          {t`Acknowledge`}
                        </Button>
                      )}
                      {raised && !dismissed && (
                        <Button
                          size='compact-sm'
                          variant='light'
                          color='orange'
                          onClick={() => setRepairing(alarm)}
                        >
                          {t`Create repair`}
                        </Button>
                      )}
                      {raised && !dismissed && (
                        <Button
                          size='compact-sm'
                          variant='subtle'
                          color='gray'
                          onClick={() =>
                            setNoting({ action: 'dismiss', alarm })
                          }
                        >
                          {t`Dismiss`}
                        </Button>
                      )}
                    </Group>
                  </Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      <AlarmNoteModal
        action={noting?.action ?? 'acknowledge'}
        target={
          noting && noting.alarm.anomaly !== null && noting.alarm.machine
            ? {
                machine: noting.alarm.machine,
                anomaly: noting.alarm.anomaly,
                summary: `${noting.alarm.label}: ${valueText(noting.alarm)}`
              }
            : null
        }
        onClose={() => setNoting(null)}
        onDone={() => {
          setNoting(null);
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
  if (alarm.anomaly_status === 'suppressed') {
    return (
      <Badge variant='outline' color='gray' miw='max-content'>
        {t`Dismissed`}
      </Badge>
    );
  }
  return alarm.anomaly_status === 'acknowledged' ? (
    <Badge variant='light' color='gray' miw='max-content'>
      {t`Acknowledged`} · {severity}
    </Badge>
  ) : (
    <Badge variant='light' color='red' miw='max-content'>
      {t`Open`} · {severity}
    </Badge>
  );
}
