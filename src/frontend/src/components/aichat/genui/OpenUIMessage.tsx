import './privacy';
import { t } from '@lingui/core/macro';
import { Box, Stack, Table, Text } from '@mantine/core';
import { Renderer } from '@openuidev/react-lang';
import {
  Component,
  type ReactNode,
  createContext,
  useContext,
  useMemo,
  useState
} from 'react';
import { EntityChips } from '../EntityChips';
import { EvidenceChips } from '../EvidenceChips';
import { ChartSnapshot } from './ChartSnapshot';
import type { RichMessageProps } from './RichMessage';
import { TrendSnapshot } from './TrendSnapshot';
import { createChatLibrary } from './definitions';
import { normalizePresentation } from './presentation';
import { validateProgram } from './program';

const Context = createContext<RichMessageProps | null>(null);
function useAnswer() {
  const value = useContext(Context);
  if (!value) throw new Error('presentation context unavailable');
  return value;
}
function Unavailable() {
  return <Text size='sm' c='dimmed'>{t`Referenced data is unavailable.`}</Text>;
}
function Entity({ reference }: { reference?: string }) {
  const { message } = useAnswer();
  const ref = message.presentation?.references.find(
    (entry) => entry.ref_id === reference && entry.kind === 'entity'
  );
  const entity = message.entities?.find(
    (entry) => ref?.source_key === `entity:${entry.model}:${entry.pk}`
  );
  return entity ? <EntityChips entities={[entity]} /> : <Unavailable />;
}
function Evidence({ reference }: { reference?: string }) {
  const { message } = useAnswer();
  const ref = message.presentation?.references.find(
    (entry) => entry.ref_id === reference && entry.kind === 'evidence'
  );
  const item = message.mediaEvidence?.find(
    (entry) =>
      ref?.source_key ===
      `media:${entry.attachment_id}:${entry.segment_index ?? 'None'}`
  );
  return item ? <EvidenceChips items={[item]} /> : <Unavailable />;
}
function CoverageMetrics({ reference }: { reference?: string }) {
  const { message } = useAnswer();
  const ref = message.presentation?.references.find(
    (entry) =>
      entry.ref_id === reference &&
      entry.kind === 'metric_dataset' &&
      entry.source_key === 'coverage'
  );
  const coverage = message.evidenceAnalysis?.coverage;
  if (
    !ref ||
    !coverage ||
    ![coverage.population_count, coverage.returned_count].every(
      (value) => Number.isSafeInteger(value) && value >= 0
    )
  )
    return <Unavailable />;
  return (
    <Box mt='sm' data-testid='presentation-metrics'>
      <Text size='sm' fw={600}>{t`Retrieval snapshot`}</Text>
      <Text size='xs' c='dimmed'>
        {t`Source: recorded evidence coverage`} ·{' '}
        {coverage.as_of || t`Observation time unavailable`}
      </Text>
      <Table captionSide='bottom'>
        <Table.Caption>{t`Counts describe the recorded search, not current equipment condition.`}</Table.Caption>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>{t`Measure`}</Table.Th>
            <Table.Th>{t`Count`}</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          <Table.Tr>
            <Table.Td>{t`Population size`}</Table.Td>
            <Table.Td>{coverage.population_count}</Table.Td>
          </Table.Tr>
          <Table.Tr>
            <Table.Td>{t`Entries returned`}</Table.Td>
            <Table.Td>{coverage.returned_count}</Table.Td>
          </Table.Tr>
        </Table.Tbody>
      </Table>
    </Box>
  );
}
function Summary() {
  return useAnswer().narrative;
}
function Qualifications() {
  return useAnswer().qualifications;
}
function Trend({ reference }: { reference?: string }) {
  const { message } = useAnswer();
  const ref = message.presentation?.references.find(
    (entry) => entry.ref_id === reference && entry.kind === 'trend_dataset'
  );
  const data = message.presentationData?.find(
    (entry) => entry.source_key === ref?.source_key
  );
  return data?.kind === 'trend_dataset' ? (
    <TrendSnapshot data={data} />
  ) : (
    <Unavailable />
  );
}
function Chart({ reference }: { reference?: string }) {
  const { message } = useAnswer();
  const ref = message.presentation?.references.find(
    (entry) => entry.ref_id === reference && entry.kind === 'chart_dataset'
  );
  const data = message.presentationData?.find(
    (entry) => entry.source_key === ref?.source_key
  );
  return data?.kind === 'chart_dataset' ? (
    <ChartSnapshot data={data} />
  ) : (
    <Unavailable />
  );
}
// Definitions and render functions are stable; historical renders do not fetch
// fresh data. No generic action, form, Query/Mutation or toolProvider is exposed.
const renderers = {
  AnswerSummary: () => <Summary />,
  CoverageNotice: () => <Qualifications />,
  AssetSummary: (reference: string | undefined) => (
    <Entity reference={reference} />
  ),
  EvidenceList: (reference: string | undefined) => (
    <Evidence reference={reference} />
  ),
  MetricComparison: (reference: string | undefined) => (
    <CoverageMetrics reference={reference} />
  ),
  TrendPreview: (reference: string | undefined) => (
    <Trend reference={reference} />
  ),
  ChartSnapshot: (reference: string | undefined) => (
    <Chart reference={reference} />
  ),
  NextSteps: (reference: string | undefined) => <Entity reference={reference} />
};
const library = createChatLibrary(renderers, 2);
const historicalLibrary = createChatLibrary(renderers, 1);
class RenderBoundary extends Component<
  { fallback: ReactNode; children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}
function ValidatedMessage(props: RichMessageProps & { fallback: ReactNode }) {
  const [failed, setFailed] = useState(false);
  const presentation = useMemo(
    () => normalizePresentation(props.message.presentation),
    [props.message.presentation]
  );
  const valid = useMemo(
    () =>
      presentation &&
      validateProgram(presentation.program, presentation.library),
    [presentation]
  );
  if (!valid || !presentation || failed) return props.fallback;
  return (
    <Context.Provider value={props}>
      <Stack gap='xs' data-testid='openui-answer'>
        <Renderer
          library={
            presentation.library === 'aimms-chat@1'
              ? historicalLibrary
              : library
          }
          response={presentation.program}
          isStreaming={false}
          publishObservability={false}
          onError={(errors) => {
            if (errors.length) setFailed(true);
          }}
        />
        {/* Older deterministic layouts may omit coverage; qualifications are
          never lost merely because a presentation omitted its slot. */}
        {!presentation.program.includes('CoverageNotice()') &&
          props.qualifications}
      </Stack>
    </Context.Provider>
  );
}
export default function OpenUIMessage(
  props: RichMessageProps & { fallback: ReactNode }
) {
  return (
    <RenderBoundary
      key={`${props.message.id}:${props.message.presentation?.program}`}
      fallback={props.fallback}
    >
      <ValidatedMessage {...props} />
    </RenderBoundary>
  );
}
