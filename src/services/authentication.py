"""Turning an API key into a caller, and minting keys in the first place.

Run as a script to mint one: just new-api-key "Some client"
"""

import json
import secrets
import sys

from src.errors.exceptions import InvalidCredentials, MissingCredentials
from src.models.caller import ApiKeyRecord, Caller, hash_api_key
from src.repositories.api_keys import ApiKeyRepository

ANONYMOUS = Caller(id="anonymous", name="anonymous")


class AuthenticationService:
    """Decides who a request belongs to, or refuses it.

    Everything downstream -- usage records, rate limit buckets, audit logs --
    keys off the identity this returns, which is why it runs before them.
    """

    def __init__(
        self, repository: ApiKeyRepository, *, require_api_key: bool = True
    ) -> None:
        self._repository = repository
        self._require_api_key = require_api_key

    async def authenticate(self, raw_key: str | None) -> Caller:
        if not raw_key:
            if not self._require_api_key:
                return ANONYMOUS
            raise MissingCredentials()

        caller = await self._repository.find_by_hash(hash_api_key(raw_key))

        if caller is None:
            # Identical error whether the key is unknown, revoked or malformed:
            # distinguishing them helps anyone probing for valid keys.
            raise InvalidCredentials()

        return caller


def mint_key(name: str) -> tuple[str, ApiKeyRecord]:
    """Generate a key and the record we store for it.

    The key is returned once and never persisted -- only its digest is, so a
    leaked config or database dump yields nothing usable.
    """
    key = f"sk-{secrets.token_urlsafe(32)}"

    record = ApiKeyRecord(
        id=secrets.token_hex(4),
        name=name,
        key_hash=hash_api_key(key),
    )

    return key, record


def main() -> None:
    key, record = mint_key(" ".join(sys.argv[1:]) or "unnamed")

    print("Give this to the client -- it cannot be recovered later:\n")
    print(f"  {key}\n")
    print("Add this to API_KEYS in your .env:\n")
    print(f"  API_KEYS='{json.dumps([record.model_dump()])}'\n")


if __name__ == "__main__":
    main()
