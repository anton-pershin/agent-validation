from __future__ import annotations

from agent_validation.application import ServiceSettings
from agent_validation.config import load_profile


def configuration_for(
    settings: ServiceSettings, profile_name: str | None = None
) -> dict:
    resolved = load_profile(profile_name or settings.active_profile, settings.profile_dir)
    return resolved.profile.model_dump(mode="json")
