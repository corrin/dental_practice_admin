#!/usr/bin/env bash
# Open a pull request on corrin/dental_practice_admin using the dental-only PAT.
#
# Why this exists: OpenHands' built-in push / "create PR" action uses the single
# configured provider (GitHub) token. On this instance that token belongs to another
# project and has no write access here, so the native button cannot open dental PRs.
# This helper instead uses the `dental_admin_github_pat` custom secret
# (OpenHands Settings > Secrets), giving dental its own credential without touching
# the platform token.
#
# Usage (from a runtime where the secret is exported):
#   dental_admin_github_pat="${dental_admin_github_pat}" bash .openhands/open-pr.sh "PR title" ["PR body"]
#
# The leading assignment makes the secret name appear on the command line, so this works
# whether OpenHands exports custom secrets globally or injects them per-command.
set -euo pipefail

REPO="corrin/dental_practice_admin"
BASE="main"

TITLE="${1:-}"
BODY="${2:-Opened via .openhands/open-pr.sh}"
TOKEN="${dental_admin_github_pat:-}"

if [ -z "$TOKEN" ]; then
  echo "error: dental_admin_github_pat is not set." >&2
  echo "       Add it under OpenHands Settings > Secrets, then invoke as:" >&2
  echo '       dental_admin_github_pat="$dental_admin_github_pat" bash .openhands/open-pr.sh "title"' >&2
  exit 1
fi
if [ -z "$TITLE" ]; then
  echo 'error: PR title required: bash .openhands/open-pr.sh "title" ["body"]' >&2
  exit 1
fi

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$BRANCH" = "$BASE" ] || [ "$BRANCH" = "HEAD" ]; then
  echo "error: refusing to open a PR from '$BRANCH' -- create a feature branch first." >&2
  exit 1
fi

# Push the current branch using the dental PAT. The token is not persisted to the repo
# config here, and OpenHands masks secret values in command output.
git push "https://x-access-token:${TOKEN}@github.com/${REPO}.git" "HEAD:refs/heads/${BRANCH}"

# Create the PR via the REST API (the runtime has no `gh`). Token is passed through the
# environment, not argv, so it does not appear in the process list.
GH_TOKEN="$TOKEN" python3 - "$TITLE" "$BODY" "$BRANCH" "$BASE" "$REPO" <<'PY'
import json, os, sys, urllib.request, urllib.error
title, body, head, base, repo = sys.argv[1:6]
token = os.environ["GH_TOKEN"]
req = urllib.request.Request(
    f"https://api.github.com/repos/{repo}/pulls",
    data=json.dumps({"title": title, "head": head, "base": base, "body": body}).encode(),
    headers={"Authorization": f"Bearer {token}",
             "Accept": "application/vnd.github+json",
             "User-Agent": "dental-open-pr"})
try:
    r = json.load(urllib.request.urlopen(req))
    print(f"Pull request opened: {r['html_url']}")
except urllib.error.HTTPError as e:
    detail = e.read().decode()
    if e.code == 422:
        # Most likely a PR already exists for this head branch -- find and report it.
        owner = repo.split("/")[0]
        q = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/pulls?head={owner}:{head}&state=open",
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json",
                     "User-Agent": "dental-open-pr"})
        try:
            existing = json.load(urllib.request.urlopen(q))
            if existing:
                print(f"Pull request already open: {existing[0]['html_url']}")
                sys.exit(0)
        except Exception:
            pass
    print(f"error: GitHub API HTTP {e.code}: {detail}", file=sys.stderr)
    sys.exit(1)
PY
