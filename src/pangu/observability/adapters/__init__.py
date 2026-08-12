"""Read-only adapters from persisted Pangu evidence into telemetry summaries."""

from .local_evidence import LocalEvidenceAdapter

__all__ = ["LocalEvidenceAdapter"]
