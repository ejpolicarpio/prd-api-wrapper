from pydantic import BaseModel, Field


class CompletionRequest(BaseModel):
    """What *our* clients send us. Deliberately smaller than the upstream schema."""

    prompt: str = Field(min_length=1, max_length=8000, examples=["Say hello."])
    model: str | None = Field(
        default=None,
        description="Overrides UPSTREAM_MODEL. Must be a model the upstream has.",
        examples=["llama3.2:3b"],
    )
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=4096)


class CompletionUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0


class CompletionResponse(BaseModel):
    """What we return. Stable even if we swap the provider underneath."""

    id: str
    model: str
    content: str
    usage: CompletionUsage
