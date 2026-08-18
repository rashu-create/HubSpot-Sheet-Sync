#!/usr/bin/env bash
set -euo pipefail

VM="rashuraj@sales-production"
ZONE="us-east4-a"
REMOTE_DIR="/opt/services/hubspot-sheet-sync"
SERVICE="hubspot-sheet-sync"
TARBALL="deploy-patch.tar.gz"

echo "==> Packing source files (no .env)..."
tar czf "/tmp/${TARBALL}" \
  src/ \
  templates/ \
  static/ \
  requirements.txt

echo "==> Copying to VM..."
gcloud compute scp "/tmp/${TARBALL}" "${VM}:${REMOTE_DIR}/" \
  --zone="${ZONE}" --tunnel-through-iap

echo "==> Unpacking and restarting on VM..."
gcloud compute ssh "${VM}" --zone="${ZONE}" --tunnel-through-iap --command="
  set -euo pipefail
  cd ${REMOTE_DIR}
  tar xzf ${TARBALL}
  rm ${TARBALL}
  sudo systemctl restart ${SERVICE}
  sleep 3
  sudo systemctl status ${SERVICE} --no-pager -l | grep -E 'Active|Scheduler|started|ERROR' || true
"

echo "==> Cleaning up local tarball..."
rm "/tmp/${TARBALL}"

echo "==> Deploy complete."
