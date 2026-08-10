from enum import StrEnum


class CircuitState(StrEnum):
    CLOSED = "closed"  # everything through
    OPEN = "open"  # provider is down; fail immediately
    HALF_OPEN = "half_open"  # let a trial request decide
