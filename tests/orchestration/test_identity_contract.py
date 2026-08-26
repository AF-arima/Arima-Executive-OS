import json

import pytest

from app.orchestration.market_response import detect_response_language
from app.orchestration.provider_prompt import ProviderPromptBuilder


@pytest.mark.parametrize(
    ("text_value", "language"),
    [
        ("Who are you?", "en"),
        ("تو کی هستی؟", "fa"),
        ("من أنت؟", "ar"),
        ("Кто ты?", "ru"),
        ("Sen kimsin?", "tr"),
        ("你是谁？", "zh"),
    ],
)
def test_identity_request_language_is_preserved(text_value: str, language: str) -> None:
    assert detect_response_language(text_value) == language


def test_identity_contract_keeps_arima_public_and_provider_internal() -> None:
    prompt = ProviderPromptBuilder.system_instructions
    assert "user-facing assistant is Arima" in prompt
    assert "Never present Nemotron, NVIDIA" in prompt
    assert "never say you were trained by NVIDIA" in prompt
    assert "explicit technical question" in prompt


def test_voice_identity_contract_is_multilingual_and_provider_safe() -> None:
    prompt = ProviderPromptBuilder.voice_system_instructions
    assert "user-facing assistant is Arima" in prompt
    assert all(language in prompt for language in ("en", "fa", "ar", "ru", "tr", "zh"))


def test_provider_prompt_carries_each_identity_request_language() -> None:
    for request, language in (
        ("Who are you?", "en"),
        ("تو کی هستی؟", "fa"),
        ("من أنت؟", "ar"),
        ("Кто ты?", "ru"),
        ("Sen kimsin?", "tr"),
        ("你是谁？", "zh"),
    ):
        payload = json.loads(
            ProviderPromptBuilder.build_gateway_prompt(query=request, context={})
        )
        assert payload["response_language"] == language


def test_technical_provider_question_remains_distinct_from_identity() -> None:
    prompt = ProviderPromptBuilder.system_instructions
    assert "question about the underlying model or provider is a separate" in prompt


@pytest.mark.parametrize(
    "text_value",
    ["Hello", "سلام", "مرحبا", "Привет", "Merhaba", "你好", "Who are you?"],
)
def test_normal_conversation_is_not_treated_as_workspace_evidence(text_value: str) -> None:
    assert ProviderPromptBuilder.request_mode(text_value) == "conversation"


def test_workspace_requests_keep_evidence_backed_mode() -> None:
    assert ProviderPromptBuilder.request_mode("What is my current portfolio balance?") == "evidence_backed"
    assert ProviderPromptBuilder.request_mode("موجودی پرتفوی من چقدر است؟") == "evidence_backed"


def test_persian_greeting_is_detected_as_persian() -> None:
    assert detect_response_language("سلام") == "fa"


@pytest.mark.parametrize(
    ("turns", "languages"),
    [
        (("سلام", "فارسی می‌تونی صحبت کنی؟"), ("fa", "fa")),
        (("Привет", "Пожалуйста, говори по-русски."), ("ru", "ru")),
        (("Hello", "Can you speak Persian?"), ("en", "en")),
    ],
)
def test_multi_turn_conversation_keeps_current_language_and_mode(
    turns: tuple[str, str], languages: tuple[str, str]
) -> None:
    for turn, language in zip(turns, languages):
        payload = json.loads(
            ProviderPromptBuilder.build_gateway_prompt(query=turn, context={})
        )
        assert detect_response_language(turn) == language
        assert payload["response_language"] == language
        assert payload["request_mode"] == "conversation"


def test_workspace_mode_is_reintroduced_only_for_workspace_request() -> None:
    conversation = json.loads(
        ProviderPromptBuilder.build_gateway_prompt(query="سلام", context={})
    )
    workspace = json.loads(
        ProviderPromptBuilder.build_gateway_prompt(
            query="What is my most urgent email?", context={}
        )
    )
    next_conversation = json.loads(
        ProviderPromptBuilder.build_gateway_prompt(
            query="فارسی می‌تونی صحبت کنی؟", context={}
        )
    )
    assert conversation["request_mode"] == "conversation"
    assert workspace["request_mode"] == "evidence_backed"
    assert next_conversation["request_mode"] == "conversation"


@pytest.mark.parametrize(
    ("query", "mode", "language"),
    [
        ("When was World War II?", "conversation", "en"),
        ("مثلاً اگه من فارسی حرف بزنم می‌فهمی؟", "conversation", "fa"),
        ("Говори по-русски, пожалуйста.", "conversation", "ru"),
        ("سلام، حالت چطوره؟", "conversation", "fa"),
        ("Привет, как дела?", "conversation", "ru"),
        ("Go check my portfolio.", "evidence_backed", "en"),
        ("How much money did I earn today?", "evidence_backed", "en"),
        ("What's your analysis on BTC?", "market", "en"),
    ],
)
def test_request_mode_is_language_independent_and_security_preserving(
    query: str, mode: str, language: str
) -> None:
    assert ProviderPromptBuilder.request_mode(query) == mode
    assert detect_response_language(query) == language


def test_conversation_validation_does_not_replace_general_answer() -> None:
    from app.orchestration.response_validation import ResponseValidator

    result = ResponseValidator().validate(
        "World War II began in 1939.",
        allowed_evidence_ids=frozenset(),
        request_mode="conversation",
    )
    assert result.accepted is True


def test_evidence_validation_remains_fail_closed() -> None:
    from app.orchestration.response_validation import ResponseValidator

    result = ResponseValidator().validate(
        "Your portfolio balance is £10,000.",
        allowed_evidence_ids=frozenset(),
        request_mode="evidence_backed",
    )
    assert result.accepted is False
