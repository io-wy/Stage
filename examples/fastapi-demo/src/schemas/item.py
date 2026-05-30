from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ItemBase(BaseModel):
    name: str


class ItemCreate(ItemBase):
    pass


class ItemRead(ItemBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
