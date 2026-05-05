#!/usr/bin/env bash
# ============================================================
# WordPress Site Manager - 一键启动脚本
# ============================================================
set -o pipefail

# ---- 颜色 ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }

# ---- 配置 ----
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="${PROJECT_DIR}/venv"
LOG_FILE="${PROJECT_DIR}/logs/app.log"
PID_FILE="${PROJECT_DIR}/app.pid"
HOST="${WP_HOST:-0.0.0.0}"
PORT="${WP_PORT:-8011}"
WORKERS="${WP_WORKERS:-4}"

# ---- 检查 Python ----
check_python() {
    if command -v python3 &>/dev/null; then
        PYTHON="python3"
    elif command -v python &>/dev/null; then
        PYTHON="python"
    else
        err "未找到 Python，请先安装 Python 3.10+"
        exit 1
    fi

    PY_VERSION=$($PYTHON -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
    if [ "$PY_MAJOR" -lt 3 ]; then
        err "Python 版本过低 ($PY_VERSION)，需要 3.10+"
        exit 1
    fi
    ok "Python $PY_VERSION"
}

# ---- 创建虚拟环境 ----
setup_venv() {
    if [ ! -d "$VENV_DIR" ]; then
        info "创建 Python 虚拟环境..."
        $PYTHON -m venv "$VENV_DIR"
        if [ $? -ne 0 ]; then
            err "创建虚拟环境失败"
            exit 1
        fi
        ok "虚拟环境已创建"
    fi
    source "$VENV_DIR/bin/activate"
    ok "虚拟环境已激活"
}

# ---- 安装依赖 ----
install_deps() {
    info "检查并安装 Python 依赖..."
    pip install --upgrade pip -q
    pip install -r "${PROJECT_DIR}/requirements.txt" -q
    if [ $? -ne 0 ]; then
        err "依赖安装失败"
        exit 1
    fi
    ok "依赖已安装"
}

# ---- 初始化目录 ----
init_dirs() {
    mkdir -p "${PROJECT_DIR}/backend/plugins"
    mkdir -p "${PROJECT_DIR}/logs"
    ok "目录已初始化"
}

# ---- 停止已有服务 ----
stop_existing() {
    if [ -f "$PID_FILE" ]; then
        OLD_PID=$(cat "$PID_FILE")
        if kill -0 "$OLD_PID" 2>/dev/null; then
            info "停止已有服务 (PID: $OLD_PID)..."
            kill "$OLD_PID" 2>/dev/null
            sleep 2
            if kill -0 "$OLD_PID" 2>/dev/null; then
                kill -9 "$OLD_PID" 2>/dev/null
            fi
        fi
        rm -f "$PID_FILE"
    fi
}

# ---- 开发模式启动 ----
start_dev() {
    info "以开发模式启动 (端口: $PORT)..."
    cd "${PROJECT_DIR}/backend"
    PORT=$PORT python3 app.py
}

# ---- 生产模式启动 ----
start_prod() {
    info "以生产模式启动 (端口: $PORT, Workers: $WORKERS)..."
    cd "${PROJECT_DIR}/backend"

    nohup gunicorn \
        --bind "${HOST}:${PORT}" \
        --workers "$WORKERS" \
        --worker-class gevent \
        --timeout 120 \
        --pid "$PID_FILE" \
        --access-logfile "${LOG_FILE}.access" \
        --error-logfile "${LOG_FILE}.error" \
        --chdir "${PROJECT_DIR}/backend" \
        "app:create_app()" \
        >> "$LOG_FILE" 2>&1 &

    GUNICORN_PID=$!
    echo "$GUNICORN_PID" > "$PID_FILE"
    sleep 2

    if kill -0 "$GUNICORN_PID" 2>/dev/null; then
        ok "服务已启动 (PID: $GUNICORN_PID, 端口: $PORT)"
        ok "访问地址: http://localhost:$PORT"
        ok "日志文件: $LOG_FILE"
    else
        err "服务启动失败，请查看日志: $LOG_FILE"
        exit 1
    fi
}

# ---- 状态检查 ----
status() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            ok "服务运行中 (PID: $PID)"
            # Quick health check
            HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:${PORT}/" 2>/dev/null || echo "000")
            if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "304" ]; then
                ok "HTTP 健康检查通过 (HTTP $HTTP_CODE)"
            else
                warn "HTTP 健康检查异常 (HTTP $HTTP_CODE)"
            fi
            return 0
        else
            warn "服务未运行 (PID 文件存在但进程已退出)"
            rm -f "$PID_FILE"
            return 1
        fi
    else
        warn "服务未运行"
        return 1
    fi
}

# ---- 停止服务 ----
stop() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            info "停止服务 (PID: $PID)..."
            kill "$PID" 2>/dev/null
            sleep 3
            if kill -0 "$PID" 2>/dev/null; then
                warn "强制停止..."
                kill -9 "$PID" 2>/dev/null
            fi
            rm -f "$PID_FILE"
            ok "服务已停止"
        else
            warn "进程已退出"
            rm -f "$PID_FILE"
        fi
    else
        warn "PID 文件不存在，尝试查找 gunicorn 进程..."
        pkill -f "gunicorn.*app:create_app" 2>/dev/null && ok "已停止 gunicorn" || warn "未找到运行中的服务"
    fi
}

# ---- 重启 ----
restart() {
    stop
    sleep 1
    start_prod
}

# ---- 帮助 ----
usage() {
    echo "WordPress 站点管理器 - 一键部署脚本"
    echo ""
    echo "用法: $0 <命令> [选项]"
    echo ""
    echo "命令:"
    echo "  dev       开发模式启动 (前台运行，自动重载)"
    echo "  start     生产模式启动 (后台运行，gunicorn)"
    echo "  stop      停止服务"
    echo "  restart   重启服务"
    echo "  status    查看服务状态"
    echo "  install   仅安装依赖"
    echo ""
    echo "环境变量:"
    echo "  WP_PORT       服务端口 (默认: 8011)"
    echo "  WP_HOST       绑定地址 (默认: 0.0.0.0)"
    echo "  WP_WORKERS    gunicorn worker 数量 (默认: 4)"
    echo ""
    echo "示例:"
    echo "  $0 dev                    # 开发模式"
    echo "  $0 start                  # 生产模式启动"
    echo "  WP_PORT=9000 $0 start     # 指定端口"
    echo "  $0 status                 # 查看状态"
    echo "  $0 stop                   # 停止"
}

# ---- 主逻辑 ----
main() {
    local cmd="${1:-help}"

    case "$cmd" in
        dev)
            check_python
            setup_venv
            install_deps
            init_dirs
            stop_existing
            start_dev
            ;;
        start)
            check_python
            setup_venv
            install_deps
            init_dirs
            start_prod
            ;;
        stop)
            stop
            ;;
        restart)
            check_python
            setup_venv
            install_deps
            restart
            ;;
        status)
            status
            ;;
        install)
            check_python
            setup_venv
            install_deps
            init_dirs
            ok "依赖安装完成"
            ;;
        help|--help|-h)
            usage
            ;;
        *)
            err "未知命令: $cmd"
            usage
            exit 1
            ;;
    esac
}

main "$@"
