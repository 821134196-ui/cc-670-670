#!/usr/bin/env bash
# 一条命令启动：监护人替换与现场复核系统（后端 + 前端）。
#
#   ./run.sh            首次运行自动准备虚拟环境、依赖并构建前端，然后启动
#   ./run.sh --dev      前后端开发模式（后端 uvicorn --reload，前端 vite 热更新）
#   ./run.sh --test     只运行后端测试
#   ./run.sh --reset    删除 SQLite 数据并重新播种后启动
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
PORT="${PORT:-8000}"

mode="serve"
[[ "${1:-}" == "--dev" ]] && mode="dev"
[[ "${1:-}" == "--test" ]] && mode="test"
[[ "${1:-}" == "--reset" ]] && { rm -f "$BACKEND/guardian.db"; echo "已重置数据库"; }

# 1) Python 虚拟环境与依赖（系统无 pip 时用 get-pip.py 引导）
if [[ ! -x "$VENV/bin/python" ]]; then
  echo ">> 创建虚拟环境 .venv"
  python3 -m venv "$VENV" --without-pip
  if [[ ! -x "$VENV/bin/pip" ]]; then
    echo ">> 引导安装 pip"
    curl -sS https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
    "$VENV/bin/python" /tmp/get-pip.py
  fi
fi
"$VENV/bin/python" -m pip install -q -r "$ROOT/requirements.txt"

if [[ "$mode" == "test" ]]; then
  echo ">> 运行后端测试"
  cd "$BACKEND"
  DB_PATH=/tmp/guardian_pytest.db AUTO_INIT_DB=0 \
    "$VENV/bin/python" -m pytest tests/ -v
  exit 0
fi

# 2) 前端依赖
if [[ ! -d "$FRONTEND/node_modules" ]]; then
  echo ">> 安装前端依赖"
  (cd "$FRONTEND" && npm install --silent)
fi

if [[ "$mode" == "dev" ]]; then
  echo ">> 开发模式：后端 http://127.0.0.1:$PORT  前端 http://127.0.0.1:5173"
  (cd "$FRONTEND" && npm run dev) &
  FRONT_PID=$!
  trap 'kill $FRONT_PID 2>/dev/null || true' EXIT
  cd "$BACKEND"
  exec "$VENV/bin/python" -m uvicorn app.main:app --reload --host 0.0.0.0 --port "$PORT"
fi

# 3) 生产模式：构建前端，由 FastAPI 托管 dist
echo ">> 构建前端"
(cd "$FRONTEND" && npm run build --silent)

echo ">> 启动服务：http://127.0.0.1:$PORT （接口文档 /docs）"
cd "$BACKEND"
exec "$VENV/bin/python" -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
