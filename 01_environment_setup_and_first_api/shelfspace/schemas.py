"""Response schemas.

A schema is not decoration. It is the *contract*: FastAPI validates every
outgoing payload against it, strips anything not declared, and publishes it as
OpenAPI so the docs can never drift from the code.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """What a load balancer or Kubernetes probe reads to decide if we live."""

    status: Literal["ok", "degraded"] = Field(
        description="'ok' means this process can serve traffic."
    )
    service: str = Field(description="Which application answered.")
    version: str = Field(description="Deployed version — invaluable in an incident.")
    environment: str = Field(description="development | staging | production")
    uptime_seconds: float = Field(
        ge=0, description="Seconds since this process started serving."
    )
    checked_at: datetime = Field(description="Server time, UTC, with an offset.")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "status": "ok",
                    "service": "Shelfspace API",
                    "version": "0.1.0",
                    "environment": "development",
                    "uptime_seconds": 12.4,
                    "checked_at": "2026-08-20T09:15:00Z",
                }
            ]
        }
    }


class Book(BaseModel):
    """One book. Day 01 serves these from memory; Day 09 moves them to Postgres."""

    id: int
    isbn: str = Field(examples=["978-0-14-303943-3"])
    title: str
    author: str
    # Money as a string — JSON has one numeric type (IEEE-754 double) and
    # 12.10 can arrive as 12.099999999999999. Day 12 revisits this in depth.
    price: str = Field(examples=["499.00"])
    stock: int = Field(ge=0)


class BookList(BaseModel):
    """Collections get an envelope from day one.

    Returning a bare JSON array leaves you nowhere to put `total`, `page`, or
    `next` when you need pagination — and changing an array into an object later
    is a breaking change for every client. Day 12 fills this envelope out.
    """

    count: int
    items: list[Book]
