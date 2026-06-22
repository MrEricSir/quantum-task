import os

import requests as _requests
from fastapi import APIRouter
from fastapi.exceptions import HTTPException
from pydantic import BaseModel

router = APIRouter()

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
JINA_BASE = "https://r.jina.ai"


class SearchRequest(BaseModel):
    query: str
    max_results: int = 5


class FetchURLRequest(BaseModel):
    url: str


@router.post("/api/search")
def web_search(req: SearchRequest):
    if not TAVILY_API_KEY:
        raise HTTPException(status_code=503, detail="Search not configured (TAVILY_API_KEY missing)")
    try:
        resp = _requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": req.query,
                "max_results": req.max_results,
                "include_answer": False,
            },
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Search failed: {e}")
    data = resp.json()
    results = [
        {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")}
        for r in data.get("results", [])
    ]
    return {"results": results}


@router.post("/api/fetch-url")
def fetch_url(req: FetchURLRequest):
    try:
        resp = _requests.get(
            f"{JINA_BASE}/{req.url}",
            headers={"Accept": "text/plain", "X-Return-Format": "text"},
            timeout=20,
        )
        resp.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Fetch failed: {e}")

    content = resp.text[:4000]

    # Jina returns "Title: ...\nURL Source: ...\n..." — try to extract title
    title = req.url
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("Title:"):
            title = line[len("Title:"):].strip()[:80]
            break
        elif line and not line.startswith("URL ") and not line.startswith("Markdown"):
            title = line[:80]
            break

    return {"title": title, "content": content}
