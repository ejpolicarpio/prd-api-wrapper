import sys

from loguru import logger

from src.configuration import Settings

HUMAN_FORMAT = (
    "<green>{time:HH:mm:ss.SSS}</green> | "
    "<level>{level: <7}</level> | "
    "<cyan>{name}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level> "
    "<dim>{extra}</dim>"
)


def configure_logging(settings: Settings) -> None:
    """Replace loguru's default sink with one shaped for the environment.

    In production the sink serialises to JSON, so whatever was bound with
    `logger.contextualize` -- the request id above all -- becomes a queryable
    field rather than text buried in a message. Locally it stays readable.
    """
    logger.remove()

    logger.add(
        sys.stdout,
        level=settings.LOG_LEVEL,
        serialize=settings.log_as_json,
        format=HUMAN_FORMAT,
        backtrace=False,
        # Never expand local variables into a traceback: they routinely hold
        # API keys, prompts and other things that should not reach a log.
        diagnose=False,
    )
