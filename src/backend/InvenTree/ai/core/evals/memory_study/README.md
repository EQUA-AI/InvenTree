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

## Offline human atom-match scoring

`python -m ai.core.evals.score_extraction_study --campaign ... --journal ...
--review ... --output ...` makes no provider calls. All input/output artifacts must
be outside the repository. The campaign/corpus must be reviewed and the private
review must bind the exact journal bytes. Review JSON has `schema_version: 1`,
`reviewed: true`, a named `reviewer`, `journal_sha256`, a `case_reviews` row for
every case/pass (`case_id`, `pass_index`, `reviewed: true`), and an `annotations`
row for every admitted candidate (`window_id`, zero-based `candidate_index`,
`gold_atom_id` or explicit null for an incorrect/unmatched proposal).

Every window/reservation/pass must be present. Model identity and deterministic
hard-zero checks are rechecked; reviewed matches must belong to that case/source.
Empty output still needs human review. Precision is correctly matched proposals
per proposal; recall is unique matched atoms per gold atom. Empty predictions have
precision 1; zero-gold abstention recall is 1 only with zero predictions. Duplicate
rate is proposals per unique matched atom minus one, floored at zero; it can
exceed 1. With no matched atoms it equals the proposal count (zero for abstention). Freeze these conventions with the campaign before any study.
The output contains metrics/identities, never candidate text, and stays
`not_qualified`: human atom scoring does not manufacture judge calibration, Mem0
admission, cost thresholds or elapsed shadow evidence. No grader has been run.

## Consolidated validation handoff

`validation_manifest.json` registers authored coverage through batch 52 and
migrations 0041–0053. Runtime testing remains paused; the manifest is a queue of
required evidence, not execution results or release approval. Read its explicit
implementation gaps and conditional-track status before treating Track A as closed.

Both study execution and offline scoring require `scoring_conventions.status`
to be `reviewed`, a named reviewer, and the exact supported metric definitions.
Changing definitions requires a corresponding reviewed implementation before
freezing a new campaign. A skipped provider window must have empty raw and
admitted candidates; integer pass identities cannot be booleans.
