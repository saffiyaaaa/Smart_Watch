"""Watchlist request and response contracts."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.watchlist.symbols import InvalidSymbolError, normalize_symbol


class WatchlistCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped


class SymbolRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=10, examples=["AAPL"])

    @field_validator("symbol")
    @classmethod
    def _normalize(cls, v: str) -> str:
        """Normalise at the edge so the rest of the system only ever sees the
        canonical form. Reuses the domain function rather than repeating the
        regex, so schema and database can never disagree about validity."""
        try:
            return normalize_symbol(v)
        except InvalidSymbolError as exc:
            raise ValueError(exc.reason) from exc


class WatchlistItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    symbol: str
    created_at: datetime


class WatchlistResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    created_at: datetime
    items: list[WatchlistItemResponse] = []
