from datetime import datetime, timezone
from urllib.parse import urlsplit

from app.core.config import Settings
from app.providers.types import ProviderName
from app.voice.schemas import VoiceHealth
from app.voice.schemas import VoiceProviderProvenance
from app.voice.speech import SpeechCapabilityService


def configured_provider_provenance(
    settings: Settings,
) -> VoiceProviderProvenance:
    """Classify provider configuration without making a provider request.

    Provider adapters perform the same credential/configuration check in their
    health methods.  This helper deliberately never treats the mock provider
    as verified and never claims verification when AI execution is disabled.
    """
    if not settings.ai_execution_enabled:
        return "unverified"
    provider = ProviderName(settings.default_provider)
    if provider is ProviderName.MOCK:
        return "mock"
    if provider is ProviderName.OLLAMA:
        parsed = urlsplit(settings.ollama_url)
        hostname = parsed.hostname
        return (
            "verified"
            if parsed.scheme == "https"
            and hostname is not None
            and hostname.lower() not in {"localhost", "127.0.0.1"}
            else "unverified"
        )
    credentials = {
        ProviderName.OPENAI: settings.openai_api_key,
        ProviderName.ANTHROPIC: settings.anthropic_api_key,
        ProviderName.GEMINI: settings.gemini_api_key,
        ProviderName.NVIDIA: settings.nvidia_api_key,
    }
    credential = credentials.get(provider)
    return (
        "verified"
        if credential is not None and credential.get_secret_value().strip()
        else "unverified"
    )


def voice_health(
    *,
    enabled: bool,
    orchestration_available: bool,
    provider_provenance: VoiceProviderProvenance,
) -> VoiceHealth:
    return VoiceHealth(
        status=(
            "healthy"
            if enabled and orchestration_available
            else "unavailable"
        ),
        enabled=enabled,
        orchestration_available=orchestration_available,
        checked_at=datetime.now(timezone.utc),
        provider_provenance=provider_provenance,
        stt_status=SpeechCapabilityService().capabilities().stt,
        tts_status=SpeechCapabilityService().capabilities().tts,
        browser_fallback=SpeechCapabilityService().capabilities().browser_fallback,
    )
