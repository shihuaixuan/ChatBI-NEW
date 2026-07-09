#!/usr/bin/env bash
set -euo pipefail

# 本脚本用于本地开发环境一键启动 SQLBot 后端和前端。
# 默认会释放 8000/5173 端口上的旧进程，再重新后台启动服务。

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"
FRONTEND_DIR="${ROOT_DIR}/frontend"
LOG_DIR="${ROOT_DIR}/.dev-logs"

BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

mkdir -p "${LOG_DIR}"

stop_port() {
  local port="$1"
  local pids
  pids="$(lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -z "${pids}" ]]; then
    return 0
  fi

  # 先给进程正常退出机会，避免开发服务残留缓存或半写日志。
  echo "停止占用端口 ${port} 的进程: ${pids}"
  kill ${pids} 2>/dev/null || true
  sleep 1

  pids="$(lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "${pids}" ]]; then
    echo "端口 ${port} 仍被占用，强制停止进程: ${pids}"
    kill -9 ${pids} 2>/dev/null || true
  fi
}

wait_http() {
  local name="$1"
  local url="$2"
  local max_attempts="${3:-40}"

  for ((attempt = 1; attempt <= max_attempts; attempt++)); do
    if curl -fsS "${url}" >/dev/null 2>&1; then
      echo "${name} 已就绪: ${url}"
      return 0
    fi
    sleep 1
  done

  echo "${name} 启动超时: ${url}" >&2
  return 1
}

echo "项目目录: ${ROOT_DIR}"

stop_port "${BACKEND_PORT}"
stop_port "${FRONTEND_PORT}"

echo "启动后端: http://${BACKEND_HOST}:${BACKEND_PORT}"
(
  cd "${BACKEND_DIR}"
  LOG_FORMAT='%(asctime)s - %(name)s - %(levelname)s:%(lineno)d - %(message)s' \
    uv run uvicorn main:app --host "${BACKEND_HOST}" --port "${BACKEND_PORT}"
) >"${LOG_DIR}/backend.log" 2>&1 &
BACKEND_PID="$!"
echo "${BACKEND_PID}" >"${LOG_DIR}/backend.pid"

echo "启动前端: http://${FRONTEND_HOST}:${FRONTEND_PORT}"
(
  cd "${FRONTEND_DIR}"
  npx vite --host "${FRONTEND_HOST}" --port "${FRONTEND_PORT}"
) >"${LOG_DIR}/frontend.log" 2>&1 &
FRONTEND_PID="$!"
echo "${FRONTEND_PID}" >"${LOG_DIR}/frontend.pid"

wait_http "后端" "http://${BACKEND_HOST}:${BACKEND_PORT}/docs"
wait_http "前端" "http://${FRONTEND_HOST}:${FRONTEND_PORT}/"

cat <<EOF

启动完成。
- 后端: http://${BACKEND_HOST}:${BACKEND_PORT}
- 前端: http://${FRONTEND_HOST}:${FRONTEND_PORT}
- 后端日志: ${LOG_DIR}/backend.log
- 前端日志: ${LOG_DIR}/frontend.log
- 后端 PID: ${BACKEND_PID}
- 前端 PID: ${FRONTEND_PID}

停止服务:
  kill ${BACKEND_PID} ${FRONTEND_PID}
EOF
