/** Read-only evidence compilation. Run with a manifest and a NEW output path. */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync, realpathSync, writeFileSync } from 'node:fs';
import { dirname, isAbsolute, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { isDeepStrictEqual } from 'node:util';

function atPointer(value, pointer) {
  assert(
    typeof pointer === 'string' && (pointer === '' || pointer.startsWith('/')),
    'Invalid JSON pointer'
  );
  for (const token of pointer === '' ? [] : pointer.slice(1).split('/')) {
    const key = token.replace(/~1/g, '/').replace(/~0/g, '~');
    if (
      value === null ||
      typeof value !== 'object' ||
      !Object.hasOwn(value, key)
    ) {
      return { available: false };
    }
    value = value[key];
  }
  return { available: true, value };
}

function matches(actual, operator, expected) {
  switch (operator) {
    case 'equals':
      return isDeepStrictEqual(actual, expected);
    case 'contains':
      assert(
        typeof actual === 'string' && typeof expected === 'string',
        'contains requires strings'
      );
      return actual.includes(expected);
    case 'one_of':
      assert(Array.isArray(expected), 'one_of requires an expected array');
      return expected.some((value) => isDeepStrictEqual(actual, value));
    default:
      throw new Error(`Unsupported assertion operator: ${operator}`);
  }
}

function aggregate(assertions) {
  const states = assertions.map((item) => item.status);
  if (states.includes('FAIL')) return 'FAIL';
  if (states.every((state) => state === 'PASS')) return 'PASS';
  if (states.includes('PASS')) return 'PARTIAL';
  return new Set(states).size === 1 ? states[0] : 'UNVERIFIED';
}

export function compileEvidence(manifest, root) {
  assert(
    typeof manifest.source_sha === 'string' && manifest.source_sha,
    'Source identity required'
  );
  assert(
    Array.isArray(manifest.cases) && manifest.cases.length,
    'Case definitions required'
  );
  root = realpathSync(root);
  const documents = new Map();
  const hashes = {};
  const read = (file) => {
    assert(
      typeof file === 'string' && file && !isAbsolute(file),
      'Relative evidence path required'
    );
    const path = realpathSync(resolve(root, file));
    const inside = relative(root, path);
    assert(
      inside !== '..' && !inside.startsWith('../') && !isAbsolute(inside),
      'Evidence must remain inside its campaign directory'
    );
    if (!documents.has(path)) {
      const bytes = readFileSync(path);
      hashes[file] = createHash('sha256').update(bytes).digest('hex');
      documents.set(
        path,
        JSON.parse(bytes.toString('utf8').replace(/^\uFEFF/, ''))
      );
    }
    return documents.get(path);
  };
  const caseIds = new Set();
  const cases = manifest.cases.map((definition) => {
    const key = `${definition.suite}:${definition.case_id}`;
    assert(
      definition.suite && definition.case_id && !caseIds.has(key),
      'Duplicate or missing case identity'
    );
    caseIds.add(key);
    assert(
      Array.isArray(definition.assertions) && definition.assertions.length,
      `Required assertions missing: ${key}`
    );
    const assertionIds = new Set();
    const assertions = definition.assertions.map((item) => {
      assert(
        item.id && !assertionIds.has(item.id),
        `Duplicate or missing assertion: ${key}`
      );
      assertionIds.add(item.id);
      assert(
        ['live', 'fixture', 'human', 'physical'].includes(item.lane),
        'Explicit evidence lane required'
      );
      if (item.coverage_status) {
        assert(
          /^(UNVERIFIED|NOT_RUN_USER_STOP|BLOCKED_[A-Z_]+)$/.test(
            item.coverage_status
          ) && item.reason,
          'Coverage requires a specific status and reason'
        );
        return {
          id: item.id,
          lane: item.lane,
          status: item.coverage_status,
          reason: item.reason,
          observations: []
        };
      }
      const reviewed = item.lane === 'human' || item.lane === 'physical';
      if (reviewed)
        assert(
          item.operator === 'reviewed_acceptance',
          'Human/device acceptance must be an explicit reviewed record, never inferred from transport'
        );
      assert(
        Array.isArray(item.evidence) && item.evidence.length,
        'Assertion requires evidence'
      );
      assert(
        Object.hasOwn(item, 'expected'),
        'Assertion requires an independent expected value'
      );
      if (manifest.require_raw_evidence && item.lane === 'live')
        assert(
          item.source_evidence?.length,
          'Live assertions require raw source evidence'
        );
      const sourceEvidence = (item.source_evidence ?? []).map((ref) => {
        assert(
          ref.attempt_id && ref.source_sha === manifest.source_sha,
          'Raw evidence requires matching attempt and source identity'
        );
        return {
          ...ref,
          available: atPointer(read(ref.file), ref.json_pointer).available
        };
      });
      if (reviewed)
        assert(
          item.expected === 'PASS',
          'Reviewed acceptance expects PASS; retain the actual reviewed outcome'
        );
      const seen = new Map();
      for (const ref of item.evidence) {
        assert(
          ref.attempt_id && ref.source_sha === manifest.source_sha,
          'Attempt and matching source identities required'
        );
        if (manifest.require_raw_evidence && item.lane === 'live')
          assert(
            sourceEvidence.some(
              (source) =>
                source.attempt_id === ref.attempt_id &&
                (source.project ?? '') === (ref.project ?? '')
            ),
            'Every live attempt requires matching raw source evidence'
          );
        const observed = atPointer(read(ref.file), ref.json_pointer);
        let outcome;
        if (reviewed && observed.available) {
          const record = observed.value;
          const valid =
            record &&
            record.reviewer_type === 'human' &&
            record.reviewer &&
            Number.isFinite(Date.parse(record.reviewed_at_utc)) &&
            ['PASS', 'FAIL'].includes(record.outcome) &&
            (item.lane === 'human'
              ? typeof record.heard_answer === 'string' &&
                (record.heard_answer.trim() ||
                  (record.outcome === 'FAIL' && record.failure_reason))
              : record.device && record.route);
          outcome = valid ? record.outcome : 'UNVERIFIED';
        }
        const observation = {
          ...ref,
          available: observed.available,
          ...(observed.available ? { received: observed.value } : {}),
          status: !observed.available
            ? 'UNVERIFIED'
            : reviewed
              ? outcome
              : matches(
                    observed.value,
                    item.operator ?? 'equals',
                    item.expected
                  )
                ? 'PASS'
                : 'FAIL'
        };
        const identity = `${ref.source_sha}:${ref.project ?? ''}:${ref.attempt_id}`;
        if (seen.has(identity))
          assert.deepEqual(
            seen.get(identity),
            observation,
            'Conflicting duplicate evidence'
          );
        else seen.set(identity, observation);
      }
      const observations = [...seen.values()];
      return {
        id: item.id,
        lane: item.lane,
        expected: item.expected,
        observations,
        status:
          sourceEvidence.some((ref) => !ref.available) &&
          aggregate(observations) !== 'FAIL'
            ? 'UNVERIFIED'
            : aggregate(observations),
        ...(sourceEvidence.length ? { source_evidence: sourceEvidence } : {})
      };
    });
    let status = aggregate(assertions);
    if (definition.review) {
      const review = definition.review;
      assert(
        review.reviewer && review.reason && review.evidence?.length,
        'Review requires attribution, reason and evidence'
      );
      for (const ref of review.evidence)
        assert(
          atPointer(read(ref.file), ref.json_pointer).available,
          'Review evidence missing'
        );
      // A review may identify a further failure or incomplete qualification.
      // It cannot manufacture a pass over missing or contradictory evidence.
      assert(
        review.status === 'FAIL' ||
          (review.status === 'PARTIAL' && status === 'PASS'),
        'Review cannot promote evidence to PASS'
      );
      status = review.status;
    }
    return {
      case_id: definition.case_id,
      suite: definition.suite,
      title: definition.title,
      status,
      qualification_lanes: [...new Set(assertions.map((item) => item.lane))],
      fixture_only: assertions.every((item) => item.lane === 'fixture'),
      assertions,
      ...(definition.review ? { review: definition.review } : {})
    };
  });
  const counts = {};
  const laneCounts = {};
  for (const item of cases)
    counts[item.status] = (counts[item.status] ?? 0) + 1;
  for (const item of cases)
    for (const assertion of item.assertions) {
      laneCounts[assertion.lane] ??= {};
      const counts = laneCounts[assertion.lane];
      counts[assertion.status] = (counts[assertion.status] ?? 0) + 1;
    }
  return {
    schema_version: 1,
    source_sha: manifest.source_sha,
    total_case_definitions: cases.length,
    counts,
    assertion_counts_by_lane: laneCounts,
    cases,
    evidence_file_sha256: hashes
  };
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  const [manifestPath, outputPath] = process.argv.slice(2);
  assert(
    manifestPath && outputPath,
    'Usage: node chat-voice-report.mjs MANIFEST.json NEW_REPORT.json'
  );
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
  const report = compileEvidence(manifest, dirname(resolve(manifestPath)));
  writeFileSync(outputPath, `${JSON.stringify(report, null, 2)}\n`, {
    flag: 'wx'
  });
}
