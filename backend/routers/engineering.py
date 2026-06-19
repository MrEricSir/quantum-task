from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

import github_sync
import models
import schemas
from deps import get_db

router = APIRouter()


class _EngineeringConfig(BaseModel):
    token: str = ""
    repos: List[str] = []


@router.get("/api/engineering/config")
def get_engineering_config(db: Session = Depends(get_db)):
    token, repos = github_sync.get_config(db)
    return {"configured": bool(token), "repos": repos}


@router.put("/api/engineering/config")
def set_engineering_config(body: _EngineeringConfig, db: Session = Depends(get_db)):
    github_sync.save_config(db, body.token or None, body.repos)
    return {"ok": True}


@router.post("/api/engineering/sync")
def run_engineering_sync(db: Session = Depends(get_db)):
    return github_sync.sync(db)


@router.get("/api/engineering/items", response_model=List[schemas.EngineeringItem])
def get_engineering_items(db: Session = Depends(get_db)):
    return db.query(models.EngineeringItem).filter_by(state="open").all()
