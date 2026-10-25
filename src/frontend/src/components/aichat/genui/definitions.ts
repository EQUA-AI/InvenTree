import './privacy';
import { createLibrary, defineComponent } from '@openuidev/react-lang';
import type { ReactNode } from 'react';
import { z } from 'zod/v4';

// Property order is the versioned positional API. This module is also the
// Node-safe CLI entry: no app, browser, authorization, or network imports.
type LeafName =
  | 'AnswerSummary'
  | 'AssetSummary'
  | 'EvidenceList'
  | 'CoverageNotice'
  | 'MetricComparison'
  | 'TrendPreview'
  | 'ChartSnapshot'
  | 'NextSteps';
type Renderers = {
  [Name in LeafName]: (reference?: string) => ReactNode;
};
export function createChatLibrary(renderers: Renderers, version: 1 | 2 = 2) {
  const summary = defineComponent({
    name: 'AnswerSummary',
    description: 'The canonical readable answer. Never supplies new facts.',
    props: z.object({}),
    component: () => renderers.AnswerSummary()
  });
  const coverage = defineComponent({
    name: 'CoverageNotice',
    description: 'The canonical evidence qualifications and coverage.',
    props: z.object({}),
    component: () => renderers.CoverageNotice()
  });
  const referenced = (
    name: Exclude<LeafName, 'AnswerSummary' | 'CoverageNotice'>
  ) =>
    defineComponent({
      name,
      description:
        'Resolve only an authorized, historical server reference. Missing data is unavailable.',
      props: z.object({ reference: z.string().min(1).max(128) }),
      component: ({ props }) => renderers[name](props.reference)
    });
  const asset = referenced('AssetSummary');
  const evidence = referenced('EvidenceList');
  const metrics = referenced('MetricComparison');
  const trend = referenced('TrendPreview');
  const chart = referenced('ChartSnapshot');
  const next = referenced('NextSteps');
  const layout = defineComponent({
    name: 'AnswerLayout',
    description: 'A bounded sequence of read-only answer sections.',
    props: z.object({
      children: z
        .array(
          z.union([
            summary.ref,
            asset.ref,
            evidence.ref,
            coverage.ref,
            metrics.ref,
            trend.ref,
            ...(version === 2 ? [chart.ref] : []),
            next.ref
          ])
        )
        .min(1)
        .max(199)
    }),
    component: ({ props, renderNode }) => renderNode(props.children)
  });
  return createLibrary({
    id: `aimms-chat@${version}`,
    root: 'AnswerLayout',
    components: [
      layout,
      summary,
      asset,
      evidence,
      coverage,
      metrics,
      trend,
      ...(version === 2 ? [chart] : []),
      next
    ]
  });
}
const empty = () => null;
export const chatLibrary = createChatLibrary({
  AnswerSummary: empty,
  AssetSummary: empty,
  EvidenceList: empty,
  CoverageNotice: empty,
  MetricComparison: empty,
  TrendPreview: empty,
  ChartSnapshot: empty,
  NextSteps: empty
});
export const historicalChatLibrary = createChatLibrary(
  {
    AnswerSummary: empty,
    AssetSummary: empty,
    EvidenceList: empty,
    CoverageNotice: empty,
    MetricComparison: empty,
    TrendPreview: empty,
    ChartSnapshot: empty,
    NextSteps: empty
  },
  1
);
export const promptOptions = {
  toolCalls: false,
  bindings: false,
  builtinFunctions: false,
  additionalRules: [
    'Stage A accepts one flat root = AnswerLayout([...]) statement only.',
    'Include AnswerSummary() exactly once. Use only literal server reference ids.',
    'Never create facts, URLs, queries, mutations, forms, expressions or state.'
  ]
};
