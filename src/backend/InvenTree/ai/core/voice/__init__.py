"""Voice Live transport primitives.

This package owns the exact Azure Voice Live wire facts (endpoint shapes,
pinned API versions, token scope) so that WS2 validation suites and the WS4
session gateway share one URL authority instead of duplicating strings.

No module in this package may import Azure SDKs at import time; provider
clients are constructed lazily by their owning services.
"""
