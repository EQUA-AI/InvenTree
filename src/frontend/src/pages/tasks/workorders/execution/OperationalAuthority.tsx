import type {
  AuthorityKind,
  AuthorityRecord,
  AuthorityTelemetry,
  OperationalAuthority
} from '@lib/types/OperationalAuthority';
import { t } from '@lingui/core/macro';
import {
  Accordion,
  Alert,
  Badge,
  Button,
  Group,
  Select,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Textarea
} from '@mantine/core';
import { useEffect, useRef, useState } from 'react';
import { MissingValue } from '../../../../components/items/ValueTooltip';
import { useUserState } from '../../../../states/UserState';
import { recordedTime } from './ExecutionFacts';
import {
  type AuthorityIntent,
  authorityDraftContext
} from './authorityCommand';
import { authorityReading } from './authorityReading';
import { useAuthorityCommand } from './useAuthorityCommand';

export function authorityLabel(kind: AuthorityKind) {
  const labels: Record<AuthorityKind, string> = {
    isolation: t`Isolation point`,
    permit: t`Permit`,
    access_window: t`Access window`,
    test_acceptance: t`Test acceptance`,
    operating_permission: t`Operating permission`,
    observed_return: t`Observed operating return`,
    service_state: t`Duty, standby and redundancy`
  };
  return labels[kind];
}

export function currentAuthority(
  value: OperationalAuthority | undefined,
  kind: AuthorityKind
) {
  return value?.rule === 'operational_authority.v1'
    ? [...value.physical, ...value.operational].filter(
        (row) => row.kind === kind && row.current
      )
    : [];
}

/** No positive colour or inferred clearance: the authored outcome stays explicit. */
export function AuthorityFact({
  record
}: Readonly<{ record: AuthorityRecord }>) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30000);
    return () => window.clearInterval(timer);
  }, []);
  const windowState =
    record.effective_until && Date.parse(record.effective_until) <= now
      ? t`Expired`
      : Date.parse(record.effective_from) > now
        ? t`Not yet effective`
        : record.effective_state === 'awaiting_verification'
          ? t`Awaiting independent verification`
          : t`Recorded effective window`;
  return (
    <Stack gap={4} data-testid={`authority-record-${record.kind}`}>
      <Group gap='xs' wrap='wrap'>
        <Text fw={600} size='sm'>
          {authorityLabel(record.kind)} ·{' '}
          {record.kind === 'service_state' && record.outcome === 'confirmed'
            ? t`Reading verified current`
            : record.outcome.replaceAll('_', ' ')}
        </Text>
        <Badge
          color='gray'
          variant='light'
          styles={{ root: { height: 'auto' }, label: { whiteSpace: 'normal' } }}
        >
          {windowState}
        </Badge>
        {record.basis_state === 'changed_or_unrecorded' && (
          <Badge
            color='orange'
            styles={{
              root: { height: 'auto' },
              label: { whiteSpace: 'normal' }
            }}
          >{t`Recorded basis changed or unavailable`}</Badge>
        )}
        {!record.current && <Badge color='gray'>{t`Superseded`}</Badge>}
      </Group>
      <Text
        size='sm'
        style={{ overflowWrap: 'anywhere', whiteSpace: 'pre-wrap' }}
      >
        {record.statement}
      </Text>
      <Text size='sm' fw={600}>
        {record.gate_id ? t`Safety gate` : t`Asset / station`}:{' '}
        {(record.gate_id ? record.gate_name : record.target_name) ||
          t`Not recorded`}
      </Text>
      <Text size='xs' style={{ overflowWrap: 'anywhere' }}>
        {t`Source`}: {record.source_reference} · {t`Version`}: {record.version}
      </Text>
      <Text size='xs'>
        {t`Recorded by`}: {record.recorded_by || t`Not recorded`} ·{' '}
        {recordedTime(record.recorded_at)}
      </Text>
      <Text size='xs'>
        {t`Observed`}: {recordedTime(record.observed_at)} · {t`Effective from`}:{' '}
        {recordedTime(record.effective_from)} · {t`Until`}:{' '}
        {recordedTime(record.effective_until)}
      </Text>
      {record.gate_id && (
        <Text size='xs'>
          {t`Independent verification`}: {record.verified_by || t`Not recorded`}{' '}
          · {recordedTime(record.verified_at)}
        </Text>
      )}
      {record.kind === 'service_state' && (
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          {(['duty', 'standby', 'redundancy', 'restrictions'] as const).map(
            (key) => (
              <Text key={key} size='sm'>
                {key}: {record.details[key] || t`Not recorded`}
              </Text>
            )
          )}
        </SimpleGrid>
      )}
      {record.basis.telemetry && (
        <Text size='xs'>
          {t`Telemetry basis`}: {record.basis.telemetry.tag} ·{' '}
          {authorityReading(record.basis.telemetry.value) ?? t`Not recorded`} ·{' '}
          {record.basis.telemetry.source} ·{' '}
          {recordedTime(record.basis.telemetry.observed_at)} ·{' '}
          {record.basis.telemetry.quality}
        </Text>
      )}
    </Stack>
  );
}

export function AuthorityRecords({
  value,
  kinds
}: Readonly<{ value?: OperationalAuthority; kinds: AuthorityKind[] }>) {
  const rows =
    value?.rule === 'operational_authority.v1'
      ? [...value.physical, ...value.operational].filter((row) =>
          kinds.includes(row.kind)
        )
      : [];
  const current = rows.filter((row) => row.current);
  const history = rows.filter((row) => !row.current);
  return (
    <Stack gap='md'>
      {current.length ? (
        current.map((row) => (
          <AuthorityFact key={`${row.kind}:${row.id}`} record={row} />
        ))
      ) : (
        <MissingValue
          reason={t`Not recorded — no attributed authority facts supplied for this section.`}
        />
      )}
      {history.length > 0 && (
        <Accordion variant='contained'>
          <Accordion.Item value='history'>
            <Accordion.Control>
              {t`Superseded authority records`} ({history.length})
            </Accordion.Control>
            <Accordion.Panel>
              <Stack>
                {history.map((row) => (
                  <AuthorityFact key={`${row.kind}:${row.id}`} record={row} />
                ))}
              </Stack>
            </Accordion.Panel>
          </Accordion.Item>
        </Accordion>
      )}
    </Stack>
  );
}

const localTime = () => {
  const date = new Date();
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
};

/** Exceptional manual recording, hidden unless this exact server capability is granted. */
export function AuthorityRecorder({
  value,
  mode
}: Readonly<{
  value?: OperationalAuthority;
  mode: 'physical' | 'verification' | 'service';
}>) {
  if (!value || value.rule !== 'operational_authority.v1') return null;
  const physical = mode === 'physical';
  const kinds: AuthorityKind[] = physical
    ? value.gates.some((gate) => gate.can_record)
      ? ['isolation', 'permit', 'access_window']
      : []
    : (mode === 'service'
        ? (['service_state'] as const)
        : ([
            'test_acceptance',
            'operating_permission',
            'observed_return'
          ] as const)
      ).filter((kind) => value.commands[kind]);
  if (!kinds.length) return null;
  return (
    <AuthorityRecordingForm value={value} physical={physical} kinds={kinds} />
  );
}

function AuthorityRecordingForm({
  value,
  physical,
  kinds
}: Readonly<{
  value: OperationalAuthority;
  physical: boolean;
  kinds: AuthorityKind[];
}>) {
  const { state, execute, reviewRefusal, available } = useAuthorityCommand(
    value.work_order_id
  );
  const userId = useUserState((state) => state.user?.pk);
  const [kind, setKind] = useState<AuthorityKind>(kinds[0]);
  const [outcome, setOutcome] = useState<string | null>(null);
  const [gateId, setGateId] = useState<string | null>(null);
  const [targetId, setTargetId] = useState<string | null>(null);
  const [statement, setStatement] = useState('');
  const [source, setSource] = useState('');
  const [observed, setObserved] = useState('');
  const [start, setStart] = useState('');
  const [until, setUntil] = useState('');
  const [supersedes, setSupersedes] = useState<string | null>(null);
  const [binding, setBinding] = useState<string | null>(null);
  const [selectedReading, setSelectedReading] =
    useState<AuthorityTelemetry | null>(null);
  const [service, setService] = useState({
    duty: '',
    standby: '',
    redundancy: '',
    restrictions: ''
  });
  const [error, setError] = useState('');
  const submittedKey = useRef<string | null>(null);
  const datesInitialized = useRef(false);
  const draftContext = useRef<{
    version: number;
    token: string;
  } | null>(null);
  useEffect(() => {
    draftContext.current = authorityDraftContext(
      draftContext.current,
      value.work_version,
      value.context_token
    );
  }, [value.work_version, value.context_token]);
  useEffect(() => {
    if (
      state.status !== 'confirmed' ||
      state.intent.payload.idempotency_key !== submittedKey.current
    )
      return;
    submittedKey.current = null;
    draftContext.current = null;
    datesInitialized.current = false;
    setObserved('');
    setStart('');
    setStatement('');
    setSource('');
    setOutcome(null);
    setSupersedes(null);
    setUntil('');
    setBinding(null);
    setSelectedReading(null);
    setService({ duty: '', standby: '', redundancy: '', restrictions: '' });
  }, [state]);
  const serviceKind = kind === 'service_state';
  const rows = [...value.physical, ...value.operational].filter(
    (row) =>
      row.current &&
      row.kind === kind &&
      (physical
        ? row.gate_id === Number(gateId)
        : row.target_id === Number(targetId || value.targets[0]?.id))
  );
  const options: Record<AuthorityKind, string[]> = {
    isolation: ['active', 'released', 'revoked'],
    permit: ['active', 'released', 'revoked'],
    access_window: ['active', 'released', 'revoked'],
    test_acceptance: ['accepted', 'rejected'],
    operating_permission: ['granted', 'withheld', 'revoked'],
    observed_return: ['observed_running', 'observed_stopped'],
    service_state: ['operator_recorded', 'confirmed', 'corrected']
  };
  const locked =
    !available ||
    ['pending', 'uncertain', 'blocked', 'failed', 'stale'].includes(
      state.status
    );
  const reading =
    selectedReading?.target_id === Number(targetId || value.targets[0]?.id)
      ? selectedReading
      : null;
  const submit = () => {
    try {
      const fields: AuthorityIntent['payload']['fields'] = {
        kind,
        outcome,
        statement: statement.trim(),
        source_reference: source.trim(),
        observed_at: new Date(observed || localTime()).toISOString(),
        effective_from: new Date(start || localTime()).toISOString(),
        effective_until: until ? new Date(until).toISOString() : null
      };
      if (physical) fields.gate_id = Number(gateId);
      else fields.target_id = Number(targetId || value.targets[0]?.id);
      if (serviceKind) {
        Object.assign(
          fields,
          Object.fromEntries(
            Object.entries(service).map(([key, value]) => [key, value.trim()])
          )
        );
        if (outcome === 'confirmed' || outcome === 'corrected') {
          if (!reading?.observed_at)
            throw new Error(t`Select a recorded telemetry observation.`);
          fields.binding_id = reading.binding_id;
          fields.telemetry_observed_at = reading.observed_at;
          fields.telemetry_token = reading.reading_token;
        }
      }
      setError('');
      const idempotencyKey = crypto.randomUUID();
      submittedKey.current = idempotencyKey;
      void execute({
        workOrderId: value.work_order_id,
        actorId: userId!,
        payload: {
          expected_version: draftContext.current?.version ?? value.work_version,
          expected_context: draftContext.current?.token ?? value.context_token,
          idempotency_key: idempotencyKey,
          operation: 'record',
          fields,
          ...(supersedes ? { supersedes: Number(supersedes) } : {})
        }
      });
    } catch {
      setError(
        t`Check the observation, effective dates and selected telemetry.`
      );
    }
  };
  const verify = (row: AuthorityRecord) =>
    void execute({
      workOrderId: value.work_order_id,
      actorId: userId!,
      payload: {
        expected_version: draftContext.current?.version ?? value.work_version,
        expected_context: draftContext.current?.token ?? value.context_token,
        idempotency_key: crypto.randomUUID(),
        operation: 'verify',
        fields: { gate_id: row.gate_id! },
        supersedes: row.id
      }
    });
  return (
    <Stack
      gap='sm'
      data-testid='authority-recorder'
      onFocusCapture={() => {
        if (!datesInitialized.current) {
          datesInitialized.current = true;
          const now = localTime();
          setObserved(now);
          setStart(now);
        }
        draftContext.current ??= {
          version: value.work_version,
          token: value.context_token
        };
      }}
    >
      {'intent' in state && state.status !== 'confirmed' && (
        <Text size='sm' style={{ overflowWrap: 'anywhere' }}>
          {t`Original operation`}:{' '}
          {String(
            state.intent.payload.fields.statement ||
              t`Independent gate verification`
          )}{' '}
          · {String(state.intent.payload.fields.source_reference || '')}
        </Text>
      )}
      {state.status === 'confirmed' && (
        <Alert color='blue' aria-live='polite'>
          {t`Authority record saved`} ·{' '}
          {authorityLabel(state.receipt.record.kind)} ·{' '}
          {state.receipt.record.statement} · {t`Version`}{' '}
          {state.receipt.work_version}
        </Alert>
      )}
      {'message' in state && state.message && (
        <Alert color='orange' role='alert'>
          {state.message}
        </Alert>
      )}
      {['pending', 'uncertain'].includes(state.status) && (
        <>
          <Text size='sm'>{t`The original operation is retained. Check its result before preparing another record.`}</Text>
          <Button
            variant='default'
            onClick={() => void execute()}
            loading={state.status === 'pending'}
          >{t`Check original authority operation`}</Button>
        </>
      )}
      {['failed', 'stale'].includes(state.status) && (
        <Button
          variant='default'
          onClick={() => {
            void reviewRefusal().then((context) => {
              if (context) {
                draftContext.current = context;
                setBinding(null);
                setSelectedReading(null);
              }
            });
          }}
        >{t`Review latest Work after refusal`}</Button>
      )}
      {state.status === 'blocked' && (
        <Alert color='orange'>{t`The retained command cannot be read safely. Recording is blocked; review it with an administrator.`}</Alert>
      )}
      {physical &&
        value.physical
          .filter(
            (row) =>
              row.current &&
              !row.verified_by &&
              row.recorded_by_id !== userId &&
              value.gates.some(
                (gate) =>
                  gate.id === row.gate_id &&
                  gate.can_record &&
                  gate.requires_second_person
              )
          )
          .map((row) => (
            <Button
              key={row.id}
              variant='default'
              disabled={locked}
              onClick={() => verify(row)}
            >
              {t`Verify independently`} · {row.statement}
            </Button>
          ))}
      <Accordion variant='contained'>
        <Accordion.Item value='record'>
          <Accordion.Control>
            {physical
              ? t`Record isolation, permit or access window`
              : serviceKind
                ? t`Record operator duty / standby context`
                : t`Record test acceptance or operating fact`}
          </Accordion.Control>
          <Accordion.Panel>
            <Stack gap='sm'>
              <Text
                size='sm'
                c='dimmed'
              >{t`Record the actual site-authorized fact and its source. This saves an attributed record; it does not operate equipment, confirm a gate or clear other restrictions. Corrections supersede the selected record.`}</Text>
              <SimpleGrid cols={{ base: 1, sm: 2 }}>
                <Select
                  label={t`Record kind`}
                  data={kinds.map((kind) => ({
                    value: kind,
                    label: authorityLabel(kind)
                  }))}
                  value={kind}
                  onChange={(next) => {
                    if (next) {
                      setKind(next as AuthorityKind);
                      setOutcome(null);
                      setSupersedes(null);
                    }
                  }}
                  disabled={locked}
                />
                <Select
                  label={t`Recorded outcome`}
                  data={options[kind].map((value) => ({
                    value,
                    label:
                      serviceKind && value === 'confirmed'
                        ? t`Reading verified current`
                        : value.replaceAll('_', ' ')
                  }))}
                  value={outcome}
                  onChange={setOutcome}
                  required
                  disabled={locked}
                />
                {physical ? (
                  <Select
                    label={t`Existing safety gate`}
                    data={value.gates
                      .filter((gate) => gate.can_record)
                      .map((gate) => ({
                        value: String(gate.id),
                        label: gate.name
                      }))}
                    value={gateId}
                    onChange={(id) => {
                      setGateId(id);
                      setSupersedes(null);
                    }}
                    required
                    disabled={locked}
                  />
                ) : (
                  <Select
                    label={t`Asset / station`}
                    data={value.targets
                      .filter((_, index) => serviceKind || index === 0)
                      .map((target) => ({
                        value: String(target.id),
                        label: target.name
                      }))}
                    value={targetId || String(value.targets[0]?.id)}
                    onChange={(id) => {
                      setTargetId(id);
                      setSupersedes(null);
                      setBinding(null);
                      setSelectedReading(null);
                    }}
                    disabled={locked}
                  />
                )}
                <Select
                  label={t`Correction supersedes`}
                  placeholder={t`New independent record`}
                  clearable
                  data={rows.map((row) => ({
                    value: String(row.id),
                    label: `v${row.version} · ${row.statement}`
                  }))}
                  value={supersedes}
                  onChange={setSupersedes}
                  disabled={locked}
                />
              </SimpleGrid>
              <Textarea
                label={t`Recorded fact and conditions`}
                description={
                  physical
                    ? t`Identify the isolation point, permit number or access window and applicable limits.`
                    : t`State the test scope, operating permission or actual observation and its limits.`
                }
                value={statement}
                onChange={(event) => setStatement(event.currentTarget.value)}
                required
                maxLength={4000}
                autosize
                minRows={2}
                readOnly={locked}
              />
              <TextInput
                label={t`Source document / record reference`}
                value={source}
                onChange={(event) => setSource(event.currentTarget.value)}
                maxLength={1000}
                required
                readOnly={locked}
              />
              <SimpleGrid cols={{ base: 1, sm: 3 }}>
                <TextInput
                  type='datetime-local'
                  label={t`Observation time`}
                  value={observed}
                  onChange={(event) => setObserved(event.currentTarget.value)}
                  required
                  readOnly={locked}
                />
                <TextInput
                  type='datetime-local'
                  label={t`Effective from`}
                  value={start}
                  onChange={(event) => setStart(event.currentTarget.value)}
                  required
                  readOnly={locked}
                />
                <TextInput
                  type='datetime-local'
                  label={t`Effective until / expiry`}
                  value={until}
                  onChange={(event) => setUntil(event.currentTarget.value)}
                  required={physical || kind === 'operating_permission'}
                  readOnly={locked}
                />
              </SimpleGrid>
              {serviceKind && (
                <>
                  <Select
                    label={t`Telemetry observation to confirm or correct`}
                    clearable
                    value={binding}
                    onChange={(id) => {
                      setBinding(id);
                      setSelectedReading(
                        value.telemetry.find(
                          (row) => row.binding_id === Number(id)
                        ) || null
                      );
                    }}
                    disabled={locked || outcome === 'operator_recorded'}
                    data={value.telemetry
                      .filter(
                        (row) =>
                          row.target_id ===
                            Number(targetId || value.targets[0]?.id) &&
                          row.observed_at
                      )
                      .map((row) => ({
                        value: String(row.binding_id),
                        label: `${row.tag} · ${recordedTime(row.observed_at)} · ${row.quality}${row.stale ? ' · stale' : ''}`
                      }))}
                  />
                  {reading && (
                    <Text size='sm' style={{ overflowWrap: 'anywhere' }}>
                      {t`Selected observation`}: {reading.tag} ·{' '}
                      {authorityReading(reading.value) ?? t`Not recorded`} ·{' '}
                      {recordedTime(reading.observed_at)} · {reading.quality}
                    </Text>
                  )}
                  <Text size='xs'>{t`Run status and running count are context only. Enter actual duty, available standby or redundancy explicitly. Blank facts stay Not recorded.`}</Text>
                  <SimpleGrid cols={{ base: 1, sm: 2 }}>
                    {(
                      ['duty', 'standby', 'redundancy', 'restrictions'] as const
                    ).map((key) => (
                      <TextInput
                        key={key}
                        label={key}
                        value={service[key]}
                        maxLength={1000}
                        readOnly={locked}
                        onChange={(event) =>
                          setService({
                            ...service,
                            [key]: event.currentTarget.value
                          })
                        }
                      />
                    ))}
                  </SimpleGrid>
                </>
              )}
              {error && (
                <Text c='red' role='alert'>
                  {error}
                </Text>
              )}
              <Button
                style={{ alignSelf: 'flex-start' }}
                onClick={submit}
                disabled={
                  locked ||
                  !outcome ||
                  !statement.trim() ||
                  !source.trim() ||
                  (physical && !gateId) ||
                  ((physical || kind === 'operating_permission') && !until)
                }
                loading={state.status === 'pending'}
              >{t`Save attributed record`}</Button>
            </Stack>
          </Accordion.Panel>
        </Accordion.Item>
      </Accordion>
    </Stack>
  );
}
