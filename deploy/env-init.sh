#!/usr/bin/env bash
# Run ONCE on a fresh VM to write the initial .env.
# DO NOT run on a live VM — it will overwrite existing config.
set -euo pipefail

VM="rashuraj@sales-production"
ZONE="us-east4-a"
REMOTE_DIR="/opt/services/hubspot-sheet-sync"

echo "WARNING: This will overwrite the VM .env. Ctrl-C to cancel."
read -r -p "Continue? [y/N] " confirm
[[ "${confirm}" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 1; }

gcloud compute ssh "${VM}" --zone="${ZONE}" --tunnel-through-iap --command="
cat > ${REMOTE_DIR}/.env << 'ENV'
HUBSPOT_API_TOKEN=FILL_IN
HUBSPOT_AE_EMAILS=deeksha@reo.dev,piyush@reo.dev,chandra@reo.dev,harsha@reo.dev,ayush@reo.dev,akanksha@reo.dev
GOOGLE_CREDENTIALS_FILE=${REMOTE_DIR}/credentials.json
PIPELINE_SHEET_ID=FILL_IN
PIPELINE_TAB_NAME=Sales Pipeline 2026
SOURCE_SHEET_ID=FILL_IN
SLACK_BOT_TOKEN=FILL_IN
SLACK_ALERT_CHANNEL=FILL_IN
PORT=8008
LOG_LEVEL=INFO
SCHEDULER_ENABLED=true
AUTH_ENABLED=false
ALLOWED_EMAILS=rashu@reo.dev
ENV
echo 'VM .env written. Fill in FILL_IN values before restarting the service.'
"
