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
- Claude Code bridge: job create/start/complete/error, agent script endpoints, `?repos=` filtering

**Quick Add parse integration tests** (`test_parse.py`) — requires Ollama:
- Section assignment, scheduled datetime, title preservation, tag suggestions
- `type` field (task / habit), habit recurrence detection

Tests that call Ollama are skipped automatically when Ollama is not running — no failures.

**Frontend tests** (`frontend/tests/visual.spec.js`) — no backend required:
- 134 Playwright tests verifying key elements are visible on each page
- Covers: app shell, today page, tasks board, cards, habits, quick-add modal, settings modals (tag manager, calendar, GitHub, Withings), engineering page, discovery panel, archive, search, insights, offline banner, AI assist panel (chat, breakdown, and code tabs)
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

### Claude Code Bridge

Automate implementation work by sending cards to a local Claude Code agent. The bridge monitors a job queue and launches Claude Code automatically when a new job arrives.

#### How it works

1. Open any card and click **✦ Assist** in the footer
2. Click the **Code** tab
3. Click **✦ Generate** — the AI synthesises a requirements document from the card title, developer notes, and linked GitHub issue/PR context (body + comments)
4. Review and optionally edit the requirements inline, then click **▶ Run** to queue a job
5. The local bridge agent picks up the job, checks the working tree is clean, checks out the primary branch, pulls latest, and creates a `qtask/<id>-<slug>` branch
6. Claude Code launches interactively in your terminal — you can participate, ask questions, or let it run; push is disabled so no changes leave your machine until you review them
7. When the session ends, the branch name and machine are shown in the Code tab and sent via Telegram; the bridge picks up the next queued job automatically

#### Install the bridge agent

In **Settings → Engineering → GitHub**, copy the install command:

```bash
curl http://localhost:8000/api/bridge/install.py | python3
```

This installs `qtask-bridge` into your PATH and creates `~/.config/qtask-bridge/claude.toml` (repo mappings, edit to configure multi-repo support). Then, in your project directory:

```bash
qtask-bridge --watch          # poll for jobs; launch Claude Code interactively when one arrives
qtask-bridge --card <id>      # queue and run a specific card's job once
```

The agent writes the spec to `BRIDGE_SPEC.md`, runs `claude` in your terminal on a fresh `qtask/<id>-<slug>` branch, and marks the job complete when the session ends. The branch is waiting locally for your review — the bridge never pushes.

**`--watch` mode** runs interactively: you can participate in the Claude session, ask questions, or provide direction. When Claude finishes and you exit the session, the job is marked complete and the bridge immediately polls for the next one — no intervention needed between jobs.

**`--card` mode** is the same but prompts you for an optional note to attach to the job before moving on, useful for one-off runs where you want to record context.

#### Code tab actions

| Button | What it does |
|---|---|
| **✦ Generate** | AI synthesises requirements from card + GitHub context |
| **↻ Regenerate** | Overwrites the current requirements with a fresh generation |
| **⎘ Copy** | Copies the full prompt (requirements + GitHub body + comments + notes) to clipboard for manual paste into Claude Code |
| **▶ Run** | Queues a job for the local bridge agent |
| **Edit** (footer) | Opens an inline textarea to manually write or adjust the requirements |

#### Telegram `/build`

You can also queue jobs from Telegram:

| What you send | What happens |
|---|---|
| `/build auth feature` | Queues a build job for the matching card |
| `/build 42` | Queues by card ID (shown as `#42` in the panel header) |
| `build the login card` | Natural phrasing works too |

The bot replies with the job number and notifies you when it's done or errored. See [Claude Code Bridge](#claude-code-bridge-1) in the Telegram section for more detail.

### Daily Briefing
- Streaming AI summary of your day: weather, schedule, tasks, and habits
- Respects the active tag filter
- Auto-refreshes with a 10-second debounce after any meaningful data change
- Force-regenerate anytime with the refresh button

### Telegram Integration

Receive your daily briefing as a Telegram message each morning, and send messages to your bot to query and update the app from anywhere. The bot understands natural language — you don't need to memorise exact commands.

#### Viewing your schedule and tasks

| What you send | What happens |
|---|---|
| `today` | Today's task list — overdue, scheduled, and unscheduled |
| `tomorrow` | Tomorrow's schedule (calendar + tasks) |
| `what do I have on Wednesday?` | Schedule for any named day |
| `week` | Overview of the next 7 days |
| `overdue` | All tasks past their scheduled date |
| `completed` | Everything you've finished today |
| `what did I finish yesterday?` | Completed tasks for any specific day |
| `priority` | AI recommendation on what to focus on next |
| `avoiding` | Tasks that keep getting pushed — named with brief analysis |

#### Habits and health

| What you send | What happens |
|---|---|
| `habits` | Today's habit status — done vs pending |
| `streaks` | Current streak length for each habit |
| `health` | Today's step count, weight, and body fat (Withings) |

#### Capturing and completing tasks

| What you send | What happens |
|---|---|
| `call dentist tomorrow at 2pm` | Captures a new task via the AI parser — same NLP as Quick Add |
| `meeting with Sarah next Friday` | Captures with date resolved |
| `done dentist` | Marks the matching task complete |
| `done meditation` | Marks the matching habit complete for today |
| `undo` | Reverses your last action (capture, completion, or reschedule) |
| `undo both` | Reverses the last two actions |

#### Search

| What you send | What happens |
|---|---|
| `find cards about billing` | Semantic search across tasks, notes, and GitHub items |
| `what did I write about the deployment?` | Returns matching cards ranked by relevance |
| `show me anything related to authentication` | Includes GitHub issues and PRs in results |

#### Notes

| What you send | What happens |
|---|---|
| `add a note to dentist: bring insurance card` | Appends a note to the matching task's description |
| `note on grocery run: also get olive oil` | Same — "note on" / "append to" all work |
| `what's the note on dentist?` | Returns the task's full description |
| `notes on the API task` | Same — "notes on" / "details on" all work |

#### Rescheduling

| What you send | What happens |
|---|---|
| `move dentist to Thursday at 2pm` | Reschedules a single task |
| `push the report to next week` | Moves to the This Week section |
| `move everything overdue to next week` | Bulk-moves all overdue tasks |
| `clear today's list` | Moves all Today tasks to Later |
| `move today's tasks to tomorrow` | Bulk-moves with a specific date |
| `undo` | Restores all tasks moved by a bulk reschedule |

#### Logging

| What you send | What happens |
|---|---|
| `had a salad for lunch` | Logs a food entry |
| `coffee this morning` | Logs with meal type detected |
| `energy 4` | Logs today's energy level (1–5 scale) |
| `feeling tired, 2/5` | Same — natural phrasing works |

#### Claude Code Bridge

| What you send | What happens |
|---|---|
| `/build auth feature` | Queues a Claude Code build job for the matching card |
| `/build 42` | Queues by card ID (shown as `#42` in the card panel header) |
| `build the dashboard card` | Natural phrasing works too |

The bridge picks up the job automatically (if `qtask-bridge --watch` is running locally) and launches Claude Code with the card's spec. When the session ends, the bot sends a follow-up with the result. See [Claude Code Bridge](#claude-code-bridge) in the Features section for installation and full usage.

#### Proactive notifications

Configured in **Settings → Telegram** — set a send time for each:

- **Morning briefing** — AI summary of your day: weather, schedule, tasks, and habit status
- **Evening habit reminder** — lists any habits still pending for the day
- **Midday overdue nudge** — alerts you if tasks have slipped past their scheduled date

---

**Setup (one-time):**
1. Message **@BotFather** on Telegram, send `/newbot`, and copy the token it gives you
2. Send any message to your new bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` — your numeric chat ID appears in the response
3. Paste both into **Settings → Telegram**, pick a delivery hour, and click **Save**
4. Click **Register webhook** in the Two-way chat section — this tells Telegram to send messages to your backend
5. (Production) Run `./dev.sh gcp-setup-scheduler` once to create the Cloud Scheduler job that sends the daily briefing

> **Note:** The webhook must be registered against a publicly reachable URL. It works automatically in production (Cloud Run). For local development, you would need a tunnel (e.g. ngrok) pointing to `localhost:8000` — otherwise only the daily briefing outbound direction works locally.

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

### Search
- The header search bar searches cards (tasks + notes) by keyword; results are ranked by semantic similarity when embeddings are configured
- The Telegram bot's search intent also searches GitHub engineering items by semantic similarity
- The AI Assist header bar (no section/tag filter active) automatically injects semantically relevant cards and GitHub items as context

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

### Semantic search (optional)

Semantic search uses embedding vectors to rank results by meaning rather than exact keyword match. It covers cards (tasks + notes) and GitHub engineering items.

| Variable | Default | Description |
|---|---|---|
| `EMBEDDING_BASE_URL` | falls back to `LLM_BASE_URL` | OpenAI-compatible embeddings API base URL |
| `EMBEDDING_API_KEY` | falls back to `LLM_API_KEY` | API key for the embeddings endpoint |
| `EMBEDDING_MODEL` | `nomic-embed-text` | Embedding model name |

If these are not set, search falls back to substring matching automatically. With Ollama, pull the model once:

```bash
ollama pull nomic-embed-text
```

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

After deploying, run this once to set up the Telegram cron job:

```bash
./dev.sh gcp-setup-scheduler
```

This creates a Cloud Scheduler job that hits the app hourly. The app checks whether the current local hour matches your configured send time and skips silently otherwise. The job is automatically updated on every subsequent `./dev.sh gcp-deploy`.

## Project structure

```
todo/
  backend/
    main.py              # FastAPI app: startup migrations, middleware, router mounts
    models.py            # SQLAlchemy models
    schemas/             # Pydantic schemas (cards, habits, calendar, briefing, jobs, …)
    database.py          # DB engine, reads DATABASE_URL from env
    deps.py              # Shared dependencies: DB session, LLM client, auth constants
    app_setting_keys.py  # Constants for all AppSetting key strings
    streak.py            # Habit streak computation
    push.py              # Web Push / VAPID helpers
    weather.py           # Open-Meteo fetch + WMO condition helpers
    github_sync.py       # GitHub issue/PR sync logic
    gcal.py              # iCal/ICS parsing helpers
    briefing/            # Daily briefing feature package
      router.py          # /api/briefing/stream + /weather endpoints
      generate.py        # LLM briefing generation, cache helpers
      context.py         # Today/week context builders
    telegram/            # Telegram feature package
      router.py          # Config, test, webhook, scheduler-trigger endpoints
      bot.py             # handle_update, intent parsing, reply handlers
      scheduler.py       # check_all — briefing, reminders, bridge job notifications
      notify.py          # Raw Telegram HTTP calls
    routers/             # One file per feature area
      auth.py            # Login/logout, session management
      cards.py           # Tasks + reference cards CRUD, AI parse, iOS Shortcut
      habits.py          # Habits CRUD, completion toggle
      calendar.py        # iCal feed sync, export
      jobs.py            # Workshop jobs + agentic research
      withings.py        # Withings OAuth, sync, health data
      bridge.py          # Claude Code bridge: job queue, agent script install endpoint
      discovery.py       # Public iCal discovery feeds + LLM ranking
      food.py            # Food/drink logging + nutritional assessment
      insights.py        # Habit insights and health experiment suggestions
      correlations.py    # Health experiment tracking
      tags.py            # Tag CRUD
      engineering.py     # GitHub engineering feed
      push.py            # Push subscription management
      search.py          # Cross-entity search
      assist.py          # AI assist endpoint
    model_plugins/       # Per-model prompt tuning (base + llama3.2, llama3.1-8b, phi4-mini, llama3.3-70b)
    alembic/             # Database migrations (00001–00026)
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
      test_telegram.py       # Telegram config, test, and daily-briefing endpoints
      test_bridge.py         # Claude Code bridge job queue and agent endpoints
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
        shared/        # QueueIndicator, TagInput, and other shared components
    tests/
      visual.spec.js   # Playwright functional tests (all APIs mocked, 134 tests)
    dist/              # Production build output (gitignored)
  Dockerfile           # Multi-stage build (frontend + backend)
  deploy-gcp.md        # Full GCP deployment guide
  IDEAS.md             # Feature ideas and brainstorm
  dev.sh               # Development helper script
```

## Credits

Background video by RoyaltyFreeTube: https://www.youtube.com/watch?v=v-Qv3R28aCk
