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
- Covers: app shell, today page, tasks board, notes, habits, quick-add modal (input + confirm screen), settings modals (tag manager, calendar settings), offline banner, card detail panel (assist/breakdown/code tabs, GitHub panel), and more — see the file for the current full list.

Separately, `cd frontend && npx vitest run` covers pure-logic unit tests (`src/lib/*.test.js`) and extracted-hook tests (`src/hooks/*.test.js`, e.g. `useAssistChat`/`useAssistBreakdown`/`useAssistCode`) via `@testing-library/react`'s `renderHook`. Add a hook-level test here when extracting stateful logic out of a component, rather than only relying on Playwright's end-to-end coverage.


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
- `assist/` — AI assistant chat, spec generation, card-context threads; imports: `assist.router`, `assist.generate`, `assist.context`
- `bridge/` — qtask-bridge job queue + served CLI; imports: `bridge.router`, `bridge.jobs`, `bridge.render`, `bridge.stale`. Novel shape: `bridge/scripts/` holds the served CLI (`install.py`, `agent_core.py`, `agent_claude.py`) as real, independently-syntax-checkable `.py` files — never as string literals in the router — rendered/concatenated into the servable text by `bridge/render.py` at request time. `agent_core.py` is agent-agnostic; `agent_claude.py` is the small Claude Code adapter (see its module docstring for the swap-in contract if trying an alternative coding agent later).

**Flat routers** (still in `routers/`): auth, cards, habits, calendar, tags, jobs, engineering, push, withings, search, insights, correlations, food, discovery

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
6. If a model's `ForeignKey` declares `ondelete="CASCADE"`, mirror it on the ORM relationship too (`cascade="all, delete-orphan"`) — otherwise SQLAlchemy's default relationship behavior tries to *nullify* the child's FK when the parent is deleted, which crashes against a `nullable=False` child column instead of ever reaching the DB-level cascade. (Real incident: `Card.thread`/`CardThread.card_id` — deleting any card with an assistant thread crashed unconditionally until this was added.)


## Gotchas

- The backend is designed to be run as-needed on Google Cloud Run, so the minimum instances must be 0.

### Cloud Run Cold Starts & Production Debugging

**In-memory module-level state (caches, rate-limit trackers, etc.) resets on every cold
start.** With `min-instances=0`, the backend can and does scale to zero between uses — never
assume a plain `dict`/global variable persists across requests the way it would on an
always-on server. (Real incident: `routers/discovery.py`'s `_geocode_cache` resetting on every
cold start meant every geocoding lookup re-hit the live Nominatim API from scratch far more
often than intended.)

**Any polling hook that runs indefinitely at the App-shell level (not scoped to a single page
or modal) must pause while the tab is hidden.** Production runs with `--no-cpu-throttling`
(CPU billed for the instance's full wall-clock uptime, not just active request handling — see
`dev.sh`'s `gcp_deploy()`), so an unconditional `setInterval` poll keeps the instance
perpetually billed for as long as any browser tab is open anywhere, including backgrounded or
forgotten ones. Gate on `document.visibilityState` (see `useBridgeJobStatuses.js` for the
pattern: stop the interval when hidden, refresh immediately + restart on regaining visibility).

**When a Cloud Logging `httpRequest.requestUrl` filter comes back suspiciously empty, search
`textPayload:"<keyword>"` instead** before concluding "no requests are happening at all." The
structured `httpRequest.*` fields don't reliably populate for a request that crashes mid-
handling — the backend's own `print()`/log output often has the real signal that an
HTTP-request-shaped filter misses entirely.

**One failing item in a loop over independent external resources (calendar feeds, geocoding
lookups, etc.) must not abort the whole batch.** Catch and degrade per-item, not per-batch —
an unhandled exception partway through processing one feed's events, one API call, etc.
shouldn't take down every other item being processed in the same request. (Real incident: a
single unresolvable address crashed an entire Discovery feed's worth of events, because a
helper's `None`-on-failure return value was unpacked without checking for it first.)

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
```
where `localDate` is imported from `api.js`. Never inline the date formatting logic.

**Comparing a UTC-instant DB column against a local date — the single most-repeated
timezone bug in this codebase.** `models.py`'s docstring documents which `DateTime`
columns hold a UTC instant (`Card.created_at`/`completed_at`/`archived_at`/`today_since`,
`Habit.created_at`, `HealthExperiment.created_at`/`dismissed_at`,
`WithingsCredentials.last_synced`) versus a naive local wall-clock time
(`Card.scheduled_at`, `FoodEntry.consumed_at`, `WorkoutEntry.logged_at`). A UTC-instant
column's raw `.date()`/`.hour`/`.strftime()` is the **server's** UTC date/hour, not the
client's — comparing or bucketing it directly against a local `today` (or against
`date.today()`/`datetime.now()` instead of `local_date(request)`) has been found and
fixed independently at least 6 times across `gcal.py`, `correlations.py`,
`briefing/context.py`, `telegram/scheduler.py`, `insights.py`, and `reports/generate.py`
(the last one is the mirror-image mistake — applying this same conversion to an
already-local `scheduled_at` shifts it onto the wrong day). Always convert first via
`deps.to_local_date(dt, utc_offset_minutes)` — never write this conversion inline, and
never bucket a UTC-instant column's raw value by day/hour. `backend/tests/
test_timezone_conventions.py` mechanically scans for the raw-access pattern and fails
the build if a new instance appears — if it flags a genuinely-correct usage, add it to
that test's own `_ALLOWLIST` with a reason rather than bypassing the check.

**Non-request code paths (Telegram/cron/scheduled jobs) have no request to read a
header from at all.** `telegram/scheduler.py`'s top-level `check_all()` resolves
`tz_offset = Settings(db).tz_offset` — a stored offset kept in sync by `AuthMiddleware`
opportunistically resyncing it from `X-UTC-Offset` on every authenticated webapp
request — and threads it explicitly through every check function's own parameters.
Any new scheduled/background function needing local time must accept an explicit
`tz_offset`/`utc_offset_minutes` parameter from its caller (defaulting to `0` only for
truly caller-agnostic maintenance endpoints, documented as such) — it must never default
to reading the server's own clock.

**A test that builds a fixture time via `datetime.now(timezone.utc) + timedelta(hours=N)`
(or any sub-day delta) and then separately checks it against "today" can flake in CI
whenever the real run happens to land close to UTC midnight** — the offset can silently
roll the fixture onto the next calendar day while "today" itself doesn't move. (Real
incident: `test_telegram.py`'s duration-fit tests failed in CI at 23:53 UTC for exactly
this reason.) A whole-day delta (`timedelta(days=N)`) is immune to this since it always
lands at the same time-of-day; anything finer needs either a safely-distant time-of-day
or — more robustly — a fixed clock (see `test_telegram.py`'s `_FixedDatetime`, a `datetime`
subclass patched in via `patch("telegram.bot.datetime", _FixedDatetime)`) so the test
doesn't depend on real wall-clock time at all.
