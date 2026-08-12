"""Stable exception taxonomy for infrastructure and API boundaries."""


class PanguError(Exception):
    """Base class for a controlled Pangu failure."""


class ConfigurationError(PanguError):
    """Configuration is missing, invalid or violates a safety invariant."""


class VersionDriftError(PanguError):
    """A runtime contract differs from the canonical version registry."""


class DataError(PanguError):
    """Required data evidence is missing, stale or malformed."""


class DatabaseError(PanguError):
    """A database is unavailable or fails an integrity check."""


class AIServiceError(PanguError):
    """The optional AI provider is unavailable or misconfigured."""


class StrategyHealthError(PanguError):
    """Strategy artifacts cannot be validated without changing the strategy."""


class BackupError(PanguError):
    """A backup cannot be completed or verified."""


class HealthCheckError(PanguError):
    """An engineering health check failed to produce a valid result."""


class AuditStoreError(PanguError):
    """The append-only engineering audit store is unavailable."""


class ObservabilityError(PanguError):
    """Telemetry, SLO, alert, incident or retention evidence is invalid."""
