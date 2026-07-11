# Quantum Task

A personal productivity dashboard with AI-powered quick add, calendar integration, habits tracking, health data, a daily briefing, and an agentic research workshop.

## Stack

- **Frontend**: React + Vite + Radix UI + @dnd-kit + React Query
- **Backend**: Python FastAPI
- **Database**: SQLite (via SQLAlchemy) — swappable via env var
- **AI**: Ollama locally, or any OpenAI-compatible API (Gemini, Groq, etc.)
- **Calendar**: iCal/ICS feed integration
- **Weather**: Open-Meteo (no API key required)
- **Health**: Withings API (step count, body fat %, weight) — optional

## Prerequisites

- Python 3.11+
- Node 18+
- [Ollama](https://ollama.com) — only needed for AI features when running locally

Install Ollama via Homebrew:

```bash
brew install ollama
brew services start ollama   # start on login
```

## Running the app

Use the `dev.sh` script from the project root for everything:

```bash
# Install all dependencies and pull the AI model (run once)
./dev.sh setup

# Start backend + frontend in the background
./dev.sh start

# Stop both
./dev.sh stop

# Restart (stop then start)
./dev.sh restart

# Tail logs from both processes
./dev.sh logs

# Run all tests (backend + frontend)
./dev.sh test

# Run only frontend tests
./dev.sh test-frontend

# Benchmark parse quality across all available Ollama models
./dev.sh benchmark

# Fetch Cloud Run logs (GCP only)
./dev.sh gcp-logs            # last 100 lines
./dev.sh gcp-logs 200        # last 200 lines
./dev.sh gcp-logs 100 withings  # last 100 lines, grep for "withings"
```

`setup` handles everything in one step:
- Creates the Python virtualenv and installs pip packages
- Runs `npm install` for the frontend
- Pulls the `llama3.2` model via Ollama (if Ollama is installed)

It is safe to re-run — it skips steps that are already done.

Once started:

| Service  | URL                       |
|----------|---------------------------|
| App      | http://localhost:5173      |
| API docs | http://localhost:8000/docs |

Logs are written to `backend.log` and `frontend.log` in the project root.

## Manual start (alternative)

After running `./dev.sh setup`, you can run each process in its own terminal:

```bash
# Terminal 1 — backend
cd backend && venv/bin/uvicorn main:app --reload

# Terminal 2 — frontend
cd frontend && npm run dev
```

## Testing

```bash
# Run all tests (backend + frontend)
./dev.sh test

# Run only frontend tests
./dev.sh test-frontend
```

`./dev.sh test` runs three suites in sequence:

**Backend unit tests** — no external services required:
- Calendar feed CRUD, timezone handling, iCal export/import, UID deduplication
- Briefing context builders (`build_today_context`, `build_week_context`, `compute_observations`)
- Weather fetch + WMO code mapping
- Habit streak computation (`recompute_from`, `recompute_all`, `get_current_streak`)
- Withings goal detection, `_auto_check_habits`, health metric regex
- AppSetting constants + `WithingsCredentials` model save/load
- Daily plan helpers, recurring card scheduling, food entry parsing
- Plugin post-processing: section/type overrides, tag suggestions

**Quick Add parse integration tests** (`test_parse.py`) — requires Ollama:
- Section assignment, scheduled datetime, title preservation, tag suggestions
- `type` field (task / habit), habit recurrence detection

Tests that call Ollama are skipped automatically when Ollama is not running — no failures.

**Frontend tests** (`frontend/tests/visual.spec.js`) — no backend required:
- 121 Playwright tests verifying key elements are visible on each page
- Covers: app shell, today page, tasks board, cards, habits, quick-add modal, settings modals (tag manager, calendar, GitHub, Withings), engineering page, discovery panel, archive, search, insights, offline banner
- All API calls are mocked; runs against a production build (`npm run build`)

## Features

### Today (default page)
- Daily overview showing today's schedule, tasks, and habits
- AI-generated daily briefing with weather, upcoming events, and a summary of your day
- Briefing auto-refreshes 10 seconds after data changes (new tasks, habit toggles, calendar refresh)
- Manual regenerate button always available

### Tasks Board
- Four columns: **Today**, **This Week**, **This Month**, **Later**
- Drag cards between columns or reorder within a column
- Add tasks with optional description, scheduled date/time, and tags
- Check off tasks as complete; completed tasks move to the Archive
- Edit and delete tasks via the `⋯` card menu
- Recurring tasks auto-spawn the next occurrence on completion

### Capture (AI Quick Add)
- Describe anything in plain English — the LLM classifies it as a **task**, **habit**, **food log**, **habit completion**, **task completion**, or **assist** request automatically
- Paste or type multiple items at once ("call sam at 3pm, buy milk and eggs, meditate daily") — each is split and parsed individually
- Date and time phrases are resolved to real datetimes: "call dentist tomorrow at 9am", "project review next Friday", "standup at 9"
- A confirm screen shows the detected type with a one-click override, then type-specific fields to review before saving
- Multiple items show a bulk-confirm list; click any item to open its full edit form before saving
- Deterministic post-processing catches common patterns: explicit recurrence → habit, "add a habit to X" → habit, natural past tense ("talked to a stranger", "went for a run") → habit completion
- Tags are auto-suggested from your existing tags
- **Assist mode**: conversational or planning requests ("help me plan my week") stream an AI response instead of creating a card

### Habits
- Track recurring habits with a daily completion toggle
- 7-day completion history shown as dots on each card
- Streak counter
- Archive habits instead of deleting them; restore from the archive at any time
- Link habits to a Withings health goal (step count auto-completes when goal is met)

### Health
- Connect a Withings account (watch + smart scale) to sync step count, body fat %, and weight
- Set a numeric goal per metric; step habits auto-check when the daily goal is synced
- Charts showing steps (bar) and body fat % (line) over the past 90 days
- Habit completion overlay on each chart to see how habits track with progress

### Workshop
- A freeform AI workspace for research, drafts, brainstorming, and planning
- Create named jobs with a goal prompt and optional input cards (tasks, habits, or reference cards as context)
- **Run**: streams a direct AI response to your prompt
- **Research**: agentic mode — the AI generates search queries, executes them via Tavily, then synthesizes a sourced answer; each step is shown live as it runs
- Requires `TAVILY_API_KEY` for the Research mode; Run works with any LLM

### Daily Briefing
- Streaming AI summary of your day: weather, schedule, tasks, and habits
- Respects the active tag filter
- Auto-refreshes with a 10-second debounce after any meaningful data change
- Force-regenerate anytime with the refresh button

### Calendar
- Subscribe to any iCal/ICS feed (e.g. Google Calendar, Apple Calendar)
- Events appear in the Today schedule and daily briefing
- Export your tasks as an iCal feed to subscribe from any calendar app
- Past timed events are automatically hidden

### Event Discovery
- Add public iCal feeds (local events, conferences, sports schedules) as discovery sources
- AI ranks upcoming events against your stated interests and past feedback
- Thumbs-up / thumbs-down per event trains the ranker; dismissed events are hidden on next load with an in-session undo option
- iCal feeds are cached for ~3 hours; LLM rankings are cached until interests or feedback change

### Tags
- Create and manage color-coded tags
- Filter any page to a single tag via the sidebar
- Tags are auto-suggested during AI Quick Add parsing

### Archive
- Completed tasks collected in a collapsible section, sorted by completion time
- Restore or permanently delete archived tasks

### Other
- Responsive layout, works on mobile
- Dark "cyber" theme with animated background
- Offline banner when network connection is lost
- Optional password auth (set `AUTH_PASSWORD` env var)

## Mobile capture

**Android:** The app registers as a Share Sheet target. Install it to your home screen via Chrome (three-dot menu → Install app), then use the native Share button from any app — the Quick Add modal opens with the shared text pre-filled.

**iOS:** The app works well as a home screen PWA (Safari → Share → Add to Home Screen), but iOS does not allow web apps to integrate into the Share Sheet or appear as Shortcuts targets without user-configured automation. No setup is required beyond adding to the home screen.

**API extension point:** `POST /api/shortcut/add` accepts `{"text": "..."}` with an `Authorization: Bearer <password>` header and handles parse + card creation in one step. This is intentionally left as an open endpoint for power users or future native app integration (e.g. a Capacitor build with a Share Extension, or an email-to-task pipeline).

## Configuration

All configuration is via environment variables. Defaults work for local development — no config file needed.

### LLM (AI Quick Add + Daily Briefing + Workshop)

| Variable | Default | Description |
|---|---|---|
| `LLM_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible API base URL |
| `LLM_API_KEY` | `ollama` | API key |
| `LLM_MODEL` | `llama3.2` | Model name |

Any OpenAI-compatible API works. To test a cloud provider locally, export the vars before starting:

```bash
export LLM_BASE_URL="https://api.groq.com/openai/v1"
export LLM_API_KEY="your-key"
export LLM_MODEL="llama-3.3-70b-versatile"
./dev.sh start
```

### Database

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./todos.db` | SQLAlchemy connection string |

### Auth

| Variable | Default | Description |
|---|---|---|
| `AUTH_PASSWORD` | _(unset)_ | Login password — auth disabled if not set |

When set, the password is also accepted as a Bearer token (`Authorization: Bearer <password>`) for API clients such as the iOS Shortcut.

### Workshop web search (optional)

| Variable | Default | Description |
|---|---|---|
| `TAVILY_API_KEY` | _(unset)_ | API key from [tavily.com](https://tavily.com) — enables Research mode in the Workshop. Free tier available. |

### Withings (optional)

| Variable | Default | Description |
|---|---|---|
| `WITHINGS_CLIENT_ID` | _(unset)_ | OAuth client ID from [developer.withings.com](https://developer.withings.com) |
| `WITHINGS_SECRET` | _(unset)_ | OAuth client secret |
| `WITHINGS_CALLBACK_URI` | `http://localhost:8000/api/withings/callback` | Redirect URI registered in the Withings developer console |

Withings features are disabled if `WITHINGS_CLIENT_ID` is not set.

**Local development callback:**
Withings allows `http://localhost` redirect URIs. In your Withings developer app, register **two** allowed redirect URIs:
- `http://localhost:8000/api/withings/callback` — for local development
- `https://YOUR_CLOUD_RUN_URL/api/withings/callback` — for production

The backend runs on port 8000 locally, so the OAuth redirect lands there directly, then redirects your browser back to the frontend at `http://localhost:5173/board`. No tunnel needed.

Set `WITHINGS_CALLBACK_URI` in your `.env` to `http://localhost:8000/api/withings/callback` for local use, and as a GitHub secret pointing to your deployed URL for CI/CD.

### Frontend origin (required in production)

| Variable | Default | Description |
|---|---|---|
| `ALLOWED_ORIGIN` | `http://localhost:5173` | Frontend URL — used for CORS and OAuth redirects |

In production (Cloud Run), set this to your deployed service URL.

**To benchmark Ollama models locally:**

```bash
./dev.sh benchmark
```

Compares parse quality and speed across all locally available Ollama models and writes `benchmark_report.md`.

## Deploying to GCP

See **`deploy-gcp.md`** for the full guide, including infrastructure setup, GitHub secrets, LLM provider options, and CI/CD details.

## Project structure

```
todo/
  backend/
    main.py              # FastAPI app: startup migrations, middleware, router mounts
    models.py            # SQLAlchemy models
    schemas.py           # Pydantic schemas
    database.py          # DB engine, reads DATABASE_URL from env
    deps.py              # Shared dependencies: DB session, LLM client, auth constants
    app_setting_keys.py  # Constants for all AppSetting key strings
    streak.py            # Habit streak computation
    push.py              # Web Push / VAPID helpers
    weather.py           # Open-Meteo fetch + WMO condition helpers
    briefing_context.py  # Today/week context builders for AI briefing
    health_context.py    # Health data context builder (Withings + goals)
    github_sync.py       # GitHub issue/PR sync logic
    gcal.py              # iCal/ICS parsing helpers
    routers/             # One file per feature area
      auth.py            # Login/logout, session management
      cards.py           # Tasks + reference cards CRUD, AI parse, iOS Shortcut
      habits.py          # Habits CRUD, completion toggle
      calendar.py        # iCal feed sync, export
      briefing.py        # Daily briefing SSE, AI assist, daily plan
      jobs.py            # Workshop jobs + agentic research
      withings.py        # Withings OAuth, sync, health data
      discovery.py       # Public iCal discovery feeds + LLM ranking
      food.py            # Food/drink logging + nutritional assessment
      insights.py        # Habit insights and health experiment suggestions
      correlations.py    # Health experiment tracking
      tags.py            # Tag CRUD
      engineering.py     # GitHub engineering feed
      push.py            # Push subscription management
      search.py          # Cross-entity search
    model_plugins/       # Per-model prompt tuning (base + llama3.2, llama3.1-8b, phi4-mini, llama3.3-70b)
    alembic/             # Database migrations (00001–00013)
    tests/
      test_calendar.py       # Calendar feed CRUD, timezone, iCal export/import
      test_briefing.py       # Briefing SSE unit tests
      test_briefing_context.py # Today/week context builders
      test_weather.py        # Weather fetch + WMO mapping
      test_plugins.py        # Post-processing: section overrides, type detection
      test_withings.py       # Withings goal detection, habit auto-check, streak
      test_app_settings.py   # AppSetting constants + WithingsCredentials model
      test_daily_plan.py     # Daily plan time normalization helpers
      test_recurring.py      # Recurring card scheduling
      test_localtime.py      # Local date header handling
      test_food.py           # Food entry parsing
      test_parse.py          # Quick Add parse integration tests (requires Ollama)
      benchmark.py           # Parse quality benchmark across Ollama models
    Dockerfile
    requirements.txt
  frontend/
    public/
      manifest.json    # PWA manifest (includes Web Share Target for Android)
      sw.js            # Service worker: offline shell, push notifications
      bg.webm          # Background video
    src/
      App.jsx          # Root component, routing, global state
      api.js           # All API calls
      main.jsx         # React root with QueryClientProvider + BrowserRouter
      lib/
        descriptionToHtml.js  # Shared HTML sanitizer for calendar event descriptions
      hooks/           # useCards, useHabits, useCalendar, useEngineering,
                       # useWithings, useModals, useNotifications
      context/
        ModalContext.jsx  # Context for opening modals from nested components
      components/
        pages/         # TodayPage, HabitsPage, CalendarPage,
                       # EngineeringPage, WorkshopPage, HealthPage, LoginPage
                       # (board is rendered inline in App.jsx)
        board/         # Column, TodoCard, CalendarEventCard, Archive
        layout/        # Sidebar, MobileNav, TagFilterBar
        modals/        # QuickAddModal, CardSheet, CalendarSettings, GithubSettings,
                       # WithingsSettings, TagManager, AssistModal, ...
        shared/        # QueueIndicator and other shared components
    tests/
      visual.spec.js   # Playwright functional tests (all APIs mocked, 121 tests)
    dist/              # Production build output (gitignored)
  Dockerfile           # Multi-stage build (frontend + backend)
  deploy-gcp.md        # Full GCP deployment guide
  IDEAS.md             # Feature ideas and brainstorm
  dev.sh               # Development helper script
```

## Credits

Background video by RoyaltyFreeTube: https://www.youtube.com/watch?v=v-Qv3R28aCk
