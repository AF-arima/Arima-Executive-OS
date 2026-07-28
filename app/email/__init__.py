"""Transactional account email infrastructure."""

from app.email.factory import get_transactional_email_service
from app.email.service import TransactionalEmailService

__all__ = ["TransactionalEmailService", "get_transactional_email_service"]
