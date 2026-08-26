#!/usr/bin/env bash
# Lancé par le service systemd aismap-backup (déclenché par aismap-backup.timer, toutes
# les 6h, ou manuellement via `sudo systemctl start aismap-backup.service`).
#
# Envoie vers Google Drive (via rclone) les .jsonl "clos", puis les supprime localement
# une fois le transfert confirmé (rclone move ne supprime jamais avant d'avoir vérifié le
# transfert). Par défaut, ne touche JAMAIS le fichier du jour en cours d'écriture par
# aismap-stream : on ne le sélectionne pas par date dans le nom, mais par âge de dernière
# modification (--min-age), plus robuste — un fichier encore actif a forcément une mtime
# récente.
#
# AISMAP_BACKUP_INCLUDE_TODAY=1 (via `sudo systemctl start aismap-backup-full.service`,
# cf. install.sh) force aussi l'envoi du fichier du jour en cours, pour avoir des données
# plus fraîches que 26h avant de générer une carte. Ce fichier est activement réécrit par
# aismap-stream pendant le transfert : --local-no-check-updated désactive le refus par
# défaut de rclone ("can't copy - source file is being updated") sur un fichier dont la
# taille change en cours de lecture. Risque accepté : les toutes dernières lignes ajoutées
# pendant la fenêtre de transfert (quelques secondes) peuvent être perdues, le fichier
# local étant supprimé après le transfert même s'il a grossi entre-temps — rien de grave,
# elles seront simplement absentes de cette sauvegarde (le fichier repart de zéro et se
# reremplit normalement à la prochaine écriture d'aismap-stream).
set -euo pipefail

DATA_DIR="${AISMAP_DATA_DIR:?AISMAP_DATA_DIR non défini}"
REMOTE="${AISMAP_RCLONE_REMOTE:-gdrive:aismap/stream}"
REMOTE_NAME="${REMOTE%%:*}"

if ! rclone listremotes 2>/dev/null | grep -q "^${REMOTE_NAME}:$"; then
    echo "[backup] remote rclone '${REMOTE_NAME}:' non configuré (lancer 'rclone config' en tant que $(whoami)) — rien à faire."
    exit 0
fi

EXTRA_ARGS=(--min-age 26h)
if [ "${AISMAP_BACKUP_INCLUDE_TODAY:-0}" = "1" ]; then
    EXTRA_ARGS=(--local-no-check-updated)
    echo "[backup] AISMAP_BACKUP_INCLUDE_TODAY=1 : envoi aussi du fichier du jour en cours (encore en écriture)."
fi

echo "[backup] envoi vers $REMOTE des .jsonl..."
rclone move "$DATA_DIR/stream" "$REMOTE" \
    --include "ais-*.jsonl" \
    "${EXTRA_ARGS[@]}" \
    --transfers 4 \
    --stats-one-line -v
echo "[backup] terminé."
