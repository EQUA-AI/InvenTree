# Memory implementation qualification assets

These are **authored, unexecuted drafts**, separate from the historical M1 replay
pair. The original campaign/battery hashes are captured in validation_manifest.json;
semantic on/off, consent, tags and fault cases have separate matrix entries.

## Files and review

- corpus.draft.jsonl: 60 synthetic transcripts, including 10 abstention transcripts,
  with 800 draft positive atoms across English, Spanish, German and French. Every
  transcript has at least 16 messages. Repeated templates are not independent
  evidence of statistical power. Domain reviewers must replace weak/redundant
  examples, verify locale and units, and sign the case and atom status before use.
- authority_quadruplets.draft.json: 30 four-channel comparisons. Native typed fields
  require server rereading; operator/model/excerpt prose does not gain that authority.
- calibration.template.jsonl: 80 blank human rows, with a stratified adversarial
  allocation. Populate from actual private journals, use the existing calibration
  runner, and retain its fingerprint and >=90% agreement/>=20% adversarial proof.
- summary_review.template.md: 30 blank human summary reviews. Do not substitute
  generated ratings or a new synthetic run for the existing paused M1/M2 evidence.
- promote_drop_card.template.json: all 17 decision rows (3a/3b split), initially
  not_run. Cost judgement, signed admission, shadow evidence and 100 decisions per
  origin remain human/operational inputs; none is inferred from an extraction score.

## Execution contract

`python -m ai.core.evals.run_extraction_study` only validates preparation by default.
`--execute --journal /private/path/run-<epoch>-memory.jsonl` requires an approved
campaign, named corpus review, non-placeholder model/deployment/runtime/judge pins,
and positive token/provider-call budgets. The campaign is JSON-compatible YAML;
its exact byte hash and corpus hash appear in the journal. Review changes require
re-freezing those bytes before calls. The checked-in campaign intentionally refuses
execution. No runner or provider call was executed while authoring these files.

The runner calls the production custom extractor, policy and shield seams in
process. It writes no facts, proposals or native records. Native-field verification,
consent, lifecycle, SQL constraints, query budgets, browser/voice and restore
behavior are covered by the separate suites in the validation manifest; a clean
extractor run cannot certify them. Five seeded passes shuffle transcript order;
provider sampling is not claimed reproducible. The exact provider model identity
must match the preregistered pin on every extraction response. Policy-rejected sources are excluded. Shield unavailability, malformed outputs, hard-zero failures, cost
bounds and model drift preserve an incomplete journal, never a successful score.

Journals use exclusive mode-0600 files outside the repository. Raw candidate text
belongs only in that private store. Header timestamps remain compatible with the
existing 12-month prune_journals CLI. Failed runs remain evidence. The summary is
content-free and unscored; a judge fingerprint alone is not calibration evidence.

extraction_statistics.py provides paired transcript-clustered 90% bootstrap CIs
and exact per-pass McNemar checks over reviewed labels/metrics. It refuses missing
reviewers, mismatched transcripts/passes, invalid F1 and missing hard-zero counts.
It never manufactures semantic atom matches or returns a promote decision. Produce
origin-blind human/judge labels in the private study process; cite their hashes and
calibration artifact in the card. The optional engine is not installed, imported
or admitted by this runner; Track B requires its separate package and governance
gates before adding a paired execution adapter.

## Consolidated validation order when testing resumes

1. Review prepared release configuration, migrations and isolated PG17 fixtures.
2. Run local policy/schema/lifecycle tests, then PostgreSQL read/replay and failure
   cases. Fix defects before any provider campaign; no reused live operations.
3. Run browser/voice lifecycle cases and manual audible-playback checks.
4. Complete the existing M1 replay-only pair, summary reviews and calibration.
5. Freeze reviewed semantic corpus/configuration and run the separate study/matrix.
6. Collect latency/EXPLAIN, cost, deletion residuals, restore and projection audit
   evidence; close only gates whose artifacts actually pass.

Known code/coverage gaps stay explicit in validation_manifest.json and batch
reports. An authored test or configuration file is not a qualification result.
