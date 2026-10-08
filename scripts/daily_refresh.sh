#!/bin/bash
# The whole daily data refresh, start to finish, with no judgment calls:
#   1. fast refresh   (bulk stats, team stats)            -> commit, push, wait for the deploy
#   2. slow refresh   (per-player logs + shot zones)      -> commit, push, wait for the deploy
#   3. track record   (grade yesterday, record today)
# and a final "RESULT:" line. Everything is logged to /tmp/nba_daily_refresh.log.
#
# usage:  bash scripts/daily_refresh.sh          start it (or join the run already going) and wait
#         bash scripts/daily_refresh.sh --wait   keep waiting on a run that is still going
#
# It runs detached and each call waits at most ~9 minutes, because a headless
# session's tool call is capped at 10 minutes and the slow phase takes 30-40
# once the season is under way. Calls print "STILL RUNNING" until it is done,
# then the summary. Safe to call again at any time; only one run exists at once.
set -u
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
cd "$(dirname "$SELF")/.."

LOG=/tmp/nba_daily_refresh.log
PIDFILE=/tmp/nba_daily_refresh.pid
FINISHED="=== FINISHED"
SITE=https://matchupedge.com
SNAP=nba_prop_analyzer/data/snapshot
KEYFILE="$HOME/.claude/scheduled-tasks/nba-data-snapshot-refresh/track_record_key"
TODAY=$(date +%Y-%m-%d)

# ---------------------------------------------------------------- the work ---

fail() { echo "RESULT: FAILED -- $1"; echo "$FINISHED"; exit 1; }

# Commit + push whatever changed under the snapshot dir. The identity is passed
# per command (never written to git config) because this machine's global git
# identity has been blank, which makes a plain `git commit` fail.
GIT_ID=(-c user.name="Darko Smilevski" -c user.email="darkosmilevskii@gmail.com")
publish() {
  PUBLISHED=0
  git add "$SNAP/"
  if git diff --cached --quiet; then echo "  nothing changed, nothing to publish"; return 0; fi
  git "${GIT_ID[@]}" commit -q -m "$1" || fail "git commit failed"
  if ! git push -q origin main 2>/tmp/nba_push.err; then
    # Someone pushed code in between: rebase onto it once, then retry.
    git "${GIT_ID[@]}" pull -q --rebase origin main 2>>/tmp/nba_push.err \
      && git push -q origin main 2>>/tmp/nba_push.err \
      || fail "git push failed: $(tail -n 2 /tmp/nba_push.err | tr '\n' ' ')"
  fi
  PUBLISHED=1
  echo "  published $(git log -1 --format='%h %s')"
}

# Wait (up to 6 minutes) for production to report the snapshot we just pushed.
confirm_deploy() {
  local field=$1 want live
  want=$(python3 -c "import json;print(json.load(open('$SNAP/meta.json')).get('$field'))")
  for _ in $(seq 1 12); do
    live=$(curl -s "$SITE/api/data-version" | python3 -c "import sys,json;print(json.load(sys.stdin).get('$field'))" 2>/dev/null)
    if [ "$live" = "$want" ]; then echo "  production confirmed ($field)"; return 0; fi
    sleep 30
  done
  echo "  WARNING: production did not pick up $field within 6 minutes (check Railway)"
  PROBLEMS="$PROBLEMS | deploy not confirmed ($field)"
}

run() {
  PROBLEMS=""
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') daily refresh starting"

  echo "[1/3] fast refresh"
  python3 -u scripts/refresh_snapshot.py --fast 2>&1 | grep -v "Warning\|warnings.warn" | sed 's/^/  /'
  [ "${PIPESTATUS[0]}" -eq 0 ] || fail "fast refresh did not complete (data left untouched)"
  publish "chore: refresh data snapshot, fast ($TODAY)"
  confirm_deploy refreshed_at

  echo "[2/3] per-player logs and shot zones"
  python3 -u scripts/refresh_snapshot.py --logs-only 2>&1 | grep -v "Warning\|warnings.warn" | sed 's/^/  /'
  [ "${PIPESTATUS[0]}" -eq 0 ] || PROBLEMS="$PROBLEMS | logs refresh crashed (fast data is already published)"
  publish "chore: refresh data snapshot, game logs ($TODAY)"
  if [ "$PUBLISHED" = 1 ]; then confirm_deploy logs_refreshed_at; fi

  echo "[3/3] track record"
  if [ -r "$KEYFILE" ]; then
    key=$(cat "$KEYFILE")
    score=$(curl -s "$SITE/internal/score-track-record?key=$key")
    record=$(curl -s "$SITE/internal/snapshot-track-record?key=$key")
    echo "  graded:   $score"
    echo "  recorded: $record"
    case "$score$record" in *"Not found"*) PROBLEMS="$PROBLEMS | TRACK_RECORD_KEY is not set on Railway (track record not running)";; esac
  else
    PROBLEMS="$PROBLEMS | track record key file missing ($KEYFILE)"
  fi

  if [ -z "$PROBLEMS" ]; then echo "RESULT: OK"; else echo "RESULT: OK WITH PROBLEMS${PROBLEMS}"; fi
  echo "$FINISHED $(date '+%H:%M:%S')"
}

# ------------------------------------------------------- start / wait front ---

wait_for_finish() {
  # 27 x 20s = ~9 minutes; WAIT_STEPS / WAIT_SLEEP exist only so tests can shorten it.
  for _ in $(seq 1 "${WAIT_STEPS:-27}"); do
    if grep -q "^$FINISHED" "$LOG" 2>/dev/null; then
      sed -n '/daily refresh starting/,$p' "$LOG" | grep -v '^  \[[0-9]'
      return 0
    fi
    sleep "${WAIT_SLEEP:-20}"
  done
  echo "STILL RUNNING -- run this command again to keep waiting. Latest:"
  tail -n 2 "$LOG" 2>/dev/null | sed 's/^/  /'
}

case "${1:-}" in
  --run) run ;;
  --wait) wait_for_finish ;;
  *)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "a run is already in progress, joining it"
    else
      : > "$LOG"
      nohup bash "$SELF" --run >> "$LOG" 2>&1 &
      echo $! > "$PIDFILE"
    fi
    wait_for_finish
    ;;
esac
