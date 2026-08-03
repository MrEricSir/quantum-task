# GCP Deployment

## Architecture

```
Browser
  │
  └─ All traffic ───────────────────────▶ Cloud Run  (~$0, scales to zero)
                                               │
                                   ┌───────────┴────────────┐
                                   │                        │
                              Cloud Storage            LLM API
                           SQLite on GCS (~$0)   (Gemini / Groq / Ollama)
```

Cloud Run serves both the frontend (static files) and the `/api/**` backend
from a single Docker image — no separate hosting service needed.

**Why `--max-instances 1`:** the database is a SQLite file on a Cloud
Storage FUSE volume mount, and Cloud Storage FUSE provides no real file
locking — concurrent writers to the same file last-write-wins, silently
dropping the earlier write. `dev.sh` pins the Cloud Run service to a single
instance so there is only ever one writer. `--min-instances 0` is still
fine (and free) alongside this — it just means zero *or* one instance, never
more than one.

---

## Cost estimate (personal use)

| Component | Notes | $/month |
|---|---|---|
| Cloud Run | 2M req + 360K vCPU-sec free tier | $0 |
| Cloud Storage | Tiny SQLite file, 5GB free tier | $0 |
| Gemini 2.0 Flash | ~$0.0001/request (if using Gemini) | < $0.50 |
| Groq | Free tier available | $0 |
| **Total** | | **~$0–$1/mo** |

---

## Prerequisites

```bash
brew install google-cloud-sdk
gcloud auth login
```

---

## One-time setup

### 1. Configure

```bash
cp .gcp-config.example .gcp-config
# Edit .gcp-config and fill in your values
```

Fields to set:

- **`GCP_PROJECT_ID`** — your GCP project ID (`gcloud projects list`)
- **`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`** — choose one provider:

  | Provider | LLM_BASE_URL | LLM_MODEL | Key from |
  |---|---|---|---|
  | Gemini (cheap, pay-as-you-go) | `https://generativelanguage.googleapis.com/v1beta/openai/` | `gemini-2.0-flash` | aistudio.google.com |
  | Groq (free tier) | `https://api.groq.com/openai/v1` | `llama-3.1-8b-instant` | console.groq.com |
  | Ollama (local dev only) | `http://localhost:11434/v1` | `llama3.2` | — |

- **`AUTH_PASSWORD`** — password for the login gate (leave empty to disable)

### 2. Run the setup script

```bash
./dev.sh gcp-setup
```

This script:
1. Enables all required GCP APIs
2. Creates an Artifact Registry repository for Docker images
3. Creates a Cloud Storage bucket for the SQLite database
4. Creates a Cloud Run service account with storage access
5. Builds and pushes the initial Docker image via Cloud Build
6. Deploys to Cloud Run (frontend + backend, single service)
7. Creates a GitHub Actions service account
8. Generates a service account key file (`.github-actions-sa-key.json`)
9. Prints the GitHub secrets you need to add

### 3. Set GitHub secrets

After `gcp-setup` finishes, add these to your GitHub repository under
**Settings → Secrets and variables → Actions**:

| Type | Name | Value |
|---|---|---|
| Secret | `GCP_SA_KEY` | Contents of `.github-actions-sa-key.json` |
| Secret | `AUTH_PASSWORD` | Your login password (if auth is enabled) |
| Secret | `WITHINGS_CLIENT_ID` | Withings OAuth client ID (if using health tracking) |
| Secret | `WITHINGS_SECRET` | Withings OAuth client secret (if using health tracking) |
| Secret | `WITHINGS_CALLBACK_URI` | `https://YOUR_CLOUD_RUN_URL/api/withings/callback` |
| Secret | `TAVILY_API_KEY` | Tavily API key (if using Assist web search — free tier at tavily.com) |
| Variable | `GCP_PROJECT_ID` | Your GCP project ID |

The key file is gitignored and stays on your machine only.

> **LLM settings are not stored as GitHub secrets.** They are baked into the
> Cloud Run service during `gcp-setup` and persist across deployments. To
> change providers later, update `.gcp-config` and run `./dev.sh gcp-update-env`.

> **Withings callback URI:** Register both `http://localhost:8000/api/withings/callback`
> (for local dev) and your production Cloud Run URL in the Withings developer console
> under your app's allowed redirect URIs. Use the `WITHINGS_CALLBACK_URI` env var /
> GitHub secret to select which one each environment uses.

---

## Ongoing deployments

### Via GitHub CI (recommended)

Push to `main`. The CI/CD pipeline (`.github/workflows/deploy.yml`) will:
1. Run backend unit tests
2. Build the Docker image and push to Artifact Registry
3. Deploy the new image to Cloud Run

Pull requests run the tests and a build check, but don't deploy.

### Manually

```bash
./dev.sh gcp-deploy
```

Builds via Cloud Build (native linux/amd64) and deploys from your local machine.

---

## Updating environment variables

To switch LLM provider, rotate the auth password, or change any other setting,
edit `.gcp-config` and run:

```bash
./dev.sh gcp-update-env
```

This updates the env vars on the running Cloud Run service — no image rebuild needed.

---

## Viewing logs

```bash
./dev.sh gcp-logs              # last 100 lines from Cloud Run
./dev.sh gcp-logs 200          # last 200 lines
./dev.sh gcp-logs 100 withings # last 100 lines, grep for "withings"
./dev.sh gcp-logs 50 callback  # useful for diagnosing OAuth flows
```

Requires `gcloud auth login`. Reads from the Cloud Run service configured in `.gcp-config`.

---

## Database backups

`./dev.sh gcp-setup-scheduler` also creates a daily Cloud Scheduler job,
`db-backup`, that POSTs to `/api/backup/run` once a day (09:00 UTC by
default). That endpoint copies the live SQLite database to a datestamped
file — `todos_YYYY-MM-DD.db` — in a `backups/` subdirectory of the same
Cloud Storage bucket the live database lives in (`gs://<project>-todo-db/backups/`).
Re-running on the same day overwrites that day's file rather than
accumulating multiple copies.

The copy is made with SQLite's online backup API (`sqlite3.Connection.backup`),
not a plain file copy, so it produces a consistent snapshot even while the
app is writing to the live database.

To restore, download the desired backup and point `DATABASE_URL` at it (or
copy it over the live `todos.db` in the mounted volume) before redeploying:

```bash
gcloud storage cp gs://<project>-todo-db/backups/todos_2026-08-01.db ./todos.db
```

There's also an in-process daily loop in `main.py` (`_backup_scheduler`) —
it exists purely for local-dev convenience (so backups work without any GCP
setup); in production the Cloud Scheduler job is what makes this reliable,
since a Cloud Run instance with `min-instances 0` can be recycled before 24
hours of in-process uptime pass.

Backups are not currently pruned — old dated files accumulate in `backups/`
indefinitely. Given the database is tiny (personal-use SQLite), this is a
non-issue at current scale; add lifecycle rules on the bucket if that ever
changes.

---

## Local development

Without any env vars set, the backend falls back to:
- SQLite at `./todos.db`
- Ollama at `http://localhost:11434/v1` (model: `llama3.2`)
- No auth (login gate disabled)

To test a cloud LLM provider locally, export the vars before starting:

```bash
export LLM_BASE_URL="https://api.groq.com/openai/v1"
export LLM_API_KEY="gsk_..."
export LLM_MODEL="llama-3.1-8b-instant"
./dev.sh start
```

`./dev.sh start` continues to work as before for local development.
