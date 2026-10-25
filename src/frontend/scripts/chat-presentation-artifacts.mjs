import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const output = resolve(
  frontend,
  '../backend/InvenTree/ai/core/presentation/generated'
);
const versions = {
  renderer: '1.0.1',
  cli: '0.6.0',
  zod: '4.6.7',
  library: 'aimms-chat@1'
};
for (const [name, expected] of [
  ['@openuidev/react-lang', versions.renderer],
  ['@openuidev/cli', versions.cli],
  ['zod', versions.zod]
]) {
  const actual = JSON.parse(
    readFileSync(join(frontend, `node_modules/${name}/package.json`))
  ).version;
  if (actual !== expected)
    throw new Error(`Unqualified OpenUI dependency: ${name}`);
}
const scratch = mkdtempSync(join(tmpdir(), 'aimms-presentation-artifacts-'));
try {
  const prompt = join(scratch, 'chat-library.txt');
  const result = spawnSync(
    process.execPath,
    [
      'node_modules/@openuidev/cli/dist/index.js',
      '--no-telemetry',
      'generate',
      'src/components/aichat/genui/definitions.ts',
      '--export',
      'chatLibrary',
      '--prompt-options',
      'promptOptions',
      '--no-interactive',
      '--out',
      prompt
    ],
    {
      cwd: frontend,
      encoding: 'utf8',
      env: { ...process.env, DO_NOT_TRACK: '1', OPENUI_TELEMETRY_DISABLED: '1' }
    }
  );
  if (result.status !== 0)
    throw new Error(`OpenUI generation failed: ${result.stderr}`);
  const files = ['chat-library.txt', 'chat-library.spec.json'];
  const hashes = {};
  for (const name of files)
    hashes[name] = createHash('sha256')
      .update(readFileSync(join(scratch, name)))
      .digest('hex');
  writeFileSync(
    join(scratch, 'manifest.json'),
    `${JSON.stringify({ ...versions, artifacts: hashes }, null, 2)}\n`
  );
  files.push('manifest.json');
  if (!process.argv.includes('--check')) mkdirSync(output, { recursive: true });
  for (const name of files) {
    const bytes = readFileSync(join(scratch, name));
    if (process.argv.includes('--check')) {
      if (!bytes.equals(readFileSync(join(output, name))))
        throw new Error(`Presentation artifact drift: ${name}`);
    } else writeFileSync(join(output, name), bytes);
  }
  console.log(
    process.argv.includes('--check')
      ? 'Presentation artifacts are current.'
      : 'Generated packaged presentation artifacts.'
  );
} finally {
  rmSync(scratch, { recursive: true, force: true });
}
