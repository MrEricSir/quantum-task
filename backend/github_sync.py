"""
GitHub sync: polls assigned issues and PR review requests,
upserts them into the EngineeringItem table, and marks items
closed when they're no longer open on GitHub.

external_id format: "github:{owner}/{repo}/issues/{number}"
                 or "github:{owner}/{repo}/pull/{number}"
"""
import re
from datetime import datetime, timezone

import requests
from sqlalchemy.orm import Session

import models

GITHUB_API = "https://api.github.com"


def get_config(db: Session) -> tuple[str | None, list[str]]:
    """Return (token, repos) from AppSettings. repos is [] for all accessible repos."""
    token_row = db.query(models.AppSetting).filter_by(key="github_token").first()
    repos_row = db.query(models.AppSetting).filter_by(key="github_repos").first()
    token = token_row.value.strip() if token_row and token_row.value else None
    repos = (
        [r.strip() for r in repos_row.value.splitlines() if r.strip()]
        if repos_row and repos_row.value
        else []
    )
    return token, repos


def save_config(db: Session, token: str | None, repos: list[str]) -> None:
    for key, value in [("github_token", token or ""), ("github_repos", "\n".join(repos))]:
        row = db.query(models.AppSetting).filter_by(key=key).first()
        if row:
            row.value = value
        else:
            db.add(models.AppSetting(key=key, value=value))
    db.commit()


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _is_pr(item: dict) -> bool:
    return "/pull/" in item["html_url"]


def _external_id(item: dict) -> str:
    return "github:" + item["html_url"].replace("https://github.com/", "")


def _repo_full_name(html_url: str) -> str:
    """'https://github.com/owner/repo/issues/123' → 'owner/repo'"""
    m = re.match(r"https://github\.com/([^/]+/[^/]+)/", html_url)
    return m.group(1) if m else ""


def _fetch_items(token: str, repos: list[str]) -> list[dict]:
    """Fetch open assigned issues + open PRs requesting my review."""
    h = _headers(token)
    items: list[dict] = []

    # Assigned issues
    if repos:
        for repo in repos:
            r = requests.get(
                f"{GITHUB_API}/repos/{repo}/issues",
                params={"assignee": "@me", "state": "open", "per_page": 50},
                headers=h, timeout=10,
            )
            r.raise_for_status()
            items.extend(i for i in r.json() if not _is_pr(i))
    else:
        r = requests.get(
            f"{GITHUB_API}/issues",
            params={"filter": "assigned", "state": "open", "per_page": 50},
            headers=h, timeout=10,
        )
        r.raise_for_status()
        items.extend(i for i in r.json() if not _is_pr(i))

    # PRs requesting my review
    query = "is:pr is:open review-requested:@me archived:false"
    if repos:
        query += " " + " ".join(f"repo:{repo}" for repo in repos)
    r = requests.get(
        f"{GITHUB_API}/search/issues",
        params={"q": query, "per_page": 50},
        headers=h, timeout=10,
    )
    r.raise_for_status()
    items.extend(r.json().get("items", []))

    return items


def sync(db: Session) -> dict:
    """
    Main sync entry point. Returns {created, closed, skipped, error}.

    - New open items  → create EngineeringItem (state=open)
    - Existing item, still open → refresh title
    - Existing item, now closed → set state=closed
    """
    token, repos = get_config(db)
    if not token:
        return {"created": 0, "closed": 0, "skipped": 0, "error": "No GitHub token configured"}

    try:
        open_items = {_external_id(i): i for i in _fetch_items(token, repos)}
    except Exception as e:
        return {"created": 0, "closed": 0, "skipped": 0, "error": str(e)}

    now = datetime.now(timezone.utc)
    created = closed = skipped = 0

    # Upsert open items
    for ext_id, item in open_items.items():
        existing = db.query(models.EngineeringItem).filter_by(external_id=ext_id).first()
        if existing:
            existing.title = item["title"]
            existing.state = "open"
            existing.synced_at = now
            skipped += 1
        else:
            db.add(models.EngineeringItem(
                external_id=ext_id,
                title=item["title"],
                item_type="pr" if _is_pr(item) else "issue",
                repo=_repo_full_name(item["html_url"]),
                number=item["number"],
                url=item["html_url"],
                state="open",
                synced_at=now,
            ))
            created += 1

    # Close items no longer in the open set; auto-archive linked cards
    tracked_open = db.query(models.EngineeringItem).filter_by(state="open").all()
    for eng_item in tracked_open:
        if eng_item.external_id not in open_items:
            eng_item.state = "closed"
            eng_item.synced_at = now
            closed += 1
            # Archive any cards linked to this item via external_id
            linked_cards = db.query(models.Todo).filter_by(
                external_id=eng_item.external_id, archived=False
            ).all()
            for card in linked_cards:
                card.archived = True
                card.archived_at = now
                card.updated_at = now

    db.commit()
    return {"created": created, "closed": closed, "skipped": skipped, "error": None}
