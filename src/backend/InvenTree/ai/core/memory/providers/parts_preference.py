"""
Parts Preference Context Provider

MAF-compliant ContextProvider for part selection preferences.
Learns and applies user preferences for parts and suppliers.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from ai.core.config import settings

logger = structlog.get_logger(__name__)


class PartsPreferenceProvider:
    """
    Context provider for parts and supplier preferences.

    Implements MAF ContextProvider interface:
    - invoking(messages, **kwargs) -> Context
    - invoked(request_messages, response_messages, invoke_exception, **kwargs)

    Tracks:
    - Preferred parts for specific use cases
    - Preferred suppliers for categories
    - Alternative part mappings
    - Price sensitivity indicators
    """

    def __init__(self, preferences_dir: Path | None = None) -> None:
        """
        Initialize the parts preference provider.

        Args:
            preferences_dir: Directory for preference files.
        """
        self.preferences_dir = preferences_dir or (settings.data_dir / "parts_preferences")
        self.preferences_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "PartsPreferenceProvider initialized", preferences_dir=str(self.preferences_dir)
        )

    def _get_preference_path(self, user_id: str) -> Path:
        """Get the file path for user preferences."""
        return self.preferences_dir / f"{user_id}.json"

    async def get_preferences(self, user_id: str) -> dict[str, Any]:
        """
        Get a user's parts preferences.

        Args:
            user_id: The user identifier.

        Returns:
            Preferences data or default preferences.
        """
        pref_path = self._get_preference_path(user_id)

        if pref_path.exists():
            try:
                with Path(pref_path).open() as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load parts preferences", user_id=user_id, error=str(e))

        return self._default_preferences(user_id)

    async def update_preferences(
        self,
        user_id: str,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Update a user's parts preferences.

        Args:
            user_id: The user identifier.
            updates: Fields to update.

        Returns:
            Updated preferences.
        """
        prefs = await self.get_preferences(user_id)
        self._deep_merge(prefs, updates)
        prefs["updated_at"] = datetime.now(UTC).isoformat()

        pref_path = self._get_preference_path(user_id)
        with Path(pref_path).open("w") as f:
            json.dump(prefs, f, indent=2, default=str)

        logger.debug("Parts preferences updated", user_id=user_id)
        return prefs

    def _default_preferences(self, user_id: str) -> dict[str, Any]:
        """Create default parts preferences."""
        return {
            "user_id": user_id,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            "part_preferences": {
                # Maps use case to preferred part IPNs
                # Example: {"decoupling_capacitor": ["CAP-100NF-0402", "CAP-100NF-0603"]}
            },
            "supplier_preferences": {
                # Maps category to preferred suppliers
                # Example: {"capacitors": ["DigiKey", "Mouser"]}
            },
            "alternative_mappings": {
                # Maps part IPN to approved alternatives
                # Example: {"CAP-100UF-16V": ["CAP-100UF-16V-ALT1", "CAP-100UF-25V"]}
            },
            "price_preferences": {
                "prefer_lowest_price": False,
                "prefer_fastest_delivery": True,
                "max_premium_percentage": 10,  # Pay up to 10% more for preferred supplier
            },
            "quality_preferences": {
                "require_rohs": True,
                "require_automotive_grade": False,
                "min_supplier_rating": 3,  # 1-5 scale
            },
            "history": {
                "parts_selected": [],  # Recent part selections
                "suppliers_used": [],  # Recent suppliers used
            },
        }

    def _deep_merge(self, base: dict[str, Any], updates: dict[str, Any]) -> None:
        """Deep merge updates into base dict."""
        for key, value in updates.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value

    async def invoking(
        self,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Called before agent invocation.

        Provides parts preference context.

        Args:
            messages: The conversation messages.
            **kwargs: Additional context including user_id.

        Returns:
            Context dictionary with parts preferences.
        """
        user_id = kwargs.get("user_id", "default")
        prefs = await self.get_preferences(user_id)

        # Build preference hints for LLM
        hints = []

        # Part preferences
        if prefs.get("part_preferences"):
            hints.append("User has specific part preferences:")
            for use_case, parts in list(prefs["part_preferences"].items())[:5]:
                hints.append(f"  - For {use_case}: prefer {', '.join(parts[:3])}")

        # Supplier preferences
        if prefs.get("supplier_preferences"):
            hints.append("\nUser has supplier preferences:")
            for category, suppliers in list(prefs["supplier_preferences"].items())[:5]:
                hints.append(f"  - For {category}: prefer {', '.join(suppliers[:3])}")

        # Price/quality preferences
        price_prefs = prefs.get("price_preferences", {})
        if price_prefs.get("prefer_lowest_price"):
            hints.append("\nUser prioritizes lowest price.")
        elif price_prefs.get("prefer_fastest_delivery"):
            hints.append("\nUser prioritizes fastest delivery over lowest price.")

        quality_prefs = prefs.get("quality_preferences", {})
        if quality_prefs.get("require_rohs"):
            hints.append("User requires RoHS compliant parts.")
        if quality_prefs.get("require_automotive_grade"):
            hints.append("User requires automotive-grade parts.")

        # Recent history
        recent_parts = prefs.get("history", {}).get("parts_selected", [])[:5]
        if recent_parts:
            hints.append(f"\nRecently selected parts: {', '.join(recent_parts)}")

        context = {
            "parts_preference_hints": "\n".join(hints)
            if hints
            else "No specific parts preferences recorded.",
            "raw_preferences": prefs,
            "has_preferences": bool(
                prefs.get("part_preferences") or prefs.get("supplier_preferences")
            ),
        }

        logger.debug(
            "PartsPreferenceProvider.invoking",
            user_id=user_id,
            has_preferences=context["has_preferences"],
        )

        return context

    async def invoked(
        self,
        request_messages: list[dict[str, Any]],
        response_messages: list[dict[str, Any]],
        invoke_exception: Exception | None,
        **kwargs: Any,
    ) -> None:
        """
        Called after agent invocation.

        Updates preferences based on user selections.

        Args:
            request_messages: The original request messages.
            response_messages: The response messages.
            invoke_exception: Exception if invocation failed.
            **kwargs: Additional context including selections.
        """
        if invoke_exception:
            return

        user_id = kwargs.get("user_id", "default")

        # Check for part selections in this turn
        parts_selected = kwargs.get("parts_selected", [])
        supplier_used = kwargs.get("supplier_used")
        use_case = kwargs.get("use_case")

        if not parts_selected and not supplier_used:
            return

        prefs = await self.get_preferences(user_id)
        updates: dict[str, Any] = {}

        # Update part preferences if use case is known
        if parts_selected and use_case:
            current_prefs = prefs.get("part_preferences", {}).get(use_case, [])
            for part_ipn in parts_selected:
                if part_ipn not in current_prefs:
                    current_prefs.insert(0, part_ipn)
            current_prefs = current_prefs[:10]  # Keep top 10
            updates.setdefault("part_preferences", {})[use_case] = current_prefs

        # Update history
        history = prefs.get("history", {})

        if parts_selected:
            recent_parts = history.get("parts_selected", [])
            for part_ipn in parts_selected:
                if part_ipn in recent_parts:
                    recent_parts.remove(part_ipn)
                recent_parts.insert(0, part_ipn)
            updates.setdefault("history", {})["parts_selected"] = recent_parts[:50]

        if supplier_used:
            recent_suppliers = history.get("suppliers_used", [])
            if supplier_used in recent_suppliers:
                recent_suppliers.remove(supplier_used)
            recent_suppliers.insert(0, supplier_used)
            updates.setdefault("history", {})["suppliers_used"] = recent_suppliers[:20]

        if updates:
            await self.update_preferences(user_id, updates)
            logger.debug(
                "PartsPreferenceProvider.invoked - preferences updated",
                user_id=user_id,
                parts_selected=parts_selected,
                supplier_used=supplier_used,
            )


# Module-level singleton
_provider: PartsPreferenceProvider | None = None


def get_parts_preference_provider() -> PartsPreferenceProvider:
    """Get the singleton parts preference provider."""
    global _provider
    if _provider is None:
        _provider = PartsPreferenceProvider()
    return _provider
