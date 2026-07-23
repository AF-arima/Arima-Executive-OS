class ServiceError(Exception):
    """Base class for expected service-layer errors."""


class ResourceNotFoundError(ServiceError):
    pass


class ResourceConflictError(ServiceError):
    pass


class PermissionDeniedError(ServiceError):
    pass


class InvalidAnalyticsRequestError(ServiceError):
    pass
