#!/usr/bin/env bash
# Commit bot state (data/) back to the repo.
set -e
git config user.name "paper-bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add data/
git diff --cached --quiet && exit 0
git commit -qm "data: $1 $(date -u +%Y-%m-%dT%H:%MZ)"
for i in 1 2 3; do
  git pull -q --rebase --autostash -X theirs origin "${GITHUB_REF_NAME:-main}" && git push -q && exit 0
  sleep 5
done
exit 1
