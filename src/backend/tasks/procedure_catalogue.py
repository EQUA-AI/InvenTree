"""Live procedure catalogue enrichment requires the direct reader's scope.

The applied snapshot remains Work-owned. Live labels and a current catalogue
comparison are a separate read, authorized once through the canonical filter.
"""

from .scope import scoped_procedures


def _drift_status(application):
    """Compare the immutable applied identity with the catalogue at this read."""
    from tasks.models import ProcedureRevisionStatus

    revision = application.revision
    procedure = revision.procedure
    snapshot = application.snapshot
    identities = (
        (snapshot.get('procedure_id'), procedure.pk),
        (snapshot.get('revision_id'), revision.pk),
        (snapshot.get('revision', {}).get('revision'), revision.revision),
    )
    if any(
        type(recorded) is not int or recorded != actual
        for recorded, actual in identities
    ):
        return 'not_evaluated'
    if procedure.current_revision_id is None:
        return 'not_evaluated'
    if procedure.current_revision_id != revision.pk:
        return 'different_revision'
    if not procedure.active:
        return 'procedure_inactive'
    if revision.status != ProcedureRevisionStatus.PUBLISHED:
        return 'revision_not_published'
    return 'current'


def application_catalogues(actor, applications):
    """Project live metadata only for current customer-authorized procedures."""
    applications = list(applications)
    authorized_ids = set(
        scoped_procedures(actor)
        .filter(pk__in={item.revision.procedure_id for item in applications})
        .values_list('pk', flat=True)
    )
    rows = {}
    for application in applications:
        authorized = application.revision.procedure_id in authorized_ids
        procedure = application.revision.procedure
        rows[application.pk] = {
            'current_catalogue_code': (procedure.code or None) if authorized else None,
            'current_catalogue_name': (procedure.name or None) if authorized else None,
            'revision_status': application.revision.status if authorized else None,
            'drift_status': _drift_status(application)
            if authorized
            else 'not_evaluated',
            'drift_basis': 'catalogue_at_read' if authorized else 'not_evaluated',
        }
    return rows
