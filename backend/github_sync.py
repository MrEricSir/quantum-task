"""
GitHub sync: polls assigned issues and PR review requests,
upserts them into the EngineeringItem table, and marks items
closed when they're no longer open on GitHub.

external_id format: "github:{owner}/{repo}/issues/{number}"
                 or "github:{owner}/{repo}/pull/{number}"
"""
import re
import json
import logging
from datetime import datetime, timezone

import requests
from sqlalchemy.orm import Session

import models
import app_setting_keys as setting_keys

GITHUB_API = "https://api.github.com"
GITHUB_GRAPHQL = "https://api.github.com/graphql"

log = logging.getLogger(__name__)


def get_config(db: Session) -> tuple[str | None, list[str]]:
    """Return (token, repos) from AppSettings. repos is [] for all accessible repos."""
    token_row = db.query(models.AppSetting).filter_by(key=setting_keys.GITHUB_TOKEN).first()
    repos_row = db.query(models.AppSetting).filter_by(key=setting_keys.GITHUB_REPOS).first()
    token = token_row.value.strip() if token_row and token_row.value else None
    repos = (
        [r.strip() for r in repos_row.value.splitlines() if r.strip()]
        if repos_row and repos_row.value
        else []
    )
    return token, repos


def save_config(db: Session, token: str | None, repos: list[str]) -> None:
    updates = [(setting_keys.GITHUB_REPOS, "\n".join(repos))]
    if token:  # only overwrite stored token when a new value is provided
        updates.append((setting_keys.GITHUB_TOKEN, token))
    for key, value in updates:
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


def _fetch_comments(token: str, owner: str, repo: str, number: int) -> list[dict]:
    """Fetch all issue/conversation-tab comments for a single issue or PR (up to 100)."""
    h = _headers(token)
    r = requests.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/issues/{number}/comments",
        params={"per_page": 100},
        headers=h, timeout=10,
    )
    r.raise_for_status()
    return r.json()


# CodeRabbit's bot account login (GitHub's own "[bot]" suffix convention). Hardcoded rather
# than configurable -- see CLAUDE_CODE_INTEGRATION.md's "CodeRabbit feedback integration"
# open questions for why this was left as a simple default rather than a config.toml/Settings
# key for now.
CODERABBIT_LOGIN = "coderabbitai[bot]"


def _wants_review_comment(login: str | None) -> bool:
    """True for CodeRabbit and any human reviewer; False for every other bot/app account
    (dependabot[bot], github-actions[bot], etc). Human co-workers' inline PR suggestions are
    exactly as much "helpful feedback worth surfacing" as CodeRabbit's -- the original filter
    only wanted CodeRabbit specifically because that was the concrete pain point raised, not
    because other humans' comments are unwanted. Other *bots* stay excluded: their comments
    are typically automated status noise (a CI bot, a dependency bot), not the kind of
    reviewer suggestion this feature exists to surface."""
    if not login:
        return False
    return login == CODERABBIT_LOGIN or not login.endswith("[bot]")


def _fetch_review_comments(token: str, owner: str, repo: str, number: int) -> list[dict]:
    """Fetch all PR review (inline diff) comments for a single PR (up to 100). Only PRs have
    these -- calling this for an issue number would 404."""
    h = _headers(token)
    r = requests.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}/comments",
        params={"per_page": 100},
        headers=h, timeout=10,
    )
    r.raise_for_status()
    return r.json()


def _upsert_and_prune_comments(
    db: Session, item_id: int, gh_comments: list[dict], comment_type: str
) -> None:
    """Upsert every comment in gh_comments as the given comment_type, then delete any
    existing DB row of that SAME comment_type for this item no longer present in
    gh_comments. Scoping both the upsert lookup and the prune to comment_type means
    completing one type's sync never touches the other type's rows -- see _sync_comments."""
    gh_ids = {c["id"] for c in gh_comments}

    for c in gh_comments:
        existing = db.query(models.EngineeringItemComment).filter_by(
            github_id=c["id"], comment_type=comment_type,
        ).first()
        created_at = datetime.fromisoformat(c["created_at"].replace("Z", "+00:00"))
        updated_at = datetime.fromisoformat(c["updated_at"].replace("Z", "+00:00"))
        diff_path = c.get("path") if comment_type == "pr_review_comment" else None
        diff_line = (c.get("line") or c.get("original_line")) if comment_type == "pr_review_comment" else None
        if existing:
            existing.author = (c.get("user") or {}).get("login")
            existing.body = c["body"] or ""
            existing.updated_at = updated_at
            existing.diff_path = diff_path
            existing.diff_line = diff_line
        else:
            db.add(models.EngineeringItemComment(
                item_id=item_id,
                github_id=c["id"],
                comment_type=comment_type,
                author=(c.get("user") or {}).get("login"),
                body=c["body"] or "",
                created_at=created_at,
                updated_at=updated_at,
                diff_path=diff_path,
                diff_line=diff_line,
            ))

    db.query(models.EngineeringItemComment).filter(
        models.EngineeringItemComment.item_id == item_id,
        models.EngineeringItemComment.comment_type == comment_type,
        models.EngineeringItemComment.github_id.notin_(gh_ids),
    ).delete(synchronize_session=False)


def _sync_comments(db: Session, eng_item: models.EngineeringItem, token: str) -> None:
    """Upsert comments for one engineering item, deleting any removed from GitHub.

    Issue comments sync for both issues and PRs (GitHub's issue-comments endpoint covers a
    PR's Conversation-tab comments too). PR review (inline diff) comments only exist for
    PRs, and are filtered via _wants_review_comment -- CodeRabbit plus human reviewers,
    excluding other bot/app accounts (see CLAUDE_CODE_INTEGRATION.md).

    The two comment types' fetch/upsert/prune are independent, each with its own try/except:
    one type's fetch failing (a transient GitHub API error) never touches the other type's
    rows, and never wipes its OWN type's existing rows either -- see
    _upsert_and_prune_comments, only reached on a successful fetch.
    """
    parsed = _parse_external_id(eng_item.external_id)
    if not parsed:
        return
    owner, repo, _, number = parsed

    try:
        gh_comments = _fetch_comments(token, owner, repo, number)
    except Exception as e:
        log.warning("Failed to fetch comments for %s: %s", eng_item.external_id, e)
    else:
        _upsert_and_prune_comments(db, eng_item.id, gh_comments, "issue_comment")

    if eng_item.item_type != "pr":
        return

    try:
        gh_review_comments = [
            c for c in _fetch_review_comments(token, owner, repo, number)
            if _wants_review_comment((c.get("user") or {}).get("login"))
        ]
    except Exception as e:
        log.warning("Failed to fetch review comments for %s: %s", eng_item.external_id, e)
    else:
        _upsert_and_prune_comments(db, eng_item.id, gh_review_comments, "pr_review_comment")


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


_DEFAULT_IN_PROGRESS = "In Progress"
_DEFAULT_DONE = "Done"


def get_status_config(db: Session) -> dict:
    """Return the per-repo status name config dict. Falls back to {} if not set."""
    row = db.query(models.AppSetting).filter_by(key=setting_keys.GITHUB_STATUS_CONFIG).first()
    if row and row.value:
        try:
            return json.loads(row.value)
        except Exception:
            pass
    return {}


def save_status_config(db: Session, config: dict) -> None:
    row = db.query(models.AppSetting).filter_by(key=setting_keys.GITHUB_STATUS_CONFIG).first()
    if row:
        row.value = json.dumps(config)
    else:
        db.add(models.AppSetting(key=setting_keys.GITHUB_STATUS_CONFIG, value=json.dumps(config)))
    db.commit()


def _get_status_names(config: dict, repo: str) -> tuple[str, str]:
    """Return (in_progress_name, done_name) for a repo, falling back to defaults."""
    cfg = config.get(repo) or config.get("default") or {}
    return (
        cfg.get("in_progress") or _DEFAULT_IN_PROGRESS,
        cfg.get("done") or _DEFAULT_DONE,
    )


def get_repo_tags_config(db: Session) -> dict[str, list[int]]:
    """Return the repo → tag-ids config dict. Falls back to {} if not set."""
    row = db.query(models.AppSetting).filter_by(key=setting_keys.GITHUB_REPO_TAGS).first()
    if row and row.value:
        try:
            return json.loads(row.value)
        except Exception:
            pass
    return {}


def save_repo_tags_config(db: Session, config: dict[str, list[int]]) -> None:
    row = db.query(models.AppSetting).filter_by(key=setting_keys.GITHUB_REPO_TAGS).first()
    if row:
        row.value = json.dumps(config)
    else:
        db.add(models.AppSetting(key=setting_keys.GITHUB_REPO_TAGS, value=json.dumps(config)))
    db.commit()


def _tags_for_repo(config: dict[str, list[int]], repo: str) -> list[int]:
    """Tag ids for a repo: union of the owner-level rule and the exact repo-level rule.

    Matching is case-insensitive — GitHub owner/repo names are case-preserving
    but case-insensitive, so a rule for "trainsit" must still match a repo
    reported back as "Trainsit/trainsit".
    """
    owner = repo.split("/")[0] if "/" in repo else repo
    owner_l, repo_l = owner.lower(), repo.lower()
    ids: set[int] = set()
    for pattern, tag_ids in config.items():
        if pattern.lower() in (owner_l, repo_l):
            ids.update(tag_ids or [])
    return sorted(int(i) for i in ids)


def tag_ids_for_external_id(db: Session, external_id: str | None) -> list[int]:
    """Repo-rule tag ids for a card linked to a GitHub item, or [] if unlinked/no rules match."""
    if not external_id:
        return []
    parsed = _parse_external_id(external_id)
    if not parsed:
        return []
    owner, repo, _, _ = parsed
    return _tags_for_repo(get_repo_tags_config(db), f"{owner}/{repo}")


def _parse_external_id(external_id: str) -> tuple[str, str, str, int] | None:
    """
    Parse 'github:owner/repo/issues/123' or 'github:owner/repo/pull/123'
    into (owner, repo, graphql_type, number).
    graphql_type is 'issue' or 'pullRequest'.
    """
    m = re.match(r"github:([^/]+)/([^/]+)/(issues|pull)/(\d+)$", external_id)
    if not m:
        return None
    owner, repo, kind, number = m.groups()
    gql_type = "pullRequest" if kind == "pull" else "issue"
    return owner, repo, gql_type, int(number)


def _fetch_project_statuses(token: str, items: list[models.EngineeringItem]) -> dict[int, dict]:
    """
    Fetch GitHub Projects v2 board status for a list of EngineeringItems in a
    single batched GraphQL request.

    Returns {item.id: {"project_name": str, "project_status": str}} for items
    that belong to a project. Items not in any project are omitted.

    Requires the token to have the `read:project` OAuth scope; silently returns
    {} if the request fails (e.g. missing scope).
    """
    if not items:
        return {}

    # Build one alias per item
    fragments = []
    index_map: dict[str, int] = {}  # alias → item.id
    for i, item in enumerate(items):
        parsed = _parse_external_id(item.external_id)
        if not parsed:
            continue
        owner, repo, gql_type, number = parsed
        alias = f"item{i}"
        index_map[alias] = item.id
        sub = f"""
          {alias}: repository(owner: "{owner}", name: "{repo}") {{
            {gql_type}(number: {number}) {{
              projectItems(first: 5) {{
                nodes {{
                  project {{ title }}
                  fieldValues(first: 20) {{
                    nodes {{
                      ... on ProjectV2ItemFieldSingleSelectValue {{
                        name
                        field {{ ... on ProjectV2SingleSelectField {{ name }} }}
                      }}
                    }}
                  }}
                }}
              }}
            }}
          }}"""
        fragments.append(sub)

    if not fragments:
        return {}

    query = "query {" + "\n".join(fragments) + "\n}"
    try:
        resp = requests.post(
            GITHUB_GRAPHQL,
            json={"query": query},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data") or {}
    except Exception as exc:
        log.warning("GitHub Projects GraphQL fetch failed: %s", exc)
        return {}

    result: dict[int, dict] = {}
    for alias, item_id in index_map.items():
        repo_data = data.get(alias) or {}
        # repo_data has one key: "issue" or "pullRequest"
        inner = next(iter(repo_data.values()), None) if repo_data else None
        if not inner:
            continue
        project_items = (inner.get("projectItems") or {}).get("nodes") or []
        if not project_items:
            continue
        # Use the first project item found
        pi = project_items[0]
        project_name = (pi.get("project") or {}).get("title")
        project_status = None
        for fv in (pi.get("fieldValues") or {}).get("nodes") or []:
            field_name = (fv.get("field") or {}).get("name", "")
            if field_name.lower() == "status":
                project_status = fv.get("name")
                break
        if project_name:
            result[item_id] = {"project_name": project_name, "project_status": project_status}

    return result


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
        gh_updated_at = None
        raw_updated = item.get("updated_at")
        if raw_updated:
            try:
                gh_updated_at = datetime.fromisoformat(raw_updated.replace("Z", "+00:00"))
            except Exception:
                pass

        existing = db.query(models.EngineeringItem).filter_by(external_id=ext_id).first()
        if existing:
            existing.title = item["title"]
            existing.state = "open"
            existing.synced_at = now
            existing.body = item.get("body") or existing.body
            # SQLite returns naive datetimes; compare as naive UTC throughout
            _gh_naive = gh_updated_at.replace(tzinfo=None) if gh_updated_at else None
            needs_comment_sync = (
                _gh_naive is not None
                and (existing.body_updated_at is None or _gh_naive > existing.body_updated_at)
            )
            if gh_updated_at:
                existing.body_updated_at = gh_updated_at
            skipped += 1
        else:
            new_item = models.EngineeringItem(
                external_id=ext_id,
                title=item["title"],
                item_type="pr" if _is_pr(item) else "issue",
                repo=_repo_full_name(item["html_url"]),
                number=item["number"],
                url=item["html_url"],
                state="open",
                synced_at=now,
                body=item.get("body"),
                body_updated_at=gh_updated_at,
            )
            db.add(new_item)
            existing = new_item
            needs_comment_sync = True
            created += 1

        if needs_comment_sync:
            db.flush()  # ensure item has an id before syncing comments
            _sync_comments(db, existing, token)

    db.flush()  # ensure new items have IDs before GraphQL enrichment and embedding

    # Embed new/updated items in the background
    try:
        import embeddings as _embeddings
        all_open = db.query(models.EngineeringItem).filter_by(state="open").all()
        for eng_item in all_open:
            _embeddings.upsert_eng_bg(eng_item.id, eng_item.title, eng_item.repo)
    except Exception:
        pass

    # Enrich open items with Projects v2 board status; detect In Progress / Done transitions
    open_eng_items = db.query(models.EngineeringItem).filter_by(state="open").all()
    project_data = _fetch_project_statuses(token, open_eng_items)
    status_config = get_status_config(db)
    repo_tags_config = get_repo_tags_config(db)
    cards_created = 0
    for eng_item in open_eng_items:
        if eng_item.id not in project_data:
            continue
        old_status = eng_item.project_status
        new_status = project_data[eng_item.id]["project_status"]
        eng_item.project_name = project_data[eng_item.id]["project_name"]
        eng_item.project_status = new_status

        in_progress_name, done_name = _get_status_names(status_config, eng_item.repo)

        if new_status == in_progress_name and old_status != in_progress_name:
            # Transition → In Progress: create a linked card if one doesn't exist yet
            exists = db.query(models.Card).filter_by(
                external_id=eng_item.external_id, archived=False
            ).first()
            if not exists:
                new_card = models.Card(
                    title=eng_item.title,
                    description="",
                    section="today",
                    external_id=eng_item.external_id,
                    position=0,
                    today_since=now,
                    created_at=now,
                    updated_at=now,
                )
                tag_ids = _tags_for_repo(repo_tags_config, eng_item.repo)
                if tag_ids:
                    new_card.tags = db.query(models.Tag).filter(models.Tag.id.in_(tag_ids)).all()
                db.add(new_card)
                cards_created += 1

        elif new_status == done_name and old_status != done_name:
            # Transition → Done: complete the linked card
            card = db.query(models.Card).filter_by(
                external_id=eng_item.external_id, archived=False, completed=False
            ).first()
            if card:
                card.completed = True
                card.completed_at = now
                card.updated_at = now

    # Close items no longer in the open set; complete + archive linked cards
    tracked_open = db.query(models.EngineeringItem).filter_by(state="open").all()
    for eng_item in tracked_open:
        if eng_item.external_id not in open_items:
            eng_item.state = "closed"
            eng_item.synced_at = now
            closed += 1
            linked_cards = db.query(models.Card).filter_by(
                external_id=eng_item.external_id, archived=False
            ).all()
            for card in linked_cards:
                if not card.completed:
                    card.completed = True
                    card.completed_at = now
                card.archived = True
                card.archived_at = now
                card.updated_at = now

    db.commit()
    return {"created": created, "closed": closed, "skipped": skipped, "cards_created": cards_created, "error": None}
