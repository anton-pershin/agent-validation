"""Service exception hierarchy."""


class ValidationServiceError(Exception):
    """Base class for expected service errors."""


class InvalidRequestError(ValidationServiceError):
    """The caller supplied an invalid request."""


class ProfileError(ValidationServiceError):
    """A validation profile cannot be loaded or resolved."""


class ProfileNotFoundError(ProfileError):
    """A requested registered profile does not exist."""


class ProvisioningError(ValidationServiceError):
    """A solution revision cannot be prepared."""


class BenchmarkError(ValidationServiceError):
    """The benchmark cannot execute or score an episode."""


class StorageError(ValidationServiceError):
    """Validation evidence cannot be persisted."""


class RunDirectoryExistsError(StorageError):
    """A run identifier has already been allocated."""


class ServiceCapacityError(ValidationServiceError):
    """The configured service-wide capacity has been reached."""


class ProvenanceError(ValidationServiceError):
    """The exact validator source and runtime cannot be established."""


class SolutionProtocolError(Exception):
    """The solution emitted an invalid protocol message."""


class SolutionTimeoutError(Exception):
    """The solution exceeded its episode time limit."""


class SolutionOutputLimitError(Exception):
    """The solution exceeded its configured output limit."""


class SolutionExitedError(Exception):
    """The solution process exited before a final response."""


def bind_run_context(error: BaseException, run_id: str, artifacts_location: str) -> None:
    """Attach public run identity without changing the original exception type."""
    error.run_id = run_id
    error.artifacts_location = artifacts_location
