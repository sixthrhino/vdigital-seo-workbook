#!/usr/bin/env bash
# Deploy seo-testing-mcp and seo-testing-agent to Cloud Run.
#
# Separate from deploy.sh (the seo-workbook pair) since each agent must live
# in its own GCP project (a Google Chat API constraint — see deploy.sh's own
# comment on this) — this pair already deploys to vdigital-500922, distinct
# from seo-workbook's vdigital-services-seo.
#
# Migrated from vdigital-testing-agent/deploy.sh, adjusted for this repo's
# paths/naming — deployment mechanism (gcloud CLI, GCR images, no Terraform/
# CI) and Cloud Run configuration are otherwise unchanged from that original.
#
# Prerequisites:
#   gcloud auth login && gcloud auth configure-docker
#
# Usage:
#   ./deploy-seo-testing.sh          # deploy both services
#   ./deploy-seo-testing.sh mcp      # deploy only seo-testing-mcp
#   ./deploy-seo-testing.sh agent    # deploy only seo-testing-agent
#   ./deploy-seo-testing.sh setup    # one-time project setup
set -euo pipefail

PROJECT="${GCP_PROJECT:-vdigital-500922}"
REGION="${GCP_REGION:-us-central1}"
REPORTS_BUCKET="${GCS_REPORT_BUCKET:-${PROJECT}-qa-reports}"
AGENT_MODEL="${AGENT_MODEL:-gemini-2.5-flash}"

MCP_IMAGE="gcr.io/${PROJECT}/seo-testing-mcp"
AGENT_IMAGE="gcr.io/${PROJECT}/seo-testing-agent"

# seo-workbook-mcp — Mode B's plan data source (see
# plan_session_source.py). Defaults to where the canonical, consolidated
# deployment lives today; override if seo-workbook-mcp ever moves.
WORKBOOK_MCP_PROJECT="${WORKBOOK_MCP_PROJECT:-vdigital-seo-assistant}"
WORKBOOK_MCP_SERVICE="${WORKBOOK_MCP_SERVICE:-seo-workbook-mcp-server}"

TARGET="${1:-both}"

# Neither service is given a dedicated --service-account below, so both run as
# the project's default compute service account. That one identity needs
# Vertex AI access (for the agent's Gemini calls), Cloud Run Invoker on
# seo-testing-mcp (so the agent's ID-token-authenticated MCP calls are
# accepted), and (self-granted) Service Account Token Creator so
# generate_report can sign report URLs without key material.
_project_number() {
  gcloud projects describe "${PROJECT}" --format="value(projectNumber)"
}

_default_compute_sa() {
  echo "$(_project_number)-compute@developer.gserviceaccount.com"
}

_workbook_mcp_url() {
  gcloud run services describe "${WORKBOOK_MCP_SERVICE}" \
    --project "${WORKBOOK_MCP_PROJECT}" --region "${REGION}" \
    --format="value(status.url)"
}

# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------
deploy_mcp() {
  echo "==> Building seo-testing-mcp image..."
  gcloud builds submit . \
    --config=mcp-servers/seo-testing-mcp/cloudbuild.yaml \
    --substitutions="_IMAGE=${MCP_IMAGE}" \
    --project "${PROJECT}"

  echo "==> Deploying seo-testing-mcp to Cloud Run..."
  # content_check_grammar calls Gemini directly (the only non-deterministic
  # check this service runs) — needs the same Vertex AI routing vars as
  # seo-testing-agent; GEMINI_MODEL mirrors AGENT_MODEL so both services stay
  # on the same model without a second config knob to keep in sync.
  #
  # CITIES_GCS_URI / SITE_DICTIONARIES_GCS_URI: same reports bucket, under a
  # qa-data/ prefix — makes geo_add_city / dictionary_add_term's writes take
  # effect without a redeploy (unlike the local-file fallback, which only
  # this one instance's disk would see, and which is lost on the next
  # deploy anyway). Run setup_project() before the first deploy that relies
  # on this — see its "Seeding qa-data/" step.
  # --session-affinity: seo-testing-mcp's SSE transport holds each session's
  # state in the handling instance's memory (session_id -> message queue),
  # not anything shared across instances. Without affinity, Cloud Run is
  # free to route a session's follow-up POST /messages/?session_id=... to a
  # *different* instance than the one holding its SSE GET connection, which
  # 404s. Affinity keeps a given client pinned to one instance for the life
  # of its session.
  gcloud run deploy seo-testing-mcp \
    --image "${MCP_IMAGE}" \
    --region "${REGION}" \
    --project "${PROJECT}" \
    --no-allow-unauthenticated \
    --session-affinity \
    --set-env-vars "GCS_REPORT_BUCKET=${REPORTS_BUCKET},GEMINI_MODEL=${AGENT_MODEL},GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=${PROJECT},GOOGLE_CLOUD_LOCATION=${REGION},CITIES_GCS_URI=gs://${REPORTS_BUCKET}/qa-data/uscities.json,SITE_DICTIONARIES_GCS_URI=gs://${REPORTS_BUCKET}/qa-data/site_dictionaries.json" \
    --memory 512Mi \
    --timeout 300

  MCP_URL=$(gcloud run services describe seo-testing-mcp \
    --region "${REGION}" --project "${PROJECT}" \
    --format "value(status.url)")
  echo "==> MCP server: ${MCP_URL}"

  echo "==> Granting the (shared default compute) service account permission to invoke seo-testing-mcp..."
  gcloud run services add-iam-policy-binding seo-testing-mcp \
    --region "${REGION}" \
    --project "${PROJECT}" \
    --member="serviceAccount:$(_default_compute_sa)" \
    --role="roles/run.invoker" \
    --quiet
}

# ---------------------------------------------------------------------------
# Agent / chat service
# ---------------------------------------------------------------------------
deploy_agent() {
  MCP_URL="${MCP_URL:-$(gcloud run services describe seo-testing-mcp \
    --region "${REGION}" --project "${PROJECT}" \
    --format "value(status.url)")}"
  WORKBOOK_MCP_URL="${WORKBOOK_MCP_URL:-$(_workbook_mcp_url)/mcp}"

  echo "==> Building seo-testing-agent image..."
  gcloud builds submit . \
    --config=agents/seo-testing-agent/cloudbuild.yaml \
    --substitutions="_IMAGE=${AGENT_IMAGE}" \
    --project "${PROJECT}"

  # /run has no other auth (it must stay reachable without Cloud Run IAM,
  # same service as /chat). Generate a key if the caller didn't already set
  # one, so a redeploy doesn't silently rotate/disable it.
  if [ -z "${RUN_API_KEY:-}" ]; then
    RUN_API_KEY=$(openssl rand -hex 32)
    echo "==> Generated RUN_API_KEY (save this — required as the X-Api-Key header on /run):"
    echo "    ${RUN_API_KEY}"
  fi

  # Google Chat signs requests with an ID token whose audience is the HTTP
  # endpoint URL itself (confirmed from a real token's `aud` claim) — used by
  # main.py to verify a request actually came from Chat before doing any
  # work. The service's URL is stable across redeploys, so fetch it if the
  # service already exists; on a brand-new deploy this stays empty until the
  # first deploy completes (chat verification is simply skipped until then).
  EXISTING_AGENT_URL=$(gcloud run services describe seo-testing-agent \
    --region "${REGION}" --project "${PROJECT}" \
    --format "value(status.url)" 2>/dev/null || true)
  CHAT_AUDIENCE="${EXISTING_AGENT_URL:+${EXISTING_AGENT_URL}/chat}"

  echo "==> Deploying seo-testing-agent to Cloud Run..."
  # GOOGLE_GENAI_USE_VERTEXAI / GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION are
  # read directly by the underlying google-genai client to route Gemini calls
  # through Vertex AI + ADC instead of the (unset in prod) Developer API key.
  # GCP_PROJECT_ID / GCP_LOCATION are this app's own settings (config.py),
  # used separately for VertexAiSessionService — both sets are needed.
  # --no-cpu-throttling: the /chat handler returns its ack immediately and
  # keeps working in a FastAPI BackgroundTask (_run_and_reply) afterward.
  # Cloud Run's default CPU throttling freezes CPU once it considers the
  # request "done" (i.e. right after the response is sent), which stalls
  # that background work — this flag keeps CPU allocated between requests.
  gcloud run deploy seo-testing-agent \
    --image "${AGENT_IMAGE}" \
    --region "${REGION}" \
    --project "${PROJECT}" \
    --allow-unauthenticated \
    --no-cpu-throttling \
    --set-env-vars "ENVIRONMENT=production,MCP_SERVER_URL=${MCP_URL},WORKBOOK_MCP_URL=${WORKBOOK_MCP_URL},GCP_PROJECT_ID=${PROJECT},GCP_LOCATION=${REGION},AGENT_MODEL=${AGENT_MODEL},GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=${PROJECT},GOOGLE_CLOUD_LOCATION=${REGION},RUN_API_KEY=${RUN_API_KEY},CHAT_AUDIENCE=${CHAT_AUDIENCE}" \
    --memory 1Gi \
    --timeout 3600 \
    --concurrency 10

  AGENT_URL=$(gcloud run services describe seo-testing-agent \
    --region "${REGION}" --project "${PROJECT}" \
    --format "value(status.url)")
  echo "==> Agent service: ${AGENT_URL}"
  echo ""
  echo "==> Google Chat webhook URL: ${AGENT_URL}/chat"
  echo "    Register this URL in Google Cloud Console → Google Chat API → Configuration"
  echo ""
  if [ -z "${EXISTING_AGENT_URL}" ]; then
    echo "==> This was a first-time deploy, so CHAT_AUDIENCE was empty this run."
    echo "    Re-run './deploy-seo-testing.sh agent' once more to set it now that the URL is stable."
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
    storage.googleapis.com \
    --project "${PROJECT}"

  local sa
  sa=$(_default_compute_sa)
  echo "==> Granting Vertex AI access to the default compute service account (${sa})..."
  gcloud projects add-iam-policy-binding "${PROJECT}" \
    --member="serviceAccount:${sa}" \
    --role="roles/aiplatform.user" \
    --condition=None \
    --quiet

  echo "==> Granting ${sa} permission to sign its own tokens (needed for generate_report's signed URLs)..."
  gcloud iam service-accounts add-iam-policy-binding "${sa}" \
    --project "${PROJECT}" \
    --member="serviceAccount:${sa}" \
    --role="roles/iam.serviceAccountTokenCreator" \
    --quiet

  echo "==> Creating GCS reports bucket: ${REPORTS_BUCKET}"
  gcloud storage buckets create "gs://${REPORTS_BUCKET}" \
    --project "${PROJECT}" \
    --location "${REGION}" \
    --uniform-bucket-level-access \
    || echo "Bucket already exists, skipping."
  # Reports are NOT public — generate_report returns a time-limited signed
  # URL per report instead of relying on a bucket-wide public ACL.

  echo "==> Seeding qa-data/ from local uscities.json / site_dictionaries.json (skipped if already present in GCS, so a re-run never clobbers edits made via geo_add_city/dictionary_add_term)..."
  for f in uscities.json site_dictionaries.json; do
    if gcloud storage ls "gs://${REPORTS_BUCKET}/qa-data/${f}" &>/dev/null; then
      echo "    gs://${REPORTS_BUCKET}/qa-data/${f} already exists, skipping."
    elif [ -f "mcp-servers/seo-testing-mcp/data/${f}" ]; then
      gcloud storage cp "mcp-servers/seo-testing-mcp/data/${f}" "gs://${REPORTS_BUCKET}/qa-data/${f}" --project "${PROJECT}"
    else
      echo "    mcp-servers/seo-testing-mcp/data/${f} not found locally — skipping seed (geo_add_city/dictionary_add_term will start from an empty file)."
    fi
  done

  echo ""
  echo "==> One cross-project grant still needs running once, by someone with"
  echo "    access to ${WORKBOOK_MCP_PROJECT} (this project's gcloud identity"
  echo "    likely can't do this itself) — review_plan_against_live_site calls"
  echo "    seo-workbook-mcp there to fetch a client's recorded plan:"
  echo ""
  echo "    gcloud run services add-iam-policy-binding ${WORKBOOK_MCP_SERVICE} \\"
  echo "      --project ${WORKBOOK_MCP_PROJECT} --region ${REGION} \\"
  echo "      --member=\"serviceAccount:$(_default_compute_sa)\" --role=\"roles/run.invoker\" --quiet"
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
