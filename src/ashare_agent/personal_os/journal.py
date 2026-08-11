from __future__ import annotations

from typing import Any, Mapping

from .store import PersonalOSStore


class InvestmentJournal:
    """Store decisions, outcomes and lessons as non-executable evidence."""

    def __init__(self, store: PersonalOSStore) -> None:
        self.store = store

    def create(self, values: Mapping[str, Any]) -> dict[str, Any]:
        return self.store.create_journal(values)

    def update(self, journal_id: str, values: Mapping[str, Any]) -> dict[str, Any]:
        return self.store.update_journal(journal_id, values)

    def archive(self, journal_id: str, expected_version: int) -> dict[str, Any]:
        return self.store.archive_journal(journal_id, expected_version)

    def list(self, limit: int = 200) -> list[dict[str, Any]]:
        return self.store.journals(limit)

