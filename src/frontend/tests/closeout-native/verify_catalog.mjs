import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import vm from 'node:vm';
import { setupI18n } from '@lingui/core';
import { generateMessageId } from '@lingui/message-utils/generateMessageId';
import ts from 'typescript';

// Exercise the production compiler output, rather than the unit-test macro
// replacement or development-only default-message fallback.
const probes = [
  [
    'Captured: {0}',
    { 0: '2026-11-13T12:00:00Z' },
    'Captured: 2026-11-13T12:00:00Z'
  ],
  [
    'Full population unknown · revision completeness unknown',
    {},
    'Full population unknown · revision completeness unknown'
  ],
  ['One source record observed', {}, 'One source record observed'],
  [
    'Recorded gap: {start} to {end} · {0}',
    { start: '11:00Z', end: '12:00Z', 0: 'source_unavailable' },
    'Recorded gap: 11:00Z to 12:00Z · source_unavailable'
  ],
  [
    'Recorded history · {0} samples returned',
    { 0: 3 },
    'Recorded history · 3 samples returned'
  ],
  [
    'Recorded history · 1 sample returned',
    {},
    'Recorded history · 1 sample returned'
  ],
  [
    'Recorded window (UTC): {start} to {end} (end excluded)',
    { start: '11:00Z', end: '12:00Z' },
    'Recorded window (UTC): 11:00Z to 12:00Z (end excluded)'
  ],
  ['Selected window complete', {}, 'Selected window complete'],
  ['Selected window incomplete', {}, 'Selected window incomplete'],
  ['Source observation', {}, 'Source observation'],
  [
    'Source unavailable under current access or source state.',
    {},
    'Source unavailable under current access or source state.'
  ],
  ['Synthetic fixture', {}, 'Synthetic fixture']
];
const config = JSON.parse(await readFile('.linguirc', 'utf8'));
const checks = [];
for (const locale of config.locales) {
  const filename = path.resolve(`src/locales/${locale}/messages.ts`);
  const source = await readFile(filename, 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS }
  });
  const sandbox = { exports: {} };
  vm.runInNewContext(compiled.outputText, sandbox, { filename });
  const messages = sandbox.exports.messages;
  const i18n = setupI18n({ locale, messages: { [locale]: messages } });
  for (const [message, values, english] of probes) {
    const id = generateMessageId(message);
    assert(
      Object.hasOwn(messages, id),
      `${locale} lacks compiled source observation key: ${message}`
    );
    const result = i18n._(id, values);
    assert.notEqual(result, id, `${locale} shows an opaque message ID`);
    if (locale === 'en') assert.equal(result, english);
  }
  checks.push({
    locale,
    sourceObservationKeys: probes.length,
    outcome: 'passed'
  });
}
await writeFile(
  path.join(process.env.AIMMS_BUILD_RUNTIME, 'catalog-results.json'),
  `${JSON.stringify({ checks, englishExact: true, otherLocales: 'Existing translations or configured source-locale fallback; no new translations claimed.' }, null, 2)}\n`
);
