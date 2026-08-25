#!/usr/bin/env bash
# Installation en une fois, entièrement via SSH, sur un Raspberry Pi (Debian/Raspberry Pi OS).
# À lancer avec sudo depuis une copie déjà clonée du dépôt :
#
#   git clone https://github.com/DADABOSS4/AISMap.git ~/aismap
#   cd ~/aismap
#   sudo ./deploy/install.sh
#
# Idempotent : relançable sans risque (ex. pour changer la clé API) après un premier
# passage — il détecte ce qui existe déjà et ne recrée que ce qui manque.
#
# Met en place :
#   - ~/aismap-data/            données persistantes (jamais touchées par git, cf. README)
#   - service systemd aismap-stream   : le collecteur, tourne en continu, redémarre seul
#   - service+timer aismap-update     : toutes les 10 min, tire GitHub et redémarre si besoin
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
    echo "Ce script doit être lancé avec sudo : sudo ./deploy/install.sh" >&2
    exit 1
fi

REAL_USER="${SUDO_USER:-$(logname 2>/dev/null || true)}"
if [ -z "$REAL_USER" ] || [ "$REAL_USER" = "root" ]; then
    echo "Impossible de déterminer l'utilisateur non-root cible (lance bien via 'sudo', pas en étant déjà root)." >&2
    exit 1
fi
REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${AISMAP_DATA_DIR:-$REAL_HOME/aismap-data}"
BRANCH="${AISMAP_BRANCH:-main}"

echo "Utilisateur cible     : $REAL_USER"
echo "Dépôt (code, git)     : $REPO_DIR"
echo "Données (persistant)  : $DATA_DIR"
echo

# --- Clé API aisstream.io ---
ENV_FILE="$DATA_DIR/aismap.env"
if [ -z "${AISSTREAM_API_KEY:-}" ] && [ -f "$ENV_FILE" ]; then
    echo "Clé API déjà configurée dans $ENV_FILE, conservée telle quelle."
    echo "(pour la changer : relance avec AISSTREAM_API_KEY=nouvelle_cle sudo -E ./deploy/install.sh)"
elif [ -z "${AISSTREAM_API_KEY:-}" ]; then
    read -rsp "Clé API aisstream.io : " AISSTREAM_API_KEY
    echo
    if [ -z "$AISSTREAM_API_KEY" ]; then
        echo "Clé API vide, abandon." >&2
        exit 1
    fi
fi

# --- Paquets système ---
echo "Installation des paquets système (git, python3-venv)..."
apt-get update -qq
apt-get install -y -qq git python3-venv python3-pip >/dev/null

# --- Dossier de données (jamais écrasé par un git pull, car hors de $REPO_DIR) ---
sudo -u "$REAL_USER" mkdir -p "$DATA_DIR/stream"

# --- Environnement Python ---
if [ ! -d "$DATA_DIR/venv" ]; then
    echo "Création du venv Python..."
    sudo -u "$REAL_USER" python3 -m venv "$DATA_DIR/venv"
fi
sudo -u "$REAL_USER" "$DATA_DIR/venv/bin/pip" install --quiet --upgrade pip
sudo -u "$REAL_USER" "$DATA_DIR/venv/bin/pip" install --quiet pandas numpy websockets

# --- Clé API : fichier d'environnement séparé du code, jamais dans git ---
if [ -n "${AISSTREAM_API_KEY:-}" ]; then
    printf 'AISSTREAM_API_KEY=%s\n' "$AISSTREAM_API_KEY" > "$ENV_FILE"
    chown "$REAL_USER:$REAL_USER" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
fi
if [ ! -f "$ENV_FILE" ]; then
    echo "Aucune clé API disponible et aucun $ENV_FILE existant — abandon." >&2
    exit 1
fi

# --- targets.csv initial (régénéré ensuite à chaque update par deploy/update.sh) ---
echo "Génération initiale de targets.csv..."
sudo -u "$REAL_USER" "$DATA_DIR/venv/bin/python3" "$REPO_DIR/tools/coaster_heatmap.py" targets \
    --input "$REPO_DIR/tools/coasters_multi_flottes_imo_mmsi.csv" \
    --out "$DATA_DIR/targets.csv"

# --- Autorisation ciblée : le service de mise à jour (non-root) peut redémarrer
#     UNIQUEMENT aismap-stream.service, rien d'autre, sans mot de passe. ---
SYSTEMCTL_BIN="$(command -v systemctl)"
SUDOERS_FILE=/etc/sudoers.d/aismap-update
cat > "$SUDOERS_FILE" <<EOF
$REAL_USER ALL=(root) NOPASSWD: $SYSTEMCTL_BIN restart aismap-stream.service
EOF
chmod 440 "$SUDOERS_FILE"
visudo -cf "$SUDOERS_FILE" >/dev/null

# --- Service : collecteur AIS en continu ---
cat > /etc/systemd/system/aismap-stream.service <<EOF
[Unit]
Description=AISMap - collecteur AIS temps reel (coaster_heatmap.py stream)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$REAL_USER
WorkingDirectory=$REPO_DIR
EnvironmentFile=$ENV_FILE
Environment=AISMAP_REPO_DIR=$REPO_DIR
Environment=AISMAP_DATA_DIR=$DATA_DIR
Environment=PYTHONUNBUFFERED=1
ExecStart=/bin/bash $REPO_DIR/deploy/run_stream.sh
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# --- Service : vérification + application des mises à jour GitHub ---
cat > /etc/systemd/system/aismap-update.service <<EOF
[Unit]
Description=AISMap - verifie et applique les mises a jour depuis GitHub

[Service]
Type=oneshot
User=$REAL_USER
Environment=AISMAP_REPO_DIR=$REPO_DIR
Environment=AISMAP_DATA_DIR=$DATA_DIR
Environment=AISMAP_BRANCH=$BRANCH
ExecStart=/bin/bash $REPO_DIR/deploy/update.sh
EOF

# --- Timer : déclenche aismap-update toutes les 20 min ---
cat > /etc/systemd/system/aismap-update.timer <<EOF
[Unit]
Description=AISMap - verifie les mises a jour GitHub toutes les 20 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=20min
Persistent=true

[Install]
WantedBy=timers.target
EOF

# --- rclone (sauvegarde des .jsonl clos vers Proton Drive, pour libérer l'espace du Pi) ---
if ! command -v rclone >/dev/null 2>&1; then
    echo "Installation de rclone (script officiel)..."
    curl -fsSL https://rclone.org/install.sh | bash >/dev/null
fi

RCLONE_REMOTE="${AISMAP_RCLONE_REMOTE:-protondrive:aismap/stream}"
RCLONE_REMOTE_NAME="${RCLONE_REMOTE%%:*}"

cat > /etc/systemd/system/aismap-backup.service <<EOF
[Unit]
Description=AISMap - envoie les .jsonl clos vers Proton Drive (rclone)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$REAL_USER
Environment=AISMAP_DATA_DIR=$DATA_DIR
Environment=AISMAP_RCLONE_REMOTE=$RCLONE_REMOTE
ExecStart=/bin/bash $REPO_DIR/deploy/backup.sh
EOF

cat > /etc/systemd/system/aismap-backup.timer <<EOF
[Unit]
Description=AISMap - declenche la sauvegarde Proton Drive toutes les 6 heures

[Timer]
OnBootSec=10min
OnUnitActiveSec=6h
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable aismap-stream.service
# restart (pas juste enable --now) : si le service tournait déjà avec une unité plus
# ancienne, il faut le relancer pour qu'il prenne en compte les changements ci-dessus
# (ex. PYTHONUNBUFFERED) — un simple `enable --now` sur un service déjà actif ne fait rien.
systemctl restart aismap-stream.service
systemctl enable --now aismap-update.timer

if sudo -H -u "$REAL_USER" rclone listremotes 2>/dev/null | grep -q "^${RCLONE_REMOTE_NAME}:$"; then
    systemctl enable --now aismap-backup.timer
    BACKUP_STATUS="activée (remote '$RCLONE_REMOTE_NAME:' détecté)"
else
    systemctl disable aismap-backup.timer >/dev/null 2>&1 || true
    BACKUP_STATUS="EN ATTENTE : remote rclone '$RCLONE_REMOTE_NAME:' non configuré.
    1) en tant que $REAL_USER (pas root, pas de sudo) : rclone config
       -> New remote, nom '$RCLONE_REMOTE_NAME', type 'protondrive', identifiants Proton
    2) puis : sudo $REPO_DIR/deploy/install.sh (relançable sans risque)"
fi

echo
echo "Installation terminée."
echo "  Statut du collecteur    : systemctl status aismap-stream"
echo "  Logs en direct           : journalctl -u aismap-stream -f"
echo "  Forcer une mise à jour   : sudo systemctl start aismap-update.service"
echo "  Sauvegarde Proton Drive  : $BACKUP_STATUS"
echo "  Logs de mise à jour     : journalctl -u aismap-update"
