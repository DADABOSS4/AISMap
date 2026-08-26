# AISMap

Heatmap de présence des coasters d'Europe du Nord à partir d'AIS (flux temps réel aisstream.io).

## Les 4 commandes du script

`tools/coaster_heatmap.py` s'utilise via 4 sous-commandes, une par étape du pipeline :

| Commande | Rôle | Qui la lance |
|---|---|---|
| `targets` | Régénère `targets.csv` (liste des navires à identifier par MMSI/IMO) depuis `coasters_multi_flottes_imo_mmsi.csv` | **Automatique** — appelée par `install.sh` et `update.sh` à chaque installation/mise à jour du Pi. À ne lancer toi-même que si tu testes en local. |
| `stream` | Collecteur temps réel : se connecte à aisstream.io et tourne en continu (jours/semaines), écrit un `.jsonl` par jour | **Automatique** — tourne en service systemd (`aismap-stream`) sur le Pi, cf. `deploy/README.md`. |
| `stream-map` | Construit la grille de présence directement depuis les `.jsonl` collectés et trace la carte (PNG + HTML) | **Manuel** — c'est la commande que tu lances en local pour générer la heatmap (voir ci-dessous). |
| `map` | Retrace la carte à partir d'une grille déjà sauvegardée (via `stream-map --grid-out`), sans relire les `.jsonl` | **Manuel, optionnel** — utile pour ajuster titre/légende sans tout recalculer (les `.jsonl` peuvent peser plusieurs dizaines de Mo après quelques jours de collecte). |

En pratique, au quotidien tu n'as besoin que de `stream-map` (section suivante) — `targets` et `stream` sont gérées par les services systemd, `map` n'est qu'un raccourci pour retracer plus vite.

## Déploiement

Voir [`deploy/README.md`](deploy/README.md) pour installer et configurer le collecteur sur un Raspberry Pi.

## Générer la heatmap locale

Une fois que le Pi a collecté des données (quelques jours minimum pour une couverture correcte), tu peux générer la carte sur ta machine locale. Le flux recommandé passe **uniquement par Google Drive** (pas de SSH/`scp` nécessaires pour cette partie) :

### 1. Récupérer les fichiers `.jsonl` depuis Google Drive

Va sur [drive.google.com](https://drive.google.com), ouvre le dossier `aismap/stream` (alimenté par la sauvegarde automatique du Pi, voir `deploy/README.md`), sélectionne les fichiers voulus (ou tout le dossier) et télécharge le zip. Extrais-le quelque part sur ta machine, ex. `~/Downloads/aismap-stream/`.

Par défaut, Drive ne contient que les `.jsonl` sans écriture depuis plus de 26h (jamais le fichier du jour en cours). Si tu veux des données plus fraîches avant de générer ta carte, force l'envoi du fichier du jour depuis le Pi (commande documentée dans `deploy/README.md`, section "Avoir des données plus fraîches que 26h") puis retélécharge.

### 2. Installer les dépendances locales

```
pip install -r requirements-map.txt
```

Voir [`requirements-map.txt`](requirements-map.txt) : `pandas`/`numpy`/`matplotlib` obligatoires, `folium` optionnel (carte HTML interactive), `cartopy` optionnel (fond de côtes/frontières sur le PNG — installation parfois plus lourde, décommente la ligne dans le fichier si tu la veux).

### 3. (optionnel) Config locale pour ne pas retaper les mêmes options

Copie [`aismap.local.env.example`](aismap.local.env.example) en `aismap.local.env` (à la racine du dépôt, ignoré par git) et renseigne au moins `AISMAP_DRIVE_DIR` (le dossier où tu as extrait le zip Drive à l'étape 1). Une fois ce fichier créé, `stream-map`/`map` l'utilisent automatiquement — un flag explicite sur la ligne de commande garde toujours la priorité.

### 4. Générer la carte

Depuis le dossier du dépôt (sans config locale, en précisant `--dir` toi-même) :

```
python3 tools/coaster_heatmap.py stream-map --dir ~/Downloads/aismap-stream
```

Ou, avec la config locale de l'étape 3 :

```
python3 tools/coaster_heatmap.py stream-map
```

Cela lit tous les `ais-*.jsonl`, rééchantillonne (5 min par défaut), accumule les heures de présence par cellule, affiche un résumé (période couverte, navires distincts, top 5 des cellules les plus fréquentées) et sort par défaut dans `out/` :
- `out/heatmap-<date du jour>.png` : image PNG en échelle log (fond de côtes si `cartopy` est installé)
- `out/heatmap-<date du jour>.html` : carte interactive folium (si folium est installé)

Le nommage horodaté évite d'écraser les cartes précédentes — pratique pour comparer l'évolution dans le temps.

### Options utiles

**Filtrage des navires** :
- `--mode id` : uniquement les navires identifiés avec certitude (MMSI/IMO connus dans `targets.csv`)
- `--mode generic` : uniquement ceux détectés par gabarit coaster (LOA 60-140 m, tirant d'eau 2-8 m)
- `--mode all` : les deux (défaut)

**Filtrage par activité** :
- `--cruising-status cruising` : uniquement navires en route (SOG > 0)
- `--cruising-status harbour` : uniquement à quai/mouillage (SOG == 0)

**Résolution spatiale** :
- `--cell-deg 0.01` : ~1,1 km par cellule (défaut) — réduis si peu de données
- `--cell-deg 0.05` : ~5,5 km par cellule — utile pour grille moins clairsemée

**Rééchantillonnage** :
- `--step-min 5` : par défaut, chaque position vaut 5 min (pas du message brut, évite le biais des navires à quai qui émettent plus)

Chacune de ces options peut aussi être réglée une fois pour toutes dans `aismap.local.env` (étape 3) — voir les commentaires du fichier `.example`.

### Réutiliser une grille sans relire les `.jsonl`

Si tu veux juste changer le titre/légende sans tout recalculer :

```
python3 tools/coaster_heatmap.py stream-map --dir ~/Downloads/aismap-stream --grid-out out/grid.npz
```

Puis :

```
python3 tools/coaster_heatmap.py map --grid out/grid.npz --title "Mon titre" --caption "Ma légende"
```

## Structure du code

- `tools/coaster_heatmap.py` : script principal (4 sous-commandes : `targets`, `stream`, `stream-map`, `map`)
- `deploy/` : scripts systemd pour le Raspberry Pi (installation, mise à jour, sauvegarde)
- `requirements-pi.txt` / `requirements-map.txt` : dépendances Python, respectivement pour le Pi (collecteur) et pour générer des cartes en local
- `aismap.local.env.example` : modèle de config locale optionnelle (génération de carte)
