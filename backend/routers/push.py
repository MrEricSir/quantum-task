from fastapi import APIRouter, Depends
from fastapi.exceptions import HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import models
import push as push_lib
from deps import get_db

router = APIRouter()


class _PushSubscribeBody(BaseModel):
    endpoint: str
    keys: dict  # {"auth": str, "p256dh": str}


@router.get("/api/push/vapid-key")
def get_vapid_key(db: Session = Depends(get_db)):
    key = push_lib.get_vapid_public_key(db)
    if not key:
        raise HTTPException(status_code=503, detail="VAPID keys not initialised")
    return {"public_key": key}


@router.post("/api/push/subscribe", status_code=201)
def subscribe_push(body: _PushSubscribeBody, db: Session = Depends(get_db)):
    existing = db.query(models.PushSubscription).filter_by(endpoint=body.endpoint).first()
    if existing:
        existing.keys_auth   = body.keys.get("auth", "")
        existing.keys_p256dh = body.keys.get("p256dh", "")
    else:
        db.add(models.PushSubscription(
            endpoint=body.endpoint,
            keys_auth=body.keys.get("auth", ""),
            keys_p256dh=body.keys.get("p256dh", ""),
        ))
    db.commit()
    return {"ok": True}


@router.delete("/api/push/unsubscribe")
def unsubscribe_push(body: _PushSubscribeBody, db: Session = Depends(get_db)):
    db.query(models.PushSubscription).filter_by(endpoint=body.endpoint).delete()
    db.commit()
    return {"ok": True}
