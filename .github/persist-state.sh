#!/usr/bin/env bash
# Persist small JSON state files to a dedicated `state` branch so they survive
# across GitHub Actions runs. Unlike actions/cache (LRU-evicted, immutable per
# key), a branch is durable and inspectable — you can open it on GitHub to see
# the current seen-cache / watchlist / Hunter counter.
#
# Each workflow persists ONLY the files it owns (via $FILES), so the funding and
# companies digests never overwrite each other's state. The retry loop handles
# the rare case where both workflows push to `state` at the same instant: we always
# start from the latest remote `state`, re-apply just our files, and push again.
#
# Requires: git >= 2.42 (for `worktree add --orphan`); GitHub ubuntu runners ship
# a newer git. Needs `permissions: contents: write` on the job.
set -uo pipefail

FILES="${FILES:?set FILES to a space-separated list of state files to persist}"

git config user.name  "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

# Snapshot the freshly-written files now — the working tree may be reset while we
# retry against a moving remote branch.
tmp="$(mktemp -d)"
have=0
for f in $FILES; do
  if [ -f "$f" ]; then cp "$f" "$tmp/$f"; have=1; fi
done
[ "$have" = 1 ] || { echo "no state files produced; nothing to persist"; exit 0; }

for attempt in 1 2 3 4 5; do
  rm -rf .state_wt
  if git fetch -q origin state 2>/dev/null; then
    git worktree add -q -B state .state_wt origin/state
  else
    git worktree add -q --orphan -b state .state_wt        # first ever run
  fi

  added=0
  for f in $FILES; do
    if [ -f "$tmp/$f" ]; then
      cp "$tmp/$f" ".state_wt/$f"
      git -C .state_wt add -f "$f"
      added=1
    fi
  done
  [ "$added" = 1 ] || { echo "nothing to add"; git worktree remove --force .state_wt; exit 0; }

  if git -C .state_wt diff --cached --quiet; then
    echo "state unchanged"; git worktree remove --force .state_wt; exit 0
  fi

  git -C .state_wt commit -qm "state: ${GITHUB_WORKFLOW:-run} #${GITHUB_RUN_NUMBER:-0}"
  if git -C .state_wt push -q origin state; then
    echo "state pushed (attempt $attempt)"; git worktree remove --force .state_wt; exit 0
  fi

  echo "push race on state branch, retry $attempt"
  git worktree remove --force .state_wt
done

echo "[warn] could not persist state after 5 attempts"; exit 0
