# GCP Deployment

## Architecture

```
Browser
  │
  ├─ static assets ──────────────────────▶ Firebase Hosting  (free tier)
  │
  └─ /api/** (rewrite) ─────────────────▶ Cloud Run         (~$0, scales to zero)
                                                │
                                    ┌───────────┴────────────┐
                                    │                        │
                               Cloud Storage            Gemini 2.0 Flash
                            SQLite on GCS (~$0)          (replaces Ollama)
```

Firebase Hosting rewrites `/api/**` to Cloud Run — no frontend code changes needed.

---

## Cost estimate (personal use)

| Component | Notes | $/month |
|---|---|---|
| Cloud Run | 2M req + 360K vCPU-sec free tier | $0 |
| Cloud Storage | Tiny SQLite file, 5GB free tier | $0 |
| Firebase Hosting | 10GB bandwidth free tier | $0 |
| Gemini 2.0 Flash | ~$0.0001/request | < $0.50 |
| **Total** | | **< $1/mo** |

---

## Prerequisites

Install the required CLIs if you haven't already:

```bash
brew install google-cloud-sdk
npm install -g firebase-tools

gcloud auth login
firebase login
```

---

## One-time setup

### 1. Configure

```bash
cp .gcp-config.example .gcp-config
# Edit .gcp-config — fill in GCP_PROJECT_ID, GEMINI_API_KEY, ALLOWED_ORIGIN
```

Get a free Gemini API key from [Google AI Studio](https://aistudio.google.com).

For `ALLOWED_ORIGIN`, use `https://YOUR_PROJECT_ID.web.app` initially (you can
update it after the first deploy with `./dev.sh gcp-update-env`).

### 2. Initialize Firebase Hosting (one time)

```bash
firebase init hosting
# - Use existing project: your-project-id
# - Public directory: frontend/dist
# - Configure as single-page app: yes
# - Don't overwrite index.html
```

This creates `.firebaserc` and updates `firebase.json`.

### 3. Run the setup script

```bash
./dev.sh gcp-setup
```

This script:
1. Enables all required GCP APIs
2. Creates an Artifact Registry repository for Docker images
3. Creates a Cloud Storage bucket for the SQLite database
4. Creates a Cloud Run service account with storage access
5. Builds and pushes the initial Docker image
6. Deploys the backend to Cloud Run
7. Builds and deploys the frontend to Firebase Hosting
8. Creates a GitHub Actions service account + Firebase deploy service account
9. Generates service account key files (`.github-actions-sa-key.json`, `.firebase-sa-key.json`)
10. Prints the GitHub secrets you need to add

### 4. Set GitHub secrets

After `gcp-setup` finishes, add these to your GitHub repository under
**Settings → Secrets and variables → Actions**:

| Type | Name | Value |
|---|---|---|
| Secret | `GCP_SA_KEY` | Contents of `.github-actions-sa-key.json` |
| Secret | `FIREBASE_SERVICE_ACCOUNT` | Contents of `.firebase-sa-key.json` |
| Secret | `GEMINI_API_KEY` | Your Gemini API key |
| Variable | `GCP_PROJECT_ID` | Your GCP project ID |

The key files are gitignored and stay on your machine only.

---

## Ongoing deployments

### Via GitHub CI (recommended)

Push to `main`. The CI/CD pipeline (`.github/workflows/deploy.yml`) will:
1. Run backend unit tests
2. Build the frontend (fails fast if the build is broken)
3. Build and push the Docker image to Artifact Registry
4. Deploy the new image to Cloud Run
5. Deploy the built frontend to Firebase Hosting

Pull requests run the tests and a Docker build check, but don't deploy.

### Manually

```bash
./dev.sh gcp-deploy
```

Builds and deploys both backend and frontend from your local machine.

---

## Updating environment variables

To rotate the Gemini API key or update the CORS origin, edit `.gcp-config`
and run:

```bash
./dev.sh gcp-update-env
```

This updates only the env vars on the running Cloud Run service — no
redeploy of the image needed.

---

## Local development (unchanged)

Without any env vars set, the backend falls back to:
- SQLite at `./todos.db`
- Ollama at `http://localhost:11434/v1`
- CORS origin `http://localhost:5173`

`./dev.sh start` continues to work as before.
