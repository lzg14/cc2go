#!/bin/bash

echo ""
echo "═══════════════════════════════════════"
echo "  Starting cc2go..."
echo "═══════════════════════════════════════"
echo ""

cd "$(dirname "$0")/.."

if [ ! -f ".env" ]; then
    cp .env.example .env 2>/dev/null || echo "Create .env manually from .env.example"
fi

echo "Starting cc2go..."
python3 src/router.py
