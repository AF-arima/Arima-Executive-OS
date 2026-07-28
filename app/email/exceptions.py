class EmailProviderError(Exception):
    """A configured transactional provider could not accept a message."""


class EmailProviderConfigurationError(EmailProviderError):
    """Transactional email has not been configured for this environment."""
