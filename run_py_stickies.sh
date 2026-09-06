#!/bin/bash

# PyStickies 控制脚本
# 支持 start、stop、restart、status 命令

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/pystickies.pid"
LOG_FILE="$SCRIPT_DIR/pystickies.log"
APP_NAME="PyStickies"
PYTHON_CMD="python"
PLIST_LABEL="com.pystickies.app"
PLIST_FILE="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"

# 检查Python命令
if ! command -v "$PYTHON_CMD" &> /dev/null; then
    echo "错误: Python 未找到，请安装 Python"
    exit 1
fi

# 检查sticky_notes.py是否存在
if [ ! -f "$SCRIPT_DIR/sticky_notes.py" ]; then
    echo "错误: sticky_notes.py 未找到"
    exit 1
fi

# 获取进程ID
get_pid() {
    if [ -f "$PID_FILE" ]; then
        cat "$PID_FILE"
    else
        echo ""
    fi
}

# 检查进程是否在运行
is_running() {
    local pid=$(get_pid)
    if [ -n "$pid" ]; then
        if ps -p "$pid" > /dev/null 2>&1; then
            return 0
        else
            rm -f "$PID_FILE"
        fi
    fi
    return 1
}

# 启动应用
start_app() {
    if is_running; then
        echo "$APP_NAME 已经在运行 (PID: $(get_pid))"
        exit 0
    fi
    
    echo "正在启动 $APP_NAME..."
    # -u：无缓冲输出，日志实时可见
    nohup "$PYTHON_CMD" -u "$SCRIPT_DIR/sticky_notes.py" > "$LOG_FILE" 2>&1 &
    local pid=$!
    echo $pid > "$PID_FILE"
    
    # 等待应用启动
    sleep 2
    
    if is_running; then
        echo "$APP_NAME 启动成功 (PID: $pid)"
    else
        echo "$APP_NAME 启动失败，查看日志: $LOG_FILE"
        exit 1
    fi
}

# 停止应用
stop_app() {
    if ! is_running; then
        echo "$APP_NAME 没有在运行"
        return 0
    fi
    
    local pid=$(get_pid)
    echo "正在停止 $APP_NAME (PID: $pid)..."
    
    # 温柔地终止进程
    kill "$pid" 2>/dev/null
    
    # 等待最多5秒让应用优雅退出
    local count=0
    while is_running && [ $count -lt 10 ]; do
        sleep 0.5
        count=$((count + 1))
    done
    
    # 如果还在运行，强制终止
    if is_running; then
        echo "强制终止 $APP_NAME..."
        kill -9 "$pid" 2>/dev/null
        sleep 1
    fi
    
    rm -f "$PID_FILE"
    
    if ! is_running; then
        echo "$APP_NAME 已停止"
    else
        echo "停止 $APP_NAME 失败"
        exit 1
    fi
}

# 重启应用
restart_app() {
    echo "正在重启 $APP_NAME..."
    stop_app
    sleep 1
    start_app
}

# 显示状态
status_app() {
    if is_running; then
        local pid=$(get_pid)
        echo "$APP_NAME 正在运行 (PID: $pid)"
        echo "日志文件: $LOG_FILE"
        echo "数据文件: $SCRIPT_DIR/notes_data.json"
    else
        echo "$APP_NAME 没有在运行"
    fi
}

# 安装开机自启（LaunchAgent）
install_agent() {
    local python_path
    python_path="$(command -v "$PYTHON_CMD")"
    if [ -z "$python_path" ]; then
        echo "错误: 找不到 $PYTHON_CMD 的绝对路径"
        exit 1
    fi

    mkdir -p "$HOME/Library/LaunchAgents"
    cat > "$PLIST_FILE" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$python_path</string>
        <string>$SCRIPT_DIR/sticky_notes.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$SCRIPT_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$LOG_FILE</string>
    <key>StandardErrorPath</key>
    <string>$LOG_FILE</string>
</dict>
</plist>
EOF

    launchctl unload "$PLIST_FILE" 2>/dev/null
    launchctl load "$PLIST_FILE"
    echo "已安装开机自启: $PLIST_FILE"
    echo "日志输出: $LOG_FILE"
}

# 卸载开机自启
uninstall_agent() {
    if [ -f "$PLIST_FILE" ]; then
        launchctl unload "$PLIST_FILE" 2>/dev/null
        rm -f "$PLIST_FILE"
        echo "已取消开机自启"
    else
        echo "未安装开机自启"
    fi
}

# 显示帮助
show_help() {
    echo "用法: $0 {start|stop|restart|status|install|uninstall|help}"
    echo ""
    echo "命令:"
    echo "  start     启动 $APP_NAME"
    echo "  stop      停止 $APP_NAME"
    echo "  restart   重启 $APP_NAME"
    echo "  status    显示运行状态"
    echo "  install   安装开机自启（LaunchAgent）"
    echo "  uninstall 取消开机自启"
    echo "  help      显示此帮助信息"
    echo ""
    echo "文件位置:"
    echo "  主程序: $SCRIPT_DIR/sticky_notes.py"
    echo "  日志:   $LOG_FILE"
    echo "  数据:   $SCRIPT_DIR/notes_data.json"
    echo "  PID:    $PID_FILE"
}

# 主逻辑
case "${1:-help}" in
    start)
        start_app
        ;;
    stop)
        stop_app
        ;;
    restart)
        restart_app
        ;;
    status)
        status_app
        ;;
    install)
        install_agent
        ;;
    uninstall)
        uninstall_agent
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        echo "错误: 未知命令 '$1'"
        echo ""
        show_help
        exit 1
        ;;
esac