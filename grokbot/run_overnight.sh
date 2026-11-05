#!/usr/bin/env bash
# Paper only. Runs the desk for N hours (default 10) in the background.
cd "$(dirname "$0")/.." || exit 1
HOURS="${1:-10}"
mkdir -p grokbot/state
CMD="python3 -m grokbot.desk --hours $HOURS"
command -v caffeinate >/dev/null && CMD="caffeinate -i $CMD"   # keep a Mac awake
nohup $CMD >> grokbot/state/run.log 2>&1 &
echo "Grok Bot paper desk running (pid $!) for ${HOURS}h. Watch: tail -f grokbot/state/run.log"
