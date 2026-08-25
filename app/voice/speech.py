from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core.config import Settings, get_settings


SupportedVoiceLanguage = Literal["en", "fa", "ru", "ar"]
SpeechCapabilityStatus = Literal["not_configured", "unavailable", "available"]


def normalize_voice_language(value: str) -> SupportedVoiceLanguage:
    language = value.strip().casefold().replace("_", "-").split("-", 1)[0]
    if language not in {"en", "fa", "ru", "ar"}:
        raise ValueError("Voice language is not supported")
    return language  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class SpeechCapabilities:
    stt: SpeechCapabilityStatus = "not_configured"
    tts: SpeechCapabilityStatus = "not_configured"
    browser_fallback: Literal["fallback_only"] = "fallback_only"


class SpeechCapabilityService:
    """Server-side speech contract; no provider is activated by default."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def capabilities(self) -> SpeechCapabilities:
        tts: SpeechCapabilityStatus = (
            "available"
            if self.settings.azure_speech_enabled
            and self.settings.azure_speech_key is not None
            and self.settings.azure_speech_key.get_secret_value().strip()
            else "not_configured"
        )
        return SpeechCapabilities(tts=tts)
