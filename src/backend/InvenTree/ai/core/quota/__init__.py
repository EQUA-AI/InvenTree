"""Service-limit controls (S12 quota profiles, S13 admission control).

The quota engine lives here in the AI plane; the durable policy/assignment/
reservation/audit models live in the ``aichat`` Django app. Nothing in this
package queries the ORM on the hot path — policy resolution goes through a
cached snapshot with a lazy, swappable loader (``assignment_source``), so the
``ai/core`` pytest island stays DB-free.
"""
