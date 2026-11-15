"""Domain-specific exceptions for ACCOMMODATE."""


class AccommodateError(Exception):
    """Base exception for all accommodate domain errors."""
    default_code = 'ACCOMMODATE_ERROR'

    def __init__(self, message: str, code: str = None):
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code


class PermissionDeniedError(AccommodateError):
    """Actor lacks required role or authority for the requested action."""
    default_code = 'PERMISSION_DENIED'


class CapacityRequiredError(AccommodateError):
    """Action blocked because the group has reached its declared member limit."""
    default_code = 'CAPACITY_REQUIRED'


class OverlapConflictError(AccommodateError):
    """Action blocked because the student already holds an active membership with overlapping dates."""
    default_code = 'OVERLAPPING_MEMBERSHIP_CONFLICT'


class GroupStateError(AccommodateError):
    """Action invalid for the group's current lifecycle or recruitment state."""
    default_code = 'INVALID_GROUP_STATE'


class ApplicationStateError(AccommodateError):
    """Action invalid for the application's current state."""
    default_code = 'INVALID_APPLICATION_STATE'
