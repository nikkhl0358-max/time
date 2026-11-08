#!/usr/bin/env bash
# Локальная сборка для проверки. На Amvera фронтенд собирается автоматически Dockerfile'ом.
set -euo pipefail
cd "$(dirname "$0")"
python3 tools/check_scheduler_parity.py
cd frontend
npm install --no-audit --no-fund
npm run build
rm -rf ../static
mkdir -p ../static
cp -r dist/* ../static/
echo "Готово: static/ обновлён. На Amvera этот шаг выполняет Dockerfile автоматически."
