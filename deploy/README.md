# Déploiement sur Raspberry Pi (100% via SSH, sans accès physique)

## Installation initiale (une seule fois)

Depuis ta machine :

    ssh pi@IP_DU_PI

Puis sur le Pi :

    git clone https://github.com/DADABOSS4/AISMap.git ~/aismap
    cd ~/aismap
    sudo ./deploy/install.sh

Le script demande la clé API aisstream.io (saisie masquée), installe Python/venv/les
dépendances, génère `targets.csv` depuis `tools/coasters_multi_flottes_imo_mmsi.csv`, puis
crée et démarre deux services systemd :

- **aismap-stream** : le collecteur AIS (`coaster_heatmap.py stream`), tourne en continu,
  redémarre seul en cas de plantage ou de coupure réseau.
- **aismap-update** (+ son timer) : toutes les 10 min, vérifie GitHub ; s'il y a du nouveau,
  applique le code (`git reset --hard` sur la branche suivie), régénère `targets.csv`, puis
  redémarre `aismap-stream`. S'il n'y a rien de nouveau, ne touche à rien (pas de coupure
  websocket pour rien).

## Ensuite, pour mettre à jour le Pi

Rien à faire sur le Pi : un `git push` sur `main` est repris automatiquement en moins de
10 minutes.

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

## Arborescence sur le Pi

    ~/aismap/                   dépôt git (code) — écrasé/synchronisé à chaque update,
                                 ne JAMAIS y stocker quoi que ce soit à la main
    ~/aismap-data/               données persistantes, jamais touchées par git
      venv/                      environnement Python (pandas, numpy, websockets)
      aismap.env                 clé API aisstream.io (chmod 600, hors du dépôt)
      targets.csv                régénéré à chaque update depuis
                                  tools/coasters_multi_flottes_imo_mmsi.csv (source unique)
      mmsi_imo_cache.json         cache persistant MMSI->IMO, survit aux mises à jour/redémarrages
      stream/                     fichiers .jsonl collectés (ais-YYYY-MM-DD.jsonl)

## Pourquoi cette séparation code / données

`~/aismap` reflète toujours exactement la branche GitHub (reset --hard à chaque update) :
rien de précieux ne doit y vivre. Tout ce qui doit survivre à une mise à jour — le cache
MMSI→IMO, les `.jsonl` déjà collectés, la clé API — est dans `~/aismap-data`, en dehors du
dépôt, donc jamais écrasé ni en conflit avec un `git pull`/`reset --hard`.
