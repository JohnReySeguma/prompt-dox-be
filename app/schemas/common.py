"""Base schema: camelCase JSON keys and ISO-8601 UTC timestamps with a Z suffix."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer
from pydantic.alias_generators import to_camel

from app.core.time import iso_z

UtcDatetime = Annotated[datetime, PlainSerializer(iso_z, return_type=str, when_used="json")]


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class Citation(CamelModel):
    page: int
    paragraph: int
