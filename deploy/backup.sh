#!/usr/bin/env bash
# Lancé par le service systemd aismap-backup (déclenché par aismap-backup.timer, toutes
# les 6h, ou manuellement via `sudo systemctl start aismap-backup.service`).
#
# Envoie vers Proton Drive (via rclone) les .jsonl "clos", puis les supprime localement
# une fois le transfert confirmé (rclone move ne supprime jamais avant d'avoir vérifié le
# transfert). Ne touche JAMAIS le fichier du jour en cours d'écriture par aismap-stream :
# on ne le sélectionne pas par date dans le nom, mais par âge de dernière modification
# (--min-age), plus robuste — un fichier encore actif a forcément une mtime récente.
set -euo pipefail

DATA_DIR="${AISMAP_DATA_DIR:?AISMAP_DATA_DIR non défini}"
REMOTE="${AISMAP_RCLONE_REMOTE:-protondrive:aismap/stream}"
REMOTE_NAME="${REMOTE%%:*}"

if ! rclone listremotes 2>/dev/null | grep -q "^${REMOTE_NAME}:$"; then
    echo "[backup] remote rclone '${REMOTE_NAME}:' non configuré (lancer 'rclone config' en tant que $(whoami)) — rien à faire."
    exit 0
fi

echo "[backup] envoi vers $REMOTE des .jsonl clos (aucune écriture depuis >26h)..."
rclone move "$DATA_DIR/stream" "$REMOTE" \
    --include "ais-*.jsonl" \
    --min-age 26h \
    --transfers 4 \
    --stats-one-line -v
echo "[backup] terminé."
