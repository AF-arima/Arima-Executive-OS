class VoiceError(Exception):
    """Base voice gateway exception."""


class VoiceSessionNotFound(VoiceError):
    pass


class VoiceSessionAccessDenied(VoiceError):
    pass


class VoiceSessionBusy(VoiceError):
    """A transcript is already being processed for this voice session."""


class InvalidVoiceStateTransition(VoiceError):
    pass


class VoicePermissionDenied(VoiceError):
    pass


class VoiceGatewayUnavailable(VoiceError):
    pass


class VoiceExecutionTimeout(VoiceError):
    """The provider did not complete within the voice execution deadline."""


class VoiceProviderUnavailable(VoiceError):
    """The verified provider could not execute the voice request."""
