from typing import List

from fastapi import APIRouter, Depends
from fastapi.exceptions import HTTPException
from sqlalchemy.orm import Session

import models
import schemas
from deps import get_db

router = APIRouter()


@router.get("/api/tags", response_model=List[schemas.Tag])
def get_tags(db: Session = Depends(get_db)):
    return db.query(models.Tag).order_by(models.Tag.name).all()


@router.post("/api/tags", response_model=schemas.Tag, status_code=201)
def create_tag(tag: schemas.TagCreate, db: Session = Depends(get_db)):
    if db.query(models.Tag).filter_by(name=tag.name).first():
        raise HTTPException(status_code=409, detail="Tag already exists")
    db_tag = models.Tag(**tag.model_dump())
    db.add(db_tag)
    db.commit()
    db.refresh(db_tag)
    return db_tag


@router.put("/api/tags/{tag_id}", response_model=schemas.Tag)
def update_tag(tag_id: int, tag: schemas.TagUpdate, db: Session = Depends(get_db)):
    db_tag = db.query(models.Tag).filter(models.Tag.id == tag_id).first()
    if not db_tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    if tag.name is not None:
        existing = db.query(models.Tag).filter_by(name=tag.name).first()
        if existing and existing.id != tag_id:
            raise HTTPException(status_code=409, detail="Tag name already exists")
        db_tag.name = tag.name
    if tag.color is not None:
        db_tag.color = tag.color
    if tag.is_project is not None:
        db_tag.is_project = tag.is_project
    db.commit()
    db.refresh(db_tag)
    return db_tag


@router.post("/api/tags/{tag_id}/replace")
def replace_tag(tag_id: int, body: schemas.TagReplacement, db: Session = Depends(get_db)):
    """Move all cards from tag_id to new_tag_id, then delete tag_id."""
    from_tag = db.query(models.Tag).filter(models.Tag.id == tag_id).first()
    to_tag = db.query(models.Tag).filter(models.Tag.id == body.new_tag_id).first()
    if not from_tag or not to_tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    cards_with_tag = (
        db.query(models.Card)
        .filter(models.Card.tags.any(models.Tag.id == tag_id))
        .all()
    )
    for card in cards_with_tag:
        if from_tag in card.tags:
            card.tags.remove(from_tag)
        if to_tag not in card.tags:
            card.tags.append(to_tag)
    db.delete(from_tag)
    db.commit()
    return {"ok": True}


@router.delete("/api/tags/{tag_id}")
def delete_tag(tag_id: int, db: Session = Depends(get_db)):
    db_tag = db.query(models.Tag).filter(models.Tag.id == tag_id).first()
    if not db_tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    db.delete(db_tag)
    db.commit()
    return {"ok": True}
