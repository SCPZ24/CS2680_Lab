#!/bin/bash
# Called inside each task container before madsLoop.py.
set -euo pipefail
python3 -m pip install -q 'openai>=1.0'
