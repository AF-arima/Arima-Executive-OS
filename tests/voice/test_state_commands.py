import pytest

from app.voice.commands import VoiceCommandName, extract_command, resolve_command
from app.voice.exceptions import InvalidVoiceStateTransition
from app.voice.state import VoiceState, validate_transition


def test_legal_and_illegal_state_transitions() -> None:
    validate_transition(VoiceState.IDLE, VoiceState.LISTENING)
    validate_transition(VoiceState.THINKING, VoiceState.TOOL_EXECUTION)
    validate_transition(VoiceState.SPEAKING, VoiceState.INTERRUPTED)
    with pytest.raises(InvalidVoiceStateTransition):
        validate_transition(VoiceState.IDLE, VoiceState.SPEAKING)


@pytest.mark.parametrize(
    ("transcript", "expected"),
    [
        ("Open my portfolio", VoiceCommandName.OPEN_PORTFOLIO),
        ("Take me to Quant Research", VoiceCommandName.OPEN_QUANT_RESEARCH),
        ("What did Growth create today?", VoiceCommandName.GROWTH_TODAY),
        ("Enter Arima", VoiceCommandName.ENTER_ARIMA),
        ("Exit the neural core", VoiceCommandName.EXIT_NEURAL_CORE),
        ("Stop talking", VoiceCommandName.STOP_SPEAKING),
        ("Go back", VoiceCommandName.GO_BACK),
    ],
)
def test_command_extraction(
    transcript: str, expected: VoiceCommandName
) -> None:
    command = extract_command(transcript)
    assert command is not None
    assert command.name == expected.value


def test_unknown_command_is_not_claimed() -> None:
    assert extract_command("Analyse the implications of this project") is None


def test_navigation_and_panel_actions() -> None:
    portfolio = extract_command("Open Portfolio")
    briefing = extract_command("Show today's briefing")
    assert portfolio is not None
    assert briefing is not None
    assert resolve_command(portfolio).navigation.path == "/portfolio-lab"
    assert resolve_command(briefing).panel.panel == "executive_briefing"
    enter = extract_command("Open your intelligence")
    assert enter is not None
    assert resolve_command(enter).navigation.focus == "enter"
