import './privacy';
import { createParser } from '@openuidev/react-lang';
import { chatLibrary } from './definitions';

const parser = createParser(chatLibrary.toJSONSchema());
const literal = String.raw`"(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"`;
const leaf = new RegExp(
  String.raw`\s*(AnswerSummary|CoverageNotice|AssetSummary|EvidenceList|MetricComparison|TrendPreview|NextSteps)\(\s*(${literal})?\s*\)\s*`,
  'y'
);
/**
 * Stage A accepts a deliberately smaller language than the renderer: one root
 * and flat, literal reference calls. Reject expressions, queries, aliases,
 * cycles, unused statements and nested graphs BEFORE the runtime sees them.
 */
export function validateProgram(program: string): boolean {
  if (new TextEncoder().encode(program).length > 65536) return false;
  // Trim once so overlapping trailing whitespace quantifiers cannot turn
  // rejection of a bounded but malformed program into quadratic backtracking.
  const outer = /^root\s*=\s*AnswerLayout\(\s*\[([\s\S]*)\]\s*\)\s*;?$/.exec(
    program.trim()
  );
  if (!outer) return false;
  const body = outer[1];
  let position = 0;
  let count = 1;
  let summaries = 0;
  while (position < body.length) {
    leaf.lastIndex = position;
    const match = leaf.exec(body);
    if (!match || ++count > 200) return false;
    const noArg = match[1] === 'AnswerSummary' || match[1] === 'CoverageNotice';
    if (noArg === (match[2] !== undefined)) return false;
    if (match[1] === 'AnswerSummary') summaries++;
    if (
      match[2] &&
      (JSON.parse(match[2]).length > 128 || !JSON.parse(match[2]).trim())
    )
      return false;
    position = leaf.lastIndex;
    if (position === body.length) break;
    if (body[position] !== ',') return false;
    position++;
    if (!body.slice(position).trim()) return false;
  }
  if (count < 2 || summaries !== 1) return false;
  try {
    const result = parser.parse(program);
    return (
      !!result.root &&
      result.root.typeName === 'AnswerLayout' &&
      !result.meta.incomplete &&
      result.meta.errors.length === 0 &&
      result.meta.unresolved.length === 0 &&
      result.meta.orphaned.length === 0 &&
      result.meta.statementCount === 1 &&
      result.queryStatements.length === 0 &&
      result.mutationStatements.length === 0 &&
      Object.keys(result.stateDeclarations).length === 0
    );
  } catch {
    return false;
  }
}
