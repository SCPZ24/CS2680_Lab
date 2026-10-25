#!/bin/bash
# Run the frozen harness without exposing gold patches or held-out tests.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
IDX="${1:?usage: mytest/run.sh <task_index>}"
BASELINE=e7a0e61e26cf41e428a4a0428189cb139b4d5b69
IID=$(python3 -c 'import json,sys; print(list(json.load(open(sys.argv[1])))[int(sys.argv[2])])' "$HERE/tasks.json" "$IDX")
STAGE=$(mktemp -d /tmp/madsloop-mytest.XXXXXX)
cleanup() {
  if [ -d "$STAGE/madsLoop_logs/$IID" ]; then
    mkdir -p "$ROOT/madsLoop_logs/$IID"
    cp -R "$STAGE/madsLoop_logs/$IID/." "$ROOT/madsLoop_logs/$IID/"
  fi
  if [ -f "$STAGE/model_patch_$IID.diff" ]; then
    cp "$STAGE/model_patch_$IID.diff" "$HERE/results/$IID.agent.diff"
  fi
  rm -rf "$STAGE"
}
trap cleanup EXIT
# Archive only executable harness files. No task history, gold or grading data.
git -C "$(git -C "$ROOT" rev-parse --show-toplevel)" archive "$BASELINE:assignment2" madsLoop.py src docker_env.sh | tar -x -C "$STAGE"
export MADSLOOP_REPO="$STAGE"
export TASKS_JSON="$HERE/tasks.json"
export MADSLOOP_BASE_URL=https://openrouter.ai/api/v1
export MADSLOOP_API_KEY="${OPENROUTER_API_KEY:?Set OPENROUTER_API_KEY}"
mkdir -p "$HERE/results"
bash "$ROOT/evaluation_scripts/run_task.sh" "$IDX"
