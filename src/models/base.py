import re
from typing import Any, ClassVar

from sqlalchemy.orm import DeclarativeBase, declared_attr


def camel_to_snake(name: str) -> str:
    name = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", name).lower()


class BaseModel(DeclarativeBase):
    """Declarative base: a table's name is derived from its class name, so
    ApiKey maps to api_key without anyone writing it twice.
    """

    #: ClassVar, so annotated declarative leaves it alone instead of trying to
    #: map it as a column.
    alias: ClassVar[str]

    @declared_attr.directive
    def __tablename__(cls) -> str:
        return camel_to_snake(cls.__name__)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        # After super(), the class is mapped, so this is a plain attribute the
        # ORM will not inspect.
        super().__init_subclass__(**kwargs)
        cls.alias = camel_to_snake(cls.__name__)
