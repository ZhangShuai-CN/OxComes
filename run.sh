#!/bin/bash
# 启动 OxComes 本地服务: http://127.0.0.1:8300
# （若端口被旧实例占用会自动清理后重启）
cd "$(dirname "$0")"
PORT="${PORT:-8300}"

if [ ! -d .venv ]; then
    echo "首次运行: 创建虚拟环境并安装依赖..."
    uv venv --python python3.10 .venv
    uv pip install -r requirements.txt --python .venv/bin/python
fi

OLD_PID=$(lsof -ti :"$PORT" 2>/dev/null)
if [ -n "$OLD_PID" ]; then
    echo "端口 $PORT 被旧实例 (PID $OLD_PID) 占用，自动清理..."
    kill -9 $OLD_PID 2>/dev/null
    sleep 1
fi

source .venv/bin/activate
exec uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "$PORT"
