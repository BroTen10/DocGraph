#!/usr/bin/env bash
# =============================================================================
# DocGraph 本地开发环境启停（macOS / Linux）
#
#   ./dev.sh start            启动后端(FastAPI) + 前端(Vite)，必要时拉起数据库容器
#   ./dev.sh start backend    只启动后端
#   ./dev.sh start frontend   只启动前端
#   ./dev.sh stop             停止前后端（不影响数据库容器）
#   ./dev.sh stop --with-deps 连数据库容器一起停
#   ./dev.sh restart          重启前后端
#   ./dev.sh status           查看进程 / 端口 / 健康检查 / 数据库容器
#   ./dev.sh logs [服务]      跟踪日志（backend|frontend，默认 backend）
#   ./dev.sh deps             只拉起数据库容器（postgres + neo4j）
#   ./dev.sh admin <子命令>   本地账号工具：list / set-password / unlock
#   ./dev.sh doctor           体检：依赖、配置、端口、容器
#
# 说明：
#   - 端口从 backend/.env 的 APP_PORT 读取（默认 8800）；前端默认 5173，
#     可用 FRONTEND_PORT=xxxx ./dev.sh start 覆盖；
#   - 后台进程 PID 与日志写在 .dev/（已在 .gitignore 中）；
#   - 前端 dev server 通过 VITE_API_TARGET 反向代理到后端，端口冲突会自动跟随；
#   - 停止只杀本项目 .dev/*.pid 记录的进程；PID 丢失时按端口兜底回收。
# =============================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="${ROOT}/backend"
FRONTEND_DIR="${ROOT}/frontend"
RUN_DIR="${ROOT}/.dev"
BACKEND_PID_FILE="${RUN_DIR}/backend.pid"
FRONTEND_PID_FILE="${RUN_DIR}/frontend.pid"
BACKEND_LOG="${RUN_DIR}/backend.log"
FRONTEND_LOG="${RUN_DIR}/frontend.log"
VENV_PY="${BACKEND_DIR}/.venv/bin/python"
VITE_BIN="${FRONTEND_DIR}/node_modules/.bin/vite"
COMPOSE="docker compose --env-file ${BACKEND_DIR}/.env"

log()  { printf '\033[1;34m[dev]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[ ok ]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[fail]\033[0m %s\n' "$*" >&2; exit 1; }

# ---------- 配置读取 ----------
read_env() {
  local key="$1" default="${2:-}" line value
  if [ ! -f "${BACKEND_DIR}/.env" ]; then printf '%s' "${default}"; return; fi
  line="$(grep -E "^[[:space:]]*${key}=" "${BACKEND_DIR}/.env" | tail -1 || true)"
  if [ -z "${line}" ]; then printf '%s' "${default}"; return; fi
  value="${line#*=}"
  value="${value%$'\r'}"
  value="$(printf '%s' "${value}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' \
    -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")"
  printf '%s' "${value:-${default}}"
}

APP_PORT="$(read_env APP_PORT 8800)"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
BACKEND_URL="http://127.0.0.1:${APP_PORT}"
FRONTEND_URL="http://127.0.0.1:${FRONTEND_PORT}"

# ---------- 进程工具 ----------
pid_in_file() { [ -f "$1" ] && tr -d '[:space:]' < "$1" || true; }
is_alive()    { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }
port_pids()   { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null || true; }

wait_http() {
  local url="$1" seconds="$2" i
  for ((i = 0; i < seconds * 2; i++)); do
    if curl -sf -o /dev/null --max-time 2 "${url}"; then return 0; fi
    sleep 0.5
  done
  return 1
}

wait_port() {
  local port="$1" seconds="$2" i
  for ((i = 0; i < seconds * 2; i++)); do
    [ -n "$(port_pids "${port}")" ] && return 0
    sleep 0.5
  done
  return 1
}

# ---------- 依赖检查 ----------
require_backend_env() {
  if [ ! -f "${BACKEND_DIR}/.env" ]; then
    die "缺少 backend/.env：先执行 cp backend/.env.example backend/.env，并填写 LLM_API_KEY / PG_PASSWORD / APP_SECRET_KEY"
  fi
}

require_venv() {
  if [ ! -x "${VENV_PY}" ]; then
    die "缺少后端虚拟环境：cd backend && python3.12 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt"
  fi
}

require_frontend_deps() {
  if [ ! -x "${VITE_BIN}" ]; then
    die "缺少前端依赖：cd frontend && pnpm install（或 npm ci）"
  fi
  command -v node >/dev/null 2>&1 || die "PATH 中找不到 node，请先安装 Node.js（建议 20+）"
}

# ---------- 数据库容器 ----------
deps_running() {
  docker ps --format '{{.Names}}' 2>/dev/null | grep -qx doc-review-postgres \
    && docker ps --format '{{.Names}}' 2>/dev/null | grep -qx doc-review-neo4j
}

start_deps() {
  command -v docker >/dev/null 2>&1 || { warn "未安装 Docker，跳过数据库容器"; return 0; }
  if ! docker info >/dev/null 2>&1; then
    warn "Docker 未运行：请先启动 Docker Desktop，否则后端连不上数据库"
    return 0
  fi
  if deps_running; then ok "数据库容器已在运行（postgres / neo4j）"; return 0; fi

  log "拉起数据库容器：postgres + neo4j"
  # shellcheck disable=SC2086
  (cd "${ROOT}" && ${COMPOSE} up -d postgres neo4j) || die "数据库容器启动失败，请检查 backend/.env 的 PG_PASSWORD / NEO4J_PASSWORD"

  local i
  for ((i = 0; i < 60; i++)); do
    if docker exec doc-review-postgres pg_isready -U postgres >/dev/null 2>&1; then
      ok "PostgreSQL 就绪（$(read_env PG_DB doc_review)@127.0.0.1:$(read_env PG_PORT 5432)）"
      return 0
    fi
    sleep 1
  done
  warn "PostgreSQL 健康检查超时，后端可能启动失败：docker logs doc-review-postgres"
}

stop_deps() {
  command -v docker >/dev/null 2>&1 || return 0
  deps_running || { ok "数据库容器未运行"; return 0; }
  log "停止数据库容器（数据卷保留）"
  # shellcheck disable=SC2086
  (cd "${ROOT}" && ${COMPOSE} stop postgres neo4j)
}

# ---------- 后端 ----------
start_backend() {
  require_backend_env
  require_venv
  local pid; pid="$(pid_in_file "${BACKEND_PID_FILE}")"
  if is_alive "${pid}"; then ok "后端已在运行（PID ${pid}，${BACKEND_URL}）"; return 0; fi

  mkdir -p "${RUN_DIR}"
  log "启动后端：${VENV_PY} run.py（端口 ${APP_PORT}，日志 .dev/backend.log）"
  (
    cd "${BACKEND_DIR}"
    nohup "${VENV_PY}" run.py >>"${BACKEND_LOG}" 2>&1 &
    echo $! >"${BACKEND_PID_FILE}"
  )
  pid="$(pid_in_file "${BACKEND_PID_FILE}")"

  if wait_http "${BACKEND_URL}/api/version" 40; then
    ok "后端就绪：${BACKEND_URL}（PID ${pid}）"
  else
    warn "后端 40 秒内未就绪，检查日志：./dev.sh logs backend"
    tail -n 20 "${BACKEND_LOG}" 2>/dev/null || true
    return 1
  fi
}

# ---------- 前端 ----------
start_frontend() {
  require_frontend_deps
  local pid; pid="$(pid_in_file "${FRONTEND_PID_FILE}")"
  if is_alive "${pid}"; then ok "前端已在运行（PID ${pid}，${FRONTEND_URL}）"; return 0; fi

  mkdir -p "${RUN_DIR}"
  log "启动前端：vite（端口 ${FRONTEND_PORT}，代理 /api → ${BACKEND_URL}，日志 .dev/frontend.log）"
  (
    cd "${FRONTEND_DIR}"
    VITE_API_TARGET="${BACKEND_URL}" nohup "${VITE_BIN}" --port "${FRONTEND_PORT}" --strictPort \
      >>"${FRONTEND_LOG}" 2>&1 &
    echo $! >"${FRONTEND_PID_FILE}"
  )
  pid="$(pid_in_file "${FRONTEND_PID_FILE}")"

  if wait_port "${FRONTEND_PORT}" 40; then
    ok "前端就绪：${FRONTEND_URL}（PID ${pid}）"
  else
    warn "前端 40 秒内未就绪，检查日志：./dev.sh logs frontend"
    tail -n 20 "${FRONTEND_LOG}" 2>/dev/null || true
    return 1
  fi
}

# ---------- 停止 ----------
stop_one() {
  local name="$1" pid_file="$2" port="$3"
  local pid; pid="$(pid_in_file "${pid_file}")"

  if is_alive "${pid}"; then
    log "停止 ${name}（PID ${pid}）"
    kill "${pid}" 2>/dev/null || true
    local i
    for ((i = 0; i < 20; i++)); do
      is_alive "${pid}" || break
      sleep 0.5
    done
    is_alive "${pid}" && { warn "${name} 未响应 TERM，强制结束"; kill -9 "${pid}" 2>/dev/null || true; }
  else
    [ -n "${pid}" ] && warn "${name} 的 PID 文件已失效（${pid}）"
  fi
  rm -f "${pid_file}"

  # 兜底：端口仍被占用时回收（只可能是本项目的服务）
  local leftovers; leftovers="$(port_pids "${port}")"
  if [ -n "${leftovers}" ]; then
    warn "端口 ${port} 仍被占用，回收 PID：$(echo "${leftovers}" | tr '\n' ' ')"
    # shellcheck disable=SC2086
    kill ${leftovers} 2>/dev/null || true
    sleep 1
    leftovers="$(port_pids "${port}")"
    # shellcheck disable=SC2086
    [ -n "${leftovers}" ] && kill -9 ${leftovers} 2>/dev/null || true
  fi
  ok "${name} 已停止"
}

# ---------- 状态 ----------
show_status() {
  printf '\n%-10s %-8s %-22s %s\n' "服务" "PID" "地址" "状态"
  printf '%s\n' "--------------------------------------------------------------------"

  local pid state
  pid="$(pid_in_file "${BACKEND_PID_FILE}")"
  if is_alive "${pid}"; then
    if curl -sf -o /dev/null --max-time 2 "${BACKEND_URL}/api/version"; then state="运行中（健康检查通过）"; else state="进程在，但健康检查未通过"; fi
  else
    [ -n "$(port_pids "${APP_PORT}")" ] && state="未托管运行（端口 ${APP_PORT} 被其他进程占用）" || state="已停止"
  fi
  printf '%-10s %-8s %-22s %s\n' "后端" "${pid:--}" "${BACKEND_URL}" "${state}"

  pid="$(pid_in_file "${FRONTEND_PID_FILE}")"
  if is_alive "${pid}"; then
    if curl -sf -o /dev/null --max-time 2 "${FRONTEND_URL}"; then state="运行中"; else state="进程在，但页面无响应"; fi
  else
    [ -n "$(port_pids "${FRONTEND_PORT}")" ] && state="未托管运行（端口 ${FRONTEND_PORT} 被其他进程占用）" || state="已停止"
  fi
  printf '%-10s %-8s %-22s %s\n' "前端" "${pid:--}" "${FRONTEND_URL}" "${state}"

  printf '\n数据库容器：'
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    local names; names="$(docker ps --format '{{.Names}}\t{{.Status}}' 2>/dev/null | grep -E 'doc-review-(postgres|neo4j)' || true)"
    if [ -n "${names}" ]; then printf '\n%s\n' "${names}"; else printf '未运行（./dev.sh deps 可拉起）\n'; fi
  else
    printf 'Docker 未运行\n'
  fi

  printf '\n登录入口：%s（后端接口文档 %s/docs，需 APP_DEBUG=true）\n\n' "${FRONTEND_URL}" "${BACKEND_URL}"
}

# ---------- 体检 ----------
doctor() {
  printf '\nDocGraph 本地开发环境体检\n---------------------------------------\n'
  [ -f "${BACKEND_DIR}/.env" ] && ok "backend/.env 存在" || warn "缺少 backend/.env（cp backend/.env.example backend/.env）"
  [ -x "${VENV_PY}" ] && ok "后端虚拟环境：$("${VENV_PY}" -V 2>&1)" || warn "缺少后端虚拟环境（cd backend && python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt）"
  if command -v node >/dev/null 2>&1; then ok "node：$(node -v)"; else warn "PATH 中找不到 node"; fi
  [ -x "${VITE_BIN}" ] && ok "前端依赖已安装（vite）" || warn "缺少前端依赖（cd frontend && pnpm install）"
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    ok "Docker 可用"
    deps_running && ok "数据库容器在运行" || warn "数据库容器未运行（./dev.sh deps）"
  else
    warn "Docker 不可用或未启动"
  fi
  for p in "${APP_PORT}" "${FRONTEND_PORT}"; do
    local owners; owners="$(port_pids "${p}")"
    [ -z "${owners}" ] && ok "端口 ${p} 空闲" || warn "端口 ${p} 已被占用（PID $(echo "${owners}" | tr '\n' ' ')）"
  done
  printf '\n配置摘要：APP_PORT=%s  FRONTEND_PORT=%s  PG_DB=%s  PG_PORT=%s  入口=%s\n\n' \
    "${APP_PORT}" "${FRONTEND_PORT}" "$(read_env PG_DB doc_review)" "$(read_env PG_PORT 5432)" "${FRONTEND_URL}"
}

# ---------- 入口 ----------
usage() { sed -n '3,21p' "$0" | sed 's/^# \{0,1\}//'; }

cmd="${1:-}"; shift || true
target="${1:-all}"

case "${cmd}" in
  start)
    case "${target}" in
      backend)  start_backend ;;
      frontend) start_frontend ;;
      all|"")   start_deps; start_backend; start_frontend ;;
      *) die "未知目标：${target}（可选 backend|frontend）" ;;
    esac
    printf '\n'
    show_status
    ;;
  stop)
    stop_one "前端" "${FRONTEND_PID_FILE}" "${FRONTEND_PORT}"
    stop_one "后端" "${BACKEND_PID_FILE}" "${APP_PORT}"
    [ "${1:-}" = "--with-deps" ] && stop_deps
    printf '\n'
    ;;
  restart)
    "$0" stop
    exec "$0" start "$([ "${target}" = "all" ] && echo all || echo "${target}")"
    ;;
  status) show_status ;;
  deps)   start_deps; printf '\n' ;;
  admin)
    require_backend_env
    require_venv
    "${VENV_PY}" "${ROOT}/scripts/dev_admin.py" "$@"
    ;;
  doctor) doctor ;;
  logs)
    case "${1:-backend}" in
      backend)  log "跟踪 ${BACKEND_LOG}（Ctrl-C 退出）"; tail -n 50 -f "${BACKEND_LOG}" ;;
      frontend) log "跟踪 ${FRONTEND_LOG}（Ctrl-C 退出）"; tail -n 50 -f "${FRONTEND_LOG}" ;;
      *) die "未知服务：${1}（可选 backend|frontend）" ;;
    esac
    ;;
  ""|-h|--help|help) usage ;;
  *) usage; die "未知命令：${cmd}" ;;
esac
