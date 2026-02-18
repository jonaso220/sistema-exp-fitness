#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Netlify build script  –  Sistema EXP Fitness
#
# 1. Copies static assets into "public/" so they are served from Netlify CDN.
# 2. Copies Flask app source into the function directory so the serverless
#    handler can import everything it needs.
# 3. Installs Python dependencies into the function directory.
# ---------------------------------------------------------------------------
set -euo pipefail

FUNC_DIR="netlify/functions/api"

# ---- 1. Static assets for CDN ----
echo "==> Preparing static assets for CDN..."
mkdir -p public/static
cp -r static/* public/static/

# ---- 2. Copy application source into the function directory ----
echo "==> Copying application source to ${FUNC_DIR}..."
cp app.py          "${FUNC_DIR}/"
cp translations.py "${FUNC_DIR}/"
cp -r templates    "${FUNC_DIR}/"

# ---- 3. Install Python packages into the function directory ----
echo "==> Installing Python dependencies into ${FUNC_DIR}..."
pip install -r requirements.txt \
    --target "${FUNC_DIR}" \
    --upgrade \
    --quiet \
    --no-cache-dir \
    --implementation cp \
    --only-binary=:all: 2>/dev/null \
  || pip install -r requirements.txt \
    --target "${FUNC_DIR}" \
    --upgrade \
    --quiet \
    --no-cache-dir

echo "==> Build complete."
