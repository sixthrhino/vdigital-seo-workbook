#!/usr/bin/env bash
# Deploy seo-assistant (the orchestrator) to Cloud Run.
#
# This is the only agent that needs its own GCP project going forward — it's
# the one registering the Google Chat app now; seo-workbook-agent and
# seo-testing-agent keep running in their existing projects purely as MCP
# clients embedded in this process (their own standalone Cloud Run
# deployments can stay up too, e.g. for direct testing, or be retired later
# — this script doesn't touch them).
#
# Cross-project note: this service calls seo-workbook-mcp (vdigital-services-seo)
# and seo-testing-mcp (vdigital-500922) directly — setup_project() below only
# handles this project's own grants; the two `add-iam-policy-binding` calls
# for the *other* projects' MCP servers need running once, by someone with
# access to those projects (see the printed commands at the end of setup).
#
# Report links: unchanged and untouched by this script — render_session_report
# still builds links against seo-workbook-mcp's existing SEO_WORKBOOK_AGENT_PUBLIC_URL
# (the original seo-workbook-agent's URL), so that service needs to keep
# running for existing/new report links to resolve. Pointing reports at the
# orchestrator instead is a separate, later change (would need updating that
# setting on seo-workbook-mcp, plus Mongo/bucket config and a cross-project
# GCS grant here).
#
# Prerequisites:
#   gcloud auth login
#   Run `./deploy-seo-assistant.sh setup` once per project before the first deploy.
#
# Usage:
#   ./deploy-seo-assistant.sh          # deploy
#   ./deploy-seo-assistant.sh setup    # one-time project setup
set -euo pipefail

PROJECT="${GCP_PROJECT:-your-project-id}"
REGION="${GCP_REGION:-us-central1}"
AR_REPO="${AR_REPO:-seo-assistant}"
AGENT_MODEL="${AGENT_MODEL:-gemini-2.5-flash}"

# The two existing MCP servers this orchestrator calls — override if their
# projects/regions/service names ever change.
WORKBOOK_MCP_PROJECT="${WORKBOOK_MCP_PROJECT:-vdigital-services-seo}"
WORKBOOK_MCP_SERVICE="${WORKBOOK_MCP_SERVICE:-seo-workbook-mcp-server}"
TESTING_MCP_PROJECT="${TESTING_MCP_PROJECT:-vdigital-500922}"
TESTING_MCP_SERVICE="${TESTING_MCP_SERVICE:-seo-testing-mcp}"

IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${AR_REPO}/seo-assistant"

TARGET="${1:-deploy}"

_project_number() {
  gcloud projects describe "${PROJECT}" --format="value(projectNumber)"
}

_default_compute_sa() {
  echo "$(_project_number)-compute@developer.gserviceaccount.com"
}

_mcp_url() {
  # $1=project $2=service
  gcloud run services describe "$2" --project "$1" --region "${REGION}" --format="value(status.url)"
}

deploy() {
  echo "==> Building seo-assistant image..."
  gcloud builds submit . \
    --config=agents/seo-assistant/cloudbuild.yaml \
    --substitutions="_IMAGE=${IMAGE}" \
    --project "${PROJECT}"

  WORKBOOK_MCP_URL=$(_mcp_url "${WORKBOOK_MCP_PROJECT}" "${WORKBOOK_MCP_SERVICE}")
  TESTING_MCP_URL=$(_mcp_url "${TESTING_MCP_PROJECT}" "${TESTING_MCP_SERVICE}")

  # /run has no other auth (it must stay reachable without Cloud Run IAM,
  # same service as /chat). Generate a key if the caller didn't already set
  # one, and store it in Secret Manager rather than passing it as a plain
  # env var.
  if [ -z "${RUN_API_KEY:-}" ]; then
    RUN_API_KEY=$(openssl rand -hex 32)
    echo "==> Generated RUN_API_KEY (save this — required as the X-Api-Key header on /run):"
    echo "    ${RUN_API_KEY}"
  fi
  if gcloud secrets describe seo-assistant-run-api-key --project "${PROJECT}" &>/dev/null; then
    echo -n "${RUN_API_KEY}" | gcloud secrets versions add seo-assistant-run-api-key --project "${PROJECT}" --data-file=-
  else
    echo -n "${RUN_API_KEY}" | gcloud secrets create seo-assistant-run-api-key --project "${PROJECT}" --data-file=- --replication-policy=automatic
  fi

  # Same bootstrap-order caveat as the other two deploy scripts: empty on a
  # brand-new deploy until the URL is stable, then re-run once more.
  EXISTING_URL=$(gcloud run services describe seo-assistant \
    --region "${REGION}" --project "${PROJECT}" \
    --format "value(status.url)" 2>/dev/null || true)
  CHAT_AUDIENCE="${EXISTING_URL:+${EXISTING_URL}/chat}"

  echo "==> Deploying seo-assistant to Cloud Run..."
  # SEO_WORKBOOK_* vars configure the reused seo-workbook-agent HTTP/chat
  # layer (create_app/AgentCore/http_router/chat_router — see main.py) that
  # this whole service runs on top of. MCP_SERVER_URL / AGENT_MODEL /
  # ENVIRONMENT (bare, no prefix) configure seo-testing-agent's sub-agent —
  # ENVIRONMENT=production is required for it to attach an ID token when
  # calling seo-testing-mcp (see seo_testing_agent/agent.py's
  # _mcp_auth_headers). --no-cpu-throttling for the same reason as the
  # other two agent deploys: /chat acks immediately and keeps working in a
  # background task afterward.
  gcloud run deploy seo-assistant \
    --image "${IMAGE}" \
    --region "${REGION}" \
    --project "${PROJECT}" \
    --allow-unauthenticated \
    --no-cpu-throttling \
    --set-env-vars "SEO_WORKBOOK_ENVIRONMENT=production,SEO_WORKBOOK_MCP_SERVER_URL=${WORKBOOK_MCP_URL}/mcp,SEO_WORKBOOK_AGENT_MODEL=${AGENT_MODEL},SEO_WORKBOOK_CHAT_AUDIENCE=${CHAT_AUDIENCE},MCP_SERVER_URL=${TESTING_MCP_URL},AGENT_MODEL=${AGENT_MODEL},ENVIRONMENT=production,GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=${PROJECT},GOOGLE_CLOUD_LOCATION=${REGION}" \
    --set-secrets "SEO_WORKBOOK_RUN_API_KEY=seo-assistant-run-api-key:latest" \
    --memory 1Gi \
    --timeout 300 \
    --concurrency 10

  AGENT_URL=$(gcloud run services describe seo-assistant \
    --region "${REGION}" --project "${PROJECT}" \
    --format "value(status.url)")
  echo "==> seo-assistant: ${AGENT_URL}"
  echo ""
  echo "==> Google Chat webhook URL: ${AGENT_URL}/chat"
  echo "    Register this URL in Google Cloud Console → Google Chat API → Configuration"
  echo ""
  if [ -z "${EXISTING_URL}" ]; then
    echo "==> This was a first-time deploy, so SEO_WORKBOOK_CHAT_AUDIENCE was empty this run."
    echo "    Re-run './deploy-seo-assistant.sh' once more to set it now that the URL is stable."
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

  echo "==> Granting ${sa} access to read Secret Manager secrets (RUN_API_KEY)..."
  gcloud projects add-iam-policy-binding "${PROJECT}" \
    --member="serviceAccount:${sa}" \
    --role="roles/secretmanager.secretAccessor" \
    --condition=None \
    --quiet

  echo "==> Granting ${sa} permission to sign its own tokens (only needed if reports are ever served through this service instead of seo-workbook-agent)..."
  gcloud iam service-accounts add-iam-policy-binding "${sa}" \
    --project "${PROJECT}" \
    --member="serviceAccount:${sa}" \
    --role="roles/iam.serviceAccountTokenCreator" \
    --quiet

  echo ""
  echo "==> This project's setup is done. Two CROSS-PROJECT grants still need"
  echo "    running once by someone with access to the *other* two projects"
  echo "    (this project's gcloud identity likely can't do this itself):"
  echo ""
  echo "    gcloud run services add-iam-policy-binding ${WORKBOOK_MCP_SERVICE} \\"
  echo "      --project ${WORKBOOK_MCP_PROJECT} --region ${REGION} \\"
  echo "      --member=\"serviceAccount:${sa}\" --role=\"roles/run.invoker\" --quiet"
  echo ""
  echo "    gcloud run services add-iam-policy-binding ${TESTING_MCP_SERVICE} \\"
  echo "      --project ${TESTING_MCP_PROJECT} --region ${REGION} \\"
  echo "      --member=\"serviceAccount:${sa}\" --role=\"roles/run.invoker\" --quiet"
}

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
case "${TARGET}" in
  deploy) deploy ;;
  setup)  setup_project ;;
  *)      echo "Usage: $0 [deploy|setup]"; exit 1 ;;
esac
