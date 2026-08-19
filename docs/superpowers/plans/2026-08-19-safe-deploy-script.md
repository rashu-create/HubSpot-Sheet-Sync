# Safe Deploy Script Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the ad-hoc `tar + scp` deploy with a `deploy/deploy.sh` script that never bundles `.env`, so local config can never clobber VM secrets.

**Architecture:** A single shell script packs only source code files (never `.env`), SCPs the tarball to the VM, unpacks it, and restarts the service. VM `.env` is managed independently and never touched by deploys. A `deploy/env-init.sh` helper exists for first-time VM env setup only.

**Tech Stack:** bash, gcloud CLI (SCP + SSH via IAP), systemd

## Global Constraints

- Never include `.env` in any deploy tarball — ever
- VM path: `/opt/services/hubspot-sheet-sync/`
- VM instance: `rashuraj@sales-production`, zone `us-east4-a`, tunnel `--tunnel-through-iap`
- Service name: `hubspot-sheet-sync`
- Always verify `SCHEDULER_ENABLED=true` after deploy (log check, not sed)
- Scripts are executable (`chmod +x`) and committed

---

### Task 1: Write and test `deploy/deploy.sh`

**Files:**
- Create: `deploy/deploy.sh`

**Interfaces:**
- Produces: executable script that deploys code-only tarball and restarts service

- [ ] **Step 1: Create the script**

```bash
cat > ~/hubspot-sheet-sync/deploy/deploy.sh << 'SCRIPT'
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
  sudo systemctl status ${SERVICE} --no-pager -l | grep -E 'Active|Scheduler|started|ERROR'
"

echo "==> Cleaning up local tarball..."
rm "/tmp/${TARBALL}"

echo "==> Deploy complete."
SCRIPT
chmod +x ~/hubspot-sheet-sync/deploy/deploy.sh
```

- [ ] **Step 2: Verify the script never touches `.env`**

```bash
grep -n "\.env" ~/hubspot-sheet-sync/deploy/deploy.sh
# Expected: no output — .env must not appear anywhere in the script
```

- [ ] **Step 3: Dry-run the tar to confirm contents (no credentials, no .env)**

```bash
tar czf /tmp/dry-run-check.tar.gz \
  -C ~/hubspot-sheet-sync \
  src/ templates/ static/ requirements.txt
tar tzf /tmp/dry-run-check.tar.gz | head -30
rm /tmp/dry-run-check.tar.gz
# Expected: only src/, templates/, static/, requirements.txt — no .env, no credentials.json
```

- [ ] **Step 4: Run a real deploy to confirm end-to-end**

```bash
cd ~/hubspot-sheet-sync && bash deploy/deploy.sh
# Expected:
#   ==> Packing source files (no .env)...
#   ==> Copying to VM...
#   ==> Unpacking and restarting on VM...
#   Active: active (running) ...
#   Scheduler started — jobs: sync_morning (04:30 UTC), sync_evening (16:30 UTC)
#   ==> Deploy complete.
```

- [ ] **Step 5: Confirm SCHEDULER_ENABLED still true on VM after deploy**

```bash
gcloud compute ssh rashuraj@sales-production --zone=us-east4-a --tunnel-through-iap \
  --command="grep SCHEDULER_ENABLED /opt/services/hubspot-sheet-sync/.env"
# Expected: SCHEDULER_ENABLED=true
```

- [ ] **Step 6: Commit**

```bash
cd ~/hubspot-sheet-sync
git add deploy/deploy.sh
git commit -m "deploy: add safe deploy script — never bundles .env"
```

---

### Task 2: Write `deploy/env-init.sh` for first-time VM env setup

This is a one-time helper that documents what env vars need to be set on the VM. It never runs automatically — only used when standing up the service fresh.

**Files:**
- Create: `deploy/env-init.sh`

**Interfaces:**
- Produces: a documented script that shows what a valid VM `.env` looks like

- [ ] **Step 1: Create the script**

```bash
cat > ~/hubspot-sheet-sync/deploy/env-init.sh << 'SCRIPT'
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
SCRIPT
chmod +x ~/hubspot-sheet-sync/deploy/env-init.sh
```

- [ ] **Step 2: Verify env-init.sh hardcodes the VM credentials path**

```bash
grep GOOGLE_CREDENTIALS_FILE ~/hubspot-sheet-sync/deploy/env-init.sh
# Expected: GOOGLE_CREDENTIALS_FILE=/opt/services/hubspot-sheet-sync/credentials.json
```

- [ ] **Step 3: Commit**

```bash
cd ~/hubspot-sheet-sync
git add deploy/env-init.sh
git commit -m "deploy: add env-init.sh for first-time VM env setup"
```

---

## Self-Review

| Requirement | Covered by |
|---|---|
| Never bundle `.env` | Task 1 — grep check + tar contents dry-run |
| VM credentials path correct | Task 2 — hardcoded in env-init.sh |
| Scheduler stays enabled after deploy | Task 1 Step 5 — explicit grep check |
| One-command deploy | Task 1 — `bash deploy/deploy.sh` |
| First-time setup documented | Task 2 — env-init.sh with confirmation gate |
