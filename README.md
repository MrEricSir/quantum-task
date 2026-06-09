# Quantum Task

A personal productivity dashboard with AI-powered quick add, calendar integration, habits tracking, notes, and a daily briefing.

## Stack

- **Frontend**: React + Vite + Radix UI + @dnd-kit
- **Backend**: Python FastAPI
- **Database**: SQLite (via SQLAlchemy) — swappable via env var
- **AI**: Ollama locally, or any OpenAI-compatible API (Gemini, Groq, etc.)
- **Calendar**: iCal/ICS feed integration
- **Weather**: Open-Meteo (no API key required)

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

**Backend unit tests** (`test_calendar.py`, `test_briefing.py`, `test_plugins.py`) — no external services required:
- Calendar feed CRUD, section assignment, past-event filtering, tag attachment
- iCal export/import, UID deduplication, timezone handling
- Plugin post-processing: deterministic section/type overrides from input text

**Quick Add parse integration tests** (`test_parse.py`) — requires Ollama:
- Section assignment, scheduled datetime, title preservation, tag suggestions
- `type` field (task / habit / note), habit recurrence, note/list detection
- Regression tests

Tests that call Ollama are skipped automatically when Ollama is not running — no failures.

**Frontend tests** (`frontend/tests/visual.spec.js`) — no backend required:
- 34 Playwright tests verifying key elements are visible on each page
- Covers: app shell, today page, tasks board, notes, habits, quick-add modal (input + confirm screen), settings modals
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

### AI Quick Add
- Describe anything in plain English — the LLM classifies it as a **task**, **habit**, or **note** automatically
- Paste or type multiple items at once ("call sam at 3pm, buy milk and eggs, meditate daily") — each is split and parsed individually
- Date and time phrases are resolved to real datetimes: "call dentist tomorrow at 9am", "project review next Friday", "standup at 9"
- A confirm screen shows the detected type with a one-click override, then type-specific fields to review before saving
- Multiple items show a bulk-confirm list; click any item to open its full edit form before saving
- Deterministic post-processing catches common patterns (list inputs → note, explicit recurrence → habit, "add a habit to X" → habit) even when the model guesses wrong
- If type is genuinely ambiguous, a clarifying question is surfaced before you confirm
- Tags are auto-suggested from your existing tags

### Habits
- Track recurring habits with a daily completion toggle
- 7-day completion history shown as dots on each card
- Streak counter
- Archive habits instead of deleting them; restore from the archive at any time

### Notes
- Plain-text quick capture — no markdown, no formatting
- Notes can also be created directly from Quick Add
- Notes can be promoted to tasks with one click
- Tag and archive notes; restore from archive

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

## Configuration

All configuration is via environment variables. Defaults work for local development — no config file needed.

### LLM (AI Quick Add + Daily Briefing)

| Variable | Default | Description |
|---|---|---|
| `LLM_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible API base URL |
| `LLM_API_KEY` | `ollama` | API key |
| `LLM_MODEL` | `llama3.2` | Model name |

Any OpenAI-compatible API works. To test a cloud provider locally, export the vars before starting:

```bash
export LLM_BASE_URL="https://api.groq.com/openai/v1"
export LLM_API_KEY="your-key"
export LLM_MODEL="llama-3.1-8b-instant"
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
    main.py            # FastAPI app, all routes
    models.py          # SQLAlchemy models
    schemas.py         # Pydantic schemas
    database.py        # DB engine, reads DATABASE_URL from env
    model_plugins/     # Per-model prompt tuning (base + llama3.2, llama3.1-8b, phi4-mini)
    tests/
      test_calendar.py # Calendar feed CRUD, timezone, iCal export/import
      test_briefing.py # Daily briefing unit tests
      test_plugins.py  # Post-processing: section overrides, type detection, list→note
      test_parse.py    # Quick Add parse integration tests (requires Ollama)
      benchmark.py     # Parse quality benchmark across Ollama models
    Dockerfile
    requirements.txt
  frontend/
    public/
      bg.webm          # Background video
    src/
      App.jsx          # Root component, routing, global state
      api.js           # All API calls
      components/      # TodayPage, TasksBoard, HabitsPage, NotesPage, CalendarPage,
                       # DailyBriefing, QuickAddModal, modals, sidebar, archive
    tests/
      visual.spec.js   # Playwright functional tests (all APIs mocked)
    dist/              # Production build output (gitignored)
  Dockerfile           # Multi-stage build (frontend + backend)
  deploy-gcp.md        # Full GCP deployment guide
  dev.sh               # Development helper script
  LLM_IDEAS.md         # Backlog of LLM integration ideas
```

## Credits

Background video by RoyaltyFreeTube: https://www.youtube.com/watch?v=v-Qv3R28aCk
