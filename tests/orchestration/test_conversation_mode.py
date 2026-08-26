from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.orchestration.market_response import detect_response_language
from app.orchestration.pipeline import OrchestrationPipeline
from app.orchestration.planner import OrchestrationPlanner
from app.orchestration.provider_prompt import ProviderPromptBuilder
from app.orchestration.response_validation import ResponseValidator
from app.orchestration.schemas import OrchestrationIntent, PlanTarget


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
        ("What is economy in Persian language?", "conversation", "fa"),
        ("Explain economy in Russian.", "conversation", "ru"),
        ("Explain quantitative trading in Russian.", "conversation", "ru"),
        ("اقتصاد چیست؟", "conversation", "fa"),
        ("Что такое экономика?", "conversation", "ru"),
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


def test_identity_contract_is_system_level_and_provider_safe() -> None:
    instructions = ProviderPromptBuilder.system_instructions_for("Who are you?")
    assert "Arima" in instructions
    for forbidden_identity in ("Nemotron", "NVIDIA", "OpenAI", "Anthropic"):
        assert forbidden_identity in instructions
    assert "Only an explicit technical model/provider question" in instructions


@pytest.mark.parametrize(
    "query",
    [
        "What day is it?",
        "What date is it?",
        "What is today's date?",
        "What is the date today?",
    ],
)
def test_natural_date_questions_use_runtime_date_tool(query: str) -> None:
    plan = OrchestrationPlanner().plan(OrchestrationIntent.GENERAL, query)
    assert any(
        step.target is PlanTarget.TOOL and step.name == "runtime.current_date"
        for step in plan.steps
    )


def test_native_workspace_tools_are_not_offered_to_conversation() -> None:
    pipeline = object.__new__(OrchestrationPipeline)
    pipeline.provider_prompt = ProviderPromptBuilder()
    pipeline.native_tool_registry = SimpleNamespace(declarations=lambda: ("tool",))
    conversation = SimpleNamespace(
        request=SimpleNamespace(
            content="سلام",
            metadata={"tenant_id": "tenant", "workspace_id": "workspace"},
        )
    )
    workspace = SimpleNamespace(
        request=SimpleNamespace(
            content="Go check my portfolio.",
            metadata={"tenant_id": "tenant", "workspace_id": "workspace"},
        )
    )
    with patch(
        "app.core.config.get_settings",
        return_value=SimpleNamespace(microsoft_integration_enabled=True),
    ):
        assert pipeline._native_tools_enabled(conversation) is False
        assert pipeline._native_tools_enabled(workspace) is True


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
