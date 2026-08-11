from __future__ import annotations

from typing import Any, Mapping

from .store import PersonalOSStore


class PersonalKnowledgeBase:
    """Keep company, market and personal lessons separate from factor evidence."""

    def __init__(self, store: PersonalOSStore) -> None:
        self.store = store

    def create(self, values: Mapping[str, Any]) -> dict[str, Any]:
        return self.store.create_knowledge(values)

    def archive(self, knowledge_id: str, expected_version: int) -> dict[str, Any]:
        return self.store.archive_knowledge(knowledge_id, expected_version)

    def list(self, limit: int = 200) -> list[dict[str, Any]]:
        return self.store.knowledge_items(limit)

