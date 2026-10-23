# P2 execution ledger

- 23 October 2026: resumed dedicated r8-p2 worktree at 270b3b74e, read-only Git
  inspection; no earlier P2 source changes present. Reviewed supplied R8 package,
  binding owner decisions, repository guidance and requested memory notes.
- Implemented B04 named-screen combined placement, complete charges/locks/baselines,
  stable approval-create replay, raw SupplierPart correction and placed-line shortage
  validation. Implemented B05 exact native PDF/MIME evidence with local-only storage,
  hashing/replay/readback, no sample fallback, and attachment reconciliation.
- First original-profile offline run: 28 tests, 2 failed expectations and 5 known
  missing WorkOrder.recurrence_of_id schema errors. Failures retained at
  data/devscratch/r8-p2/backend-first/. Expectations corrected; the inherited schema
  gap is not recorded as a P2 pass. Existing R6 O27 report documents the same gap.
- Second focused run started, with real WeasyPrint rendering and byte-level
  evidence tests. A separate guarded synthetic Work/RuleSet schema supplement is
  prepared for the unchanged linkage/voice tests. It cannot prove migrations or
  deployment. No shared database/container, Azure, Cosmos, secrets, frontend,
  showcase data, Git write, push or PR access.

- Final original-profile runner: **31/31 pass**, backend-second/backend.log and
  manifest.json (earlier 34/35/29/30-case passes remain historical). Actual native PDF
  rendered and raster inspected; status display corrected from code to native text.
- Final guarded synthetic-schema run: **47/47 pass**, backend-supplemental/;
  seven linkage/reconciliation, 31 inherited voice tests (explicit fixture policy
  key), nine unchanged email regressions. Intermediate supplement failures retained.
- New order0123 migration applies in isolated SQLite; its model-state comparison
  passes. Inherited R6 migration drift/Work/RuleSet gap is not fixed or accepted by
  this work. PostgreSQL concurrency/deployment/product acceptance remains unclaimed.
- All applicable repository prek hooks and git diff --check pass.
- Delivery contract: contracts/p2.md; report: reports/P2.md; final hook log and
  source/artifact manifest: data/devscratch/r8-p2/. No mail or external writes.

Current state: B04/B05 Ready for review; source and local verification complete.
Coordinator's merged UI/real-data/PostgreSQL/deployment gates remain separate.
