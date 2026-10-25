import { describe, expect, it } from 'vitest';
import { normalizePresentation } from './presentation';
import { validateProgram } from './program';

const envelope = (
  program = 'root = AnswerLayout([AnswerSummary(),AssetSummary("r0")])'
) => ({
  schema_version: 1,
  format: 'openui-lang',
  library: 'aimms-chat@1',
  program,
  references: [
    { ref_id: 'r0', kind: 'entity', source_key: 'entity:assetmachine:7' }
  ]
});
describe('terminal read-only presentation boundary', () => {
  it('round trips escaped language literals through the pinned parser', () => {
    for (const value of [
      'r0',
      'quote"\\line\n机器',
      ']) ; Query("private")',
      'emoji 🛠'
    ]) {
      expect(
        validateProgram(
          `root = AnswerLayout([AnswerSummary(),AssetSummary(${JSON.stringify(value)})])`
        )
      ).toBe(true);
    }
  });
  it.each([
    'root = AnswerLayout([Unknown()])',
    'root = AnswerLayout([AnswerSummary(),Query("secret")])',
    'root = AnswerLayout([AnswerSummary(),Mutation("write")])',
    'root = AnswerLayout([AnswerSummary(),AssetSummary($state)])',
    'root = AnswerLayout([AnswerSummary(),AnswerLayout([AnswerSummary()])])',
    'root = loop; loop = root',
    'root = AnswerLayout([AnswerSummary()]); extra = Query("secret")',
    'root = AnswerLayout([AssetSummary("r0")])',
    'root = AnswerLayout([AnswerSummary(),AnswerSummary()])',
    'root = AnswerLayout([AnswerSummary(),AssetSummary("r0")',
    `root = AnswerLayout([AnswerSummary()])${' '.repeat(30000)}invalid`,
    `root = AnswerLayout([AnswerSummary(),${Array(200).fill('AssetSummary("r0")').join(',')}])`
  ])('rejects unsupported, incomplete or excessive graphs: %s', (program) => {
    expect(validateProgram(program)).toBe(false);
  });
  it('rejects unsupported envelopes, duplicate references and byte overflow', () => {
    const valid = envelope();
    expect(normalizePresentation(valid)).toEqual(valid);
    for (const invalid of [
      null,
      [],
      { ...valid, schema_version: true },
      { ...valid, library: 'aimms-chat@99' },
      { ...valid, program: '机'.repeat(22000) },
      { ...valid, references: [...valid.references, ...valid.references] },
      { ...valid, extra: 'private' },
      { ...valid, references: Array(101).fill(valid.references[0]) }
    ]) {
      expect(normalizePresentation(invalid)).toBeNull();
    }
    const normalized = normalizePresentation(valid)!;
    valid.references[0].source_key = 'changed';
    expect(normalized.references[0].source_key).toBe('entity:assetmachine:7');
  });
});
