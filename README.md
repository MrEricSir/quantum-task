# Quantum Task

A personal productivity dashboard with AI-powered quick add, calendar integration, habits tracking, health data, and a daily briefing.

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

# Verify a fresh cold start correctly restores data from the litestream
# replica (needs docker; no GCP access required — uses a local replica)
./dev.sh test-litestream
```

Runs in CI on every PR and again right before every production deploy — a
regression here would have caught the 2026-08-03 data-loss incident before
it shipped. See [Database storage](deploy-gcp.md#database-storage-sqlite--litestream)
for the failure mode this guards against.

`./dev.sh test` runs three suites in sequence:

**Backend unit tests** — no external services required:
- Calendar feed CRUD, timezone handling, iCal export/import, UID deduplication
- Briefing context builders (`build_today_context`, `build_week_context`, `compute_observations`)
- Weather fetch + WMO code mapping
- Habit streak computation (`recompute_from`, `recompute_all`, `get_current_streak`)
- Withings goal detection, `_auto_check_habits`, health metric regex
- AppSetting constants + `WithingsCredentials` model save/load
- Daily plan helpers, recurring card scheduling, food entry parsing
- Plugin post-processing: section/type overrides, tag suggestions, workout type detection
- Claude Code bridge: job create/start/complete/error, agent script endpoints, `?repos=` filtering, heartbeat + stale-job detection, post-implementation verification (`test_cmd` + `verify_acceptance`), manual verification (`--run`, with built-in Procfile support)
- Workout log: CRUD, date filtering, timezone handling, batch chart endpoint
- Food quality trend: daily averages, null-quality exclusion, date range filtering

**Quick Add parse integration tests** (`test_parse.py`) — requires Ollama:
- Section assignment, scheduled datetime, title preservation, tag suggestions
- `type` field (task / habit), habit recurrence detection

Tests that call Ollama are skipped automatically when Ollama is not running — no failures.

**Frontend tests** (`frontend/tests/visual.spec.js`) — no backend required:
- 130 Playwright tests verifying key elements are visible on each page
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
- Describe anything in plain English — the LLM classifies it as a **task**, **habit**, **food log**, **workout log**, **habit completion**, **task completion**, or **assist** request automatically
- Paste or type multiple items at once ("call sam at 3pm, buy milk and eggs, meditate daily") — each is split and parsed individually
- Date and time phrases are resolved to real datetimes: "call dentist tomorrow at 9am", "project review next Friday", "standup at 9"
- A confirm screen shows the detected type with a one-click override, then type-specific fields to review before saving
- Multiple items show a bulk-confirm list; click any item to open its full edit form before saving
- Deterministic post-processing catches common patterns: explicit recurrence → habit, "add a habit to X" → habit, natural past tense ("talked to a stranger", "went for a run") → habit completion, past-tense exercise verbs ("rowed 5000m", "bench pressed 185 lbs", "jogged 3 miles") → workout log
- Tags are auto-suggested from your existing tags
- **Assist mode**: conversational or planning requests ("help me plan my week") stream an AI response instead of creating a card

### Habits
- Track recurring habits with a daily completion toggle
- 7-day completion history shown as dots on each card; click it to expand a ~12-week completion heatmap and see your all-time best streak for that habit
- Streak counter, plus a weekly tier badge (🥉/🥈/🥇 for 3+/5+/7 days completed in the trailing week)
- Archive habits instead of deleting them; restore from the archive at any time
- Link habits to a Withings health goal (step count auto-completes when goal is met)

### Health
- Connect a Withings account (watch + smart scale) to sync step count, body fat %, and weight
- Set a numeric goal per metric; step habits auto-check when the daily goal is synced
- Charts showing steps (bar) and body fat % (line) over the past 90 days
- Habit completion overlay on each chart to see how habits track with progress
- **Workout log**: log any workout in plain English ("rowed 5000m", "bench pressed 185 lbs"); entries appear on the day's log and on a 30/60/90-day type-presence chart
- **Food quality trend**: daily average food quality score plotted over the selected range, averaged from individual food log ratings
- **Health experiments**: set a hypothesis, start/end date, and metric; the app tracks experiment progress and evaluates outcomes
- **Correlation scatter plots**: explore relationships between tracked metrics (e.g. steps vs. sleep, food quality vs. energy)

### Claude Code Bridge

Automate implementation work by sending cards to a local Claude Code agent. The bridge monitors a job queue and launches Claude Code automatically when a new job arrives.

#### How it works

1. Open any card and click **✦ Assist** in the footer
2. Click the **Code** tab
3. Click **✦ Generate** — the AI synthesises a requirements document from the card title, developer notes, and linked GitHub issue/PR context (body + comments)
4. Review and optionally edit the requirements inline, then click **▶ Run** to queue a job
5. The local bridge agent picks up the job, fetches the repo, and creates an isolated `git worktree` on a fresh `qtask/<id>-<slug>` branch off the latest primary branch — your own working directory is never touched, so this works even if you have uncommitted changes there
6. Claude Code launches interactively in your terminal — you can participate, ask questions, or let it run; push is disabled so no changes leave your machine until you review them
7. When the session ends, the branch, machine, and full worktree path are shown in the Code tab and sent via Telegram (with a copy button in the UI); the worktree is left in place for you to review, test, and push; the bridge picks up the next queued job automatically

You never have to go hunting for where a job's code landed — see [Finding your worktree](#finding-your-worktree) below for every way it's surfaced.

**If the agent process dies mid-session** (crash, network drop, laptop sleeps), the job would otherwise sit at "running" forever with no way to tell it apart from one that's actually still working. The bridge pings a heartbeat every 5 minutes while a session is active; if a job goes 20+ minutes without one, it's automatically marked **stalled** (shown in the Code tab, distinct from an outright error) and — if Telegram is configured — you get a notification. Re-running the card queues a fresh job.

#### Install the bridge agent

In **Settings → Engineering → GitHub**, copy the install command:

```bash
curl "http://localhost:8000/api/bridge/install.py?token=<install-token>" | python3
```

The `Copy` button fills in the real token for you — it's a separate, rotatable secret
(distinct from your app password) that only unlocks this one-time install script, shown so
the command can be fetched by a bare `curl` on a machine with no prior login. If you paste it
somewhere it might leak, click **Rotate token** in the same panel; that invalidates the old
command without touching your app password or any machine you've already installed on.

This installs `qtask-bridge` into your PATH, creates `~/.config/qtask-bridge/claude.toml`, and
configures git to ignore the files the bridge writes into every worktree (`BRIDGE_SPEC.md`,
`.claude/settings.local.json`, `.env.qtask`) **globally** — via git's own `core.excludesFile`
mechanism, not by editing any target repo's own `.gitignore`. If you don't already have a
`core.excludesFile` configured, the installer creates `~/.config/git/ignore_qtask_bridge` and
points git at it; if you do, it appends to whatever you've already got. Either way, no repo's
tracked files are ever touched — re-running the installer is safe and idempotent if you're
already set up.

**Where you run it from only matters if you skip configuration.** For any card linked to a GitHub issue, the bridge resolves the actual repo directory from `claude.toml` — not from your current directory — so once that's set up, `qtask-bridge` can be run from anywhere (your home directory, a cron job, doesn't matter). Configure it one of two ways:

```toml
# Option A — explicit path per repo (also where a per-repo setup_cmd goes)
[repos]
"owner/project_1" = "~/folder_a/project_1"
"owner/project_2" = "~/folder_a/project_2"

# Option B — auto-discovery: point at the parent folder and the bridge finds
# every repo under it by matching each subdirectory's git remote
repo_roots = ["~/folder_a"]
```

Your current directory is used only as a fallback, for a card with *no* linked GitHub issue — for that case, run the bridge from inside the repo you want it to act on.

```bash
qtask-bridge --watch          # poll for jobs; launch Claude Code interactively when one arrives
qtask-bridge --card <id>      # queue and run a specific card's job once
qtask-bridge --tag work       # queue + run every "work"-tagged card with a spec, unattended
qtask-bridge --list           # list qtask worktrees across configured repos (read-only)
qtask-bridge --cleanup        # list finished qtask worktrees and remove the ones you're done with
qtask-bridge --run [branch]   # run the app in a qtask worktree (cwd, last one, or a branch fragment)
```

The agent writes the spec to `BRIDGE_SPEC.md`, runs `claude` in an isolated git worktree on a fresh `qtask/<id>-<slug>` branch, and marks the job complete when the session ends. The worktree is left in place locally for your review — the bridge never pushes.

**`--watch` mode** runs interactively: you can participate in the Claude session, ask questions, or provide direction. When Claude finishes and you exit the session, the job is marked complete and the bridge immediately polls for the next one — no intervention needed between jobs.

**`--card` mode** is the same but prompts you for an optional note to attach to the job before moving on, useful for one-off runs where you want to record context.

**`--tag` mode** is for batching: tag several cards (each with a spec already generated) the same way, then run `qtask-bridge --tag <name>` to work through all of them sequentially, unattended — no interactive prompts, each card gets its own worktree. Since it's unattended, it runs Claude Code with `--dangerously-skip-permissions`; only use it on cards you trust to run without a human approving each action.

#### Finding your worktree

Every job gets its own isolated worktree, which is what makes it safe to run
without touching your own working directory — but that only works if you can
actually find it afterward. It's surfaced five ways, so you're never stuck
running `git worktree list` to figure out where a job's code went:

- **In the app** — the Code tab shows the full path under the branch name, with a copy button
- **In Telegram** — the completion message includes the path alongside the branch
- **In Claude Code itself** — each worktree gets a local, gitignored `.claude/settings.local.json` configuring a status line that shows the branch and path for the entire session, so you never have to wonder mid-conversation where you are. Note: Claude Code's workspace trust prompt gates this — on the very first launch in a brand-new worktree (which is every worktree), you may need to accept that prompt before the status line appears.
- **In your terminal tab** — interactive sessions (`--watch`/`--card`) set the tab/window title to the branch name, so multiple job tabs stay identifiable at a glance
- **From any shell** — `qtask-bridge --list` prints every qtask worktree across your configured repos (read-only, no prompt, safe to run anytime). For a one-keystroke jump to the most recent one specifically, add this to your shell config:
  ```bash
  qcd() { cd "$(cat ~/.local/share/qtask-bridge/last-worktree)"; }
  ```

`--list` and `--cleanup` only scan repos listed explicitly under `[repos]` in `claude.toml` — a repo resolved via `repo_roots` auto-discovery won't show up in either.

#### Avoiding port and database collisions

Isolating the *code* doesn't isolate the *runtime* — two jobs (or a job and your own dev instance of the same app) can still fight over the same port or the same local database if nothing tells them apart. Every worktree gets a `.env.qtask` file with a port range and database name reserved just for that job, derived deterministically from the job ID so re-running the same card later still gets a fresh reservation:

```bash
QTASK_JOB_ID=77
QTASK_PORT_BASE=20770
QTASK_PORT_RANGE=20770-20779
QTASK_DB_NAME=qtask_job_77
```

The prompt Claude receives explicitly points it at this file and asks it to use these values instead of framework defaults for anything it starts locally. The bridge doesn't know or care what the target app's architecture looks like (frontend port vs. backend port vs. database), so it reserves a namespace rather than trying to wire specific services — Claude (already reading the codebase to implement the feature) resolves the actual wiring per-repo. Nothing enforces the reservation; it's a convention, not a lock, and job IDs cycle through a 400-slot range, so collisions are possible if you have hundreds of uncleaned worktrees running dev servers simultaneously — not a realistic scenario for how this tool is meant to be used (sequentially, one job at a time).

#### Verifying a fix before you review it

Two opt-in checks run automatically after a session ends, before the job is marked complete — both off by default, so nothing changes unless you configure them:

```toml
[repos."owner/project_1"]
path = "~/folder_a/project_1"
test_cmd = "npm test"          # run your test suite; pass/fail + a truncated output tail
                                 # is added to the job result. No LLM call, purely mechanical.
verify_acceptance = true        # one extra, read-only Claude check of the diff against the
                                 # spec's Acceptance Criteria checklist, reporting MET/NOT MET
                                 # per item. Costs one extra LLM call per job.

# top-level fallback for repos that don't set their own, same as setup_cmd
test_cmd = "pytest"
verify_acceptance = true
```

`verify_acceptance` is explicitly told not to modify any files — it only reports, it never fixes. Both results get prepended to the job's `result` text, so they show up in the Code tab and Telegram alongside whatever note you'd normally see. If the implementation session itself errors out, verification is skipped — nothing useful to test against a session that didn't complete.

#### Trying a change yourself

For anything the automated checks above don't cover — visual/UX judgment, exploratory testing — `qtask-bridge --run` runs the app for you, right in the resolved worktree:

```bash
qtask-bridge --run             # cwd if you're already in a qtask worktree, else the last one used
qtask-bridge --run 84-ranking  # branch fragment; prompts a numbered pick if it matches more than one
```

What actually runs, in order: a `Procfile.dev` in the worktree, if present (starts every process it lists concurrently — the case a separate frontend and backend that need to run together calls for); else a plain `Procfile`; else a configured `run_cmd`; else a message telling you to set one up. `Procfile.dev` wins over a bare `Procfile` on purpose — a repo's root `Procfile` is often meant for production/Heroku, not a scratch dev worktree, so an explicit dev-specific file (the same convention Rails 7+ ships in `bin/dev`) takes precedence.

```toml
[repos."owner/api"]
path = "~/folder_a/api"
run_cmd = "npm run dev"        # used only when the worktree has no Procfile.dev/Procfile

# top-level fallback, same as setup_cmd/test_cmd
run_cmd = "npm run dev"
```

Multiple processes from a Procfile print with a colorized `[name]` prefix so their output stays distinguishable, and stop together — on Ctrl-C, or when any one of them exits on its own. The reserved port range and database name from `.env.qtask` (see above) are loaded automatically into whatever runs, so there's no manual `source .env.qtask` step.

qtask-bridge is a single, dependency-free file, so `--run` doesn't shell out to an external process manager (Foreman, Overmind, Honcho) — the Procfile support above is built in.

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
| `energy 4` | Logs today's energy level (1--5 scale) |
| `feeling tired, 2/5` | Same -- natural phrasing works |
| `rowed 5000m` | Logs a workout entry (type, value, and unit parsed automatically) |
| `bench pressed 185 lbs` | Logs a strength workout |
| `went for a 3 mile run` | Logs a run |

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
- **Health check-in** (same time as the evening habit reminder) — a bundled, low-noise nudge for patterns worth knowing about: a multi-day streak not yet done today, a habit whose completion rate has dropped over the past week, a couple of days with nothing logged in the food log, or a Withings-tracked goal (steps, body fat) drifting off target over the past week. Deliberately ignores single missed days — each signal has its own cooldown so a persistent issue is flagged once, not nagged about daily.
- **Streak celebrations** (automatic, runs alongside the checks above) — a message at 3/7/14/21/30/60/100/365 days for habit streaks, a daily food-quality streak (consecutive days averaging a good quality score), and a task-completion streak (consecutive days with at least one task done). A habit milestone also calls out when it's a new personal best for that habit.

---

**Setup (one-time):**
1. Message **@BotFather** on Telegram, send `/newbot`, and copy the token it gives you
2. Send any message to your new bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` — your numeric chat ID appears in the response
3. Paste both into **Settings → Telegram**, pick a delivery hour, and click **Save**
4. Click **Register webhook** in the Two-way chat section — this tells Telegram to send messages to your backend
5. (Production) Run `./dev.sh gcp-setup-scheduler` once to create the Cloud Scheduler jobs that drive this (and also keep Withings syncing reliably — see [Deploying to GCP](#deploying-to-gcp))

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
- Ranking runs in the background so the panel shows events immediately (a "Ranking recommendations..." hint appears while it works) instead of blocking on the LLM call

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

### LLM (AI Quick Add + Daily Briefing + Assist)

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
| `BACKUP_DIR` | same directory as the database | Where `POST /api/backup/run` writes dated snapshots (see [Database backups](deploy-gcp.md#database-backups)) |

In production, `DATABASE_URL` points at local container disk
(`sqlite:////app/db/todos.db`), not Cloud Storage — see
[Database storage](deploy-gcp.md#database-storage-sqlite--litestream) for why.

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

### Assist web search (optional)

| Variable | Default | Description |
|---|---|---|
| `TAVILY_API_KEY` | _(unset)_ | API key from [tavily.com](https://tavily.com) — enables the AI Assist panel to search the web when a request needs current information. Free tier available. |

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

## Data notes

### Workout and food log timestamps

Workout and food log entries are stored as **naive local datetimes** (no timezone suffix). The backend derives the user's local time from the `X-UTC-Offset` request header that the frontend injects automatically via `apiFetch`. Date-range queries compare this stored local time against the requested date string directly.

If you have existing workout or food entries that were logged before this convention was adopted (i.e., they were stored as UTC timestamps instead of local time), those entries may appear under the wrong date for users in UTC-offset timezones. There is no automatic migration -- to correct affected entries, update the `logged_at` / `consumed_at` column values directly in SQLite using the known UTC offset at the time of logging.

## Deploying to GCP

See **`deploy-gcp.md`** for the full guide, including infrastructure setup, GitHub secrets, LLM provider options, and CI/CD details.

After deploying, run this once to set up the Cloud Scheduler jobs:

```bash
./dev.sh gcp-setup-scheduler
```

This creates three Cloud Scheduler jobs:
- `telegram-daily-briefing` — hourly, hits `/api/telegram/daily-briefing`, which runs every scheduled Telegram check (briefing, habit reminder, overdue nudge, health check-in, streak milestones). The app checks whether the current local hour matches your configured send time for each and skips silently otherwise.
- `withings-sync` — hourly, hits `/api/withings/sync` directly, so Withings data (and the habit auto-checks that depend on it) syncs reliably even when the app has no traffic. Cloud Run runs with min instances 0, so the in-process sync loop in `main.py` (still there, and still what drives sync locally in dev) can't be relied on to run on a fixed cadence in production — this job covers that gap. Both paths call the same sync function, which is idempotent, so there's no harm in both being active.
- `db-backup` — daily, hits `/api/backup/run`, which copies the live SQLite database to a datestamped file in a `backups/` subdirectory of the same bucket. See [Database backups](deploy-gcp.md#database-backups) in the deployment guide.

All three jobs are automatically updated on every subsequent `./dev.sh gcp-deploy`.

The live database runs on local disk under [litestream](https://litestream.io),
continuously replicated to Cloud Storage — see [Database storage](deploy-gcp.md#database-storage-sqlite--litestream)
in the deployment guide for why, and [Grabbing the database for debugging](deploy-gcp.md#grabbing-the-database-for-debugging)
(`./dev.sh gcp-db-pull`) for pulling a queryable local copy.

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
    backup/              # Database backup feature package
      router.py          # POST /api/backup/run
      run.py             # SQLite online-backup-API copy to backups/todos_<date>.db
    assist/              # AI assistant feature package
      router.py          # Card-thread CRUD + route registration
      generate.py        # LLM system prompts, streaming chat/spec-generation logic
      context.py         # Calendar/GitHub context builders, web search
    bridge/              # Claude Code bridge feature package
      router.py          # Job queue endpoints, install/agent script serving, heartbeat, check-stale
      jobs.py            # Prompt building, job queueing/serialization
      render.py          # Renders served CLI scripts from bridge/scripts/ (placeholder substitution + concatenation)
      stale.py           # Detects bridge jobs with no heartbeat and marks them "stalled"
      scripts/           # The served CLI as real .py files, not string literals
        install.py        # curl-able installer (GET /api/bridge/install.py)
        agent_core.py      # Agent-agnostic job polling, git worktree lifecycle, CLI commands
        agent_claude.py    # Claude Code adapter (swap this to try a different coding agent)
    routers/             # One file per feature area
      auth.py            # Login/logout, session management
      cards.py           # Tasks + reference cards CRUD, AI parse, iOS Shortcut
      habits.py          # Habits CRUD, completion toggle
      calendar.py        # iCal feed sync, export
      withings.py        # Withings OAuth, sync, health data
      discovery.py       # Public iCal discovery feeds + LLM ranking
      food.py            # Food/drink logging + nutritional assessment
      workouts.py        # Workout log CRUD + batch chart endpoint
      insights.py        # Habit insights and health experiment suggestions
      correlations.py    # Health experiment tracking
      tags.py            # Tag CRUD
      engineering.py     # GitHub engineering feed
      push.py            # Push subscription management
    model_plugins/       # Per-model prompt tuning (base + llama3.2, llama3.1-8b, phi4-mini, llama3.3-70b)
    alembic/             # Database migrations (00001–00029)
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
      test_food.py           # Food entry CRUD, date filtering, quality trend
      test_workouts.py       # Workout log CRUD, timezone handling, chart endpoint
      test_telegram.py       # Telegram config, test, and daily-briefing endpoints
      test_assist_thread.py  # AI assist chat/thread endpoints, one-shot + global assist
      test_bridge_jobs.py    # Claude Code bridge job queue endpoints
      test_bridge_scripts.py # Served install.py/agent.py content + rendering
      test_bridge_stale.py   # Stale bridge job detection, heartbeat, notifications
      test_backup.py         # Database backup: run_backup + POST /api/backup/run
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
                       # EngineeringPage, HealthPage, LoginPage
                       # (board is rendered inline in App.jsx)
        board/         # Column, TodoCard, CalendarEventCard, Archive
        layout/        # Sidebar, MobileNav, TagFilterBar
        modals/        # QuickAddModal, CardSheet, CalendarSettings, GithubSettings,
                       # WithingsSettings, TagManager, AssistModal, ...
        shared/        # QueueIndicator, TagInput, and other shared components
    tests/
      visual.spec.js   # Playwright functional tests (all APIs mocked, 130 tests)
    dist/              # Production build output (gitignored)
  Dockerfile           # Multi-stage build (frontend + backend + litestream)
  litestream.yml       # Litestream config, baked into the image at /etc/litestream.yml
  deploy-gcp.md        # Full GCP deployment guide
  IDEAS.md             # Feature ideas and brainstorm
  dev.sh               # Development helper script
```

## Credits

Background video by RoyaltyFreeTube: https://www.youtube.com/watch?v=v-Qv3R28aCk
