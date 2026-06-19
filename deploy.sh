#!/bin/bash
# Calamo Deploy Script — push to GitHub + update server
set -e

echo "📦 Committing changes..."
cd "$(dirname "$0")"
git add -A
git commit -m "${1:-update}" || echo "Nothing to commit"

echo "🚀 Pushing to GitHub..."
git push origin main

# 🔄 Updating server...
ssh -i ~/.ssh/antigravity_key root@185.5.75.211 "cd /opt/calamo && git pull origin main && docker compose build && docker compose up -d && docker compose restart nginx"

echo "✅ Deploy complete! https://calamo.lol"
