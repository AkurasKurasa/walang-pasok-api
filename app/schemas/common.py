from math import ceil
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """A page of results from a list endpoint."""

    items: list[T]
    total: int = Field(description="Total matching items across all pages.")
    page: int
    size: int
    pages: int

    @classmethod
    def build(cls, items: list[T], total: int, page: int, size: int) -> "Page[T]":
        return cls(items=items, total=total, page=page, size=size, pages=ceil(total / size))


class ErrorBody(BaseModel):
    code: str = Field(examples=["not_found"])
    message: str = Field(examples=["Location 'atlantis' not found."])
    details: Any | None = None


class ErrorResponse(BaseModel):
    """Shape of every 4xx/5xx response."""

    error: ErrorBody
