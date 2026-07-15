# Claude Code Instructions

***Important***: Do not commit files to git or push changes!

## README.md

Keep this file up-to-date as the source of truth for how to configure and use this webapp. When asked for help with using the app, you should look here first for any relevant steps.

The **Telegram Integration** section contains a full table of every supported query and example phrase. When new bot intents or commands are added, update that section to match.


## Frontend Tests

After any UI change that adds, removes, or renames interactive elements (buttons, headings, nav links, modals), update the functional tests to match:

```bash
cd frontend && npx playwright test
```

Tests are in `frontend/tests/visual.spec.js`. They check element presence and visibility — not pixel snapshots — so they're stable across platforms without any snapshot files to maintain.

- All API calls are mocked — no backend needed
- Clock is frozen to `2026-06-03T10:00:00`
- 34 tests covering: app shell, today page, tasks board, notes, habits, quick-add modal (input + confirm screen), settings modals (tag manager, calendar settings), offline banner


## Backend Architecture

The backend is organized into feature packages. Each package follows this structure:

```
feature/
  __init__.py    # re-exports (router, key functions) — zero import breakage
  router.py      # thin FastAPI endpoints only (HTTP adapters)
  generate.py    # business logic, LLM calls, data fetching
  context.py     # prompt-building helpers (briefing only)
  bot.py         # message handling (telegram only)
  scheduler.py   # background/scheduled tasks (telegram only)
  notify.py      # raw HTTP calls to external service (telegram only)
```

**Current feature packages:**
- `briefing/` — daily briefing generation and streaming; imports: `briefing.router`, `briefing.generate`, `briefing.context`
- `telegram/` — bot, scheduler, webhook; imports: `telegram.router`, `telegram.bot`, `telegram.scheduler`, `telegram.notify`

**Flat routers** (still in `routers/`): auth, cards, habits, calendar, tags, jobs, engineering, push, withings, search, insights, correlations, food, discovery, assist

**Shared infrastructure:**
- `schemas/` — Pydantic models organized by domain (`cards.py`, `habits.py`, `calendar.py`, `briefing.py`, `jobs.py`, `withings.py`, `engineering.py`, `common.py`); `__init__.py` re-exports all for zero breakage
- `settings.py` — `Settings(db)` typed wrapper over `AppSetting` KV table; all config access goes through here
- `deps.py` — `llm_client()` singleton, `get_db()`, `local_date()`, auth constants
- `database.py` — SQLAlchemy engine + `SessionLocal`

**Rules for adding new features:**
1. New feature = new package under `backend/` (not `routers/`)
2. Router file is a thin adapter — all logic in separate modules
3. All config reads/writes go through `Settings(db)` in `settings.py`
4. Schema types go in `schemas/<domain>.py` and re-exported from `schemas/__init__.py`
5. When patching in tests, patch the module where the name is *used*, not where it's defined (e.g. `briefing.router.llm_client`, not `deps.llm_client`)


## Gotchas

- The backend is designed to be run as-needed on Google Cloud Run, so the minimum instances must be 0.

### Timezone Handling

The server runs UTC (Cloud Run). All date/time logic must use the client's local clock. 

**Backend**: Always read timezone from the request via `deps.py` helpers — never from the request body:
- `local_date(request)` → client's local `YYYY-MM-DD` (from `X-Local-Date` header)         
- `utc_offset_minutes(request)` → JS-convention offset (UTC+10 → -600) (from `X-UTC-Offset` header)                                                                                   

**Frontend**: Always use `apiFetch` from `api.js` — it injects both headers automatically. 
                                                                                        
If raw `fetch` is needed (e.g. SSE streaming with `AbortController`), manually add:        
```js                                                                                    
headers: { ..., 'X-Local-Date': localDate(), 'X-UTC-Offset': String(new                    
Date().getTimezoneOffset()) }                                                              
where localDate is imported from api.js. Never inline the date formatting logic.
