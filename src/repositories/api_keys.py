from collections.abc import Iterable
from typing import Protocol

from src.models.caller import ApiKeyRecord, Caller


class ApiKeyRepository(Protocol):
    """Where keys are looked up. Deliberately async even though the current
    implementation needs no I/O -- phase 7 replaces it with a Postgres-backed
    one, and nothing that depends on this should have to change."""

    async def find_by_hash(self, key_hash: str) -> Caller | None: ...


class SettingsApiKeyRepository:
    """Keys supplied through configuration.

    Lookup is a dict keyed by digest, so no secret is ever compared byte by
    byte -- which sidesteps timing attacks rather than defending against them.
    """

    def __init__(self, records: Iterable[ApiKeyRecord]) -> None:
        self._callers_by_hash = {
            record.key_hash.lower(): Caller(id=record.id, name=record.name)
            for record in records
        }

    async def find_by_hash(self, key_hash: str) -> Caller | None:
        return self._callers_by_hash.get(key_hash.lower())
