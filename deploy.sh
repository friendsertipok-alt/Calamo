#!/bin/bash
# Calamo Deploy Script — push to GitHub + update server
set -e

echo "📦 Committing changes..."
cd "$(dirname "$0")"
git add -A
git commit -m "${1:-update}" || echo "Nothing to commit"

echo "🚀 Pushing to GitHub..."
git push origin main

# 🔄 Updating server (requires SSH config)
# Set these environment variables or configure in ~/.ssh/config:
#   DEPLOY_HOST — your server IP or hostname
#   DEPLOY_USER — SSH user (default: root)
#   SSH_KEY_PATH — path to SSH private key
DEPLOY_USER="${DEPLOY_USER:-root}"
DEPLOY_HOST="${DEPLOY_HOST:?Set DEPLOY_HOST env variable}"
SSH_KEY_PATH="${SSH_KEY_PATH:-~/.ssh/id_rsa}"

ssh -i "${SSH_KEY_PATH}" "${DEPLOY_USER}@${DEPLOY_HOST}" "cd /opt/calamo && git pull origin main && docker compose build && docker compose up -d"

echo "✅ Deploy complete! https://calamo.lol"
