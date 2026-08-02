from datetime import datetime, timezone
from typing import Any, Dict, List

import requests as _requests
from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload

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


@router.get("/api/engineering/status-config")
def get_status_config(db: Session = Depends(get_db)):
    return github_sync.get_status_config(db)


@router.put("/api/engineering/status-config")
def set_status_config(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    github_sync.save_status_config(db, body)
    return {"ok": True}


@router.get("/api/engineering/repo-tags")
def get_repo_tags_config(db: Session = Depends(get_db)):
    return github_sync.get_repo_tags_config(db)


@router.put("/api/engineering/repo-tags")
def set_repo_tags_config(body: Dict[str, List[int]] = Body(...), db: Session = Depends(get_db)):
    github_sync.save_repo_tags_config(db, body)
    return {"ok": True}


def _item_sort_key(item: models.EngineeringItem):
    """Group by repo, then by board status/lane (items with no status last
    within their repo), then by title — keeps same-repo (same-tag) items
    clustered together instead of interleaved in arbitrary DB order."""
    return (
        item.repo.lower(),
        item.project_status is None,
        (item.project_status or "").lower(),
        item.title.lower(),
    )


@router.get("/api/engineering/items", response_model=List[schemas.EngineeringItem])
def get_engineering_items(db: Session = Depends(get_db)):
    items = (
        db.query(models.EngineeringItem)
        .options(selectinload(models.EngineeringItem.comments))
        .filter_by(state="open")
        .all()
    )
    items.sort(key=_item_sort_key)

    # Repo tags aren't a DB relationship on EngineeringItem — they're computed
    # from the repo-tag rules so the list reflects config changes immediately.
    repo_tags_config = github_sync.get_repo_tags_config(db)
    tag_ids_by_item = {item.id: github_sync._tags_for_repo(repo_tags_config, item.repo) for item in items}
    all_tag_ids = {tid for ids in tag_ids_by_item.values() for tid in ids}
    tags_by_id = (
        {t.id: t for t in db.query(models.Tag).filter(models.Tag.id.in_(all_tag_ids)).all()}
        if all_tag_ids else {}
    )

    results = []
    for item in items:
        validated = schemas.EngineeringItem.model_validate(item)
        validated.tags = [
            schemas.Tag.model_validate(tags_by_id[tid])
            for tid in tag_ids_by_item[item.id] if tid in tags_by_id
        ]
        results.append(validated)
    return results


@router.post("/api/engineering/{item_id}/refresh")
def refresh_engineering_item(item_id: int, db: Session = Depends(get_db)):
    """Force re-sync of body and comments for a single item from GitHub."""
    item = db.query(models.EngineeringItem).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    token, _ = github_sync.get_config(db)
    if not token:
        raise HTTPException(status_code=400, detail="No GitHub token configured")

    parsed = github_sync._parse_external_id(item.external_id)
    if not parsed:
        raise HTTPException(status_code=400, detail="Cannot parse external_id")
    owner, repo, _, number = parsed

    try:
        r = _requests.get(
            f"{github_sync.GITHUB_API}/repos/{owner}/{repo}/issues/{number}",
            headers=github_sync._headers(token),
            timeout=10,
        )
        r.raise_for_status()
    except _requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"GitHub request failed: {e}")
    data = r.json()

    item.title = data["title"]
    item.body = data.get("body") or ""
    raw_updated = data.get("updated_at")
    if raw_updated:
        item.body_updated_at = datetime.fromisoformat(raw_updated.replace("Z", "+00:00"))

    github_sync._sync_comments(db, item, token)
    item.synced_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(item)
    return schemas.EngineeringItem.model_validate(item)
