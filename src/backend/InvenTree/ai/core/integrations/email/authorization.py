"""Current-actor email capability checks shared by tools and business commands."""

from functools import wraps

from asgiref.sync import sync_to_async


def require_email_permission(actor, action):
    """Rehydrate grants and account state; never rely on cached tool visibility."""
    from django.contrib.auth import get_user_model

    if action not in ("view", "send"):
        raise PermissionError("Unknown email capability")
    owner = get_user_model().objects.filter(pk=getattr(actor, "pk", None), is_active=True).first()
    if owner is None or not owner.has_perm(f"users.{action}_email"):
        raise PermissionError(f"Email {action} permission is required")
    return owner


def email_capability(action):
    """Guard tool invocation even when its offered tool list predates revocation."""

    def decorate(function):
        @wraps(function)
        async def guarded(*args, **kwargs):
            from types import SimpleNamespace

            from ai.core.auth import get_current_principal

            principal = get_current_principal()
            actor = SimpleNamespace(pk=principal.user_pk if principal else None)
            await sync_to_async(require_email_permission, thread_sensitive=True)(actor, action)
            return await function(*args, **kwargs)

        return guarded

    return decorate
