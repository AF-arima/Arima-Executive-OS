"""Safe classifications for operational failures that may reach durable state."""


def safe_failure_detail(context: str, error: Exception) -> str:
    """Classify a failure without persisting provider or tenant-supplied text."""

    return f"{context} ({type(error).__name__})"
