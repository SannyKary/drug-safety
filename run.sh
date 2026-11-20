#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"

if [ -n "${DOSSIER_PYTHON:-}" ]; then
    exec "$DOSSIER_PYTHON" -m mcp_tools.launch
fi

if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
fi
if ! cmp -s requirements.txt .venv/installed-requirements.txt; then
    .venv/bin/python -m pip install -r requirements.txt
    cp requirements.txt .venv/installed-requirements.txt
fi
exec .venv/bin/python -m mcp_tools.launch
