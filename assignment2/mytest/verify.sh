#!/bin/bash
# Run the isolated evaluator; exit 0 means resolved, 1 unresolved, 2 setup failure.
set -euo pipefail
exec python3 "$(cd "$(dirname "$0")" && pwd)/verify.py" "$@"
