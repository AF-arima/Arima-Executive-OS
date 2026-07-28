from enum import Enum

from app.voice.exceptions import InvalidVoiceStateTransition


class VoiceState(str, Enum):
    IDLE = "idle"
    REQUESTING_MICROPHONE = "requesting_microphone"
    LISTENING = "listening"
    SPEECH_DETECTED = "speech_detected"
    PROCESSING = "processing"
    THINKING = "thinking"
    TOOL_EXECUTION = "tool_execution"
    AWAITING_APPROVAL = "awaiting_approval"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    ERROR = "error"
    CANCELLED = "cancelled"


LEGAL_TRANSITIONS: dict[VoiceState, frozenset[VoiceState]] = {
    VoiceState.IDLE: frozenset(
        {
            VoiceState.REQUESTING_MICROPHONE,
            VoiceState.LISTENING,
            VoiceState.PROCESSING,
            VoiceState.CANCELLED,
            VoiceState.ERROR,
        }
    ),
    VoiceState.REQUESTING_MICROPHONE: frozenset(
        {
            VoiceState.LISTENING,
            VoiceState.CANCELLED,
            VoiceState.ERROR,
        }
    ),
    VoiceState.LISTENING: frozenset(
        {
            VoiceState.SPEECH_DETECTED,
            VoiceState.PROCESSING,
            VoiceState.CANCELLED,
            VoiceState.ERROR,
        }
    ),
    VoiceState.SPEECH_DETECTED: frozenset(
        {
            VoiceState.PROCESSING,
            VoiceState.LISTENING,
            VoiceState.CANCELLED,
            VoiceState.ERROR,
        }
    ),
    VoiceState.PROCESSING: frozenset(
        {
            VoiceState.THINKING,
            VoiceState.SPEAKING,
            VoiceState.COMPLETED,
            VoiceState.CANCELLED,
            VoiceState.ERROR,
        }
    ),
    VoiceState.THINKING: frozenset(
        {
            VoiceState.TOOL_EXECUTION,
            VoiceState.AWAITING_APPROVAL,
            VoiceState.SPEAKING,
            VoiceState.CANCELLED,
            VoiceState.ERROR,
        }
    ),
    VoiceState.TOOL_EXECUTION: frozenset(
        {
            VoiceState.THINKING,
            VoiceState.AWAITING_APPROVAL,
            VoiceState.SPEAKING,
            VoiceState.CANCELLED,
            VoiceState.ERROR,
        }
    ),
    VoiceState.AWAITING_APPROVAL: frozenset(
        {
            VoiceState.THINKING,
            VoiceState.CANCELLED,
            VoiceState.ERROR,
        }
    ),
    VoiceState.SPEAKING: frozenset(
        {
            VoiceState.INTERRUPTED,
            VoiceState.COMPLETED,
            VoiceState.CANCELLED,
            VoiceState.ERROR,
        }
    ),
    VoiceState.INTERRUPTED: frozenset(
        {
            VoiceState.IDLE,
            VoiceState.PROCESSING,
            VoiceState.CANCELLED,
        }
    ),
    VoiceState.COMPLETED: frozenset(
        {
            VoiceState.IDLE,
            VoiceState.LISTENING,
            VoiceState.PROCESSING,
            VoiceState.INTERRUPTED,
            VoiceState.CANCELLED,
        }
    ),
    VoiceState.ERROR: frozenset(
        {
            VoiceState.IDLE,
            VoiceState.PROCESSING,
            VoiceState.CANCELLED,
        }
    ),
    VoiceState.CANCELLED: frozenset(),
}


def validate_transition(current: VoiceState, target: VoiceState) -> None:
    if target is current:
        return
    if target not in LEGAL_TRANSITIONS[current]:
        raise InvalidVoiceStateTransition(
            f"Illegal voice state transition: {current.value} "
            f"to {target.value}"
        )
