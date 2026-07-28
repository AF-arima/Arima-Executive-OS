from enum import Enum


class VoiceEventType(str, Enum):
    SESSION_STARTED = "session_started"
    MICROPHONE_READY = "microphone_ready"
    LISTENING_STARTED = "listening_started"
    TRANSCRIPT_PARTIAL = "transcript_partial"
    TRANSCRIPT_FINAL = "transcript_final"
    THINKING_STARTED = "thinking_started"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    APPROVAL_REQUIRED = "approval_required"
    RESPONSE_CHUNK = "response_chunk"
    NAVIGATION_REQUESTED = "navigation_requested"
    PANEL_REQUESTED = "panel_requested"
    SPEAKING_STARTED = "speaking_started"
    SPEAKING_STOPPED = "speaking_stopped"
    SESSION_COMPLETED = "session_completed"
    SESSION_FAILED = "session_failed"
