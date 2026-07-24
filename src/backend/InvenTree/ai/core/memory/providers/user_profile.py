"""
User Profile Context Provider

MAF-compliant ContextProvider for user preferences and history.
Injects personalized context at the start of agent invocation.
"""

import json
from pathlib import Path
from typing import Any

import structlog
from ai.core.config import get_settings

logger = structlog.get_logger(__name__)


class UserProfileProvider:
    """
    Context provider for user profile information.

    Implements MAF ContextProvider interface:
    - invoking(messages, **kwargs) -> Context
    - invoked(request_messages, response_messages, invoke_exception, **kwargs)

    User profile includes:
    - Preferred units (metric/imperial)
    - Preferred suppliers
    - Default categories
    - Language preferences
    - Recent interaction history
    """

    def __init__(self, profiles_dir: Path | None = None) -> None:
        """
        Initialize the user profile provider.

        Args:
            profiles_dir: Directory for user profile files.
        """
        settings = get_settings()
        self.profiles_dir = profiles_dir or (settings.data_dir / "profiles")
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        logger.info("UserProfileProvider initialized", profiles_dir=str(self.profiles_dir))

    def _get_profile_path(self, user_id: str) -> Path:
        """Get the file path for a user profile."""
        return self.profiles_dir / f"{user_id}.json"

    async def get_profile(self, user_id: str) -> dict[str, Any]:
        """
        Get a user's profile.

        Args:
            user_id: The user identifier.

        Returns:
            User profile data or default profile.
        """
        profile_path = self._get_profile_path(user_id)

        if profile_path.exists():
            try:
                with Path(profile_path).open() as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load user profile", user_id=user_id, error=str(e))

        return self._default_profile(user_id)

    async def update_profile(
        self,
        user_id: str,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Update a user's profile.

        Args:
            user_id: The user identifier.
            updates: Fields to update.

        Returns:
            Updated profile.
        """
        profile = await self.get_profile(user_id)
        profile.update(updates)

        profile_path = self._get_profile_path(user_id)
        with Path(profile_path).open("w") as f:
            json.dump(profile, f, indent=2)

        logger.debug("User profile updated", user_id=user_id, fields=list(updates.keys()))
        return profile

    def _default_profile(self, user_id: str) -> dict[str, Any]:
        """Create a default user profile."""
        return {
            "user_id": user_id,
            "preferences": {
                "units": "metric",
                "language": "en",
                "date_format": "ISO",
                "currency": "USD",
            },
            "defaults": {
                "preferred_suppliers": [],
                "preferred_categories": [],
                "default_location": None,
            },
            "settings": {
                "show_low_stock_alerts": True,
                "email_notifications": False,
                "auto_approve_threshold": 0,  # No auto-approve
            },
            "history": {
                "recent_parts": [],
                "recent_searches": [],
                "interaction_count": 0,
            },
        }

    async def invoking(
        self,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Called before agent invocation.

        Retrieves and formats user context for injection into the prompt.

        Args:
            messages: The conversation messages.
            **kwargs: Additional context including user_id.

        Returns:
            Context dictionary with user profile information.
        """
        user_id = kwargs.get("user_id", "default")
        profile = await self.get_profile(user_id)

        # Format context for LLM consumption
        context = {
            "user_preferences": f"""
User Profile Context:
- Preferred units: {profile["preferences"]["units"]}
- Language: {profile["preferences"]["language"]}
- Currency: {profile["preferences"]["currency"]}

User Defaults:
- Preferred suppliers: {", ".join(profile["defaults"]["preferred_suppliers"]) or "None set"}
- Preferred categories: {", ".join(profile["defaults"]["preferred_categories"]) or "None set"}

User Settings:
- Show low stock alerts: {profile["settings"]["show_low_stock_alerts"]}
- Auto-approve threshold: ${profile["settings"]["auto_approve_threshold"]} (0 = no auto-approve)

Interaction History:
- Recent parts viewed: {", ".join(profile["history"]["recent_parts"][-5:]) or "None"}
- Total interactions: {profile["history"]["interaction_count"]}
""".strip(),
            "raw_profile": profile,
        }

        logger.debug(
            "UserProfileProvider.invoking",
            user_id=user_id,
            interaction_count=profile["history"]["interaction_count"],
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

        Updates user profile with interaction history.

        Args:
            request_messages: The original request messages.
            response_messages: The response messages.
            invoke_exception: Exception if invocation failed.
            **kwargs: Additional context.
        """
        if invoke_exception:
            # Don't update history on error
            return

        user_id = kwargs.get("user_id", "default")
        profile = await self.get_profile(user_id)

        # Increment interaction count
        profile["history"]["interaction_count"] = profile["history"].get("interaction_count", 0) + 1

        # Extract any part references from response for history
        parts_mentioned = kwargs.get("parts_mentioned", [])
        if parts_mentioned:
            recent_parts = profile["history"].get("recent_parts", [])
            for part_ipn in parts_mentioned[:5]:  # Limit additions
                if part_ipn not in recent_parts:
                    recent_parts.insert(0, part_ipn)
            profile["history"]["recent_parts"] = recent_parts[:20]  # Keep last 20

        await self.update_profile(user_id, profile)

        logger.debug(
            "UserProfileProvider.invoked",
            user_id=user_id,
            new_interaction_count=profile["history"]["interaction_count"],
        )


# Module-level singleton
_provider: UserProfileProvider | None = None


def get_user_profile_provider() -> UserProfileProvider:
    """Get the singleton user profile provider."""
    global _provider
    if _provider is None:
        _provider = UserProfileProvider()
    return _provider
