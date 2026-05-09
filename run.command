#!/bin/zsh
set -e

SCRIPT_DIR="$(CDPATH= cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

exec .venv/bin/python -m streamlit run codex_nomad_surface/app.py --server.address 0.0.0.0
