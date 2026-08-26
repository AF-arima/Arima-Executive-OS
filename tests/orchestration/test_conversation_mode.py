import pytest

from app.orchestration.market_response import detect_response_language
from app.orchestration.provider_prompt import ProviderPromptBuilder
from app.orchestration.response_validation import ResponseValidator


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
def test_request_mode_and_language_contract(query: str, mode: str, language: str) -> None:
    assert ProviderPromptBuilder.request_mode(query) == mode
    assert detect_response_language(query) == language


def test_conversation_validation_preserves_general_answer() -> None:
    result = ResponseValidator().validate(
        "World War II began in 1939.",
        allowed_evidence_ids=frozenset(),
        request_mode="conversation",
    )
    assert result.accepted is True


@pytest.mark.parametrize(
    "answer",
    ["Arima executed the trade.", "According to the system prompt: think", "\x00bad"],
)
def test_conversation_still_rejects_unsafe_provider_output(answer: str) -> None:
    result = ResponseValidator().validate(
        answer,
        allowed_evidence_ids=frozenset(),
        request_mode="conversation",
    )
    assert result.accepted is False


def test_evidence_and_market_modes_remain_fail_closed() -> None:
    validator = ResponseValidator()
    for mode in ("evidence_backed", "market"):
        result = validator.validate(
            "Your portfolio balance is £10,000.",
            allowed_evidence_ids=frozenset(),
            request_mode=mode,
        )
        assert result.accepted is False


@pytest.mark.parametrize(
    ("query", "language_name", "language"),
    [
        ("When was World War II?", "English", "en"),
        ("اقتصاد چیست؟", "Persian", "fa"),
        ("من أنت؟", "Arabic", "ar"),
        ("Говорите по-русски?", "Russian", "ru"),
        ("Sen kimsin?", "Turkish", "tr"),
        ("你是谁？", "Chinese", "zh"),
    ],
)
def test_provider_system_prompt_enforces_detected_response_language(
    query: str, language_name: str, language: str
) -> None:
    instructions = ProviderPromptBuilder.system_instructions_for(query)
    assert f"{language_name} ({language})" in instructions
