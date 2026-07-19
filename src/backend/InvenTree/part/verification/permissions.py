"""Service-level authorization for verification commands.

Django model permissions plus the custom service permissions declared on the
verification models. Object visibility and source-document permission remain
additional requirements owned by their source models (spec section 17.1).
"""

from part.verification.errors import VerificationPermissionError

PERM_VIEW = 'part.view_partverificationsession'
PERM_ADD = 'part.add_partverificationsession'
PERM_CHANGE = 'part.change_partverificationsession'
PERM_REVIEW = 'part.review_partverification'
PERM_CONFIRM = 'part.confirm_partverification'
PERM_INVALIDATE = 'part.invalidate_partverification'
PERM_USE = 'part.use_partverification'
PERM_MANAGE_POLICY = 'part.manage_partverificationpolicy'


def require_permission(actor, permission: str):
    """Require an authenticated actor holding the given permission."""
    if (
        actor is None
        or not getattr(actor, 'is_authenticated', False)
        or not actor.has_perm(permission)
    ):
        raise VerificationPermissionError(
            f'Permission required for this action: {permission}'
        )
    return actor
