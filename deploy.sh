#!/usr/bin/env bash
# Deploy the SEO Workbook MCP server and agent service to Cloud Run.
#
# Prerequisites:
#   gcloud auth login
#   Run `./deploy.sh setup` once per project before the first deploy.
#   PROJECT, REGION set below (or override via env)
#
# Usage:
#   ./deploy.sh          # deploy both services
#   ./deploy.sh mcp      # deploy only mcp-server
#   ./deploy.sh agent    # deploy only agent-service
#   ./deploy.sh setup    # one-time project setup
set -euo pipefail

PROJECT="${GCP_PROJECT:-your-project-id}"
REGION="${GCP_REGION:-us-central1}"
AR_REPO="${AR_REPO:-seo-workbook}"
REPORTS_BUCKET="${GCS_REPORT_BUCKET:-${PROJECT}-seo-workbook-reports}"
AGENT_MODEL="${AGENT_MODEL:-gemini-2.5-flash}"

MCP_IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${AR_REPO}/seo-workbook-mcp-server"
AGENT_IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${AR_REPO}/seo-workbook-agent-service"

TARGET="${1:-both}"

# Both services run as the project's default compute service account —
# simplest IAM story for now (matches the precedent this was adapted from).
# That identity needs Vertex AI access (for the agent's Gemini calls),
# Cloud Run Invoker on mcp-server (so the agent's ID-token-authenticated MCP
# calls are accepted), Secret Manager access (for RUN_API_KEY and
# MONGO_URI), object admin on the reports bucket, and self-signBlob
# (serviceAccountTokenCreator) permission for generating signed report
# URLs — the last two used by mcp-server's render_session_report AND
# agent-service's /reports/{token} redirect, since both share this same
# identity, setup_project()'s grants cover both already.
_project_number() {
  gcloud projects describe "${PROJECT}" --format="value(projectNumber)"
}

_default_compute_sa() {
  echo "$(_project_number)-compute@developer.gserviceaccount.com"
}

# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------
deploy_mcp() {
  echo "==> Building mcp-server image..."
  gcloud builds submit . \
    --config=mcp-servers/seo-workbook-mcp/cloudbuild.yaml \
    --substitutions="_IMAGE=${MCP_IMAGE}" \
    --project "${PROJECT}"

  # finalize_session always persists to MongoDB (it's the system of record,
  # not an optional export) — MONGO_URI is the full Atlas connection string
  # (mongodb+srv://user:password@host/?...) with the real password already
  # substituted; unlike RUN_API_KEY this can't be auto-generated. Stored in
  # Secret Manager, never passed as a plain env var or logged.
  #
  # MONGO_URI is only required in *this* shell the first time, or whenever
  # you're rotating the password — if the secret already exists and you
  # didn't set MONGO_URI, this reuses the existing version rather than
  # forcing you to have the password in this environment on every deploy.
  # To create/update it out-of-band instead (recommended, keeps the
  # password out of this shell entirely):
  #   echo -n '<connection-string>' | gcloud secrets create seo-workbook-mongo-uri \
  #     --project "${PROJECT}" --data-file=- --replication-policy=automatic
  if [ -n "${MONGO_URI:-}" ]; then
    if gcloud secrets describe seo-workbook-mongo-uri --project "${PROJECT}" &>/dev/null; then
      echo -n "${MONGO_URI}" | gcloud secrets versions add seo-workbook-mongo-uri --project "${PROJECT}" --data-file=-
    else
      echo -n "${MONGO_URI}" | gcloud secrets create seo-workbook-mongo-uri --project "${PROJECT}" --data-file=- --replication-policy=automatic
    fi
  elif ! gcloud secrets describe seo-workbook-mongo-uri --project "${PROJECT}" &>/dev/null; then
    echo "ERROR: seo-workbook-mongo-uri secret doesn't exist yet and MONGO_URI isn't set."
    echo "Either export MONGO_URI and re-run, or create the secret directly:"
    echo "  echo -n '<connection-string>' | gcloud secrets create seo-workbook-mongo-uri \\"
    echo "    --project ${PROJECT} --data-file=- --replication-policy=automatic"
    exit 1
  else
    echo "==> MONGO_URI not set — reusing existing seo-workbook-mongo-uri secret."
  fi

  # render_session_report returns a short https://<agent-url>/reports/{token}
  # link rather than a raw signed GCS URL (see reports_router.py) — needs
  # agent-service's own stable URL, which only exists once agent-service has
  # been deployed at least once. Empty on a brand-new project's first `mcp`
  # deploy; re-run './deploy.sh mcp' after the first 'agent' deploy to pick
  # it up, same caveat as CHAT_AUDIENCE below.
  EXISTING_AGENT_URL=$(gcloud run services describe seo-workbook-agent-service \
    --region "${REGION}" --project "${PROJECT}" \
    --format "value(status.url)" 2>/dev/null || true)

  echo "==> Deploying mcp-server to Cloud Run..."
  # --session-affinity: our in-memory SessionStore (in-progress PlanSessions,
  # see session_store.py) lives in the handling instance's memory, not
  # anything shared across instances — affinity keeps one conversation
  # pinned to one instance for its lifetime, same reasoning as the
  # vds-mcp-server precedent this is adapted from.
  #
  # NOTE: MongoDB Atlas's default Network Access list will reject Cloud
  # Run's connections unless the project's outbound IPs are allowed — either
  # allow 0.0.0.0/0 in Atlas (quick, less secure) or route egress through a
  # Serverless VPC Connector + Cloud NAT with a static IP and allowlist that
  # instead. Not set up by this script.
  gcloud run deploy seo-workbook-mcp-server \
    --image "${MCP_IMAGE}" \
    --region "${REGION}" \
    --project "${PROJECT}" \
    --no-allow-unauthenticated \
    --session-affinity \
    --set-env-vars "SEO_WORKBOOK_GCP_PROJECT_ID=${PROJECT},SEO_WORKBOOK_GCP_REGION=${REGION},SEO_WORKBOOK_REPORTS_BUCKET=${REPORTS_BUCKET},SEO_WORKBOOK_MONGO_DATABASE=${MONGO_DATABASE:-seo_workbook},SEO_WORKBOOK_MONGO_COLLECTION=${MONGO_COLLECTION:-plan_sessions},SEO_WORKBOOK_AGENT_PUBLIC_URL=${EXISTING_AGENT_URL}" \
    --set-secrets "SEO_WORKBOOK_MONGO_URI=seo-workbook-mongo-uri:latest" \
    --memory 512Mi \
    --timeout 300

  MCP_URL=$(gcloud run services describe seo-workbook-mcp-server \
    --region "${REGION}" --project "${PROJECT}" \
    --format "value(status.url)")
  echo "==> MCP server: ${MCP_URL}"

  echo "==> Granting the (shared default compute) service account permission to invoke mcp-server..."
  gcloud run services add-iam-policy-binding seo-workbook-mcp-server \
    --region "${REGION}" \
    --project "${PROJECT}" \
    --member="serviceAccount:$(_default_compute_sa)" \
    --role="roles/run.invoker" \
    --quiet

  if [ -z "${EXISTING_AGENT_URL}" ]; then
    echo "==> agent-service doesn't exist yet, so SEO_WORKBOOK_AGENT_PUBLIC_URL was empty this run."
    echo "    Deploy agent-service, then re-run './deploy.sh mcp' once more to set it."
  fi
}

# ---------------------------------------------------------------------------
# Agent / chat service
# ---------------------------------------------------------------------------
deploy_agent() {
  MCP_URL="${MCP_URL:-$(gcloud run services describe seo-workbook-mcp-server \
    --region "${REGION}" --project "${PROJECT}" \
    --format "value(status.url)")}"

  echo "==> Building agent-service image..."
  gcloud builds submit . \
    --config=agents/seo-workbook-agent/cloudbuild.yaml \
    --substitutions="_IMAGE=${AGENT_IMAGE}" \
    --project "${PROJECT}"

  # /run has no other auth (must stay reachable without Cloud Run IAM, same
  # service as /chat). Generate a key if the caller didn't already set one,
  # and store it in Secret Manager rather than passing it as a plain env
  # var (visible to anyone with Viewer on the service otherwise).
  if [ -z "${RUN_API_KEY:-}" ]; then
    RUN_API_KEY=$(openssl rand -hex 32)
    echo "==> Generated RUN_API_KEY (save this — required as the X-Api-Key header on /run):"
    echo "    ${RUN_API_KEY}"
  fi
  if gcloud secrets describe seo-workbook-run-api-key --project "${PROJECT}" &>/dev/null; then
    echo -n "${RUN_API_KEY}" | gcloud secrets versions add seo-workbook-run-api-key --project "${PROJECT}" --data-file=-
  else
    echo -n "${RUN_API_KEY}" | gcloud secrets create seo-workbook-run-api-key --project "${PROJECT}" --data-file=- --replication-policy=automatic
  fi

  # Google Chat signs requests with an ID token whose audience is the HTTP
  # endpoint URL itself (confirmed from a real token's `aud` claim — NOT the
  # numeric project number some docs suggest as the default) — verified in
  # chat_auth.py before doing any work. The service's URL is stable across
  # redeploys, so fetch it if the service already exists; on a brand-new
  # deploy this stays empty until the first deploy completes (chat
  # verification is simply unconfigured — the router 503s — until then).
  EXISTING_AGENT_URL=$(gcloud run services describe seo-workbook-agent-service \
    --region "${REGION}" --project "${PROJECT}" \
    --format "value(status.url)" 2>/dev/null || true)
  CHAT_AUDIENCE="${EXISTING_AGENT_URL:+${EXISTING_AGENT_URL}/chat}"

  echo "==> Deploying agent-service to Cloud Run..."
  # GOOGLE_GENAI_USE_VERTEXAI / GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION
  # are read directly by ADK/google-genai to route Gemini calls through
  # Vertex AI + ADC. --allow-unauthenticated is required: Google Chat can't
  # attach a Cloud Run identity token, so /chat enforces its own OIDC check
  # in-app instead (see chat_auth.py) — /run does the same with the
  # RUN_API_KEY secret above.
  # --no-cpu-throttling: /chat acks immediately and keeps working in a
  # FastAPI BackgroundTask afterward (see chat_router.py) — Cloud Run's
  # default CPU throttling freezes CPU once it considers the request
  # "done" (i.e. right after the response is sent), which would stall
  # that background work. This keeps CPU allocated until it actually
  # finishes and posts the real reply back to Chat.
  # SEO_WORKBOOK_REPORTS_BUCKET / MONGO_DATABASE / MONGO_URI: the
  # /reports/{token} redirect route (reports_router.py) resolves a
  # short share-token back to a signed GCS URL, so it needs the same
  # bucket + Mongo database mcp-server writes tokens into. Both services
  # run as the same default compute SA (see comment atop this file), so no
  # extra IAM grants are needed beyond what setup_project() already did for
  # mcp-server — reused here as-is. seo-workbook-mongo-uri must already
  # exist (created by a prior './deploy.sh mcp' run).
  gcloud run deploy seo-workbook-agent-service \
    --image "${AGENT_IMAGE}" \
    --region "${REGION}" \
    --project "${PROJECT}" \
    --allow-unauthenticated \
    --no-cpu-throttling \
    --set-env-vars "SEO_WORKBOOK_ENVIRONMENT=production,SEO_WORKBOOK_MCP_SERVER_URL=${MCP_URL}/mcp,SEO_WORKBOOK_GCP_PROJECT_ID=${PROJECT},SEO_WORKBOOK_GCP_REGION=${REGION},SEO_WORKBOOK_AGENT_MODEL=${AGENT_MODEL},GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=${PROJECT},GOOGLE_CLOUD_LOCATION=${REGION},SEO_WORKBOOK_CHAT_AUDIENCE=${CHAT_AUDIENCE},SEO_WORKBOOK_REPORTS_BUCKET=${REPORTS_BUCKET},SEO_WORKBOOK_MONGO_DATABASE=${MONGO_DATABASE:-seo_workbook}" \
    --set-secrets "SEO_WORKBOOK_RUN_API_KEY=seo-workbook-run-api-key:latest,SEO_WORKBOOK_MONGO_URI=seo-workbook-mongo-uri:latest" \
    --memory 1Gi \
    --timeout 300 \
    --concurrency 10

  AGENT_URL=$(gcloud run services describe seo-workbook-agent-service \
    --region "${REGION}" --project "${PROJECT}" \
    --format "value(status.url)")
  echo "==> Agent service: ${AGENT_URL}"
  echo ""
  echo "==> Google Chat webhook URL: ${AGENT_URL}/chat"
  echo "    Register this URL in Google Cloud Console → Google Chat API → Configuration"
  echo ""
  if [ -z "${EXISTING_AGENT_URL}" ]; then
    echo "==> This was a first-time deploy, so CHAT_AUDIENCE was empty this run."
    echo "    Re-run './deploy.sh agent' once more to set it now that the URL is stable."
  fi
}

# ---------------------------------------------------------------------------
# One-time setup (run manually before first deploy)
# ---------------------------------------------------------------------------
setup_project() {
  echo "==> Enabling required APIs..."
  gcloud services enable \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    aiplatform.googleapis.com \
    artifactregistry.googleapis.com \
    secretmanager.googleapis.com \
    storage.googleapis.com \
    sheets.googleapis.com \
    drive.googleapis.com \
    --project "${PROJECT}"

  echo "==> Creating Artifact Registry repo: ${AR_REPO}"
  gcloud artifacts repositories create "${AR_REPO}" \
    --repository-format=docker \
    --location="${REGION}" \
    --project "${PROJECT}" \
    || echo "Repo already exists, skipping."

  local sa
  sa=$(_default_compute_sa)

  echo "==> Granting Vertex AI access to the default compute service account (${sa})..."
  gcloud projects add-iam-policy-binding "${PROJECT}" \
    --member="serviceAccount:${sa}" \
    --role="roles/aiplatform.user" \
    --condition=None \
    --quiet

  echo "==> Granting ${sa} access to read Secret Manager secrets..."
  gcloud projects add-iam-policy-binding "${PROJECT}" \
    --member="serviceAccount:${sa}" \
    --role="roles/secretmanager.secretAccessor" \
    --condition=None \
    --quiet

  echo "==> Granting ${sa} permission to sign its own tokens (needed for render_session_report's signed URLs)..."
  gcloud iam service-accounts add-iam-policy-binding "${sa}" \
    --project "${PROJECT}" \
    --member="serviceAccount:${sa}" \
    --role="roles/iam.serviceAccountTokenCreator" \
    --quiet

  echo "==> Creating reports bucket: ${REPORTS_BUCKET}"
  gcloud storage buckets create "gs://${REPORTS_BUCKET}" \
    --project "${PROJECT}" \
    --location "${REGION}" \
    --uniform-bucket-level-access \
    || echo "Bucket already exists, skipping."
  # Reports are NOT public — render_session_report returns a 7-day signed
  # URL per report instead of relying on a bucket-wide public ACL.

  echo "==> Granting ${sa} object admin on the reports bucket..."
  gcloud storage buckets add-iam-policy-binding "gs://${REPORTS_BUCKET}" \
    --member="serviceAccount:${sa}" \
    --role="roles/storage.objectAdmin" \
    --quiet
}

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
case "${TARGET}" in
  mcp)   deploy_mcp ;;
  agent) deploy_agent ;;
  setup) setup_project ;;
  both)  deploy_mcp && deploy_agent ;;
  *)     echo "Usage: $0 [mcp|agent|setup|both]"; exit 1 ;;
esac
