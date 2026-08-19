import json

from fastapi import APIRouter, Depends
from fastapi.exceptions import HTTPException
from sqlalchemy.orm import Session

import app_setting_keys as keys
import schemas
from deps import get_db
from settings import Settings

router = APIRouter()

# Every navigable top-level page, in the app's built-in default order. The frontend's
# Sidebar/MobileNav read this same set from the API rather than hardcoding it, so a saved
# order/default can never reference a page id that no longer exists.
NAV_PAGE_IDS = ["today", "board", "calendar", "health", "engineering"]


@router.get("/api/settings/navigation", response_model=schemas.NavPreferences)
def get_navigation_preferences(db: Session = Depends(get_db)):
    settings = Settings(db)
    saved_order = json.loads(settings.nav_order or "[]")
    # Drop any id no longer in NAV_PAGE_IDS, then append missing ids (a page added after
    # this was last saved) in default order.
    order = [p for p in saved_order if p in NAV_PAGE_IDS]
    order += [p for p in NAV_PAGE_IDS if p not in order]

    default_page = settings.default_page
    if default_page not in NAV_PAGE_IDS:
        default_page = "today"

    return schemas.NavPreferences(order=order, default_page=default_page)


@router.put("/api/settings/navigation", response_model=schemas.NavPreferences)
def set_navigation_preferences(prefs: schemas.NavPreferences, db: Session = Depends(get_db)):
    if set(prefs.order) != set(NAV_PAGE_IDS):
        raise HTTPException(status_code=400, detail="order must contain exactly the known nav pages")
    if prefs.default_page not in prefs.order:
        raise HTTPException(status_code=400, detail="default_page must be one of the nav pages")

    settings = Settings(db)
    settings.set(keys.NAV_ORDER, json.dumps(prefs.order))
    settings.set(keys.DEFAULT_PAGE, prefs.default_page)
    db.commit()
    return prefs
