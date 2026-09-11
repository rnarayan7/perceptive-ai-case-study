#!/usr/bin/env bash
# Install, build, and serve the Next.js frontend on :3000.
# It reads the API base from NEXT_PUBLIC_API_BASE (default http://127.0.0.1:8001).
set -euo pipefail
cd "$(dirname "$0")/../frontend"
npm install
npm run build
PORT="${PORT:-3000}" exec npm run start
