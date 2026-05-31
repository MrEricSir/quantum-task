# Todo Dashboard

A Kanban-style todo dashboard with AI-powered task parsing, calendar integration, and a daily briefing.

## Stack

- **Frontend**: React + Vite + Radix UI + @dnd-kit
- **Backend**: Python FastAPI
- **Database**: SQLite (via SQLAlchemy) — swappable via env var
- **AI Quick Add**: Ollama + llama3.2 locally, or any OpenAI-compatible API (e.g. Gemini)
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

All configuration is via environment variables. Defaults are set for local development — no `.env` file is required to run locally. Copy `.env.example` to `.env` to override specific values.

### LLM (AI Quick Add + Daily Briefing)

| Variable | Default | Description |
|---|---|---|
| `LLM_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible API base URL |
| `LLM_API_KEY` | `ollama` | API key (`ollama` for local Ollama) |
| `LLM_MODEL` | `llama3.2` | Model name |

The backend uses the OpenAI Python SDK for all LLM calls. Any service with an OpenAI-compatible API works — including Ollama locally or Gemini in production.

**To use Gemini instead of Ollama:**

```bash
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/ \
LLM_API_KEY=your-gemini-api-key \
LLM_MODEL=gemini-2.0-flash \
./dev.sh start
```

A free Gemini API key is available from [Google AI Studio](https://aistudio.google.com). This is also the path taken in the GCP deployment (see `deploy-gcp.md`).

**To benchmark Ollama models locally:**

```bash
./dev.sh benchmark
```

Compares parse quality and speed across all locally available Ollama models and writes `benchmark_report.md`.

### Database

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./todos.db` | SQLAlchemy connection string |

The default SQLite database is created at `backend/todos.db` on first run. To use PostgreSQL (e.g. for multi-user deployments), set `DATABASE_URL` to a `postgresql+psycopg2://` connection string and add `psycopg2-binary` to `requirements.txt`.

### CORS

| Variable | Default | Description |
|---|---|---|
| `ALLOWED_ORIGIN` | `http://localhost:5173` | Allowed frontend origin |

Set this to your production frontend URL when deploying.

## Deploying to GCP

See `deploy-gcp.md` for the full guide. The short version:

- **Frontend**: Firebase Hosting (free tier) — built by CI, deployed automatically on push to `main`
- **Backend**: Cloud Run (scales to zero, ~$0 for personal use) — Docker container deployed by CI
- **Database**: SQLite on a Cloud Storage volume mount (~$0/mo)
- **AI**: Gemini 2.0 Flash via the existing OpenAI-compatible client (~$0.50/mo)

### First-time setup

```bash
# 1. Copy and fill in your GCP project ID, Gemini API key, and hosting URL
cp .gcp-config.example .gcp-config

# 2. Initialise Firebase Hosting (one time, interactive)
firebase init hosting

# 3. Provision all infrastructure and do the first deploy
./dev.sh gcp-setup
```

`gcp-setup` handles everything: enables GCP APIs, creates Artifact Registry and Cloud Storage,
builds and pushes the Docker image, deploys to Cloud Run, deploys the frontend to Firebase,
creates CI service accounts, and prints the GitHub secrets to add.

### Subsequent deploys

Push to `main` — GitHub Actions runs tests, then deploys backend and frontend automatically.
Or deploy manually at any time:

```bash
./dev.sh gcp-deploy
```

### Rotating secrets / updating env vars

Edit `.gcp-config` and run:

```bash
./dev.sh gcp-update-env
```

**To test the Docker container locally:**

```bash
docker build -t todo-backend ./backend
docker run -p 8080:8080 \
  -e LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/ \
  -e LLM_API_KEY=your-gemini-api-key \
  -e LLM_MODEL=gemini-2.0-flash \
  -e ALLOWED_ORIGIN=http://localhost:5173 \
  todo-backend
```

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
  firebase.json        # Firebase Hosting config + Cloud Run rewrite rules
  .env.example         # Environment variable reference
  deploy-gcp.md        # Full GCP deployment plan
  dev.sh               # Development helper script
```

## Credits

Background video by RoyaltyFreeTube: https://www.youtube.com/watch?v=v-Qv3R28aCk
