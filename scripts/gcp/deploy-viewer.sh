#!/usr/bin/env bash
# Build and deploy the read-only wiki viewer to Cloud Run, then enable IAP
# and grant access to the configured Workspace domain. Safe to re-run.
#
# Assumes bootstrap.sh has already run (APIs enabled, bucket exists) and that
# the OAuth consent screen is configured for the project.
#
# Usage:
#   bash scripts/gcp/deploy-viewer.sh
#   GCP_PROJECT=foo GCP_IAP_DOMAIN=example.com bash scripts/gcp/deploy-viewer.sh

set -euo pipefail

PROJECT="${GCP_PROJECT:-voice-eval-stack-im}"
REGION="${GCP_REGION:-asia-south1}"
SERVICE="${GCP_SERVICE:-email-kb-viewer}"
DOMAIN="${GCP_IAP_DOMAIN:-indiamart.com}"

echo "==> Deploying ${SERVICE} with IAP enabled to ${REGION} in ${PROJECT}..."
gcloud run deploy "${SERVICE}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --source=. \
  --no-allow-unauthenticated \
  --iap \
  --memory=256Mi \
  --cpu=1 \
  --max-instances=5 \
  --min-instances=0 \
  --port=8080

echo "==> Granting domain:${DOMAIN} the iap.httpsResourceAccessor role..."
gcloud iap web add-iam-policy-binding \
  --project="${PROJECT}" \
  --resource-type=cloud-run \
  --service="${SERVICE}" \
  --region="${REGION}" \
  --member="domain:${DOMAIN}" \
  --role='roles/iap.httpsResourceAccessor'

URL="$(gcloud run services describe "${SERVICE}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --format='value(status.url)')"

echo "==> Deploy complete: ${URL}"
echo "    Smoke test:"
echo "      curl -sI '${URL}'   # expect 302 to accounts.google.com"
echo "      open '${URL}'       # sign in with an @${DOMAIN} account"
