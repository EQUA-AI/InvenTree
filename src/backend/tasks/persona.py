"""R6 UI10 first-use Work-list default from an explicit, admin-named persona.

A persona is never inferred from CRUD roles, staff or superuser status: those
say what a user may do, not which job they hold. The deployment names the
groups that identify supervisors and technicians
(``AIMMS_WORK_SUPERVISOR_GROUPS`` / ``AIMMS_WORK_TECHNICIAN_GROUPS``, both
empty by default) and membership is read live on every request.

- supervisor → "Needs attention"; technician → "My work".
- A member of both is a supervisor, so a mixed account always gets the same
  view instead of one that depends on which group was checked first.
- No mapping, or no matching group: no persona and the neutral "All work".

This only chooses a starting view. It grants nothing, and an explicit URL or
the user's own saved choice always wins in the Work list.
"""

from django.conf import settings

WORK_PERSONA_RULE = 'work_persona.v1'

#: Default Work-list scope per persona, highest precedence first.
DEFAULT_SCOPES = (('supervisor', 'attention'), ('technician', 'mine'))
NEUTRAL_SCOPE = 'all'

_SETTINGS = {
    'supervisor': 'AIMMS_WORK_SUPERVISOR_GROUPS',
    'technician': 'AIMMS_WORK_TECHNICIAN_GROUPS',
}


def work_persona(actor) -> dict:
    """The actor's persona and the Work-list scope it starts on."""
    names = {
        persona: set(getattr(settings, setting, None) or [])
        for persona, setting in _SETTINGS.items()
    }
    configured = set().union(*names.values())
    held = (
        set(actor.groups.filter(name__in=configured).values_list('name', flat=True))
        if configured and actor.is_authenticated
        else set()
    )
    for persona, scope in DEFAULT_SCOPES:
        if held & names[persona]:
            return {
                'rule': WORK_PERSONA_RULE,
                'persona': persona,
                'default_scope': scope,
            }
    return {'rule': WORK_PERSONA_RULE, 'persona': None, 'default_scope': NEUTRAL_SCOPE}
