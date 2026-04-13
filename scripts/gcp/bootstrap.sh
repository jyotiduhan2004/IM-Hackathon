#!/usr/bin/env bash
# One-time GCP bootstrap for the email-knowledge-base viewer.
# Creates the GCS bucket (uniform access, public-access prevention, versioning)
# and enables the APIs needed for Cloud Run + IAP. Safe to re-run.
#
# Prereqs (outside this script):
#   - Owner/Editor on $GCP_PROJECT.
#   - OAuth consent screen configured (Internal) in the Cloud Console.
#
# Usage:
#   bash scripts/gcp/bootstrap.sh
#   GCP_PROJECT=foo GCP_REGION=us-central1 GCP_BUCKET=my-bucket bash scripts/gcp/bootstrap.sh

set -euo pipefail

PROJECT="${GCP_PROJECT:-voice-eval-stack-im}"
REGION="${GCP_REGION:-asia-south1}"
BUCKET="${GCP_BUCKET:-indiamart-email-kb}"

echo "==> project=${PROJECT} region=${REGION} bucket=${BUCKET}"

echo "==> Enabling required APIs..."
gcloud services enable \
  run.googleapis.com \
  iap.googleapis.com \
  iamcredentials.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  storage.googleapis.com \
  cloudresourcemanager.googleapis.com \
  --project="${PROJECT}"

echo "==> Ensuring bucket gs://${BUCKET} exists..."
if gcloud storage buckets describe "gs://${BUCKET}" --project="${PROJECT}" >/dev/null 2>&1; then
  echo "    bucket already exists — skipping create"
else
  gcloud storage buckets create "gs://${BUCKET}" \
    --project="${PROJECT}" \
    --location="${REGION}" \
    --uniform-bucket-level-access \
    --public-access-prevention
fi

echo "==> Enabling Object Versioning..."
gcloud storage buckets update "gs://${BUCKET}" \
  --versioning \
  --project="${PROJECT}"

echo "==> Applying lifecycle rule (age off noncurrent versions > 180 days)..."
LIFECYCLE_FILE="$(mktemp)"
trap 'rm -f "${LIFECYCLE_FILE}"' EXIT
cat > "${LIFECYCLE_FILE}" <<'EOF'
{
  "rule": [
    {
      "action": {"type": "Delete"},
      "condition": {"daysSinceNoncurrentTime": 180}
    }
  ]
}
EOF
gcloud storage buckets update "gs://${BUCKET}" \
  --lifecycle-file="${LIFECYCLE_FILE}" \
  --project="${PROJECT}"

echo "==> Bootstrap complete."
echo "    Next: seed the bucket from your laptop:"
echo "      gsutil -m rsync -r raw/  gs://${BUCKET}/raw/"
echo "      gsutil -m rsync -r wiki/ gs://${BUCKET}/wiki/"
echo "    Then deploy the viewer:"
echo "      bash scripts/gcp/deploy-viewer.sh"
