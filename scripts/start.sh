#!/bin/bash

# cc2go 启动脚本 (Linux/Mac)

echo ""
echo "═══════════════════════════════════════"
echo "  cc2go 启动中..."
echo "═══════════════════════════════════════"
echo ""

cd "$(dirname "$0")/.."

if [ ! -f ".env" ]; then
    echo "[配置] .env 不存在，创建默认配置..."
    cp .env.example .env 2>/dev/null || echo "请手动创建 .env"
fi

echo "[启动] 正在启动 cc2go..."
python3 src/router.py
