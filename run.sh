#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# 一条命令启动：工厂危险作业监护人替换与现场复核系统
#
#   ./run.sh          生产式启动：构建前端 → 初始化 SQLite/种子 → http://localhost:8000
#   ./run.sh dev      开发模式：FastAPI(8000) + Vite 热更新(5173)
#   ./run.sh test     运行后端全部 pytest
#   ./run.sh reset    删除本地数据库后重新播种
# ─────────────────────────────────────────────────────────────────────────────────
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
PORT="${PORT:-8000}"
PY="python3"
NPM_REGISTRY="${NPM_REGISTRY:-https://registry.npmmirror.com}"

log() { printf "\033[36m▶ %s\033[0m\n" "$*"; }

ensure_python_deps() {
  if ! "$PY" -c "import fastapi, uvicorn, sqlalchemy, pydantic" 2>/dev/null; then
    log "安装 Python 依赖…"
    if ! "$PY" -m pip --version >/dev/null 2>&1; then
      curl -sS https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
      "$PY" /tmp/get-pip.py --break-system-packages
    fi
    "$PY" -m pip install --break-system-packages -r "$BACKEND/requirements.txt"
  fi
}

ensure_frontend_deps() {
  if [ ! -d "$FRONTEND/node_modules" ]; then
    log "安装前端依赖（镜像 $NPM_REGISTRY）…"
    (cd "$FRONTEND" && npm install --registry="$NPM_REGISTRY")
  fi
}

reset_db() {
  rm -f "$BACKEND/guardian_handover.db"
  log "已删除旧数据库"
}

seed_db() {
  (cd "$BACKEND" && "$PY" -m app.seed)
}

case "${1:-serve}" in
  test)
    ensure_python_deps
    log "运行后端测试…"
    (cd "$BACKEND" && "$PY" -m pytest -q)
    ;;

  reset)
    ensure_python_deps
    reset_db
    seed_db
    ;;

  dev)
    ensure_python_deps
    ensure_frontend_deps
    seed_db
    log "启动 FastAPI http://localhost:$PORT （前端 Vite http://localhost:5173）"
    (cd "$BACKEND" && "$PY" -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --reload) &
    API_PID=$!
    (cd "$FRONTEND" && npm run dev) &
    WEB_PID=$!
    trap 'kill $API_PID $WEB_PID 2>/dev/null || true' EXIT
    wait
    ;;

  serve|"")
    ensure_python_deps
    ensure_frontend_deps
    log "构建前端…"
    (cd "$FRONTEND" && npm run build)
    seed_db
    log "启动系统：请用浏览器打开 http://localhost:$PORT"
    cd "$BACKEND"
    exec "$PY" -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
    ;;

  *)
    echo "用法: ./run.sh [serve|dev|test|reset]"; exit 1
    ;;
esac
