"""Merge the IoT registry chain and the demo-metrics chain into one leaf.

Two reviewed chains extend ``assets.0010_remove_assetmachine_customer``
independently:

- the IoT equipment-registry/ingestion chain
  (``0011_equipment_registry`` -> ``0012_ingestioncheckpoint`` ->
  ``0013_station_poll_progress`` -> ``0014_station_activation``), and
- the governed demo-metrics chain
  (``0017_demometricscoverageinterval_demometricssession_and_more`` ->
  ``0018_alter_demometricsreceipt_operation_kind``), which sits on the target
  chain through ``0016_assetmachine_placement_version_and_more``.

Their operations are disjoint: the IoT chain adds registry identity fields,
registry/dictionary models and ingestion checkpoint/poll-progress state, while
the target and demo-metrics chains add profiles, barcodes, client scoping,
placement history and the demo ledger models. No migration edits the same
field or constraint on both sides, so the two branches of the graph linearize
without a state repair and this migration joins them into a single leaf.

All imported migration names, dependencies and operations remain unchanged.
``0011_equipment_registry`` resolves its cross-app dependencies on
``common.0049_merge_upstream_sync`` and
``part.0154_partverificationsession_scope_client`` in this tree.

Ordinary deep SQLite rollback across these independent branches is unsupported:
Django's backwards traversal can differ from the inverse canonical forward
order used to reconstruct historical states. Migration rehearsals use the
explicit canonical-suffix planner in ``InvenTree.migration_rewind`` rather than
rewriting applied history or retaining sibling schema through a table rebuild.
Deployments remain forward-fix only; this join is not a recovery procedure.
"""

from django.db import migrations


class Migration(migrations.Migration):
    """Join the IoT station-activation leaf and the demo-metrics leaf."""

    dependencies = [
        ('assets', '0014_station_activation'),
        ('assets', '0018_alter_demometricsreceipt_operation_kind'),
    ]

    operations = []
