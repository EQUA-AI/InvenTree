"""Typed verification command errors with stable codes.

Every error carries a stable machine-readable code (spec section 15.3) and an
HTTP status hint so the API layer can build uniform error envelopes without
interpreting message text.
"""

from part.verification.schema import CommandCodes, ConsumerCodes


class VerificationCommandError(Exception):
    """Base class for verification command failures."""

    code = CommandCodes.RPF_STATE_CONFLICT
    http_status = 409
    retryable = False

    def __init__(self, message: str = '', *, code: str = '', blockers=None):
        """Store the stable code and optional blockers beside the message."""
        super().__init__(message or self.__class__.__doc__ or 'Command failed')
        if code:
            self.code = code
        self.blockers = blockers or []


class VerificationDisabled(VerificationCommandError):  # noqa: N818 - stable command result name
    """The requested verification capability is disabled."""

    code = CommandCodes.RPF_DISABLED
    http_status = 503


class VerificationPermissionError(VerificationCommandError):
    """The actor lacks permission for this verification action."""

    code = CommandCodes.RPF_PERMISSION_DENIED
    http_status = 403


class VerificationScopeError(VerificationCommandError):
    """Actor or target verification scope is unresolved or mismatched."""

    code = CommandCodes.RPF_SCOPE_MISMATCH
    http_status = 403


class VerificationNotFound(VerificationCommandError):  # noqa: N818 - stable command result name
    """Scope-safe not found: the object is absent or hidden."""

    code = CommandCodes.RPF_CONTEXT_INVALID
    http_status = 404


class VerificationStateConflict(VerificationCommandError):  # noqa: N818 - stable command result name
    """The session state does not permit this command."""

    code = CommandCodes.RPF_STATE_CONFLICT
    http_status = 409


class VerificationRevisionConflict(VerificationCommandError):  # noqa: N818 - stable command result name
    """The expected session revision does not match the current revision."""

    code = CommandCodes.RPF_REVISION_CONFLICT
    http_status = 409


class VerificationIdempotencyConflict(VerificationCommandError):  # noqa: N818 - stable command result name
    """The idempotency key was reused with a different payload."""

    code = CommandCodes.RPF_IDEMPOTENCY_CONFLICT
    http_status = 409


class VerificationContextInvalid(VerificationCommandError):  # noqa: N818 - stable command result name
    """The command context is incomplete or inconsistent."""

    code = CommandCodes.RPF_CONTEXT_INVALID
    http_status = 422


class VerificationRequirementsIncomplete(VerificationCommandError):  # noqa: N818 - stable command result name
    """Hard requirements are missing, invalid, or conflicting."""

    code = CommandCodes.RPF_REQUIREMENTS_INCOMPLETE
    http_status = 422


class VerificationPolicyUnavailable(VerificationCommandError):  # noqa: N818 - stable command result name
    """The bound policy is missing, inactive, revoked, or corrupt."""

    code = CommandCodes.RPF_POLICY_UNAVAILABLE
    http_status = 424


class VerificationCandidateIneligible(VerificationCommandError):  # noqa: N818 - stable command result name
    """The selected candidate is excluded, missing data, or rejected."""

    code = CommandCodes.RPF_CANDIDATE_INELIGIBLE
    http_status = 409


class VerificationCandidateStale(VerificationCommandError):  # noqa: N818 - stable command result name
    """The selected candidate's facts changed since evaluation."""

    code = CommandCodes.RPF_CANDIDATE_STALE
    http_status = 409


class VerificationSessionStale(VerificationCommandError):  # noqa: N818 - stable command result name
    """The verified facts changed after evaluation or confirmation."""

    code = CommandCodes.RPF_SESSION_STALE
    http_status = 409


class VerificationSessionExpired(VerificationCommandError):  # noqa: N818 - stable command result name
    """The session or decision validity window has passed."""

    code = CommandCodes.RPF_SESSION_EXPIRED
    http_status = 409


class VerificationNoSafeMatchInvalid(VerificationCommandError):  # noqa: N818 - stable command result name
    """No-safe-match preconditions are not met."""

    code = CommandCodes.RPF_NO_SAFE_MATCH_INVALID
    http_status = 409


class VerificationRevalidationIndeterminate(VerificationCommandError):  # noqa: N818 - stable command result name
    """Current facts cannot be observed safely; use is blocked."""

    code = CommandCodes.RPF_REVALIDATION_INDETERMINATE
    http_status = 424


class VerificationUseError(VerificationCommandError):
    """A downstream consumer precondition failed.

    The code is one of the stable consumer codes (spec section 13.3).
    """

    code = ConsumerCodes.PART_VERIFICATION_REQUIRED
    http_status = 409
