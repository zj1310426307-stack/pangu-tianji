"""Explicit failure types for the fail-closed Quant Research Lab."""


class QuantLabError(RuntimeError):
    """Base error for experiment governance and evidence failures."""


class ContractError(QuantLabError, ValueError):
    """Reject an invalid experiment, split, benchmark or result contract."""


class DatasetBlockedError(QuantLabError):
    """Stop an experiment whose point-in-time evidence is not trustworthy."""


class ArtifactIntegrityError(QuantLabError):
    """Reject missing, mutable or hash-mismatched experiment artifacts."""


class ImmutableExperimentError(QuantLabError):
    """Prevent mutation of a completed experiment run."""


class InvalidStateTransitionError(QuantLabError):
    """Prevent an experiment from skipping required lifecycle states."""
