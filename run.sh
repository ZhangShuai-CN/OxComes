#!/bin/bash
# 启动 OxComes 本地服务: http://127.0.0.1:8300
# 端口占用自愈：先 SIGTERM 优雅退出，3 秒不死再 SIGKILL。
# 残留根因提示：APScheduler 的同步调度 job 跑在非守护线程池上，Ctrl+C 时
# 若恰有 job 在网络 IO（源站频控重试等长任务）中，Python 退出会被线程
# join 拖住 → 进程不退 → 端口看起来仍被占用（TIME_WAIT 也会造成假象，
# lsof 无 PID 列出时即为此类，重启不受影响）。
cd "$(dirname "$0")"
PORT="${PORT:-8300}"

if [ ! -d .venv ]; then
    echo "首次运行: 创建虚拟环境并安装依赖..."
    uv venv --python python3.10 .venv
    uv pip install -r requirements.txt --python .venv/bin/python
fi

pids_on_port() { lsof -ti tcp:"$PORT" 2>/dev/null; }

wait_port_free() {   # 轮询等端口释放；$1=最长秒数；成功返回 0
    local i=0
    while [ -n "$(pids_on_port)" ] && [ "$i" -lt $(($1 * 10)) ]; do
        sleep 0.1; i=$((i + 1))
    done
    [ -z "$(pids_on_port)" ]
}

OLD_PID=$(pids_on_port)
if [ -n "$OLD_PID" ]; then
    echo "端口 $PORT 被旧实例 (PID $(echo $OLD_PID | tr '\n' ' '))占用，自动清理..."
    kill $OLD_PID 2>/dev/null                       # 先优雅 SIGTERM（lifespan 收尾）
    if ! wait_port_free 3; then
        echo "  优雅退出超时，强制结束..."
        kill -9 $(pids_on_port) 2>/dev/null         # 3 秒不死 → 强杀
        wait_port_free 2 || { echo "❌ 端口 $PORT 仍被占用，请手动检查: lsof -nP -iTCP:$PORT"; exit 1; }
    fi
fi

source .venv/bin/activate
exec uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "$PORT"
