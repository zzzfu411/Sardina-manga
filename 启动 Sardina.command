#!/bin/bash
set -eu
SARDINA_ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$SARDINA_ROOT"
if [ ! -x .venv/bin/python ]; then
  echo "请先在项目目录执行 uv sync。"
  exit 1
fi
.venv/bin/python scripts/sardina_service.py start
open http://127.0.0.1:8765/
