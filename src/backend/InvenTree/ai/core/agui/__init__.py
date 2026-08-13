"""S49: the spec-clean AG-UI adapter.

Translates the FROZEN internal event dialect (ai.core.streaming.AGUIEvent
records — also the persisted replay dialect) into official AG-UI protocol
events at the edge. The stored dialect never changes; ``/agui`` is a view.
"""
