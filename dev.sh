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
  echo "==> Playwright (visual regression tests)"
  cd "$SCRIPT_DIR/frontend"
  npx playwright install --with-deps chromium

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
  # Calendar and briefing tests are pure unit tests (no Ollama required).
  # Parse tests call the live Ollama model and are skipped automatically if it is not running.
  echo ""
  echo "==> Backend unit tests"
  "$SCRIPT_DIR/backend/venv/bin/pytest" tests/test_calendar.py tests/test_briefing.py tests/test_plugins.py tests/test_localtime.py -v
  echo ""
  echo "==> Quick Add parse integration tests (requires Ollama)"
  "$SCRIPT_DIR/backend/venv/bin/pytest" tests/test_parse.py -v
  echo ""
  echo "==> Frontend tests"
  cd "$SCRIPT_DIR/frontend"
  npx playwright test
}

test_frontend() {
  if [[ ! -d "$SCRIPT_DIR/frontend/node_modules" ]]; then
    echo "Dependencies not installed. Run './dev.sh setup' first."
    exit 1
  fi
  cd "$SCRIPT_DIR/frontend"
  npx playwright test
}

benchmark() {
  if [[ ! -d "$SCRIPT_DIR/backend/venv" ]]; then
    echo "Dependencies not installed. Run './dev.sh setup' first."
    exit 1
  fi
  echo "Installing test dependencies..."
  "$SCRIPT_DIR/backend/venv/bin/pip" install -r "$SCRIPT_DIR/backend/requirements-dev.txt" -q
  cd "$SCRIPT_DIR/backend"
  "$SCRIPT_DIR/backend/venv/bin/python" tests/benchmark.py "$@"
}

# ── GCP shared helpers ────────────────────────────────────────────────────────

_check_gcp_auth() {
  if ! gcloud auth print-access-token --quiet &>/dev/null; then
    echo "ERROR: Not authenticated with gcloud."
    echo "  Run: gcloud auth login"
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
  for var in GCP_PROJECT_ID GCP_REGION GCP_SERVICE_NAME GCP_AR_REPO LLM_BASE_URL LLM_API_KEY LLM_MODEL AUTH_PASSWORD; do
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

_gcp_prereqs() {
  echo "==> Checking prerequisites..."
  if ! command -v gcloud &>/dev/null; then
    echo "  gcloud not found. Install: brew install google-cloud-sdk"
    exit 1
  fi
  _check_gcp_auth
}

# Build the combined frontend+backend image using Cloud Build (native linux/amd64)
# and push all given tags. Primary tag is built; extra tags are applied cheaply.
# Usage: _build_and_push PRIMARY_TAG [EXTRA_TAG ...]
# Requires $IMAGE and $GCP_PROJECT_ID to be set.
_build_and_push() {
  local primary="$1"

  echo "==> Building and pushing image via Cloud Build ($primary)..."
  gcloud builds submit "$SCRIPT_DIR" \
    --tag "$IMAGE:$primary" \
    --project "$GCP_PROJECT_ID" \
    --quiet

  for t in "${@:2}"; do
    gcloud artifacts docker tags add \
      "$IMAGE:$primary" "$IMAGE:$t" \
      --project "$GCP_PROJECT_ID" --quiet
  done
}

# ── GCP commands ──────────────────────────────────────────────────────────────

gcp_setup() {
  _gcp_prereqs
  _load_gcp_config

  local IMAGE="$GCP_REGION-docker.pkg.dev/$GCP_PROJECT_ID/$GCP_AR_REPO/backend"
  local GCS_BUCKET="${GCP_PROJECT_ID}-todo-db"
  local CLOUD_RUN_SA="cloud-run@${GCP_PROJECT_ID}.iam.gserviceaccount.com"
  local GHA_SA="github-actions@${GCP_PROJECT_ID}.iam.gserviceaccount.com"

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
    --project "$GCP_PROJECT_ID" --quiet

  # ── 2. Artifact Registry ─────────────────────────────────────────────────────
  echo "==> Creating Artifact Registry repository..."
  gcloud artifacts repositories create "$GCP_AR_REPO" \
    --repository-format=docker \
    --location="$GCP_REGION" \
    --project "$GCP_PROJECT_ID" 2>/dev/null \
    && echo "    Created." || echo "    Already exists — skipping."

  # ── 3. Cloud Storage bucket for SQLite ───────────────────────────────────────
  echo "==> Creating Cloud Storage bucket..."
  gcloud storage buckets create "gs://$GCS_BUCKET" \
    --location="$GCP_REGION" \
    --project "$GCP_PROJECT_ID" 2>/dev/null \
    && echo "    Created gs://$GCS_BUCKET." || echo "    Already exists — skipping."

  # ── 4. Cloud Run service account ─────────────────────────────────────────────
  echo "==> Creating Cloud Run service account..."
  gcloud iam service-accounts create cloud-run \
    --display-name="Cloud Run Backend" \
    --project "$GCP_PROJECT_ID" 2>/dev/null \
    && echo "    Created $CLOUD_RUN_SA." || echo "    Already exists — skipping."

  echo "==> Granting Cloud Run SA access to the database bucket..."
  gcloud storage buckets add-iam-policy-binding "gs://$GCS_BUCKET" \
    --member="serviceAccount:$CLOUD_RUN_SA" \
    --role="roles/storage.objectAdmin" --quiet

  # ── 5. Build and push image ───────────────────────────────────────────────────
  _build_and_push latest

  # ── 6. Deploy to Cloud Run ────────────────────────────────────────────────────
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
LLM_BASE_URL=$LLM_BASE_URL,\
LLM_API_KEY=$LLM_API_KEY,\
LLM_MODEL=$LLM_MODEL,\
AUTH_PASSWORD=$AUTH_PASSWORD" \
    --project "$GCP_PROJECT_ID" \
    --quiet

  echo ""
  echo "==> Deployed! Service URL:"
  gcloud run services describe "$GCP_SERVICE_NAME" \
    --region "$GCP_REGION" --project "$GCP_PROJECT_ID" \
    --format 'value(status.url)'

  # ── 7. GitHub Actions service account ────────────────────────────────────────
  echo ""
  echo "==> Creating GitHub Actions service account..."
  gcloud iam service-accounts create github-actions \
    --display-name="GitHub Actions CI/CD" \
    --project "$GCP_PROJECT_ID" 2>/dev/null \
    && echo "    Created $GHA_SA." || echo "    Already exists — skipping."

  for role in roles/run.admin roles/artifactregistry.writer roles/cloudbuild.builds.builder roles/iam.serviceAccountUser roles/logging.viewer; do
    gcloud projects add-iam-policy-binding "$GCP_PROJECT_ID" \
      --member="serviceAccount:$GHA_SA" \
      --role="$role" --quiet
  done

  # ── 8. Generate and save service account key ──────────────────────────────────
  echo "==> Generating GitHub Actions service account key..."
  rm -f "$SCRIPT_DIR/.github-actions-sa-key.json"
  gcloud iam service-accounts keys create "$SCRIPT_DIR/.github-actions-sa-key.json" \
    --iam-account "$GHA_SA" --project "$GCP_PROJECT_ID"

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
  echo "    AUTH_PASSWORD           $AUTH_PASSWORD"
  echo ""
  echo "  VARIABLES (non-sensitive — use 'New repository variable'):"
  echo "    GCP_PROJECT_ID          $GCP_PROJECT_ID"
  echo ""
  echo "  LLM settings ($LLM_MODEL) are baked into the Cloud Run service."
  echo "  To change providers later, edit .gcp-config and run: ./dev.sh gcp-update-env"
  echo ""
  echo "  Key file saved (gitignored): .github-actions-sa-key.json"
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

  echo "==> Deploying to Cloud Run..."
  gcloud run deploy "$GCP_SERVICE_NAME" \
    --image "$IMAGE:$TAG" \
    --region "$GCP_REGION" \
    --platform managed \
    --project "$GCP_PROJECT_ID" \
    --quiet

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
LLM_BASE_URL=$LLM_BASE_URL,\
LLM_API_KEY=$LLM_API_KEY,\
LLM_MODEL=$LLM_MODEL,\
AUTH_PASSWORD=$AUTH_PASSWORD" \
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
  test-frontend)  test_frontend ;;
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
    echo "  test           Run all tests (backend unit + AI parse integration + frontend)"
  echo "  test-frontend  Run only the frontend Playwright tests"
    echo "  benchmark  Run tests across all models and write benchmark_report.md"
    echo ""
    echo "GCP deployment:"
    echo "  gcp-setup       One-time GCP infrastructure setup + initial deploy"
    echo "  gcp-deploy      Build and deploy manually"
    echo "  gcp-update-env  Push updated env vars from .gcp-config to Cloud Run"
    exit 1
    ;;
esac
