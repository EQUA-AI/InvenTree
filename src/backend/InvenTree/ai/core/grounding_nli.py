"""S40 (skeleton, dark): NLI-based groundedness checking.

A self-hosted entailment checker (Vectara HHEM-2.1-Open, with
Bespoke-MiniCheck as the alternative) that scores whether an answer's claims
are entailed by the retrieved manual chunks — the cheap first stage of the
planned cascade (NLI -> LLM judge only on borderline -> abstain on failure).

Everything here is dark by construction:

- Model dependencies (transformers + CPU torch) live in
  ``ai/requirements-eval.txt``, which is NEVER installed into the container
  image — zero image-size cost.
- Imports are lazy behind ``is_available()``; without the deps the checker
  reports unavailable and does nothing.
- ``FEATURE_NLI_GROUNDEDNESS`` (default False) gates any future live wiring;
  Phase 7 ships only the offline evaluation harness
  (``ai.core.evals.run_nli_eval``). Cascade wiring into live turns is
  Phase 8.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

logger = logging.getLogger(__name__)

#: HHEM-2.1-Open: purpose-built hallucination evaluation, CPU-viable.
DEFAULT_MODEL_ID = "vectara/hallucination_evaluation_model"


@lru_cache(maxsize=1)
def is_available() -> bool:
    """True when the optional eval dependencies are importable."""
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except ImportError:
        return False
    return True


@dataclass(frozen=True)
class NLIScore:
    """Entailment score for one (premise, claim) pair; higher = grounded."""

    score: float
    model_id: str


class NLIGroundednessChecker:
    """Lazy-loading entailment scorer over (evidence, answer) pairs."""

    def __init__(self, model_id: str = DEFAULT_MODEL_ID) -> None:
        """Store configuration; nothing heavyweight happens until first use."""
        self.model_id = model_id
        self._model = None

    def _load(self):
        if self._model is None:
            from transformers import AutoModelForSequenceClassification

            self._model = AutoModelForSequenceClassification.from_pretrained(
                self.model_id, trust_remote_code=True
            )
        return self._model

    def score(self, evidence: str, answer: str) -> NLIScore | None:
        """Score whether ``answer`` is entailed by ``evidence``.

        Returns None when the optional dependencies are absent — callers
        must treat that as "no signal", never as grounded or ungrounded.
        """
        if not is_available():
            return None
        try:
            model = self._load()
            # HHEM exposes a predict() over (premise, hypothesis) pairs.
            value = float(model.predict([(evidence, answer)])[0])
            return NLIScore(score=value, model_id=self.model_id)
        except Exception:
            logger.warning("NLI groundedness scoring failed model=%s", self.model_id)
            return None


__all__ = ["DEFAULT_MODEL_ID", "NLIGroundednessChecker", "NLIScore", "is_available"]
