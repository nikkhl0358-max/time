#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/frontend"
npm ci --no-audit --no-fund
npm run build
mkdir -p ../static
cp -r dist/. ../static/
echo "Frontend built."
