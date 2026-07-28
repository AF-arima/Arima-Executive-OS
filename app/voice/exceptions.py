class VoiceError(Exception):
    """Base voice gateway exception."""


class VoiceSessionNotFound(VoiceError):
    pass


class VoiceSessionAccessDenied(VoiceError):
    pass


class InvalidVoiceStateTransition(VoiceError):
    pass


class VoicePermissionDenied(VoiceError):
    pass


class VoiceGatewayUnavailable(VoiceError):
    pass
