import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import { t } from '@lingui/core/macro';
import {
  Alert, Badge, Button, FileInput, Group, Loader, Modal, Pagination,
  Select, Stack, Table, Tabs, Text, Textarea, TextInput, Title
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../../App';
import { showApiErrorMessage } from '../../functions/notifications';
import { useUserState } from '../../states/UserState';

type Equipment = { pk: number; uuid: string; name: string; parent: number | null; source_key: string; source_entity_uuid: string | null };
type Component = { pk: number; uuid: string; machine: number; machine_name: string; part: number; part_name: string; virtual: boolean; code: string; name: string; status: string; provenance: string; review_note: string };
type Point = { pk: number; uuid: string; machine: number; machine_name: string; path: string; raw_tag: string; display_name: string; component: number | null; component_name: string | null; template: number | null; template_name: string | null; match_method: string; issue: string; data_type: string; unit: string; unit_status: string; status: string; review_note: string };
type PreviewPoint = { path: string; raw_tag: string; owner_key: string; part_name: string; template_name: string; match_method: string; unit: string; unit_status: string; issue: string; existing_status: string | null };
type Preview = { station: number; source_hash: string; pumps: string[]; counts: Record<string, number>; points: PreviewPoint[] };
type Page<T> = { count: number; results: T[] };
type Options = { clients: { pk: number; name: string }[]; parts: { pk: number; name: string; IPN: string; category: number }[]; templates: { pk: number; name: string; units: string }[]; assignments: { category_id: number; template_id: number }[] };

const root = apiUrl(ApiEndpoints.equipment_registry);
const choices = (items: { pk: number; name: string }[]) => items.map((x) => ({ value: String(x.pk), label: x.name }));

export default function EquipmentRegistry() {
  const user = useUserState();
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const stationId = params.get('station') ?? '';
  const ownerId = params.get('owner') ?? '';
  const selectedId = ownerId || stationId;
  const [tab, setTab] = useState<string | null>('dictionary');
  const [status, setStatus] = useState<string | null>(null);
  const [componentFilter, setComponentFilter] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewPage, setPreviewPage] = useState(1);
  const [registerOpen, setRegisterOpen] = useState(false);
  const [componentOpen, setComponentOpen] = useState(false);
  const [pumpKey, setPumpKey] = useState('');
  const [reviewPoint, setReviewPoint] = useState<Point | null>(null);
  const [reviewComponent, setReviewComponent] = useState<Component | null>(null);
  const [componentNote, setComponentNote] = useState('');
  const canAdd = user.hasAddRole(UserRoles.work_order);
  const canChange = user.hasChangeRole(UserRoles.work_order);
  const stations = useQuery({ queryKey: ['registry', 'stations'], queryFn: async () => (await api.get<Page<Equipment>>(root, { params: { limit: 500 } })).data });
  const options = useQuery({ queryKey: ['registry', 'options'], queryFn: async () => (await api.get<Options>(`${root}options/`)).data });
  const hierarchy = useQuery({ queryKey: ['registry', 'hierarchy', stationId], enabled: !!stationId, queryFn: async () => (await api.get<{ equipment: Equipment; children: Equipment[] }>(`${root}${stationId}/`)).data });
  const components = useQuery({ queryKey: ['registry', 'components', selectedId], enabled: !!selectedId, queryFn: async () => (await api.get<Page<Component>>(`${root}${selectedId}/components/`, { params: { limit: 500 } })).data });
  const points = useQuery({ queryKey: ['registry', 'points', selectedId, page, status, componentFilter, search], enabled: !!selectedId, queryFn: async () => (await api.get<Page<Point>>(`${root}${selectedId}/dictionary/`, { params: { limit: 50, offset: (page - 1) * 50, status: status || undefined, component_id: componentFilter || undefined, search } })).data });
  const stationForm = useForm({ initialValues: { name: '', client: '', source_namespace: '', source_entity_uuid: '', source_key: '' }, validate: { name: (v) => !v.trim() ? t`Required` : null, client: (v) => !v ? t`Required` : null, source_namespace: (v) => !v ? t`Required` : null, source_entity_uuid: (v) => !v ? t`Required` : null, source_key: (v) => !v ? t`Required` : null } });
  const componentForm = useForm({ initialValues: { part: '', code: '', name: '' }, validate: { part: (v) => !v ? t`Required` : null, code: (v) => !v ? t`Required` : null, name: (v) => !v ? t`Required` : null } });
  const review = useForm({ initialValues: { machine: '', component: '', template: '', display_name: '', data_type: 'unknown', unit: '', unit_status: 'unresolved', status: 'draft', review_note: '' } });
  const reviewComponents = useQuery({ queryKey: ['registry', 'review-components', review.values.machine], enabled: !!reviewPoint && !!review.values.machine, queryFn: async () => (await api.get<Page<Component>>(`${root}${review.values.machine}/components/`, { params: { limit: 500 } })).data });
  const write = useMutation({ mutationFn: async ({ url, data, method = 'post' }: { url: string; data: unknown; method?: 'post' | 'patch' }) => (await api.request({ url, method, data, timeout: 60000 })).data });
  const previewRequest = useMutation({ mutationFn: async (data: FormData) => (await api.post<Preview>(`${root}${stationId}/preview/`, data, { timeout: 60000 })).data });
  const busy = write.isPending || previewRequest.isPending;
  const equipment = hierarchy.data ? [hierarchy.data.equipment, ...hierarchy.data.children] : [];
  const selectedComponent = reviewComponents.data?.results.find((c) => String(c.pk) === review.values.component);
  const selectedPart = options.data?.parts.find((p) => p.pk === selectedComponent?.part);
  const templateIds = options.data?.assignments.filter((a) => a.category_id === selectedPart?.category).map((a) => a.template_id) ?? [];

  async function save(url: string, data: unknown, done?: (value: any) => void, method: 'post' | 'patch' = 'post') {
    try {
      const result = await write.mutateAsync({ url, data, method });
      await client.invalidateQueries({ queryKey: ['registry'] });
      notifications.show({ color: 'green', message: t`Saved successfully` });
      done?.(result);
    } catch (error) {
      showApiErrorMessage({ error, title: t`Registry update failed` });
    }
  }

  function selectStation(value: string | null) {
    setParams(value ? { station: value } : {});
    setPreview(null); setFile(null); setPage(1); setComponentFilter(null);
  }

  function selectOwner(value: string | null) {
    setParams({ station: stationId, ...(value ? { owner: value } : {}) });
    setPage(1); setComponentFilter(null);
  }

  async function previewFile() {
    if (!file) return;
    if (file.size > 8 * 1024 * 1024) {
      notifications.show({ color: 'red', message: t`JSON files must be no larger than 8 MiB` });
      return;
    }
    const data = new FormData(); data.append('data_file', file);
    setPreview(null);
    try { setPreview(await previewRequest.mutateAsync(data)); setPreviewPage(1); }
    catch (error) { showApiErrorMessage({ error, title: t`Preview failed` }); }
  }

  function importFile() {
    if (!file || !preview || preview.station !== Number(stationId)) return;
    const data = new FormData(); data.append('data_file', file); data.append('source_hash', preview.source_hash);
    void save(`${root}${stationId}/import/`, data, (result) => {
      setPreview(null); setTab('dictionary'); setPage(1);
      notifications.show({ message: t`Created ${result.created} points; preserved ${result.preserved} existing points` });
    });
  }

  function openReview(point: Point) {
    setReviewPoint(point);
    review.setValues({ machine: String(point.machine), component: point.component ? String(point.component) : '', template: point.template ? String(point.template) : '', display_name: point.display_name, data_type: point.data_type, unit: point.unit, unit_status: point.unit_status, status: point.status, review_note: point.review_note });
  }

  const error = stations.error || options.error || hierarchy.error || components.error || points.error;
  return <Stack p='md'>
    <Group justify='space-between'>
      <Title order={2}>{t`Equipment Registry`}</Title>
      <Group><Button component={Link} to='/machines/index/' variant='default'>{t`Machines`}</Button><Button disabled={!canAdd || busy} onClick={() => setRegisterOpen(true)}>{t`Register station`}</Button></Group>
    </Group>
    <Alert color='blue'>{t`Offline registry only. Inferred components are drafts, not verified installations. Mapping approval does not enable live ingestion. Uploaded readings are not stored as live values.`}</Alert>
    {error && <Alert color='red'>{t`Registry could not be loaded. Check your work-order permissions and configured Client scope, then retry.`}<Button variant='subtle' onClick={() => client.invalidateQueries({ queryKey: ['registry'] })}>{t`Retry`}</Button></Alert>}
    {stations.isLoading && <Loader />}
    <Group grow>
      <Select label={t`Pump station`} searchable clearable value={stationId || null} data={choices(stations.data?.results ?? [])} onChange={selectStation} disabled={busy} />
      <Select label={t`Equipment owner`} searchable clearable placeholder={t`Station and all pumps`} value={ownerId || null} data={choices(equipment)} onChange={selectOwner} disabled={!stationId || busy} />
    </Group>
    {hierarchy.data && <>
      <Text size='sm'>{t`Station UUID`}: {hierarchy.data.equipment.uuid}</Text>
      <Text size='sm'>{t`Source entity UUID`}: {hierarchy.data.equipment.source_entity_uuid}</Text>
      <Tabs value={tab} onChange={setTab}>
        <Tabs.List><Tabs.Tab value='equipment'>{t`Equipment`}</Tabs.Tab><Tabs.Tab value='components'>{t`Components`}</Tabs.Tab><Tabs.Tab value='dictionary'>{t`Dictionary and review`}</Tabs.Tab><Tabs.Tab value='import'>{t`Preview and import`}</Tabs.Tab></Tabs.List>
        <Tabs.Panel value='equipment' pt='md'><Stack>
          <Group><TextInput label={t`New pump source key`} placeholder='P15' value={pumpKey} onChange={(e) => setPumpKey(e.currentTarget.value)} /><Button mt='lg' disabled={!canAdd || !pumpKey || busy} onClick={() => save(`${root}${stationId}/`, { source_key: pumpKey }, () => setPumpKey(''))}>{t`Register pump slot`}</Button></Group>
          <Table.ScrollContainer minWidth={650}><Table><Table.Thead><Table.Tr><Table.Th>{t`Equipment`}</Table.Th><Table.Th>{t`Source key`}</Table.Th><Table.Th>{t`UUID`}</Table.Th></Table.Tr></Table.Thead><Table.Tbody>{equipment.map((item) => <Table.Tr key={item.pk}><Table.Td><Button variant='subtle' onClick={() => { selectOwner(String(item.pk)); setTab('dictionary'); }}>{item.name}</Button></Table.Td><Table.Td>{item.source_key}</Table.Td><Table.Td>{item.uuid}</Table.Td></Table.Tr>)}</Table.Tbody></Table></Table.ScrollContainer>
        </Stack></Tabs.Panel>
        <Tabs.Panel value='components' pt='md'><Stack>
          <Group justify='space-between'><Text>{t`Identifiable component occurrences`}</Text><Button disabled={!canAdd || busy} onClick={() => setComponentOpen(true)}>{t`Add component occurrence`}</Button></Group>
          <Text size='sm'>{t`Choose an equipment owner above before adding a component. Leaving it empty assigns the component to the station.`}</Text>
          <Table.ScrollContainer minWidth={750}><Table><Table.Thead><Table.Tr>{[t`Owner`, t`Component`, t`Catalogue part`, t`Status`, t`Actions`].map((name) => <Table.Th key={name}>{name}</Table.Th>)}</Table.Tr></Table.Thead><Table.Tbody>{components.data?.results.map((c) => <Table.Tr key={c.pk}><Table.Td>{c.machine_name}</Table.Td><Table.Td>{c.code}: {c.name}<Text size='xs'>{c.uuid}</Text></Table.Td><Table.Td>{c.part_name}{c.virtual && <Badge ml='xs'>{t`Logical group`}</Badge>}</Table.Td><Table.Td><Badge color={c.status === 'verified' ? 'green' : 'yellow'}>{c.status}</Badge></Table.Td><Table.Td><Button size='xs' disabled={!canChange} onClick={() => { setReviewComponent(c); setComponentNote(c.review_note); }}>{t`Review`}</Button></Table.Td></Table.Tr>)}</Table.Tbody></Table></Table.ScrollContainer>
          {!!components.data && components.data.count > 500 && <Alert>{t`Select a pump to narrow the component list. Showing the first 500 occurrences.`}</Alert>}
        </Stack></Tabs.Panel>
        <Tabs.Panel value='dictionary' pt='md'><Stack>
          <Group grow><TextInput label={t`Search tags`} value={search} onChange={(e) => { setSearch(e.currentTarget.value); setPage(1); }} /><Select label={t`Mapping status`} clearable data={['draft', 'approved', 'unresolved', 'rejected']} value={status} onChange={(v) => { setStatus(v); setPage(1); }} /><Select label={t`Component`} clearable searchable data={(components.data?.results ?? []).map((c) => ({ value: String(c.pk), label: `${c.machine_name}: ${c.code}` }))} value={componentFilter} onChange={(v) => { setComponentFilter(v); setPage(1); }} /></Group>
          {points.isFetching && <Loader size='sm' />}
          <Text size='sm'>{t`Dictionary points`}: {points.data?.count ?? 0}</Text>
          <Table.ScrollContainer minWidth={950}><Table striped><Table.Thead><Table.Tr>{[t`Source tag / path`, t`Owner / component`, t`Parameter`, t`Units`, t`Status`, t`Actions`].map((label) => <Table.Th key={label}>{label}</Table.Th>)}</Table.Tr></Table.Thead><Table.Tbody>{points.data?.results.map((point) => <Table.Tr key={point.pk}><Table.Td>{point.raw_tag}<Text size='xs'>{point.path}</Text>{point.issue && <Text size='xs' c='orange'>{point.issue}</Text>}</Table.Td><Table.Td>{point.machine_name}<Text size='xs'>{point.component_name ?? '—'}</Text></Table.Td><Table.Td>{point.template_name ?? t`Unresolved`}<Text size='xs'>{point.match_method}</Text></Table.Td><Table.Td>{point.unit || '—'}<Text size='xs'>{point.unit_status}</Text></Table.Td><Table.Td><Badge color={point.status === 'approved' ? 'green' : 'yellow'}>{point.status}</Badge></Table.Td><Table.Td><Button size='xs' disabled={!canChange || busy} onClick={() => openReview(point)}>{t`Review mapping`}</Button></Table.Td></Table.Tr>)}</Table.Tbody></Table></Table.ScrollContainer>
          <Pagination total={Math.max(1, Math.ceil((points.data?.count ?? 0) / 50))} value={page} onChange={setPage} />
        </Stack></Tabs.Panel>
        <Tabs.Panel value='import' pt='md'><Stack>
          <Alert>{t`Select the authoritative station before uploading. Accepts a pd/dex payload, a Cassandra row with data1, or up to 100 rows. Maximum 8 MiB and 25,000 distinct points. Preview performs no equipment/dictionary writes. Existing mappings are preserved during import.`}</Alert>
          <FileInput label={t`Pumphouse JSON file`} accept='.json,application/json' value={file} clearable disabled={busy} onChange={(value) => { setFile(value); setPreview(null); }} />
          <Group><Button loading={previewRequest.isPending} disabled={!file || busy} onClick={previewFile}>{t`Preview dictionary`}</Button><Button color='green' disabled={!preview || !canAdd || !canChange || busy} loading={write.isPending} onClick={importFile}>{t`Import draft mappings`}</Button></Group>
          {preview && <><Group>{Object.entries(preview.counts).map(([key, value]) => <Badge key={key}>{key}: {value}</Badge>)}</Group><Text>{t`Pump slots`}: {preview.pumps.join(', ')}</Text><Table.ScrollContainer minWidth={850}><Table><Table.Thead><Table.Tr>{[t`Raw tag`, t`Owner`, t`Proposed part / parameter`, t`Result`].map((label) => <Table.Th key={label}>{label}</Table.Th>)}</Table.Tr></Table.Thead><Table.Tbody>{preview.points.slice((previewPage - 1) * 100, previewPage * 100).map((p) => <Table.Tr key={p.path}><Table.Td>{p.raw_tag}<Text size='xs'>{p.path}</Text></Table.Td><Table.Td>{p.owner_key || t`Station`}</Table.Td><Table.Td>{p.part_name || '—'}<Text size='xs'>{p.template_name}</Text></Table.Td><Table.Td>{p.existing_status ? t`Preserved` : p.match_method}<Text size='xs' c='orange'>{p.issue}</Text></Table.Td></Table.Tr>)}</Table.Tbody></Table></Table.ScrollContainer><Pagination total={Math.max(1, Math.ceil(preview.points.length / 100))} value={previewPage} onChange={setPreviewPage} /></>}
        </Stack></Tabs.Panel>
      </Tabs>
    </>}
    {!stationId && <Text>{t`Select or register a station to manage pump slots and review its dictionary.`}</Text>}
    <Modal opened={registerOpen} onClose={() => !busy && setRegisterOpen(false)} title={t`Register pump station`}>
      <form onSubmit={stationForm.onSubmit((values) => save(root, { ...values, client: Number(values.client) }, (value) => { setRegisterOpen(false); stationForm.reset(); selectStation(String(value.pk)); }))}><Stack>
        <TextInput label={t`Display name`} {...stationForm.getInputProps('name')} />
        <Select label={t`Authorized Client`} data={choices(options.data?.clients ?? [])} {...stationForm.getInputProps('client')} />
        <TextInput label={t`Source namespace`} placeholder='cassandra-scada' {...stationForm.getInputProps('source_namespace')} />
        <TextInput label={t`Source entity UUID`} {...stationForm.getInputProps('source_entity_uuid')} />
        <TextInput label={t`Source code`} placeholder='PH_3' {...stationForm.getInputProps('source_key')} />
        <Button type='submit' loading={busy}>{t`Register station`}</Button>
      </Stack></form>
    </Modal>
    <Modal opened={componentOpen} onClose={() => !busy && setComponentOpen(false)} title={t`Add component occurrence`}>
      <form onSubmit={componentForm.onSubmit((values) => save(`${root}${selectedId}/components/`, { ...values, part: Number(values.part) }, () => { setComponentOpen(false); componentForm.reset(); }))}><Stack>
        <Select label={t`Catalogue part`} searchable data={(options.data?.parts ?? []).map((p) => ({ value: String(p.pk), label: `${p.IPN}: ${p.name}` }))} {...componentForm.getInputProps('part')} />
        <TextInput label={t`Occurrence code`} placeholder='MOTOR-02' {...componentForm.getInputProps('code')} /><TextInput label={t`Display name`} {...componentForm.getInputProps('name')} />
        <Button type='submit' loading={busy}>{t`Create draft occurrence`}</Button>
      </Stack></form>
    </Modal>
    <Modal opened={!!reviewComponent} onClose={() => !busy && setReviewComponent(null)} title={t`Review component assignment`}><Stack>
      <Text>{reviewComponent?.machine_name}: {reviewComponent?.name}</Text><Text size='sm'>{reviewComponent?.provenance}</Text>
      <Textarea label={t`Review note`} value={componentNote} onChange={(e) => setComponentNote(e.currentTarget.value)} />
      <Group>{['draft', 'verified'].map((value) => <Button key={value} disabled={!componentNote.trim() || busy} onClick={() => reviewComponent && save(`${root}${reviewComponent.machine}/components/${reviewComponent.pk}/review/`, { status: value, review_note: componentNote }, () => setReviewComponent(null), 'patch')}>{value === 'verified' ? t`Verify assignment` : t`Keep draft`}</Button>)}</Group>
    </Stack></Modal>
    <Modal opened={!!reviewPoint} onClose={() => !busy && setReviewPoint(null)} title={t`Review dictionary mapping`} size='lg'>
      <form onSubmit={review.onSubmit((values) => {
        if (!reviewPoint) return;
        return save(`${root}${stationId}/dictionary/${reviewPoint.pk}/review/`, { ...values, machine: Number(values.machine), component: values.component ? Number(values.component) : null, template: values.template ? Number(values.template) : null }, () => setReviewPoint(null), 'patch');
      })}><Stack>
        <Text size='sm'>{reviewPoint?.raw_tag}</Text><Text size='xs'>{reviewPoint?.path}</Text>
        <Select label={t`Equipment owner`} data={choices(equipment)} value={review.values.machine} onChange={(value) => review.setValues({ machine: value ?? '', component: '', template: '' })} />
        <Select label={t`Component occurrence`} searchable clearable data={(reviewComponents.data?.results ?? []).filter((c) => String(c.machine) === review.values.machine).map((c) => ({ value: String(c.pk), label: `${c.code}: ${c.name}` }))} value={review.values.component || null} onChange={(value) => review.setValues({ component: value ?? '', template: '' })} />
        <Select label={t`Parameter definition`} searchable clearable data={(options.data?.templates ?? []).filter((p) => templateIds.includes(p.pk)).map((p) => ({ value: String(p.pk), label: p.name }))} {...review.getInputProps('template')} />
        <TextInput label={t`Display name`} {...review.getInputProps('display_name')} />
        <Group grow><Select label={t`Data type`} data={['number', 'status', 'boolean', 'text', 'unknown']} {...review.getInputProps('data_type')} /><TextInput label={t`Source unit`} {...review.getInputProps('unit')} /></Group>
        <Group grow><Select label={t`Unit review`} data={['verified', 'unitless', 'proposed', 'unresolved']} {...review.getInputProps('unit_status')} /><Select label={t`Mapping status`} data={['draft', 'approved', 'unresolved', 'rejected']} {...review.getInputProps('status')} /></Group>
        <Textarea label={t`Review note`} {...review.getInputProps('review_note')} />
        <Text size='xs'>{t`Approval requires a mapped component and parameter, confirmed units/type and a review note. Confirm engineering information; do not infer it from sample values.`}</Text>
        <Button type='submit' loading={busy}>{t`Save review`}</Button>
      </Stack></form>
    </Modal>
  </Stack>;
}
