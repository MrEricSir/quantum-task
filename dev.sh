#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/.dev.pids"
BACKEND_LOG="$SCRIPT_DIR/backend.log"
FRONTEND_LOG="$SCRIPT_DIR/frontend.log"

# ── Local dev ─────────────────────────────────────────────────────────────────

setup() {
  echo "==> Backend"
  cd "$SCRIPT_DIR/backend"

  if [[ ! -d venv ]]; then
    echo "    Creating virtualenv..."
    python3 -m venv venv
  else
    echo "    Virtualenv already exists, skipping."
  fi

  echo "    Installing Python dependencies..."
  venv/bin/pip install --upgrade pip -q
  venv/bin/pip install -r requirements.txt

  echo ""
  echo "==> Frontend"
  cd "$SCRIPT_DIR/frontend"
  echo "    Installing Node dependencies..."
  npm install

  echo ""
  echo "==> Ollama (AI quick-add)"
  if command -v ollama &>/dev/null; then
    echo "    Pulling llama3.2 model (this may take a while on first run)..."
    ollama pull llama3.2
  else
    echo "    Ollama not found. Install it to enable AI quick-add:"
    echo "      brew install ollama"
    echo "      brew services start ollama"
    echo "      ollama pull llama3.2"
    echo "    The app works without it; only the Quick Add feature requires Ollama."
  fi

  echo ""
  echo "Setup complete. Run './dev.sh start' to launch the app."
}

start() {
  if [[ -f "$PID_FILE" ]]; then
    echo "App is already running. Use './dev.sh stop' first."
    exit 1
  fi

  if [[ ! -d "$SCRIPT_DIR/backend/venv" ]] || [[ ! -d "$SCRIPT_DIR/frontend/node_modules" ]]; then
    echo "Dependencies not installed. Run './dev.sh setup' first."
    exit 1
  fi

  echo "Starting backend..."
  cd "$SCRIPT_DIR/backend"
  "$SCRIPT_DIR/backend/venv/bin/uvicorn" main:app --reload \
    > "$BACKEND_LOG" 2>&1 &
  BACKEND_PID=$!

  echo "Starting frontend..."
  cd "$SCRIPT_DIR/frontend"
  npm run dev > "$FRONTEND_LOG" 2>&1 &
  FRONTEND_PID=$!

  echo "$BACKEND_PID $FRONTEND_PID" > "$PID_FILE"

  echo ""
  echo "  Backend  → http://localhost:8000  (PID $BACKEND_PID)"
  echo "  Frontend → http://localhost:5173  (PID $FRONTEND_PID)"
  echo ""
  echo "Logs: backend.log / frontend.log"
  echo "Stop: ./dev.sh stop"
}

stop() {
  if [[ ! -f "$PID_FILE" ]]; then
    echo "No running app found."
    exit 0
  fi

  read -r BACKEND_PID FRONTEND_PID < "$PID_FILE"

  echo "Stopping backend (PID $BACKEND_PID)..."
  kill "$BACKEND_PID" 2>/dev/null || true
  pkill -P "$BACKEND_PID" 2>/dev/null || true

  echo "Stopping frontend (PID $FRONTEND_PID)..."
  kill "$FRONTEND_PID" 2>/dev/null || true
  pkill -P "$FRONTEND_PID" 2>/dev/null || true

  rm -f "$PID_FILE"
  echo "Done."
}

logs() {
  tail -f "$BACKEND_LOG" "$FRONTEND_LOG"
}

test() {
  if [[ ! -d "$SCRIPT_DIR/backend/venv" ]]; then
    echo "Dependencies not installed. Run './dev.sh setup' first."
    exit 1
  fi
  echo "Installing test dependencies..."
  "$SCRIPT_DIR/backend/venv/bin/pip" install -r "$SCRIPT_DIR/backend/requirements-dev.txt" -q
  cd "$SCRIPT_DIR/backend"
  # Calendar tests are pure unit tests (no Ollama required).
  # Parse tests call the live Ollama model and are skipped automatically if it is not running.
  echo ""
  echo "==> Calendar unit tests"
  "$SCRIPT_DIR/backend/venv/bin/pytest" test_calendar.py -v
  echo ""
  echo "==> Quick Add parse integration tests (requires Ollama)"
  "$SCRIPT_DIR/backend/venv/bin/pytest" test_parse.py -v
}

benchmark() {
  if [[ ! -d "$SCRIPT_DIR/backend/venv" ]]; then
    echo "Dependencies not installed. Run './dev.sh setup' first."
    exit 1
  fi
  echo "Installing test dependencies..."
  "$SCRIPT_DIR/backend/venv/bin/pip" install -r "$SCRIPT_DIR/backend/requirements-dev.txt" -q
  cd "$SCRIPT_DIR/backend"
  "$SCRIPT_DIR/backend/venv/bin/python" benchmark.py "$@"
}

# ── GCP shared helpers ────────────────────────────────────────────────────────

_check_gcp_auth() {
  if ! gcloud auth print-access-token --quiet &>/dev/null; then
    echo "ERROR: Not authenticated with gcloud."
    echo "  Run: gcloud auth login"
    exit 1
  fi
}

_check_firebase_auth() {
  if ! firebase projects:list --json 2>/dev/null | grep -q '"status"'; then
    echo "ERROR: Not authenticated with Firebase."
    echo "  Run: firebase login"
    exit 1
  fi
}

_load_gcp_config() {
  local config="$SCRIPT_DIR/.gcp-config"
  if [[ ! -f "$config" ]]; then
    echo "ERROR: .gcp-config not found."
    echo "  Copy .gcp-config.example to .gcp-config and fill in your values:"
    echo "    cp .gcp-config.example .gcp-config"
    exit 1
  fi
  # shellcheck source=/dev/null
  source "$config"

  # Validate required fields
  local missing=()
  for var in GCP_PROJECT_ID GCP_REGION GCP_SERVICE_NAME GCP_AR_REPO GEMINI_API_KEY ALLOWED_ORIGIN; do
    local val="${!var:-}"
    if [[ -z "$val" || "$val" == *"your-"* ]]; then
      missing+=("$var")
    fi
  done
  if [[ ${#missing[@]} -gt 0 ]]; then
    echo "ERROR: The following fields are not set in .gcp-config:"
    for v in "${missing[@]}"; do echo "  $v"; done
    exit 1
  fi

  gcloud config set project "$GCP_PROJECT_ID" --quiet
}

# Check that required CLIs are installed and gcloud is authenticated.
# Pass "firebase" as argument to also verify Firebase auth.
_gcp_prereqs() {
  echo "==> Checking prerequisites..."
  for cmd in gcloud firebase; do
    if ! command -v "$cmd" &>/dev/null; then
      case "$cmd" in
        gcloud)   echo "  gcloud not found. Install: brew install google-cloud-sdk" ;;
        firebase) echo "  firebase not found. Install: npm install -g firebase-tools" ;;
      esac
      exit 1
    fi
  done
  _check_gcp_auth
  [[ "${1:-}" == "firebase" ]] && _check_firebase_auth
}

# Build the backend image using Cloud Build (runs on native linux/amd64 in GCP,
# no local Docker or architecture concerns) and tag the result.
# Primary tag is built directly; any extra tags are applied via artifact tagging.
# Usage: _build_and_push PRIMARY_TAG [EXTRA_TAG ...]
# Requires $IMAGE and $GCP_PROJECT_ID to be set.
_build_and_push() {
  local primary="$1"

  echo "==> Building and pushing Docker image via Cloud Build ($primary)..."
  gcloud builds submit "$SCRIPT_DIR/backend" \
    --tag "$IMAGE:$primary" \
    --project "$GCP_PROJECT_ID" \
    --quiet

  for t in "${@:2}"; do
    gcloud artifacts docker tags add \
      "$IMAGE:$primary" "$IMAGE:$t" \
      --project "$GCP_PROJECT_ID" --quiet
  done
}

# Build the frontend and deploy to Firebase Hosting.
# Requires $GCP_PROJECT_ID to be set.
_deploy_frontend() {
  echo "==> Building frontend..."
  cd "$SCRIPT_DIR/frontend" && npm ci && npm run build
  echo "==> Deploying frontend to Firebase Hosting..."
  firebase deploy --only hosting --project "$GCP_PROJECT_ID"
}

# ── GCP commands ──────────────────────────────────────────────────────────────

gcp_setup() {
  _gcp_prereqs firebase
  _load_gcp_config

  local IMAGE="$GCP_REGION-docker.pkg.dev/$GCP_PROJECT_ID/$GCP_AR_REPO/backend"
  local GCS_BUCKET="${GCP_PROJECT_ID}-todo-db"
  local CLOUD_RUN_SA="cloud-run@${GCP_PROJECT_ID}.iam.gserviceaccount.com"
  local GHA_SA="github-actions@${GCP_PROJECT_ID}.iam.gserviceaccount.com"
  local FIREBASE_SA="firebase-deploy@${GCP_PROJECT_ID}.iam.gserviceaccount.com"

  echo ""
  echo "==> Project : $GCP_PROJECT_ID"
  echo "    Region  : $GCP_REGION"
  echo "    Image   : $IMAGE"
  echo "    Bucket  : gs://$GCS_BUCKET"
  echo ""

  # ── 1. Enable required APIs ──────────────────────────────────────────────────
  echo "==> Enabling GCP APIs (this may take a minute on a new project)..."
  gcloud services enable \
    run.googleapis.com \
    artifactregistry.googleapis.com \
    cloudbuild.googleapis.com \
    storage.googleapis.com \
    iam.googleapis.com \
    cloudresourcemanager.googleapis.com \
    firebase.googleapis.com \
    firebasehosting.googleapis.com \
    --project "$GCP_PROJECT_ID" --quiet

  # ── 2. Add Firebase to the GCP project (idempotent) ──────────────────────────
  echo "==> Adding Firebase to project..."
  firebase projects:addfirebase "$GCP_PROJECT_ID" --no-input 2>/dev/null \
    && echo "    Firebase added." || echo "    Firebase already configured — skipping."

  # ── 3. Artifact Registry ─────────────────────────────────────────────────────
  echo "==> Creating Artifact Registry repository..."
  gcloud artifacts repositories create "$GCP_AR_REPO" \
    --repository-format=docker \
    --location="$GCP_REGION" \
    --project "$GCP_PROJECT_ID" 2>/dev/null \
    && echo "    Created." || echo "    Already exists — skipping."

  # ── 4. Cloud Storage bucket for SQLite ───────────────────────────────────────
  echo "==> Creating Cloud Storage bucket..."
  gcloud storage buckets create "gs://$GCS_BUCKET" \
    --location="$GCP_REGION" \
    --project "$GCP_PROJECT_ID" 2>/dev/null \
    && echo "    Created gs://$GCS_BUCKET." || echo "    Already exists — skipping."

  # ── 5. Cloud Run service account ─────────────────────────────────────────────
  echo "==> Creating Cloud Run service account..."
  gcloud iam service-accounts create cloud-run \
    --display-name="Cloud Run Backend" \
    --project "$GCP_PROJECT_ID" 2>/dev/null \
    && echo "    Created $CLOUD_RUN_SA." || echo "    Already exists — skipping."

  echo "==> Granting Cloud Run SA access to the database bucket..."
  gcloud storage buckets add-iam-policy-binding "gs://$GCS_BUCKET" \
    --member="serviceAccount:$CLOUD_RUN_SA" \
    --role="roles/storage.objectAdmin" --quiet

  # ── 6. Build and push the initial Docker image ────────────────────────────────
  _build_and_push latest

  # ── 7. Deploy to Cloud Run ────────────────────────────────────────────────────
  echo "==> Deploying to Cloud Run (first time)..."
  gcloud run deploy "$GCP_SERVICE_NAME" \
    --image "$IMAGE:latest" \
    --region "$GCP_REGION" \
    --platform managed \
    --allow-unauthenticated \
    --service-account "$CLOUD_RUN_SA" \
    --add-volume "name=db,type=cloud-storage,bucket=$GCS_BUCKET" \
    --add-volume-mount "volume=db,mount-path=/app/data" \
    --set-env-vars "\
DATABASE_URL=sqlite:////app/data/todos.db,\
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/,\
LLM_API_KEY=$GEMINI_API_KEY,\
LLM_MODEL=gemini-2.0-flash,\
ALLOWED_ORIGIN=$ALLOWED_ORIGIN" \
    --project "$GCP_PROJECT_ID" \
    --quiet

  echo "    Backend deployed:"
  gcloud run services describe "$GCP_SERVICE_NAME" \
    --region "$GCP_REGION" --project "$GCP_PROJECT_ID" \
    --format 'value(status.url)'

  # ── 8. Frontend (Firebase) ────────────────────────────────────────────────────
  if [[ -f "$SCRIPT_DIR/.firebaserc" ]]; then
    _deploy_frontend
  else
    echo ""
    echo "    Firebase Hosting is not yet initialized. To set it up:"
    echo "      1. firebase login"
    echo "      2. firebase init hosting"
    echo "         - Use existing project: $GCP_PROJECT_ID"
    echo "         - Public directory: frontend/dist"
    echo "         - Configure as SPA: yes"
    echo "         - Overwrite index.html: no"
    echo "      3. ./dev.sh gcp-deploy   (to push the first frontend build)"
  fi

  # ── 9. GitHub Actions service account ────────────────────────────────────────
  echo ""
  echo "==> Creating GitHub Actions service account..."
  gcloud iam service-accounts create github-actions \
    --display-name="GitHub Actions CI/CD" \
    --project "$GCP_PROJECT_ID" 2>/dev/null \
    && echo "    Created $GHA_SA." || echo "    Already exists — skipping."

  for role in roles/run.admin roles/artifactregistry.writer; do
    gcloud projects add-iam-policy-binding "$GCP_PROJECT_ID" \
      --member="serviceAccount:$GHA_SA" \
      --role="$role" --quiet
  done
  # Allow CI to deploy as the Cloud Run service account
  gcloud iam service-accounts add-iam-policy-binding "$CLOUD_RUN_SA" \
    --member="serviceAccount:$GHA_SA" \
    --role="roles/iam.serviceAccountUser" \
    --project "$GCP_PROJECT_ID" --quiet

  # ── 10. Firebase deploy service account ──────────────────────────────────────
  echo "==> Creating Firebase deploy service account..."
  gcloud iam service-accounts create firebase-deploy \
    --display-name="Firebase Hosting Deploy" \
    --project "$GCP_PROJECT_ID" 2>/dev/null \
    && echo "    Created $FIREBASE_SA." || echo "    Already exists — skipping."

  gcloud projects add-iam-policy-binding "$GCP_PROJECT_ID" \
    --member="serviceAccount:$FIREBASE_SA" \
    --role="roles/firebasehosting.admin" --quiet
  gcloud projects add-iam-policy-binding "$GCP_PROJECT_ID" \
    --member="serviceAccount:$FIREBASE_SA" \
    --role="roles/serviceusage.serviceUsageConsumer" --quiet

  # ── 11. Generate and save service account keys ───────────────────────────────
  echo "==> Generating service account keys..."
  rm -f "$SCRIPT_DIR/.github-actions-sa-key.json" "$SCRIPT_DIR/.firebase-sa-key.json"
  gcloud iam service-accounts keys create "$SCRIPT_DIR/.github-actions-sa-key.json" \
    --iam-account "$GHA_SA" --project "$GCP_PROJECT_ID"
  gcloud iam service-accounts keys create "$SCRIPT_DIR/.firebase-sa-key.json" \
    --iam-account "$FIREBASE_SA" --project "$GCP_PROJECT_ID"

  # ── Summary ───────────────────────────────────────────────────────────────────
  echo ""
  echo "======================================================================"
  echo "GCP setup complete!"
  echo "======================================================================"
  echo ""
  echo "Add the following to your GitHub repository:"
  echo "  Settings > Secrets and variables > Actions"
  echo ""
  echo "  SECRETS (sensitive — use 'New repository secret'):"
  echo "    GCP_SA_KEY              $(cat "$SCRIPT_DIR/.github-actions-sa-key.json" | tr -d '\n' | head -c 60)..."
  echo "    FIREBASE_SERVICE_ACCOUNT $(cat "$SCRIPT_DIR/.firebase-sa-key.json" | tr -d '\n' | head -c 60)..."
  echo "    GEMINI_API_KEY          $GEMINI_API_KEY"
  echo ""
  echo "  VARIABLES (non-sensitive — use 'New repository variable'):"
  echo "    GCP_PROJECT_ID          $GCP_PROJECT_ID"
  echo ""
  echo "  Key files are saved (gitignored):"
  echo "    .github-actions-sa-key.json"
  echo "    .firebase-sa-key.json"
  echo ""
  echo "  Once secrets are set, push to main to trigger automatic deployment."
}

gcp_deploy() {
  _gcp_prereqs
  _load_gcp_config

  local TAG
  TAG="$(git -C "$SCRIPT_DIR" rev-parse --short HEAD 2>/dev/null || echo 'manual')"
  local IMAGE="$GCP_REGION-docker.pkg.dev/$GCP_PROJECT_ID/$GCP_AR_REPO/backend"

  _build_and_push "$TAG" latest

  echo "==> Deploying backend to Cloud Run..."
  gcloud run deploy "$GCP_SERVICE_NAME" \
    --image "$IMAGE:$TAG" \
    --region "$GCP_REGION" \
    --platform managed \
    --project "$GCP_PROJECT_ID" \
    --quiet

  _deploy_frontend

  echo ""
  echo "==> Deployment complete!"
  gcloud run services describe "$GCP_SERVICE_NAME" \
    --region "$GCP_REGION" --project "$GCP_PROJECT_ID" \
    --format 'value(status.url)'
}

gcp_update_env() {
  _check_gcp_auth
  _load_gcp_config
  echo "==> Updating Cloud Run environment variables..."
  gcloud run services update "$GCP_SERVICE_NAME" \
    --region "$GCP_REGION" \
    --project "$GCP_PROJECT_ID" \
    --update-env-vars "\
LLM_API_KEY=$GEMINI_API_KEY,\
ALLOWED_ORIGIN=$ALLOWED_ORIGIN" \
    --quiet
  echo "    Done."
}

# ── Dispatch ──────────────────────────────────────────────────────────────────

case "${1:-}" in
  setup)          setup ;;
  start)          start ;;
  stop)           stop ;;
  restart)        stop; sleep 1; start ;;
  logs)           logs ;;
  test)           test ;;
  benchmark)      benchmark "$@" ;;
  gcp-setup)      gcp_setup ;;
  gcp-deploy)     gcp_deploy ;;
  gcp-update-env) gcp_update_env ;;
  *)
    echo "Usage: ./dev.sh <command>"
    echo ""
    echo "Local development:"
    echo "  setup      Install backend and frontend dependencies (run once)"
    echo "  start      Start backend and frontend in the background"
    echo "  stop       Stop both processes"
    echo "  restart    Stop then start"
    echo "  logs       Tail backend.log and frontend.log"
    echo "  test       Run all backend tests (calendar unit tests + AI parse integration tests)"
    echo "  benchmark  Run tests across all models and write benchmark_report.md"
    echo ""
    echo "GCP deployment:"
    echo "  gcp-setup       One-time GCP infrastructure setup + initial deploy"
    echo "  gcp-deploy      Build, push, and deploy backend + frontend manually"
    echo "  gcp-update-env  Push updated env vars from .gcp-config to Cloud Run"
    exit 1
    ;;
esac
