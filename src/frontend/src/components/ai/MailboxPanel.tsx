import { t } from '@lingui/core/macro';
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  FileInput,
  Group,
  NumberInput,
  PasswordInput,
  Select,
  Stack,
  Text,
  TextInput,
  Textarea
} from '@mantine/core';
import { isEmail, isNotEmpty, useForm } from '@mantine/form';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../../App';

type Mailbox = {
  id: string;
  name: string;
  address: string;
  provider: string;
  enabled: boolean;
  send_enabled: boolean;
  receive_enabled: boolean;
  verified_send: boolean;
  verified_receive: boolean;
  health: string;
  permissions: { read: boolean; draft: boolean; send: boolean; admin: boolean };
  sync: { collection: string; status: string; has_gap: boolean }[];
};

type Message = {
  id: string;
  subject: string;
  body: string;
  sender: string[];
  reply_to: string[];
  attachments: { id: string; filename: string; scan_state: string }[];
};

type MailboxListResponse = {
  results: Mailbox[];
  setup: { microsoft_shared: boolean; send_paused: boolean };
};

const root = '/api/aichat/email/accounts/';

function Compose({
  account,
  reply,
  onDraft
}: { account: Mailbox; reply?: Message; onDraft?: () => void }) {
  const [attachments, setAttachments] = useState<string[]>([]);
  const [requestId, setRequestId] = useState(() => crypto.randomUUID());
  const [uploadError, setUploadError] = useState(false);
  const form = useForm({
    initialValues: {
      to:
        (reply?.reply_to.length ? reply.reply_to : reply?.sender)?.join(', ') ??
        '',
      cc: '',
      bcc: '',
      subject: reply ? `Re: ${reply.subject}` : '',
      body: '',
      verification: false
    },
    validate: {
      to: isNotEmpty(t`Enter a recipient`),
      subject: isNotEmpty(t`Enter a subject`)
    }
  });
  const create = useMutation({
    mutationFn: () =>
      api.post(`${root}${account.id}/drafts/`, {
        ...form.values,
        request_id: requestId,
        reply_message_id: reply?.id,
        attachment_ids: attachments
      }),
    onSuccess: () => {
      setRequestId(crypto.randomUUID());
      onDraft?.();
    }
  });
  const upload = useMutation({
    mutationFn: async (file: File) => {
      const data = new FormData();
      data.append('file', file);
      const result = await api.post(`${root}${account.id}/attachments/`, data);
      if (result.data.scan_state !== 'clean') throw new Error('quarantined');
      return result.data.id as string;
    },
    onSuccess: (id) => {
      setAttachments((old) => [...old, id]);
      setUploadError(false);
    },
    onError: () => setUploadError(true)
  });
  return (
    <form onSubmit={form.onSubmit(() => create.mutate())}>
      <Stack gap='sm'>
        <Text size='sm'>
          {t`From`}: {account.address}
        </Text>
        <TextInput label={t`To`} {...form.getInputProps('to')} />
        <Group grow>
          <TextInput label={t`CC`} {...form.getInputProps('cc')} />
          <TextInput label={t`BCC`} {...form.getInputProps('bcc')} />
        </Group>
        <TextInput label={t`Subject`} {...form.getInputProps('subject')} />
        <Textarea
          label={t`Message`}
          minRows={5}
          autosize
          {...form.getInputProps('body')}
        />
        <FileInput
          label={t`Attach a document`}
          disabled={upload.isPending || attachments.length >= 20}
          onChange={(file) => file && upload.mutate(file)}
        />
        <Text size='xs'>
          {t`Attachments ready`}: {attachments.length}
        </Text>
        {uploadError && (
          <Alert color='yellow'>{t`This attachment is quarantined or could not be scanned. It cannot be sent.`}</Alert>
        )}
        {(!account.verified_send || !account.verified_receive) && (
          <Checkbox
            label={t`Administrator verification message`}
            {...form.getInputProps('verification', { type: 'checkbox' })}
          />
        )}
        {create.isError && (
          <Alert color='red'>{t`Draft could not be created. Check mailbox access, recipient restrictions and connection status.`}</Alert>
        )}
        {create.isSuccess && (
          <Alert color='green'>{t`Draft created. Review it in Approvals before sending.`}</Alert>
        )}
        <Button
          type='submit'
          loading={create.isPending}
          disabled={create.isSuccess || upload.isPending}
        >{t`Create draft for review`}</Button>
      </Stack>
    </form>
  );
}

export function MailboxPanel({
  admin = false,
  onDraft
}: { admin?: boolean; onDraft?: () => void }) {
  const cache = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [reply, setReply] = useState<Message>();
  const [compose, setCompose] = useState(false);
  const accounts = useQuery({
    queryKey: ['mailboxes', admin],
    queryFn: async () =>
      (await api.get(root, { params: { manage: admin } }))
        .data as MailboxListResponse,
    retry: false,
    refetchInterval: 30000
  });
  const account = accounts.data?.results.find((item) => item.id === selected);
  const messages = useQuery({
    queryKey: ['mailbox-messages', selected],
    enabled: !!selected && !!account?.permissions.read,
    queryFn: async () =>
      (await api.get(`${root}${selected}/messages/`)).data.results as Message[],
    refetchInterval: 30000
  });
  const control = useMutation({
    mutationFn: async (values: Partial<Mailbox>) =>
      api.patch(`${root}${selected}/`, values),
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: ['mailboxes'] });
    }
  });
  const sync = useMutation({
    mutationFn: () => api.post(`${root}${selected}/sync/`)
  });
  const disconnect = useMutation({
    mutationFn: () => api.delete(`${root}${selected}/`),
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: ['mailboxes'] });
    }
  });
  if (accounts.isError)
    return <Alert>{t`Agent mail is unavailable or disabled.`}</Alert>;
  return (
    <Stack>
      {accounts.data?.setup?.send_paused && (
        <Alert color='yellow'>{t`Outgoing agent email is paused. You can connect a mailbox and test receiving.`}</Alert>
      )}
      <Select
        label={t`Mailbox`}
        placeholder={t`Choose a mailbox`}
        value={selected}
        data={(accounts.data?.results ?? []).map((item) => ({
          value: item.id,
          label: `${item.name} (${item.address})`
        }))}
        onChange={(id) => {
          setSelected(id);
          setReply(undefined);
          setCompose(false);
        }}
      />
      {account && (
        <>
          <Group>
            <Badge color={account.send_enabled ? 'green' : 'gray'}>
              {account.send_enabled ? t`Sending enabled` : t`Sending paused`}
            </Badge>
            <Badge>{account.health}</Badge>
          </Group>
          {account.sync.map((state) => (
            <Text size='xs' key={state.collection}>
              {state.collection}: {state.status}
              {state.has_gap ? ` — ${t`History coverage has gaps`}` : ''}
            </Text>
          ))}
          {admin && (
            <>
              <Group>
                <Button
                  loading={control.isPending}
                  onClick={() => control.mutate({ enabled: !account.enabled })}
                >
                  {account.enabled ? t`Pause account` : t`Enable account`}
                </Button>
                <Button
                  loading={control.isPending}
                  onClick={() =>
                    control.mutate({
                      receive_enabled: !account.receive_enabled
                    })
                  }
                >
                  {account.receive_enabled
                    ? t`Pause receive`
                    : t`Enable receive`}
                </Button>
                <Button
                  disabled={!account.verified_send || !account.verified_receive}
                  onClick={() =>
                    control.mutate({ send_enabled: !account.send_enabled })
                  }
                >
                  {account.send_enabled ? t`Pause send` : t`Enable send`}
                </Button>
                <Button
                  loading={sync.isPending}
                  onClick={() => sync.mutate()}
                >{t`Test receive / sync`}</Button>
                <Button
                  color='red'
                  loading={disconnect.isPending}
                  onClick={() => disconnect.mutate()}
                >{t`Disconnect`}</Button>
              </Group>
              {(control.isError || sync.isError || disconnect.isError) && (
                <Alert color='red'>{t`Mailbox control failed. Check account permissions and verification.`}</Alert>
              )}
              <OAuthConnection account={account} />
              <MailboxGrantForm account={account} />
            </>
          )}
          <Button
            variant='light'
            onClick={() => {
              setReply(undefined);
              setCompose(true);
            }}
            disabled={!account.permissions.draft}
          >{t`Compose email`}</Button>
          {compose &&
            account.permissions.draft &&
            (!reply || !messages.isError) && (
              <Compose
                key={`${account.id}:${reply?.id ?? 'new'}`}
                account={account}
                reply={reply}
                onDraft={onDraft}
              />
            )}
          {messages.isError && (
            <Alert color='red'>{t`Messages are no longer accessible.`}</Alert>
          )}
          {account.permissions.read &&
            !messages.isError &&
            messages.data?.map((message) => (
              <Stack
                key={message.id}
                gap='xs'
                p='sm'
                style={{
                  border: '1px solid var(--mantine-color-default-border)'
                }}
              >
                <Text fw={600}>{message.subject}</Text>
                <Text size='sm'>{message.sender.join(', ')}</Text>
                <Text
                  size='sm'
                  style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}
                >
                  {message.body || t`No plain text body available.`}
                </Text>
                {message.attachments.map((artifact) => (
                  <Button
                    key={artifact.id}
                    variant='subtle'
                    disabled={artifact.scan_state !== 'clean'}
                    onClick={async () => {
                      const response = await api.get(
                        `${root}${account.id}/attachments/${artifact.id}/`,
                        { responseType: 'blob' }
                      );
                      const url = URL.createObjectURL(response.data);
                      const link = document.createElement('a');
                      link.href = url;
                      link.download = artifact.filename;
                      link.click();
                      URL.revokeObjectURL(url);
                    }}
                  >
                    {artifact.filename} ({artifact.scan_state})
                  </Button>
                ))}
                <Button
                  variant='light'
                  onClick={() => {
                    setReply(message);
                    setCompose(true);
                  }}
                >{t`Draft reply`}</Button>
              </Stack>
            ))}
        </>
      )}
      {admin && accounts.data && (
        <MailboxSetup
          microsoftShared={accounts.data.setup?.microsoft_shared ?? false}
          onCreated={setSelected}
        />
      )}
    </Stack>
  );
}

function OAuthConnection({ account }: { account: Mailbox }) {
  const cache = useQueryClient();
  const form = useForm({ initialValues: { callback: '' } });
  const begin = useMutation({
    mutationFn: async () =>
      (await api.post(`${root}${account.id}/oauth/`)).data
        .authorization_url as string
  });
  const finish = useMutation({
    mutationFn: () => {
      const params = new URL(form.values.callback).searchParams;
      return api.post('/api/aichat/email/oauth/callback/', {
        state: params.get('state'),
        code: params.get('code')
      });
    },
    onSuccess: () => {
      form.reset();
      begin.reset();
      void cache.invalidateQueries({ queryKey: ['mailboxes'] });
    }
  });
  if (!['graph', 'google'].includes(account.provider)) return null;
  return (
    <Stack gap='xs'>
      <Button
        onClick={() => begin.mutate()}
        loading={begin.isPending}
      >{t`Connect with OAuth`}</Button>
      {begin.data && (
        <Button
          component='a'
          href={begin.data}
          target='_blank'
          rel='noopener noreferrer'
        >{t`Open provider consent`}</Button>
      )}
      <TextInput
        label={t`Paste the callback URL after consent`}
        {...form.getInputProps('callback')}
      />
      <Button
        onClick={() => finish.mutate()}
        loading={finish.isPending}
      >{t`Complete connection`}</Button>
      {(begin.isError || finish.isError) && (
        <Alert color='red'>{t`OAuth connection failed. Start a new consent attempt.`}</Alert>
      )}
      {finish.isSuccess && (
        <Alert color='green'>{t`Connected. Verify sending and receiving before enabling sends.`}</Alert>
      )}
    </Stack>
  );
}

function MailboxGrantForm({ account }: { account: Mailbox }) {
  const form = useForm({
    initialValues: {
      user_id: '',
      can_read: false,
      can_draft: false,
      can_send: false,
      can_admin: false
    }
  });
  const save = useMutation({
    mutationFn: () =>
      api.put(`${root}${account.id}/grants/`, {
        ...form.values,
        user_id: Number(form.values.user_id)
      })
  });
  return (
    <form onSubmit={form.onSubmit(() => save.mutate())}>
      <Stack gap='xs'>
        <NumberInput
          label={t`User ID for mailbox access`}
          min={1}
          {...form.getInputProps('user_id')}
        />
        <Group>
          <Checkbox
            label={t`Read`}
            {...form.getInputProps('can_read', { type: 'checkbox' })}
          />
          <Checkbox
            label={t`Draft`}
            {...form.getInputProps('can_draft', { type: 'checkbox' })}
          />
          <Checkbox
            label={t`Send`}
            {...form.getInputProps('can_send', { type: 'checkbox' })}
          />
          <Checkbox
            label={t`Administer`}
            {...form.getInputProps('can_admin', { type: 'checkbox' })}
          />
        </Group>
        <Button
          type='submit'
          loading={save.isPending}
        >{t`Save mailbox access`}</Button>
        {save.isError && (
          <Alert color='red'>{t`Could not save mailbox access.`}</Alert>
        )}
        {save.isSuccess && <Text size='sm'>{t`Mailbox access saved.`}</Text>}
      </Stack>
    </form>
  );
}

function MailboxSetup({
  microsoftShared,
  onCreated
}: { microsoftShared: boolean; onCreated: (id: string) => void }) {
  const cache = useQueryClient();
  const form = useForm({
    initialValues: {
      name: '',
      address: '',
      provider: microsoftShared ? 'graph' : 'smtp_imap',
      use_shared_microsoft: true,
      smtp_host: '',
      smtp_port: 587,
      smtp_tls: 'starttls',
      imap_host: '',
      imap_port: 993,
      username: '',
      password: '',
      tenant_id: '',
      client_id: '',
      client_secret: '',
      allowlist: '',
      signature: ''
    },
    validate: {
      name: isNotEmpty(t`Required`),
      address: isEmail(t`Enter a valid email address`)
    }
  });
  const save = useMutation({
    mutationFn: () => {
      const v = form.values;
      const shared =
        v.provider === 'graph' && microsoftShared && v.use_shared_microsoft;
      const options =
        v.provider === 'smtp_imap'
          ? {
              smtp_host: v.smtp_host,
              smtp_port: Number(v.smtp_port),
              smtp_tls: v.smtp_tls,
              imap_host: v.imap_host,
              imap_port: Number(v.imap_port)
            }
          : shared
            ? { oauth_application: 'shared' }
            : {
                client_id: v.client_id,
                ...(v.provider === 'graph' ? { tenant_id: v.tenant_id } : {})
              };
      const credentials =
        v.provider === 'smtp_imap'
          ? {
              smtp_username: v.username,
              smtp_password: v.password,
              imap_username: v.username,
              imap_password: v.password
            }
          : shared
            ? {}
            : { client_secret: v.client_secret };
      return api.post(root, {
        name: v.name,
        address: v.address,
        provider: v.provider,
        options,
        credentials,
        signature: v.signature,
        recipient_allowlist: v.allowlist
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean)
      });
    },
    onSuccess: (response) => {
      form.reset();
      onCreated(response.data.id);
      void cache.invalidateQueries({ queryKey: ['mailboxes'] });
    }
  });
  return (
    <form onSubmit={form.onSubmit(() => save.mutate())}>
      <Stack gap='sm'>
        <Text fw={600}>{t`Add agent mailbox`}</Text>
        <TextInput label={t`Name`} {...form.getInputProps('name')} />
        <TextInput
          label={t`Email address`}
          {...form.getInputProps('address')}
        />
        <Select
          label={t`Provider`}
          data={[
            { value: 'smtp_imap', label: 'SMTP + IMAP' },
            { value: 'graph', label: 'Microsoft Graph' },
            { value: 'google', label: 'Google' }
          ]}
          {...form.getInputProps('provider')}
        />
        {form.values.provider === 'graph' && microsoftShared && (
          <Checkbox
            label={t`Use AIMMS Microsoft connection`}
            {...form.getInputProps('use_shared_microsoft', {
              type: 'checkbox'
            })}
          />
        )}
        {form.values.provider === 'smtp_imap' ? (
          <>
            <TextInput
              label={t`SMTP host`}
              {...form.getInputProps('smtp_host')}
            />
            <NumberInput
              label={t`SMTP port`}
              {...form.getInputProps('smtp_port')}
            />
            <Select
              label={t`SMTP encryption`}
              data={[
                { value: 'starttls', label: 'STARTTLS' },
                { value: 'implicit', label: 'TLS' }
              ]}
              {...form.getInputProps('smtp_tls')}
            />
            <TextInput
              label={t`IMAP host`}
              {...form.getInputProps('imap_host')}
            />
            <NumberInput
              label={t`IMAP TLS port`}
              {...form.getInputProps('imap_port')}
            />
            <TextInput
              label={t`Mailbox username`}
              autoComplete='off'
              {...form.getInputProps('username')}
            />
            <PasswordInput
              label={t`Mailbox password`}
              autoComplete='new-password'
              {...form.getInputProps('password')}
            />
          </>
        ) : form.values.provider === 'graph' &&
          microsoftShared &&
          form.values.use_shared_microsoft ? (
          <Text size='sm'>{t`Save your mailbox, then sign in with Microsoft. AIMMS already has the application configuration.`}</Text>
        ) : (
          <>
            {form.values.provider === 'graph' && (
              <TextInput
                label={t`Tenant ID`}
                {...form.getInputProps('tenant_id')}
              />
            )}
            <TextInput
              label={t`OAuth client ID`}
              {...form.getInputProps('client_id')}
            />
            <PasswordInput
              label={t`OAuth client secret`}
              autoComplete='new-password'
              {...form.getInputProps('client_secret')}
            />
          </>
        )}
        <TextInput
          label={t`Allowed recipients or @domains, separated by commas`}
          description={t`An empty list blocks all sends.`}
          {...form.getInputProps('allowlist')}
        />
        <Textarea label={t`Signature`} {...form.getInputProps('signature')} />
        <Button
          type='submit'
          loading={save.isPending}
        >{t`Save disabled mailbox`}</Button>
        {save.isError && (
          <Alert color='red'>{t`Mailbox could not be saved. Check configuration and administrative access.`}</Alert>
        )}
        {save.isSuccess && (
          <Alert color='green'>{t`Mailbox saved. Select it to connect and verify.`}</Alert>
        )}
      </Stack>
    </form>
  );
}
