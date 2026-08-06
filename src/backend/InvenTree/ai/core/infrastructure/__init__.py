"""AIMMS infrastructure package.

The legacy file-based planes that lived here — quarantined conversation
persistence, file idempotency, the file message store and file checkpoint
storage — were deleted in S15. Durable threads, turns and idempotency are
owned exclusively by the ``aichat`` repository.
"""
