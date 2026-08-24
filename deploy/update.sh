#!/usr/bin/env bash
# Lancé par le service systemd aismap-update (déclenché par le timer aismap-update.timer,
# ou manuellement via `sudo systemctl start aismap-update.service`).
#
# Ne fait RIEN si le dépôt n'a pas bougé (évite de redémarrer le collecteur, donc de
# recouper la connexion websocket, pour rien). S'il y a du nouveau : git reset --hard sur
# la branche suivie, régénère targets.csv depuis le CSV du dépôt (source unique, cf.
# tools/coasters_multi_flottes_imo_mmsi.csv), puis redémarre aismap-stream.
set -euo pipefail

REPO_DIR="${AISMAP_REPO_DIR:?AISMAP_REPO_DIR non défini}"
DATA_DIR="${AISMAP_DATA_DIR:?AISMAP_DATA_DIR non défini}"
BRANCH="${AISMAP_BRANCH:-main}"

cd "$REPO_DIR"
BEFORE=$(git rev-parse HEAD)
git fetch --quiet origin "$BRANCH"
# reset --hard (pas de merge/rebase) : le checkout sur le Pi doit toujours refléter
# exactement la branche distante, jamais diverger. Aucune donnée persistante n'est dans
# ce dossier (voir DATA_DIR), donc rien à perdre.
git reset --hard --quiet "origin/$BRANCH"
AFTER=$(git rev-parse HEAD)

if [ "$BEFORE" = "$AFTER" ]; then
    echo "Aucun changement ($AFTER)."
    exit 0
fi

echo "Mise à jour $BEFORE -> $AFTER."
echo "Régénération de targets.csv depuis coasters_multi_flottes_imo_mmsi.csv..."
"$DATA_DIR/venv/bin/python3" "$REPO_DIR/tools/coaster_heatmap.py" targets \
    --input "$REPO_DIR/tools/coasters_multi_flottes_imo_mmsi.csv" \
    --out "$DATA_DIR/targets.csv"

echo "Redémarrage du collecteur (aismap-stream)..."
sudo -n systemctl restart aismap-stream.service
