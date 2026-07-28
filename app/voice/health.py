from datetime import datetime, timezone

from app.voice.schemas import VoiceHealth


def voice_health(*, enabled: bool, orchestration_available: bool) -> VoiceHealth:
    return VoiceHealth(
        status=(
            "healthy"
            if enabled and orchestration_available
            else "unavailable"
        ),
        enabled=enabled,
        orchestration_available=orchestration_available,
        checked_at=datetime.now(timezone.utc),
    )
