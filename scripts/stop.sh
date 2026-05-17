#!/bin/bash
# cc2go 停止脚本 (Linux/Mac)
echo "Stopping cc2go..."
pkill -f "src/router\.py" 2>/dev/null
pkill -f "src/tray\.py" 2>/dev/null
echo "Done."
