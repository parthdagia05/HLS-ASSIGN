"""Pydantic models describing API request/response shapes.

FastAPI uses these to validate responses and to generate the interactive
OpenAPI docs at /docs. Keeping them in one file makes the API contract easy to
read at a glance.
"""

from __future__ import annotations

from pydantic import BaseModel


class Suggestion(BaseModel):
    query: str
    count: int


class SuggestResponse(BaseModel):
    prefix: str                  # the normalised prefix we actually searched for
    mode: str                    # ranking used: "basic" or "trending"
    suggestions: list[Suggestion]


class SearchRequest(BaseModel):
    query: str


class SearchResponse(BaseModel):
    message: str                 # always "Searched" (the dummy search response)
    query: str                   # the normalised query we recorded


class TrendingResponse(BaseModel):
    trending: list[Suggestion]
