#!/bin/zsh
set -e

SCRIPT_DIR="$(CDPATH= cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

SKIP_COMPONENT_BUILD=0
typeset -a STREAMLIT_ARGS

while (( $# > 0 )); do
  case "$1" in
    --skip-component-build)
      SKIP_COMPONENT_BUILD=1
      shift
      ;;
    --)
      shift
      STREAMLIT_ARGS=("$@")
      break
      ;;
    *)
      echo "Usage: ./run.command [--skip-component-build] [-- STREAMLIT_ARGS...]" >&2
      exit 2
      ;;
  esac
done

if (( ! SKIP_COMPONENT_BUILD )); then
  .venv/bin/python scripts/dev.py build-components
fi

exec .venv/bin/python -m streamlit run codex_nomad_surface/app.py \
  --server.address 0.0.0.0 "${STREAMLIT_ARGS[@]}"
