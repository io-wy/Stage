from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from openagents_orchestration.app.core.database import get_db
from openagents_orchestration.app.models.item import Item
from openagents_orchestration.app.schemas.item import ItemCreate, ItemRead

router = APIRouter(prefix="/items", tags=["items"])


@router.post("/", response_model=ItemRead)
def create_item(payload: ItemCreate, db: Session = Depends(get_db)) -> Item:
    item = Item(name=payload.name)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.get("/", response_model=list[ItemRead])
def list_items(db: Session = Depends(get_db)) -> list[Item]:
    return list(db.scalars(select(Item).order_by(Item.id)))
