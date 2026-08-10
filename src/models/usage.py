from pydantic import BaseModel


class UsageEntry(BaseModel):
    """What one request cost, as the service reports it.

    Separate from the Usage table so the service records an outcome without
    knowing anything about SQLAlchemy.
    """

    api_key_id: str
    model: str
    status_code: int
    duration_ms: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
