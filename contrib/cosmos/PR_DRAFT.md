# Station telemetry activation, mimic dashboard and estate onboarding

Pumphouse telemetry can now be activated from reviewed station dictionaries and viewed in
a station/pump mimic. Polling isolates station cursors and failures, preserves progress
through empty hours, and rejects unusable samples atomically. The dashboard keeps stale,
unbound and disabled values visibly unknown; plant totals require complete reviewed inputs,
and alarms require configured thresholds.

The change includes editable provisional SVG/layout assets with coverage validation,
large-dictionary review packs and exact catalogue crosswalks, an atomic estate onboarding
command, local readiness checks, and a read-only query-duration/RU benchmark. Each completed
implementation section is committed separately on local `IoT`.

Validation includes backend ownership/replay/activation/review/estate regressions, TypeScript
and Biome checks, Lingui extraction/compilation, isolated Chromium UI checks, and real SDK
integration against a disposable Cosmos emulator. See `HANDOFF.md` for commands and the
handover's final validation record for counts.

Before production acceptance, the plant developer must replace the provisional geometry and
confirm full dictionary coverage, station inventory, physical units, alarm thresholds and
status meanings. The platform owner must verify the deployed read-only identity and live
sweep capacity. No production migration, cloud mutation, push or PR opening was performed.
