#!/bin/bash
# cc2go 停止脚本 (Linux/Mac)
cd "$(dirname "$0")/.."

if [ -f data/cc2go.pid ]; then
    PID=$(cat data/cc2go.pid)
    kill "$PID" 2>/dev/null && echo "cc2go stopped (PID=$PID)." || echo "Process $PID not found."
    rm -f data/cc2go.pid
else
    echo "No PID file. Trying pkill..."
    pkill -f "src/router\.py" 2>/dev/null
    pkill -f "src/tray\.py" 2>/dev/null
    echo "Done."
fi
