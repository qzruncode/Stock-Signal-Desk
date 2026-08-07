"""Typed inputs used by the standalone buy-criteria data service.

These models are not Agent routing capabilities. They validate structured
board identities supplied to the existing analysis service.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DomainBoardQuerySpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    label: str = Field(min_length=1, max_length=64)
    catalog_snapshot_id: str | None = Field(default=None, min_length=1, max_length=64)
    board_id: str | None = Field(default=None, min_length=1, max_length=64)
    board_name: str | None = Field(default=None, min_length=1, max_length=64)
    role_id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_]{0,31}$")
    role_label: str | None = Field(default=None, min_length=1, max_length=64)
    board_queries: list[str] = Field(default_factory=list, max_length=4)
    mapping_type: Literal["catalog_binding", "unresolved"]
    rationale: str = Field(default="", max_length=240)
    unresolved_parts: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_resolution(self) -> "DomainBoardQuerySpec":
        self.board_queries = list(
            dict.fromkeys(value.strip() for value in self.board_queries if value.strip())
        )
        self.unresolved_parts = list(
            dict.fromkeys(value.strip() for value in self.unresolved_parts if value.strip())
        )
        if self.mapping_type == "unresolved":
            if self.board_queries or self.board_id or self.board_name:
                raise ValueError("unresolved domains cannot contain bound board identity")
            if not self.unresolved_parts:
                self.unresolved_parts = [self.label]
        elif not self.board_queries:
            raise ValueError("resolved domains require at least one board_query")
        if bool(self.board_id) != bool(self.board_name):
            raise ValueError("board_id and board_name must be supplied together")
        return self


class InvestmentThesisContext(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    summary: str = Field(default="", max_length=400)
    domains: list[DomainBoardQuerySpec] = Field(default_factory=list, max_length=512)


__all__ = ["DomainBoardQuerySpec", "InvestmentThesisContext"]
