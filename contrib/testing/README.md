# Chat and voice evidence reports

`chat-voice-report.mjs` compiles assertion-level JSON evidence into case results.
It reads saved evidence only. It never runs a campaign, starts voice, or sends
requests. Give every new campaign its own manifest and new output filename:

```sh
node contrib/testing/chat-voice-report.mjs /campaign/manifest.json /campaign/report.new.json
node --test contrib/testing/chat-voice-report.test.mjs
```

The output file must not exist. Relative evidence files must remain within the
manifest directory, including after resolving symlinks. Original reports and
raw evidence are never rewritten.

```json
{
  "source_sha": "EXACT_VERIFIED_CANDIDATE_SHA",
  "require_raw_evidence": true,
  "cases": [{
    "suite": "voice",
    "case_id": "VOICE-ACCEPTANCE",
    "title": "Consecutive requests and audible final answers",
    "assertions": [{
      "id": "unique_turn_count",
      "lane": "live",
      "operator": "equals",
      "expected": 10,
      "source_evidence": [{
        "file": "continuity.raw.json",
        "json_pointer": "/turn_responses",
        "attempt_id": "continuity-1",
        "source_sha": "EXACT_VERIFIED_CANDIDATE_SHA"
      }],
      "evidence": [{
        "file": "continuity.json",
        "json_pointer": "/unique_turn_count",
        "attempt_id": "continuity-1",
        "source_sha": "EXACT_VERIFIED_CANDIDATE_SHA"
      }]
    }, {
      "id": "heard_final_answer",
      "lane": "human",
      "coverage_status": "BLOCKED_HUMAN_PHYSICAL",
      "reason": "A human listener and the intended audio route are still required"
    }]
  }]
}
```

Define expected values from independently checked native truth. Reference the
actual received value with a JSON pointer. Missing paths stay `UNVERIFIED`;
they never become zero, false, an expected answer, or a successful absence.
Supported comparisons are `equals`, `contains`, and `one_of`. A case requires
at least one assertion and every required assertion must pass for case PASS.

Evidence lanes are `live`, `fixture`, `human`, and `physical`. Human/device
acceptance uses the explicit `reviewed_acceptance` operator, `expected: "PASS"`,
and a pointer to an attributed review object containing `reviewer_type: "human"`,
`reviewer`, `reviewed_at_utc`, and actual `outcome: "PASS"` or `"FAIL"`. Human
reviews also contain the actual `heard_answer`; an empty failed answer requires
`failure_reason`. Physical reviews identify the actual `device` and `route`.
A transport boolean, RTP, response.done or a requested playback state cannot
qualify either lane.

Retain original and replay observations with separate attempt IDs. A failed
required observation keeps the case FAIL even if a replay passes. Duplicate
attempt/source/project references do not increase counts. Changed source gets
a new campaign; evidence from another source is rejected. Further reviewed
failures or incomplete qualification can be recorded with a reviewer, reason,
and evidence, but cannot promote missing or failed assertions to PASS.

Counts describe unique suite/case definitions. They are never a sum of unit,
browser, replay, and live-request totals. Output retains assertion observations,
independent expected values, lanes, missing coverage reasons, and SHA-256 hashes
of every evidence file read. A hash verifies bytes, not factual correctness.

New live campaigns must set `require_raw_evidence: true`. Each live attempt
also links its retained raw response or observation through `source_evidence`,
using the same attempt ID, source SHA and optional project as the derived
assertion. The compiler reads and hashes both files; an absent raw pointer
cannot certify a pass. It reports counts per qualification lane and marks
fixture-only cases explicitly. Legacy manifests remain readable without
rewriting their historical grades.

## Source regression checks

With the project's Python 3.12 development dependencies available:

```sh
python contrib/testing/run-ai-component-tests.py
python contrib/testing/run-ai-component-tests.py core/tests/test_openui_trend_integration.py core/tests/test_voice_baseline_grounding.py -q
```

No arguments runs `chat-voice-regression-modules.txt`. The reverse-order command
checks fixture isolation. This runner requires the isolated test settings and
refuses an inherited production settings module. Native models and SQL are
exercised in disposable scratch state without migrations; this does not qualify
fresh installations, live providers, deployment or human/device acceptance.
