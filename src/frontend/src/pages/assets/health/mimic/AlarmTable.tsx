import { t } from '@lingui/core/macro';
import {
  ActionIcon,
  Button,
  Group,
  Stack,
  Table,
  Text,
  Tooltip,
  UnstyledButton
} from '@mantine/core';
import { IconChartLine, IconExternalLink } from '@tabler/icons-react';
import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { MissingValue } from '../../../../components/items/ValueTooltip';
import { useUserState } from '../../../../states/UserState';
import { machinePanelHref } from '../../locations/locationTree';
import operations from '../../operations.module.css';
import { Timestamp } from '../Timestamp';
import { AlarmStateBadge, SeverityBadge } from '../common';
import { UnavailableReading, failedReading } from './MimicValue';
import { useDisplayClock } from './observedTime';

import { UserRoles } from '@lib/enums/Roles';

import { WorkOrderCreateModal } from '../../../maintenance/components/WorkOrderCreateModal';
import { type AlarmAction, AlarmNoteModal } from './AlarmNoteModal';
import { type Display, conditionText } from './MimicReadings';
import { valueText } from './format';
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
  const [params] = useSearchParams();
  const user = useUserState();
  const clock = useDisplayClock();
  const canChange = user.hasChangeRole(UserRoles.work_order);
  const canAdd = user.hasAddRole(UserRoles.work_order);
  const [noting, setNoting] = useState<{
    action: AlarmAction;
    alarm: MimicAlarm;
  } | null>(null);
  const [repairing, setRepairing] = useState<MimicAlarm | null>(null);

  return (
    <>
      <Table.ScrollContainer
        minWidth={0}
        type='native'
        className={operations.alarmScroll}
      >
        <Table striped className={operations.alarmTable}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t`Signal`}</Table.Th>
              <Table.Th>{t`Alarm`}</Table.Th>
              <Table.Th>{t`Value`}</Table.Th>
              <Table.Th>{t`Condition`}</Table.Th>
              <Table.Th>{t`Observed at`}</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {alarms.map((alarm) => {
              const raised = alarm.anomaly !== null && !!alarm.machine;
              const responding = ['open', 'acknowledged'].includes(
                alarm.anomaly_status ?? ''
              );
              return (
                <Table.Tr
                  key={`${alarm.machine ?? ''}:${alarm.pointer}`}
                  data-point={alarm.pointer}
                  data-anomaly={alarm.anomaly ?? undefined}
                >
                  <Table.Td>
                    <Tooltip
                      label={alarm.pointer}
                      events={{ hover: true, focus: true, touch: true }}
                    >
                      <UnstyledButton className={operations.phoneTarget}>
                        {alarm.label}
                      </UnstyledButton>
                    </Tooltip>
                  </Table.Td>
                  <Table.Td data-label={t`Alarm`}>
                    <AlarmStatus alarm={alarm} />
                  </Table.Td>
                  <Table.Td data-label={t`Value`}>
                    {alarm.value === null ||
                    alarm.reason ||
                    failedReading(alarm) ? (
                      <UnavailableReading
                        point={alarm}
                        label={alarm.label}
                        display={display}
                        focusable={false}
                      />
                    ) : (
                      display(alarm)
                    )}
                  </Table.Td>
                  <Table.Td data-label={t`Condition`}>
                    {!alarm.thresholds_configured || alarm.reason ? (
                      alarm.reason ? (
                        <UnavailableReading
                          point={{ ...alarm, value: null }}
                          label={t`Condition`}
                          focusable={false}
                        />
                      ) : (
                        <MissingValue
                          reason={conditionText(alarm)}
                          focusable={false}
                        />
                      )
                    ) : (
                      conditionText(alarm)
                    )}
                  </Table.Td>
                  <Table.Td
                    data-label={t`Observed at`}
                    style={{ whiteSpace: 'nowrap' }}
                  >
                    <Timestamp
                      value={alarm.observed_at}
                      clock={clock}
                      focusable={false}
                    />
                  </Table.Td>
                  <Table.Td className={operations.alarmActions}>
                    <Group
                      gap='sm'
                      wrap='wrap'
                      justify='flex-end'
                      preventGrowOverflow={false}
                    >
                      {raised && (
                        <Tooltip label={t`Open in Health`}>
                          <ActionIcon
                            component={Link}
                            to={`/machines/machine/${alarm.machine}/health`}
                            variant='subtle'
                            aria-label={t`Open ${alarm.label} in Health`}
                          >
                            <IconExternalLink size={16} />
                          </ActionIcon>
                        </Tooltip>
                      )}
                      {raised && (
                        <Tooltip label={t`Open occurrence in Fault History`}>
                          <ActionIcon
                            component={Link}
                            to={machinePanelHref(
                              alarm.machine!,
                              'fault-history',
                              params,
                              { occurrence: String(alarm.anomaly) }
                            )}
                            variant='subtle'
                            aria-label={t`Open occurrence for ${alarm.label}`}
                          >
                            <IconChartLine size={16} />
                          </ActionIcon>
                        </Tooltip>
                      )}
                      {canChange &&
                        raised &&
                        alarm.anomaly_status === 'open' && (
                          <Button
                            size='compact-sm'
                            className={operations.phoneTarget}
                            variant='default'
                            aria-label={t`Acknowledge ${alarm.label}`}
                            onClick={() =>
                              setNoting({ action: 'acknowledge', alarm })
                            }
                          >
                            {t`Acknowledge`}
                          </Button>
                        )}
                      {canAdd && raised && responding && (
                        <Button
                          disabled={!canAdd}
                          size='compact-sm'
                          variant='light'
                          aria-label={t`Create repair for ${alarm.label}`}
                          className={`${operations.link} ${operations.phoneTarget}`}
                          onClick={() => setRepairing(alarm)}
                        >
                          {t`Create repair`}
                        </Button>
                      )}
                      {canChange && raised && responding && (
                        <Button
                          disabled={!canChange}
                          size='compact-sm'
                          className={operations.phoneTarget}
                          variant='subtle'
                          color='gray'
                          aria-label={t`Dismiss ${alarm.label}`}
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
    return <Text size='sm'>{t`Not raised yet`}</Text>;
  }
  return (
    <Tooltip
      label={t`Acknowledgement records that someone saw the application alarm; it does not clear the condition.`}
      events={{ hover: true, focus: true, touch: true }}
      multiline
      maw={360}
    >
      <UnstyledButton className={operations.phoneTarget} data-alarm-info>
        <Stack gap={4} align='flex-start'>
          <AlarmStateBadge
            status={alarm.anomaly_status}
            severity={alarm.severity}
          />
          <SeverityBadge severity={alarm.severity} />
        </Stack>
      </UnstyledButton>
    </Tooltip>
  );
}
