import re
from dataclasses import dataclass
from enum import Enum

from app.voice.schemas import (
    VoiceCommand,
    VoiceNavigationAction,
    VoicePanelAction,
)


class VoiceCommandName(str, Enum):
    ENTER_ARIMA = "enter_arima"
    EXIT_NEURAL_CORE = "exit_neural_core"
    OPEN_EXECUTIVE = "open_executive"
    OPEN_PORTFOLIO = "open_portfolio"
    OPEN_QUANT_RESEARCH = "open_quant_research"
    OPEN_GROWTH_STUDIO = "open_growth_studio"
    OPEN_PROJECTS = "open_projects"
    OPEN_INTELLIGENCE = "open_intelligence"
    GO_BACK = "go_back"
    STOP_SPEAKING = "stop_speaking"
    CANCEL = "cancel"
    REPEAT = "repeat"
    TODAY_BRIEFING = "today_briefing"
    PENDING_APPROVALS = "pending_approvals"
    GROWTH_TODAY = "growth_today"


@dataclass(frozen=True, slots=True)
class CommandAction:
    command: VoiceCommand
    response: str
    navigation: VoiceNavigationAction | None = None
    panel: VoicePanelAction | None = None


_ALIASES: tuple[tuple[VoiceCommandName, tuple[str, ...]], ...] = (
    (
        VoiceCommandName.ENTER_ARIMA,
        (
            "enter arima",
            "open your intelligence",
            "show me your mind",
            "enter the neural core",
        ),
    ),
    (
        VoiceCommandName.EXIT_NEURAL_CORE,
        (
            "exit the neural core",
            "return to arima",
            "return to the avatar",
            "exit arima",
        ),
    ),
    (
        VoiceCommandName.GROWTH_TODAY,
        (
            "show what growth created today",
            "show me what growth created today",
            "what did growth create today",
            "what has growth created today",
        ),
    ),
    (
        VoiceCommandName.OPEN_QUANT_RESEARCH,
        (
            "open quant research",
            "take me to quant research",
            "show quant research",
        ),
    ),
    (
        VoiceCommandName.OPEN_GROWTH_STUDIO,
        ("open growth studio", "take me to growth studio"),
    ),
    (
        VoiceCommandName.OPEN_PORTFOLIO,
        ("open portfolio", "open my portfolio", "take me to my portfolio"),
    ),
    (
        VoiceCommandName.OPEN_EXECUTIVE,
        ("open executive dashboard", "open executive", "take me home"),
    ),
    (
        VoiceCommandName.OPEN_PROJECTS,
        ("open projects", "show my projects", "take me to projects"),
    ),
    (
        VoiceCommandName.OPEN_INTELLIGENCE,
        (
            "open intelligence",
            "show intelligence",
        ),
    ),
    (
        VoiceCommandName.TODAY_BRIEFING,
        (
            "show today's briefing",
            "show todays briefing",
            "what's up today",
            "whats up today",
            "brief me",
        ),
    ),
    (
        VoiceCommandName.PENDING_APPROVALS,
        ("show pending approvals", "open pending approvals"),
    ),
    (
        VoiceCommandName.STOP_SPEAKING,
        ("stop speaking", "stop talking", "be quiet"),
    ),
    (
        VoiceCommandName.GO_BACK,
        (
            "go back",
            "take me back",
        ),
    ),
    (VoiceCommandName.CANCEL, ("cancel", "cancel that")),
    (VoiceCommandName.REPEAT, ("repeat", "say that again", "repeat that")),
)


def normalize_transcript(transcript: str) -> str:
    return re.sub(r"[^a-z0-9\s']", " ", transcript.lower()).strip()


def extract_command(transcript: str) -> VoiceCommand | None:
    normalized = " ".join(normalize_transcript(transcript).split())
    normalized = re.sub(r"^(hi|hey|hello)\s+arima[\s,]*", "", normalized)
    for name, aliases in _ALIASES:
        if any(alias in normalized for alias in aliases):
            return VoiceCommand(
                name=name.value,
                transcript=transcript,
                confidence=1.0,
            )
    return None


def resolve_command(command: VoiceCommand) -> CommandAction:
    name = VoiceCommandName(command.name)
    navigation = {
        VoiceCommandName.ENTER_ARIMA: VoiceNavigationAction(
            path="/executive?enter=true",
            label="Arima Neural Core",
            focus="enter",
        ),
        VoiceCommandName.EXIT_NEURAL_CORE: VoiceNavigationAction(
            path="back",
            label="Arima avatar",
            focus="exit",
        ),
        VoiceCommandName.OPEN_EXECUTIVE: VoiceNavigationAction(
            path="/executive", label="Executive"
        ),
        VoiceCommandName.OPEN_PORTFOLIO: VoiceNavigationAction(
            path="/portfolio-lab", label="Portfolio"
        ),
        VoiceCommandName.OPEN_QUANT_RESEARCH: VoiceNavigationAction(
            path="/quant-research", label="Quant Research"
        ),
        VoiceCommandName.OPEN_GROWTH_STUDIO: VoiceNavigationAction(
            path="/growth-studio", label="Growth Studio"
        ),
        VoiceCommandName.OPEN_PROJECTS: VoiceNavigationAction(
            path="/projects", label="Projects"
        ),
        VoiceCommandName.OPEN_INTELLIGENCE: VoiceNavigationAction(
            path="/executive", label="Intelligence", focus="intelligence"
        ),
        VoiceCommandName.GO_BACK: VoiceNavigationAction(
            path="back", label="Previous view"
        ),
        VoiceCommandName.GROWTH_TODAY: VoiceNavigationAction(
            path="/growth-studio",
            label="Growth Studio",
            focus="today",
        ),
    }.get(name)
    panel = {
        VoiceCommandName.TODAY_BRIEFING: VoicePanelAction(
            panel="executive_briefing", focus="today"
        ),
        VoiceCommandName.PENDING_APPROVALS: VoicePanelAction(
            panel="executive_briefing", focus="approvals"
        ),
        VoiceCommandName.GROWTH_TODAY: VoicePanelAction(
            panel="growth_output", focus="today"
        ),
    }.get(name)
    responses = {
        VoiceCommandName.ENTER_ARIMA: "Opening my neural core.",
        VoiceCommandName.EXIT_NEURAL_CORE: "Returning to my avatar view.",
        VoiceCommandName.OPEN_EXECUTIVE: "Opening your Executive Experience.",
        VoiceCommandName.OPEN_PORTFOLIO: "Opening Portfolio Lab.",
        VoiceCommandName.OPEN_QUANT_RESEARCH: "Opening Quant Research.",
        VoiceCommandName.OPEN_GROWTH_STUDIO: "Opening Growth Studio.",
        VoiceCommandName.OPEN_PROJECTS: "Opening Projects.",
        VoiceCommandName.OPEN_INTELLIGENCE: "Opening executive intelligence.",
        VoiceCommandName.GO_BACK: "Going back.",
        VoiceCommandName.STOP_SPEAKING: "Speech stopped.",
        VoiceCommandName.CANCEL: "Voice session cancelled.",
        VoiceCommandName.REPEAT: "Repeating my last response.",
        VoiceCommandName.TODAY_BRIEFING: (
            "Your briefing is ready. Priorities, portfolio, projects, "
            "approvals and platform health are in view."
        ),
        VoiceCommandName.PENDING_APPROVALS: (
            "Opening your pending approvals."
        ),
        VoiceCommandName.GROWTH_TODAY: (
            "Opening the work Growth Studio created today."
        ),
    }
    return CommandAction(
        command=command,
        response=responses[name],
        navigation=navigation,
        panel=panel,
    )
