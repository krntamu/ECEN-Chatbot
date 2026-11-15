#!/usr/bin/env bash
# Freeze the Python package versions of a known-good image into pinned-requirements.txt.
#
# Usage:
#   ./scripts/lock_deps.sh                 # uses the image the Cloud Run service is running
#   ./scripts/lock_deps.sh <image-ref>     # or any image you name
#
# Needs: docker, and gcloud (only when no image is given).
set -euo pipefail
cd "$(dirname "$0")/.."

SERVICE="${SERVICE:-ecen-chatbot}"
REGION="${REGION:-us-central1}"
IMAGE="${1:-}"

if [[ -z "$IMAGE" ]]; then
  IMAGE="$(gcloud run services describe "$SERVICE" --region "$REGION" \
            --format='value(spec.template.spec.containers[0].image)')"
  echo "Deployed image: $IMAGE"
  gcloud auth configure-docker "${IMAGE%%/*}" --quiet >/dev/null
fi

header="$(grep '^#' pinned-requirements.txt || true)"
frozen="$(docker run --rm --entrypoint python "$IMAGE" -m pip freeze --exclude-editable)"
if [[ -z "$frozen" ]]; then
  echo "pip freeze returned nothing; leaving pinned-requirements.txt untouched" >&2
  exit 1
fi
{
  echo "$header"
  echo "#"
  echo "# Frozen from: $IMAGE"
  echo "# On: $(date -u +%Y-%m-%d)"
  echo "$frozen"
} > pinned-requirements.txt
echo "Wrote $(echo "$frozen" | wc -l | tr -d ' ') pins to pinned-requirements.txt"
