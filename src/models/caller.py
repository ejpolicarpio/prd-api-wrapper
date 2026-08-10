import hashlib

from pydantic import BaseModel, Field


class Caller(BaseModel):
    """Who is making the request, once their key has been recognised."""

    id: str
    name: str


class ApiKeyRecord(BaseModel):
    """A key as we store it: the digest, never the key itself.

    A config file or database dump therefore leaks nothing usable. Plain
    SHA-256 is the right choice *here* because API keys are long random
    strings; a password, being low-entropy and guessable, would need a slow
    hash like argon2 instead.
    """

    id: str
    name: str
    key_hash: str = Field(min_length=64, max_length=64)


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()
