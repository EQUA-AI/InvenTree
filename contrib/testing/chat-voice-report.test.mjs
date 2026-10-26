import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { compileEvidence } from './chat-voice-report.mjs';

function campaign(t, assertions, extra = {}) {
  const root = mkdtempSync(join(tmpdir(), 'chat-voice-evidence-'));
  t.after(() => rmSync(root, { recursive: true }));
  writeFileSync(
    join(root, 'raw.json'),
    JSON.stringify({
      first: { count: 4 },
      replay: { count: 2 },
      playback: 'requested'
    })
  );
  const ref = (pointer, attempt = 'original') => ({
    file: 'raw.json',
    json_pointer: pointer,
    attempt_id: attempt,
    source_sha: 'fixture-source'
  });
  const make = (items = assertions) =>
    compileEvidence(
      {
        source_sha: 'fixture-source',
        cases: [
          { suite: 'voice', case_id: 'case-1', assertions: items, ...extra }
        ]
      },
      root
    );
  return { make, ref, root };
}

test('a passing replay and unverified hearing cannot erase an original required failure', (t) => {
  const { make, ref } = campaign(t);
  const report = make([
    {
      id: 'count',
      lane: 'fixture',
      expected: 2,
      evidence: [ref('/first/count'), ref('/replay/count', 'replay')]
    },
    {
      id: 'heard',
      lane: 'human',
      coverage_status: 'BLOCKED_HUMAN_PHYSICAL',
      reason: 'No listener'
    }
  ]);
  assert.equal(report.cases[0].status, 'FAIL');
  assert.equal(report.cases[0].assertions[0].observations.length, 2);
});

test('transport success cannot qualify human hearing and missing evidence is unknown', (t) => {
  const { make, ref } = campaign(t);
  const report = make([
    {
      id: 'scheduling',
      lane: 'live',
      expected: 'requested',
      evidence: [ref('/playback')]
    },
    {
      id: 'flags',
      lane: 'live',
      expected: false,
      evidence: [ref('/bom/optional')]
    },
    {
      id: 'heard',
      lane: 'human',
      coverage_status: 'UNVERIFIED',
      reason: 'No human heard record'
    }
  ]);
  assert.equal(report.cases[0].status, 'PARTIAL');
  assert.equal(report.cases[0].assertions[1].status, 'UNVERIFIED');
  assert.equal(
    Object.hasOwn(report.cases[0].assertions[1].observations[0], 'received'),
    false
  );
});

test('duplicate references do not add cases or attempts', (t) => {
  const { make, ref } = campaign(t);
  const report = make([
    {
      id: 'count',
      lane: 'fixture',
      expected: 2,
      evidence: [ref('/replay/count'), ref('/replay/count')]
    }
  ]);
  assert.equal(report.total_case_definitions, 1);
  assert.deepEqual(report.counts, { PASS: 1 });
  assert.equal(report.cases[0].assertions[0].observations.length, 1);
});

test('case presence, cross-source evidence and permissive overrides cannot create passes', (t) => {
  const { make, ref } = campaign(t, [], {
    review: {
      status: 'PASS',
      reviewer: 'reviewer',
      reason: 'Assumed',
      evidence: [{ file: 'raw.json', json_pointer: '' }]
    }
  });
  assert.throws(() => make(), /Required assertions/);
  assert.throws(
    () =>
      make([
        {
          id: 'count',
          lane: 'fixture',
          expected: 2,
          evidence: [{ ...ref('/first/count'), source_sha: 'old-source' }]
        }
      ]),
    /matching source/
  );
  assert.throws(
    () =>
      make([
        {
          id: 'count',
          lane: 'fixture',
          expected: 2,
          evidence: [ref('/first/count')]
        }
      ]),
    /cannot promote/
  );
});

test('human acceptance cannot be derived from a response.done boolean', (t) => {
  const { make, ref } = campaign(t);
  assert.throws(
    () =>
      make([
        {
          id: 'heard',
          lane: 'human',
          expected: 'requested',
          evidence: [ref('/playback')]
        }
      ]),
    /explicit reviewed record/
  );
});

test('an attributed human review is required for a human pass', (t) => {
  const { make, ref } = campaign(t);
  const report = make([
    {
      id: 'heard',
      lane: 'human',
      operator: 'reviewed_acceptance',
      expected: 'PASS',
      evidence: [ref('/playback')]
    }
  ]);
  assert.equal(report.cases[0].status, 'UNVERIFIED');
  const { make: reviewed, ref: reviewRef, root } = campaign(t);
  writeFileSync(
    join(root, 'human.json'),
    JSON.stringify({
      reviewer_type: 'human',
      reviewer: 'Fixture listener',
      reviewed_at_utc: '2026-10-26T16:00:00Z',
      outcome: 'PASS',
      heard_answer: 'Actual heard words retained by the reviewer'
    })
  );
  assert.equal(
    reviewed([
      {
        id: 'heard',
        lane: 'human',
        operator: 'reviewed_acceptance',
        expected: 'PASS',
        evidence: [{ ...reviewRef(''), file: 'human.json' }]
      }
    ]).cases[0].status,
    'PASS'
  );
  writeFileSync(
    join(root, 'human.json'),
    JSON.stringify({
      reviewer_type: 'human',
      reviewer: 'Fixture listener',
      reviewed_at_utc: '2026-10-26T16:00:00Z',
      outcome: 'FAIL',
      heard_answer: '',
      failure_reason: 'No audio was audible'
    })
  );
  assert.equal(
    reviewed([
      {
        id: 'heard',
        lane: 'human',
        operator: 'reviewed_acceptance',
        expected: 'PASS',
        evidence: [{ ...reviewRef(''), file: 'human.json' }]
      }
    ]).cases[0].status,
    'FAIL'
  );
});

test('the CLI creates a new report and refuses to overwrite retained evidence', (t) => {
  const { root, ref } = campaign(t);
  const manifest = join(root, 'manifest.json');
  const output = join(root, 'report.json');
  writeFileSync(
    manifest,
    JSON.stringify({
      source_sha: 'fixture-source',
      cases: [
        {
          suite: 'chat',
          case_id: 'fixture',
          assertions: [
            {
              id: 'count',
              lane: 'fixture',
              expected: 2,
              evidence: [ref('/replay/count')]
            }
          ]
        }
      ]
    })
  );
  const script = fileURLToPath(
    new URL('./chat-voice-report.mjs', import.meta.url)
  );
  assert.equal(
    spawnSync(process.execPath, [script, manifest, output]).status,
    0
  );
  const second = spawnSync(process.execPath, [script, manifest, output]);
  assert.notEqual(second.status, 0);
  assert.match(second.stderr.toString(), /EEXIST/);
});
