# Déploiement sur Raspberry Pi (100% à distance, sans accès physique)

Toutes les commandes ci-dessous sont montrées en `ssh pi@IP_DU_PI ...`, mais SSH n'est pas
la seule option : **Raspberry Pi Connect** (https://connect.raspberrypi.com, intégré à
Raspberry Pi OS) donne un accès shell distant depuis un navigateur, sans configurer de
port forwarding ni connaître l'IP du Pi — pratique derrière un NAT/CGNAT ou un pare-feu
restrictif. Une fois connecté (`rpi-connect signin` sur le Pi, puis session ouverte depuis
connect.raspberrypi.com), il suffit d'ouvrir un terminal distant et d'y taper la commande
qui suit `ssh pi@IP_DU_PI` dans les exemples ci-dessous — SSH reste la méthode la plus
simple si le Pi est déjà joignable sur le réseau local.

## Installation initiale (une seule fois)

Depuis ta machine :

    ssh pi@IP_DU_PI

Puis sur le Pi :

    git clone https://github.com/DADABOSS4/AISMap.git ~/aismap
    cd ~/aismap
    sudo ./deploy/install.sh

Le script demande la clé API aisstream.io (saisie masquée), installe Python/venv/les
dépendances, génère `targets.csv` depuis `tools/coasters_multi_flottes_imo_mmsi.csv`, puis
crée et démarre trois services systemd :

- **aismap-stream** : le collecteur AIS (`coaster_heatmap.py stream`), tourne en continu,
  redémarre seul en cas de plantage ou de coupure réseau.
- **aismap-update** (+ son timer) : toutes les 20 min, vérifie GitHub ; s'il y a du nouveau,
  applique le code (`git reset --hard` sur la branche suivie), régénère `targets.csv`, puis
  redémarre `aismap-stream`. S'il n'y a rien de nouveau, ne touche à rien (pas de coupure
  websocket pour rien).
- **aismap-backup** (+ son timer) : toutes les 6h, envoie vers Proton Drive les `.jsonl`
  clos (jamais celui du jour en cours) et les supprime localement une fois l'envoi
  confirmé — pour ne pas remplir la carte SD. Désactivé tant que rclone n'est pas
  configuré (voir plus bas).

## Ensuite, pour mettre à jour le Pi

Rien à faire sur le Pi : un `git push` sur `main` est repris automatiquement en moins de
20 minutes.

Pour forcer une mise à jour immédiate sans attendre le timer :

    ssh pi@IP_DU_PI sudo systemctl start aismap-update.service

## Commandes utiles (via SSH)

    ssh pi@IP_DU_PI systemctl status aismap-stream
    ssh pi@IP_DU_PI journalctl -u aismap-stream -f       # logs en direct du collecteur
    ssh pi@IP_DU_PI journalctl -u aismap-update -n 50    # historique des mises à jour
    ssh pi@IP_DU_PI 'ls ~/aismap-data/stream'            # fichiers .jsonl collectés

## Changer la clé API

    ssh pi@IP_DU_PI
    cd ~/aismap
    AISSTREAM_API_KEY=nouvelle_cle sudo -E ./deploy/install.sh

(relancer `install.sh` est sans risque : il détecte ce qui existe déjà et ne recrée que ce
qui manque, sauf la clé API explicitement fournie en variable d'environnement.)

## Sauvegarde vers Google Drive (libère l'espace du Pi)

`rclone` gère le transfert (backend Google Drive officiel côté rclone). `install.sh`
l'installe automatiquement, mais **la connexion à ton compte Google reste une étape
manuelle** — elle utilise OAuth2, bien plus sûr qu'un mot de passe en clair.

À faire une seule fois, en SSH, **en tant qu'utilisateur normal (pas root, pas de sudo)** :

    ssh pi@IP_DU_PI
    rclone config
    # > n (New remote)
    # > name: gdrive
    # > Storage: drive (chercher "Google Drive" dans la liste, numéro 14 env.)
    # > client_id/secret: laisser vides (utilise le client par défaut de rclone)
    # > scope: lecteur/writer plein accès
    # > service_account_file: laisser vide
    # > y (confirmer l'authentification) — le navigateur s'ouvrira pour approuver l'accès
    # > N pour ne pas utiliser d'ID équipe
    # > y (confirmer), q (quitter)

Puis réactiver l'installation pour que le timer démarre :

    cd ~/aismap && sudo ./deploy/install.sh

(`install.sh` détecte automatiquement le remote `gdrive:` et active
`aismap-backup.timer` — sans lui, la sauvegarde reste installée mais désactivée, et
`install.sh` te le rappelle à chaque exécution.)

Ce qui part : uniquement les `.jsonl` sans écriture depuis plus de 26h (donc jamais le
fichier du jour en cours), vers `gdrive:aismap/stream`, supprimés localement
seulement après confirmation du transfert par rclone.

Commandes utiles :

    ssh pi@IP_DU_PI sudo systemctl start aismap-backup.service   # forcer une sauvegarde
    ssh pi@IP_DU_PI journalctl -u aismap-backup -n 50             # historique des transferts

Pour changer la destination (dossier/nom de remote) : variable `AISMAP_RCLONE_REMOTE` au
moment d'installer, ex. `AISMAP_RCLONE_REMOTE=gdrive:autre/dossier sudo -E ./deploy/install.sh`.

## Arborescence sur le Pi

    ~/aismap/                   dépôt git (code) — écrasé/synchronisé à chaque update,
                                 ne JAMAIS y stocker quoi que ce soit à la main
    ~/aismap-data/               données persistantes, jamais touchées par git
      venv/                      environnement Python (pandas, numpy, websockets)
      aismap.env                 clé API aisstream.io (chmod 600, hors du dépôt)
      targets.csv                régénéré à chaque update depuis
                                  tools/coasters_multi_flottes_imo_mmsi.csv (source unique)
      mmsi_imo_cache.json         cache persistant MMSI->IMO, survit aux mises à jour/redémarrages
      stream/                     fichiers .jsonl collectés (ais-YYYY-MM-DD.jsonl), purgés
                                  au fil de l'eau vers Proton Drive une fois clos
    ~/.config/rclone/rclone.conf  identifiants Proton Drive (créé par `rclone config`,
                                  hors du dépôt, chmod 600 par rclone)

## Pourquoi cette séparation code / données

`~/aismap` reflète toujours exactement la branche GitHub (reset --hard à chaque update) :
rien de précieux ne doit y vivre. Tout ce qui doit survivre à une mise à jour — le cache
MMSI→IMO, les `.jsonl` déjà collectés, la clé API — est dans `~/aismap-data`, en dehors du
dépôt, donc jamais écrasé ni en conflit avec un `git pull`/`reset --hard`.

## Mettre en pause et réactiver le code

Mettre en pause le stream AIS, les updates depuis github et les sauvegardes cloud :
`sudo systemctl disable --now aismap-stream.service aismap-update.timer aismap-backup.timer`

Redémarrer le stream et les mise à jour du code depuis github :
`sudo systemctl enable --now aismap-stream.service aismap-update.timer aismap-backup.timer`
