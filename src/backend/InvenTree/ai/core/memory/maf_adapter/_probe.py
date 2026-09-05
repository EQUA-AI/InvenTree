"""Which Microsoft Agent Framework shape is installed (maf_compat idiom).

``pin``: agent-framework-core 1.0.0b251120 — ``ChatAgent`` + ``ContextProvider``
(``invoking``/``invoked``/``thread_created``), no ``HistoryProvider``.
``ga``: 1.16+ — ``HistoryProvider`` exported, ``ChatAgent`` renamed ``Agent``,
providers hook ``before_run``/``after_run``. The matrix lane
(``ai_maf_matrix.yaml``) proves both.
"""

from __future__ import annotations

import agent_framework as _agent_framework

MAF_SHAPE = "ga" if hasattr(_agent_framework, "HistoryProvider") else "pin"

__all__ = ["MAF_SHAPE"]
