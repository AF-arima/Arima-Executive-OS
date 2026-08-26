from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.voice.tts import (
    AzureSpeechProvider,
    VOICE_NAMES,
    TTSNotConfigured,
    TTSRequest,
    TTSUnsupportedLocale,
    resolve_locale,
)


def test_tts_locale_resolution_is_canonical_and_fail_closed() -> None:
    assert resolve_locale("fa") == "fa-IR"
    assert resolve_locale("ru") == "ru-RU"
    assert resolve_locale("zh-CN") == "zh-CN"
    with pytest.raises(TTSUnsupportedLocale):
        resolve_locale("xx-XX")


@pytest.mark.asyncio
async def test_disabled_azure_tts_does_not_call_provider() -> None:
    provider = AzureSpeechProvider(Settings(_env_file=None))
    with pytest.raises(TTSNotConfigured):
        await provider.synthesize(TTSRequest("synthetic test", "en-US", uuid4()))


@pytest.mark.asyncio
async def test_azure_tts_preserves_locale_voice_and_binary_audio() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        seen["format"] = request.headers["X-Microsoft-OutputFormat"]
        return httpx.Response(200, content=b"synthetic-audio")

    settings = Settings(
        _env_file=None,
        azure_speech_enabled=True,
        azure_speech_key=SecretStr("test-only-key"),
        azure_speech_region="uksouth",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await AzureSpeechProvider(settings, client).synthesize(
            TTSRequest("<safe & synthetic>", "fa-IR", uuid4())
        )
    assert result.audio == b"synthetic-audio"
    assert result.locale == "fa-IR"
    assert result.voice == "fa-IR-DilaraNeural"
    assert "&lt;safe &amp; synthetic&gt;" in seen["body"]
    assert seen["format"].startswith("audio-")


@pytest.mark.asyncio
async def test_tts_provider_errors_are_sanitized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(500, content=b"provider details")

    settings = Settings(
        _env_file=None,
        azure_speech_enabled=True,
        azure_speech_key=SecretStr("test-only-key"),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(Exception, match="unavailable"):
            await AzureSpeechProvider(settings, client).synthesize(
                TTSRequest("synthetic", "en-US", uuid4())
            )


@pytest.mark.parametrize("locale,expected_voice", sorted(VOICE_NAMES.items()))
@pytest.mark.asyncio
async def test_each_supported_locale_uses_its_azure_neural_voice(
    locale: str, expected_voice: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert expected_voice in request.content.decode()
        return httpx.Response(200, content=b"synthetic-audio")

    settings = Settings(
        _env_file=None,
        azure_speech_enabled=True,
        azure_speech_key=SecretStr("test-only-key"),
        azure_speech_region="uksouth",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await AzureSpeechProvider(settings, client).synthesize(
            TTSRequest("hello", locale, uuid4())
        )
    assert result.provider == "azure_speech"
    assert result.locale == locale
    assert result.voice == expected_voice
