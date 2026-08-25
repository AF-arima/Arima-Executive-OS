from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Final, Protocol
from uuid import UUID

import httpx

from app.core.config import Settings, get_settings


class TTSException(Exception):
    """Base class for sanitized text-to-speech failures."""


class TTSNotConfigured(TTSException):
    pass


class TTSUnsupportedLocale(TTSException):
    pass


class TTSProviderError(TTSException):
    pass


class TTSTimeout(TTSException):
    pass


SupportedTTSLocale = str
VOICE_NAMES: Final[dict[str, str]] = {
    "en-US": "en-US-AvaNeural",
    "fa-IR": "fa-IR-DilaraNeural",
    "ar-SA": "ar-SA-ZariyahNeural",
    "ru-RU": "ru-RU-SvetlanaNeural",
    "tr-TR": "tr-TR-EmelNeural",
    "zh-CN": "zh-CN-XiaohanNeural",
}
LANGUAGE_LOCALE: Final[dict[str, str]] = {
    "en": "en-US", "fa": "fa-IR", "ar": "ar-SA",
    "ru": "ru-RU", "tr": "tr-TR", "zh": "zh-CN",
}


def resolve_locale(value: str | None, default: str = "en-US") -> str:
    candidate = (value or default).strip().replace("_", "-")
    if candidate in VOICE_NAMES:
        return candidate
    mapped = LANGUAGE_LOCALE.get(candidate.casefold())
    if mapped:
        return mapped
    base = candidate.split("-", 1)[0].casefold()
    mapped = LANGUAGE_LOCALE.get(base)
    if mapped:
        return mapped
    raise TTSUnsupportedLocale("The requested speech locale is not supported")


@dataclass(frozen=True, slots=True)
class TTSRequest:
    text: str
    locale: str
    request_id: UUID
    generation_id: int | None = None


@dataclass(frozen=True, slots=True)
class TTSResult:
    audio: bytes
    mime_type: str
    provider: str
    locale: str
    voice: str
    request_id: UUID


class TTSProvider(Protocol):
    async def synthesize(self, request: TTSRequest) -> TTSResult: ...


class AzureSpeechProvider:
    name = "azure_speech"

    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings or get_settings()
        self.client = client

    @property
    def configured(self) -> bool:
        key = self.settings.azure_speech_key
        return bool(
            self.settings.azure_speech_enabled
            and key is not None
            and key.get_secret_value().strip()
            and self.settings.azure_speech_region.strip()
        )

    async def synthesize(self, request: TTSRequest) -> TTSResult:
        if not self.configured:
            raise TTSNotConfigured("Azure speech is not configured")
        voice = VOICE_NAMES[request.locale]
        ssml = (
            '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
            f'xml:lang="{request.locale}"><voice name="{voice}">'
            f"{html.escape(request.text)}</voice></speak>"
        )
        url = f"https://{self.settings.azure_speech_region}.tts.speech.microsoft.com/cognitiveservices/v1"
        headers = {
            "Ocp-Apim-Subscription-Key": self.settings.azure_speech_key.get_secret_value() if self.settings.azure_speech_key is not None else "",
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": self.settings.azure_speech_output_format,
            "User-Agent": "Arima-Executive-OS-TTS",
        }
        own_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=self.settings.azure_speech_timeout_seconds)
        try:
            try:
                response = await client.post(url, content=ssml.encode(), headers=headers)
            except httpx.TimeoutException as error:
                raise TTSTimeout("Azure speech timed out") from error
            except httpx.HTTPError as error:
                raise TTSProviderError("Azure speech could not be reached") from error
            if response.status_code < 200 or response.status_code >= 300:
                raise TTSProviderError("Azure speech returned an unavailable response")
            audio = response.content
            if not audio:
                raise TTSProviderError("Azure speech returned empty audio")
            return TTSResult(audio=audio, mime_type="audio/mpeg", provider=self.name, locale=request.locale, voice=voice, request_id=request.request_id)
        finally:
            if own_client:
                await client.aclose()


class TTSProviderRegistry:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def resolve(self) -> TTSProvider:
        return AzureSpeechProvider(self.settings)


class TTSOrchestrator:
    def __init__(self, settings: Settings | None = None) -> None:
        self.registry = TTSProviderRegistry(settings)

    async def synthesize(self, request: TTSRequest) -> TTSResult:
        normalized = resolve_locale(request.locale)
        return await self.registry.resolve().synthesize(
            TTSRequest(text=request.text, locale=normalized, request_id=request.request_id, generation_id=request.generation_id)
        )
