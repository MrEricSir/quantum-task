# Todo Dashboard

A Kanban-style todo dashboard with AI-powered task parsing, calendar integration, and a daily briefing.

## Stack

- **Frontend**: React + Vite + Radix UI + @dnd-kit
- **Backend**: Python FastAPI
- **Database**: SQLite (via SQLAlchemy) — swappable via env var
- **AI Quick Add**: Ollama locally, or any OpenAI-compatible API (Gemini, Groq, etc.)
- **Calendar**: iCal/ICS feed integration
- **Weather**: Open-Meteo (no API key required)

## Prerequisites

- Python 3.11+
- Node 18+
- [Ollama](https://ollama.com) — only needed for the AI Quick Add feature when running locally

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

# Run all backend tests
./dev.sh test

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
./dev.sh test
```

This runs two test suites in sequence:

**Calendar unit tests** (`test_calendar.py`) — no external services required:
- Calendar feed CRUD (save/replace/clear mappings)
- Section assignment (today / week / month) and past-event filtering
- Tag info attachment
- iCal export: token auth (valid, invalid, missing, rotate-invalidates), scheduled tasks appear, completed/unscheduled tasks excluded, DTSTART is timezone-aware, tag filter, time round-trip
- iCal import (`gcal.fetch_events`): UTC→local timezone conversion, `STATUS:CANCELLED` filtering, all-day events, `SEQUENCE`/`UID` fields parsed
- UID deduplication: higher `SEQUENCE` wins across feeds, no-UID events are never deduped

**Quick Add parse integration tests** (`test_parse.py`) — requires Ollama:
- Section assignment, scheduled datetime, title preservation, tag suggestions
- `type` field (task / habit / note), habit recurrence, note/list detection, `note_content` formatting
- Regression tests (integer description coercion, schema name leak)

Tests that call Ollama are skipped automatically with a clear message when Ollama is not running — no failures.

## Features

### Board
- Four columns: **Today**, **This Week**, **This Month**, **Later**
- Drag cards between columns or reorder within a column
- Add tasks with optional description, scheduled date/time, and tags
- Check off tasks as complete; completed tasks move to the Archive
- Edit and delete tasks via the `⋯` card menu

### AI Quick Add
- Describe a task in plain English; the configured LLM parses it into structured fields (title, due date, tags, etc.)
- Items are submitted to the **Processing Queue** and added to the board automatically when parsing completes
- The queue persists across page refreshes; pending items resume automatically on reload
- Failed items show an error with a retry button

### Daily Briefing
- Streaming AI summary of your day: weather, upcoming tasks, and calendar events
- Reflects exactly what is currently displayed (respects tag filter, hides past events)

### Calendar
- Subscribe to any iCal/ICS feed (e.g. Google Calendar, Apple Calendar)
- Events are displayed in a strip above the board and in the daily briefing
- Past timed events are automatically hidden

### Tags
- Create and manage color-coded tags
- Filter the board to a single tag via the sidebar
- Tags are auto-suggested during AI Quick Add parsing

### Archive
- Completed tasks collected in a collapsible section, sorted by completion time
- Restore or permanently delete archived tasks

### Other
- Responsive layout, works on mobile
- Dark "cyber" theme with animated background

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
    model_plugins/     # Per-model prompt formatting (Ollama models)
    Dockerfile
    requirements.txt
  frontend/
    public/
      bg.webm          # Background video
    src/
      App.jsx          # Root component, state management
      components/      # Board, Sidebar, Modals, Queue, Briefing, Calendar, Archive
    dist/              # Production build output (gitignored)
  Dockerfile           # Multi-stage build (frontend + backend)
  deploy-gcp.md        # Full GCP deployment guide
  dev.sh               # Development helper script
```

## Credits

Background video by RoyaltyFreeTube: https://www.youtube.com/watch?v=v-Qv3R28aCk
