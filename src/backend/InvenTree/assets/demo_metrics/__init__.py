"""EQUA demo metrics: reviewed synthetic-demo adapter, ledger and services.

This package turns the frozen local fixture contract
(``resources/demo_fixture.json``) into governed application effects through the
existing services. It never runs automatically: every mutation happens only via
an explicit management command (``plan_demo_metrics`` / ``apply_demo_metrics`` /
``verify_demo_metrics`` / ``replay_demo_metrics`` / ``stop_demo_metrics`` /
``cleanup_demo_metrics``) invoked by a trusted operator.

Safety boundary: everything this package writes is explicitly synthetic and
session-owned. Borrowed machines/locations are referenced, never owned; real
telemetry, real anomalies and operator edits are preserved and reported.
"""
