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

Une fois que le Pi a collecté des données (quelques jours minimum pour une couverture correcte), tu peux générer la carte sur ta machine locale.

### 1. Récupérer les fichiers `.jsonl` du Pi

```
scp -r pi@IP_DU_PI:~/aismap-data/stream ./stream
```

Si SSH n'est pas disponible, tu peux aussi télécharger les fichiers depuis Google Drive (si la sauvegarde automatique est activée — voir `deploy/README.md`), mais seuls les `.jsonl` fermés depuis >26h y sont ; le fichier du jour en cours reste sur le Pi.

### 2. Installer les dépendances locales

```
pip install pandas numpy matplotlib folium
```

- `pandas`, `numpy`, `matplotlib` : obligatoires
- `folium` : optionnel (pour la carte HTML interactive ; sans lui, tu as juste le PNG)

### 3. Générer la carte

Depuis le dossier du dépôt :

```
python3 tools/coaster_heatmap.py stream-map --dir ./stream --png heatmap.png --html heatmap.html
```

Cela lit tous les `ais-*.jsonl`, rééchantillonne (5 min par défaut), accumule les heures de présence par cellule, et sort :
- `heatmap.png` : image PNG en échelle log (heures de présence par cellule)
- `heatmap.html` : carte interactive folium (si folium est installé)

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

### Réutiliser une grille sans relire les `.jsonl`

Si tu veux juste changer le titre/légende sans tout recalculer :

```
python3 tools/coaster_heatmap.py stream-map --dir ./stream --grid-out grid.npz
```

Puis :

```
python3 tools/coaster_heatmap.py map --grid grid.npz --png heatmap.png --title "Mon titre" --caption "Ma légende"
```

## Structure du code

- `tools/coaster_heatmap.py` : script principal (4 sous-commandes : `targets`, `stream`, `stream-map`, `map`)
- `deploy/` : scripts systemd pour le Raspberry Pi (installation, mise à jour, sauvegarde)
