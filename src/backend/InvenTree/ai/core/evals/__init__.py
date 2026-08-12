"""S39: golden-set evaluation harness.

Curated question/expected-behavior items (``golden/items.yaml``), a strict
LLM judge (``judge.py``), a live HTTP driver (``run_golden.py``) that
exercises the full spine — middleware, budgets, correlation — against a real
deployment, plus a deterministic red-team smoke (``golden/redteam.yaml``).

Scoring encodes EX-ADR-002: a wrong answer is a hard fail; abstaining on a
trap is a pass; abstaining on an answerable question is a soft warn, never a
hard fail.
"""
