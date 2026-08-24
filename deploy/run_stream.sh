#!/usr/bin/env bash
# Lancé par le service systemd aismap-stream (ExecStart). Ne pas exécuter à la main
# sauf pour déboguer : les chemins/la clé API arrivent via les variables d'env
# injectées par systemd (AISMAP_REPO_DIR, AISMAP_DATA_DIR, AISSTREAM_API_KEY).
set -euo pipefail

REPO_DIR="${AISMAP_REPO_DIR:?AISMAP_REPO_DIR non défini}"
DATA_DIR="${AISMAP_DATA_DIR:?AISMAP_DATA_DIR non défini}"

exec "$DATA_DIR/venv/bin/python3" "$REPO_DIR/tools/coaster_heatmap.py" stream \
    --key "${AISSTREAM_API_KEY:?AISSTREAM_API_KEY non défini}" \
    --targets "$DATA_DIR/targets.csv" \
    --out "$DATA_DIR/stream" \
    --cache "$DATA_DIR/mmsi_imo_cache.json"
