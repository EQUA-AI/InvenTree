<!-- Fork PR checklist (equa customizations) -->

## Summary



## Checklist

- [ ] Tests pass locally (`pytest ai/core/tests` + affected Django apps)
- [ ] **AI golden set (S39):** if this PR touches prompts, the workflow
      registry, retrieval/index chunking, or model tiering, I ran the golden
      set against a live dev deployment
      (`python -m ai.core.evals.run_golden`, or `pytest -m golden` with
      `AIMMS_GOLDEN_LIVE=1`) and it reports **zero hard fails**
- [ ] Migrations (if any) are additive + reversible, and the deploy plan
      notes them (one in flight at a time on the shared DB)
