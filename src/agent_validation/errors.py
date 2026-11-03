"""Service exception hierarchy."""


class ValidationServiceError(Exception):
    """Base class for expected service errors."""


class InvalidRequestError(ValidationServiceError):
    """The caller supplied an invalid request."""


class ProfileError(ValidationServiceError):
    """A validation profile cannot be loaded or resolved."""


class ProvisioningError(ValidationServiceError):
    """A solution revision cannot be prepared."""


class BenchmarkError(ValidationServiceError):
    """The benchmark cannot execute or score an episode."""


class StorageError(ValidationServiceError):
    """Validation evidence cannot be persisted."""


class ServiceCapacityError(ValidationServiceError):
    """The configured service-wide capacity has been reached."""


class SolutionProtocolError(Exception):
    """The solution emitted an invalid protocol message."""


class SolutionTimeoutError(Exception):
    """The solution exceeded its episode time limit."""


class SolutionOutputLimitError(Exception):
    """The solution exceeded its configured output limit."""


class SolutionExitedError(Exception):
    """The solution process exited before a final response."""
