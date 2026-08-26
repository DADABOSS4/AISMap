#!/usr/bin/env python3

"""
coaster_heatmap.py — heatmap de présence des coasters d'Europe du Nord à partir d'AIS
(flux temps réel aisstream.io).

targets — construit targets.csv EXCLUSIVEMENT à partir d'un CSV d'entrée liste Nom/IMO/MMSI
          (ex. coasters_multi_flottes_imo_mmsi.csv). Aucune autre source, aucune mise à jour
          automatique/continue : le contenu de targets.csv reflète strictement le CSV d'entrée
          fourni, tel quel, à chaque exécution.
    --input FICHIER      CSV d'entrée Nom;IMO;MMSI[;Armateur] (défaut : coasters_multi_flottes_imo_mmsi.csv)
    --out FICHIER        fichier de sortie (défaut : targets.csv)

stream — collecteur temps réel, boucle infinie (Ctrl+C pour arrêter), à laisser tourner
         plusieurs jours pour couvrir toute l'Europe du Nord. Écrit un .jsonl par jour UTC
         dans --out (ex. ais-2026-08-18.jsonl), une ligne par position retenue.
    --key CLÉ           clé API aisstream.io (obligatoire)
    --targets FICHIER   liste des navires cibles pour le matching (défaut : targets.csv) ;
                        le matching par nom seul a été retiré (trop bruité, n'apportait
                        rien face au filtre générique par gabarit)
    --out DOSSIER       dossier de sortie des .jsonl (défaut : stream)
    --cache FICHIER     cache persistant MMSI->IMO, réutilisé d'une session à l'autre
                        (défaut : mmsi_imo_cache.json)
   Chaque position retenue est étiquetée --match :
     'id'      : MMSI présent dans targets.csv OU IMO connu (via ShipStaticData, cette
                session ou une précédente grâce à --cache) présent dans targets.csv
                (fiable)
     'generic' : cargo au gabarit coaster (LOA 60-140 m, tirant d'eau 2-8 m, type AIS
                Cargo 70-79), indépendamment de targets.csv
   Les tankers (type AIS 80-89) sont exclus systématiquement, quel que soit le match.

   Pourquoi matcher aussi par IMO : le MMSI change quand un navire change de pavillon
   (constaté sur ~19 navires Wilson ASA le 18/08/2026 — Barbados/Norvège -> Bahamas),
   alors que l'IMO ne change JAMAIS. Les messages ShipStaticData du flux portent l'IMO
   (champ ImoNumber, 0 pour les navires <300 GT qui n'en ont légalement pas) mais sont
   nettement plus rares que les PositionReport (~1 pour 7 dans un test du 18/08/2026).
   Le cache --cache accumule au fil des sessions les couples MMSI->IMO observés en
   direct, ce qui reconnaît un navire cible même si son MMSI a changé depuis la
   construction de targets.csv, sans jamais avoir à corriger ce dernier à la main.

stream-map — construit la grille (heures de présence) directement depuis les .jsonl de
             'stream' et trace la carte. Affiche aussi un résumé (période couverte, top 5
             des cellules les plus fréquentées) après calcul de la grille.
    --dir DOSSIER           dossier contenant les ais-*.jsonl (défaut : stream, ou
                            AISMAP_DRIVE_DIR dans aismap.local.env)
    --mode {id,generic,all} filtre sur le champ 'match' (défaut : all, ou AISMAP_MODE)
    --cruising-status {cruising,harbour}
                            filtre sur le SOG : 'cruising' garde SOG>0 (navire en route),
                            'harbour' garde SOG==0 (navire à quai/mouillage) ; non renseigné
                            (défaut, ou AISMAP_CRUISING_STATUS) = les deux confondus. Une
                            position à SOG inconnu est écartée dès que ce filtre est actif.
    --step-min N            pas de rééchantillonnage en minutes (défaut : 5, ou AISMAP_STEP_MIN)
    --cell-deg V             taille de cellule en ° de latitude (défaut : 0.01, ~1,1 km, ou
                            AISMAP_CELL_DEG) — à augmenter (ex. 0.05) si peu de données
                            collectées, sinon la grille sera trop clairsemée
    --grid-out FICHIER       si fourni, sauvegarde aussi la grille en .npz, réutilisable
                            ensuite avec 'map --grid' sans relire les .jsonl (défaut : aucun)
    --png / --html / --title / --caption   mêmes réglages que 'map'

map — trace la heatmap (PNG) et, si folium est installé, une carte HTML interactive,
      à partir d'un grid.npz déjà construit (par 'stream-map --grid-out').
    --grid FICHIER      grille à tracer (défaut : out/grid-<date du jour>.npz)
    --png FICHIER       image de sortie (défaut : out/heatmap-<date du jour>.png)
    --html FICHIER      carte interactive de sortie, ignorée si folium absent
                        (défaut : out/heatmap-<date du jour>.html)
    --title TEXTE       titre du graphique
    --caption TEXTE     légende en bas de figure

Config locale optionnelle : si un fichier aismap.local.env existe dans le dossier courant
(voir aismap.local.env.example), ses valeurs préremplissent les défauts ci-dessus
(AISMAP_DRIVE_DIR, AISMAP_MAP_OUT_DIR, AISMAP_MODE, AISMAP_CRUISING_STATUS,
AISMAP_STEP_MIN, AISMAP_CELL_DEG) — un flag explicite sur la ligne de commande prime
toujours dessus.

Métrique de la heatmap : HEURES DE PRÉSENCE par cellule (comme EMODnet), pas le nombre de
messages AIS bruts. Un navire à quai émet à une cadence différente d'un navire en route :
compter les messages bruts biaiserait la carte vers les ports. On rééchantillonne donc chaque
trace à pas fixe (défaut 5 min, réglable via --step-min) et chaque point retenu vaut
exactement ce pas de temps.

Dépendances : pandas, numpy, matplotlib (obligatoires) ; folium, cartopy et websockets
(optionnels — folium pour la carte HTML, cartopy pour le fond de côtes sur le PNG,
websockets pour 'stream'). Voir requirements-map.txt / requirements-pi.txt.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------------------
# Constantes
# --------------------------------------------------------------------------------------

# Emprise par défaut : Manche est / mer du Nord / Skagerrak-Kattegat / Baltique ouest.
BBOX = dict(lat_min=48.0, lat_max=64.0, lon_min=-10.0, lon_max=22.0)

# Gabarit coaster (fiche projet "COASTER, navire de cabotage") :
# DWT 1 000-15 000 t / LOA jusqu'à 140 m / tirant d'eau 3-6 m.
# Le DWT n'est PAS dans l'AIS : on l'approxime par LOA + tirant d'eau, qui y sont.
# La borne basse LOA=80 m est une HYPOTHÈSE (aucune source ne la fixe) : elle vise à écarter
# les caboteurs fluvio-maritimes et les servitudes tout en gardant les coasters ~2 000 DWT.
COASTER_LOA = (60.0, 140.0)
COASTER_DRAUGHT = (2.0, 8.0)


# --------------------------------------------------------------------------------------
# Config locale optionnelle (génération de carte en local) — voir aismap.local.env.example
# --------------------------------------------------------------------------------------

LOCAL_CONFIG_FILE = "aismap.local.env"


def _load_local_config(path=LOCAL_CONFIG_FILE):
    """Charge une config locale optionnelle (une ligne KEY=value, '#' pour commenter) si
    le fichier existe dans le dossier courant. Sert uniquement à préremplir les valeurs
    par défaut de la CLI de génération de carte : un flag explicite sur la ligne de
    commande prime toujours sur ces valeurs. Absent par défaut, aucun effet si non créé.
    """
    cfg = {}
    if not os.path.exists(path):
        return cfg
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            cfg[key.strip()] = value.strip()
    return cfg


def _config_choice(config, key, choices, default):
    """Lit une valeur de config à choix contraint (ex. AISMAP_MODE) ; ignore et prévient
    si la valeur ne fait pas partie des choix valides plutôt que de planter au parsing.
    """
    val = config.get(key)
    if val is None:
        return default
    if val not in choices:
        print(f"[config] {key}={val!r} invalide dans {LOCAL_CONFIG_FILE} "
              f"(attendu parmi {choices}) — ignoré.")
        return default
    return val


def _map_output_defaults(config):
    """Calcule les chemins de sortie par défaut de 'stream-map'/'map' : dossier
    AISMAP_MAP_OUT_DIR (défaut 'out/'), fichiers horodatés à la date du jour pour garder
    un historique au lieu d'écraser la carte précédente à chaque run.
    """
    out_dir = config.get("AISMAP_MAP_OUT_DIR", "out")
    today = datetime.now().strftime("%Y-%m-%d")
    return dict(
        png=os.path.join(out_dir, f"heatmap-{today}.png"),
        html=os.path.join(out_dir, f"heatmap-{today}.html"),
        grid=os.path.join(out_dir, f"grid-{today}.npz"),
    )


# --------------------------------------------------------------------------------------
# 1. targets
# --------------------------------------------------------------------------------------

def cmd_targets(a):
    """Construit targets.csv strictement depuis --input (CSV Nom;IMO;MMSI[;Armateur]).
    Aucune autre source, aucun appariement par nom : chaque ligne de sortie correspond à
    une ligne du CSV d'entrée, identifiée uniquement par IMO et/ou MMSI. Une ligne sans
    IMO ni MMSI est écartée (rien à matcher en flux AIS sans identifiant).
    """
    if not os.path.exists(a.input):
        sys.exit(f"CSV d'entrée introuvable : {a.input!r}.")

    ids = pd.read_csv(a.input, sep=";", encoding="utf-8-sig")
    ids.columns = [c.strip() for c in ids.columns]

    t = pd.DataFrame(dict(
        name=ids["Nom"],
        owner=ids.get("Armateur"),
        mmsi=ids["MMSI"],
        imo=ids["IMO"],
    ))
    n_total = len(t)
    t = t[t["mmsi"].notna() | t["imo"].notna()]
    n_dropped = n_total - len(t)
    t.to_csv(a.out, index=False)

    print(f"{len(t)} navires cibles -> {a.out} (source : {os.path.basename(a.input)}, "
          f"identifiés par MMSI/IMO uniquement).")
    if n_dropped:
        print(f"{n_dropped} ligne(s) écartée(s) de {a.input!r} : ni MMSI ni IMO renseigné.")


# --------------------------------------------------------------------------------------
# 2. map
# --------------------------------------------------------------------------------------

def _plot_grid(H, ye, xe, png, html, title, caption):
    """Trace la heatmap (PNG log-scale) et, si folium dispo, une carte HTML interactive.
    Factorisé pour être appelé aussi bien depuis un grid.npz (cmd_map) que depuis
    une grille reconstruite directement en mémoire depuis des .jsonl (cmd_stream_map).
    Un carré rouge matérialise BBOX (l'emprise de collecte) sur les deux sorties.

    Fond de côtes/frontières sur le PNG via cartopy si installé (optionnel, cf.
    requirements-map.txt) : sans lui le PNG reste utilisable mais sans repère
    géographique, seulement la grille + le cadre BBOX (dégradation propre, comme pour
    folium/HTML plus bas).
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LogNorm
    from matplotlib.patches import Rectangle

    try:
        import cartopy.crs as ccrs
        import cartopy.feature as cfeature
        has_cartopy = True
    except ImportError:
        has_cartopy = False

    Hm = np.ma.masked_where(H <= 0, H)

    for out_path in (png, html):
        if out_path and os.path.dirname(out_path):
            os.makedirs(os.path.dirname(out_path), exist_ok=True)

    fig = plt.figure(figsize=(11, 8), dpi=150)
    if has_cartopy:
        proj = ccrs.PlateCarree()
        ax = fig.add_subplot(1, 1, 1, projection=proj)
        ax.set_extent([xe.min(), xe.max(), ye.min(), ye.max()], crs=proj)
        ax.add_feature(cfeature.LAND, facecolor="0.92", zorder=0)
        ax.add_feature(cfeature.COASTLINE, edgecolor="0.4", linewidth=0.6, zorder=1)
        ax.add_feature(cfeature.BORDERS, edgecolor="0.6", linewidth=0.4, linestyle=":", zorder=1)
        mesh = ax.pcolormesh(xe, ye, Hm, norm=LogNorm(vmin=max(Hm.min(), 0.08), vmax=Hm.max()),
                             cmap="inferno", shading="auto", transform=proj, zorder=2)
        ax.add_patch(Rectangle(
            (BBOX["lon_min"], BBOX["lat_min"]),
            BBOX["lon_max"] - BBOX["lon_min"], BBOX["lat_max"] - BBOX["lat_min"],
            fill=False, edgecolor="red", linewidth=1.5, linestyle="--", zorder=5,
            transform=proj,
        ))
        ax.gridlines(draw_labels=True, alpha=0.15, linewidth=0.4, color="0.3")
    else:
        ax = fig.add_subplot(1, 1, 1)
        mesh = ax.pcolormesh(xe, ye, Hm, norm=LogNorm(vmin=max(Hm.min(), 0.08), vmax=Hm.max()),
                             cmap="inferno", shading="auto")
        ax.add_patch(Rectangle(
            (BBOX["lon_min"], BBOX["lat_min"]),
            BBOX["lon_max"] - BBOX["lon_min"], BBOX["lat_max"] - BBOX["lat_min"],
            fill=False, edgecolor="red", linewidth=1.5, linestyle="--", zorder=5,
        ))
        ax.set_aspect(1 / np.cos(np.deg2rad(np.mean(ye))))
        ax.set_xlabel("Longitude (°E)")
        ax.set_ylabel("Latitude (°N)")
        ax.grid(alpha=0.15, lw=0.4)

    ax.set_title(title, fontsize=12)
    cb = fig.colorbar(mesh, ax=ax, shrink=0.85)
    cb.set_label("Heures de présence par cellule (échelle log)")
    if not has_cartopy:
        caption = caption + " (pip install cartopy pour un fond de côtes/frontières.)"
    fig.text(0.01, 0.01, caption, fontsize=7, color="0.35")
    fig.tight_layout()
    fig.savefig(png, bbox_inches="tight")
    print(f"-> {png}" + (" (avec fond de côtes cartopy)" if has_cartopy else ""))

    if html:
        try:
            import folium
            from folium.plugins import HeatMap
        except ImportError:
            print("folium non installé (pip install folium) — carte HTML ignorée.")
            return
        yc = (ye[:-1] + ye[1:]) / 2
        xc = (xe[:-1] + xe[1:]) / 2
        pts = [[yc[i], xc[j], float(H[i, j])] for i, j in zip(*np.nonzero(H))]
        if not pts:
            print("Grille vide (aucune cellule > 0) — carte HTML ignorée.")
            return
        mx = max(p[2] for p in pts)
        m = folium.Map(location=[float(np.mean(ye)), float(np.mean(xe))], zoom_start=6,
                       tiles="CartoDB positron")
        HeatMap([[p[0], p[1], p[2] / mx] for p in pts], radius=6, blur=5, min_opacity=0.25).add_to(m)
        folium.Rectangle(
            bounds=[[BBOX["lat_min"], BBOX["lon_min"]], [BBOX["lat_max"], BBOX["lon_max"]]],
            color="red", weight=2, dash_array="6", fill=False, tooltip="Emprise de collecte (BBOX)",
        ).add_to(m)
        m.save(html)
        print(f"-> {html}")


def cmd_map(a):
    """Trace la heatmap (PNG) et, si folium est installé, la carte HTML interactive,
    à partir d'une grille déjà sauvegardée (--grid, produite par 'stream-map --grid-out').
    """
    z = np.load(a.grid)
    H, ye, xe = z["H"], z["lat_edges"], z["lon_edges"]
    _plot_grid(H, ye, xe, a.png, a.html, a.title, a.caption)


# --------------------------------------------------------------------------------------
# 3. stream-map (construit la grille et trace la carte à partir des .jsonl de 'stream')
# --------------------------------------------------------------------------------------

def cmd_stream_map(a):
    """Construit la grille (heures de présence) directement depuis les .jsonl de 'stream'
    puis trace la carte. Rééchantillonnage par navire à pas fixe (défaut 5 min), chaque
    point retenu pèse ce pas en heures (pas un comptage de messages bruts, qui biaiserait
    vers les navires à quai / qui émettent plus souvent).
    """
    files = sorted(glob.glob(os.path.join(a.dir, "ais-*.jsonl")))
    if not files:
        sys.exit(f"Aucun fichier ais-*.jsonl dans {a.dir} — lancer d'abord 'stream'.")
    print(f"{len(files)} fichier(s) : {', '.join(os.path.basename(f) for f in files)}")

    dfs = [pd.read_json(f, lines=True) for f in files]
    df = pd.concat(dfs, ignore_index=True)
    print(f"{len(df):,} lignes lues au total.")

    if a.mode != "all":
        df = df[df["match"] == a.mode]
        print(f"Après filtre match='{a.mode}': {len(df):,} lignes.")
    if df.empty:
        sys.exit("Plus aucune ligne après filtrage — vérifier --mode.")

    if a.cruising_status:
        df["sog_num"] = pd.to_numeric(df["sog"], errors="coerce")
        n_missing = df["sog_num"].isna().sum()
        if a.cruising_status == "cruising":
            df = df[df["sog_num"] > 0]
        else:  # harbour
            df = df[df["sog_num"] == 0]
        print(f"Après filtre cruising_status='{a.cruising_status}' (SOG {'>0' if a.cruising_status=='cruising' else '==0'}): "
              f"{len(df):,} lignes ({n_missing:,} lignes à SOG inconnu écartées).")
        if df.empty:
            sys.exit("Plus aucune ligne après filtrage — vérifier --cruising-status.")

    # Timestamp aisstream : "2026-08-18 08:25:46.634491902 +0000 UTC"
    ts_clean = df["ts"].str.replace(" UTC", "", regex=False)
    df["ts_dt"] = pd.to_datetime(ts_clean, utc=True, errors="coerce")
    df = df.dropna(subset=["ts_dt", "lat", "lon", "mmsi"])

    step = pd.Timedelta(minutes=a.step_min)
    hours_per_point = a.step_min / 60.0
    df["slot"] = df["ts_dt"].dt.floor(step)
    df = df.drop_duplicates(subset=["mmsi", "slot"])
    df["hours"] = hours_per_point

    print(f"{len(df):,} positions rééchantillonnées ({a.step_min} min) | "
          f"{df['mmsi'].nunique():,} navires distincts | "
          f"{df['hours'].sum():,.1f} heures-navire")
    for k in ("id", "generic"):
        n = df.loc[df["match"] == k, "mmsi"].nunique()
        print(f"  dont match='{k}': {n} navires")
    print(f"Période couverte : {df['ts_dt'].min()} -> {df['ts_dt'].max()} (UTC).")

    df = df[df["lat"].between(BBOX["lat_min"], BBOX["lat_max"]) &
            df["lon"].between(BBOX["lon_min"], BBOX["lon_max"])]
    if df.empty:
        sys.exit("Aucune position dans la bbox après nettoyage.")

    ny = int(round((BBOX["lat_max"] - BBOX["lat_min"]) / a.cell_deg))
    nx = int(round((BBOX["lon_max"] - BBOX["lon_min"]) / (a.cell_deg * 2)))
    H, ye, xe = np.histogram2d(df["lat"], df["lon"], bins=[ny, nx],
                                range=[[BBOX["lat_min"], BBOX["lat_max"]],
                                       [BBOX["lon_min"], BBOX["lon_max"]]],
                                weights=df["hours"])

    # Résumé rapide des zones les plus fréquentées, sans avoir à ouvrir l'image.
    top_n = min(5, int((H > 0).sum()))
    if top_n:
        yc = (ye[:-1] + ye[1:]) / 2
        xc = (xe[:-1] + xe[1:]) / 2
        top_flat = np.argsort(H, axis=None)[::-1][:top_n]
        print(f"Top {top_n} cellules les plus fréquentées :")
        for rank, flat_idx in enumerate(top_flat, 1):
            i, j = np.unravel_index(flat_idx, H.shape)
            print(f"  {rank}. {yc[i]:.3f}°N, {xc[j]:.3f}°E — {H[i, j]:.1f} h de présence")

    if a.grid_out:
        if os.path.dirname(a.grid_out):
            os.makedirs(os.path.dirname(a.grid_out), exist_ok=True)
        np.savez(a.grid_out, H=H, lat_edges=ye, lon_edges=xe)
        print(f"-> {a.grid_out} (grille sauvegardée, réutilisable avec 'map --grid')")

    _plot_grid(H, ye, xe, a.png, a.html, a.title, a.caption)


# --------------------------------------------------------------------------------------
# 4. stream (collecteur temps réel aisstream.io — couverture EU du Nord complète)
# --------------------------------------------------------------------------------------

STREAM_TPL = """
Collecteur temps réel. À lancer 7 jours d'affilée (tmux / systemd / petit VPS).
Clé API gratuite : https://aisstream.io  (le filtre MMSI y est limité à 50 navires :
au-delà on souscrit à une BOUNDING BOX et on filtre localement sur MMSI + IMO).
Matching : MMSI présent dans targets.csv, OU IMO connu pour ce MMSI (via le cache
persistant --cache, alimenté par les ShipStaticData du flux) présent dans targets.csv.
Le matching par nom seul a été retiré (trop bruité).
"""


def _load_mmsi_imo_cache(path):
    """Charge le cache persistant MMSI(str) -> IMO(str). Vide si absent/corrompu."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        print(f"[cache] {path} illisible, cache MMSI->IMO réinitialisé.")
        return {}


def _save_mmsi_imo_cache(path, cache):
    """Écriture atomique (fichier temporaire + remplacement) pour ne jamais laisser
    un cache tronqué si le process est interrompu pendant l'écriture.

    Sur un dossier synchronisé (OneDrive, Dropbox...), le fichier de destination peut
    être brièvement verrouillé pendant sa synchronisation : os.replace() échoue alors
    avec WinError 32 sous Windows. On retente quelques fois avant d'abandonner —
    échouer à sauvegarder le cache ne doit jamais faire planter le collecteur.
    Retourne True si la sauvegarde a réussi, False sinon (à charge de l'appelant de
    ne pas laisser cette erreur interrompre le flux).
    """
    import time
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False, sort_keys=True)
    except OSError as e:
        print(f"[cache] échec d'écriture de {tmp} : {e}")
        return False

    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return True
        except OSError as e:
            if attempt == 4:
                print(f"[cache] {path} verrouillé après 5 tentatives ({e}) — "
                      "sauvegarde différée à la prochaine occasion. Si ça persiste, "
                      "vérifier qu'aucun programme (OneDrive, éditeur...) ne verrouille "
                      "ce fichier, ou déplacer le dossier de travail hors synchronisation.")
                return False
            time.sleep(0.5 * (attempt + 1))
    return False


def cmd_stream(a):
    """Collecteur temps réel aisstream.io : boucle infinie, se reconnecte automatiquement
    en cas d'erreur/coupure, écrit un .jsonl par jour UTC dans --out et entretient le
    cache persistant MMSI->IMO (--cache). Cf. docstring du module pour le détail du
    matching ('id' vs 'generic') et des arguments.
    """
    try:
        import asyncio
        import websockets
    except ImportError:
        sys.exit("pip install websockets")
    print(STREAM_TPL)

    targets_mmsi, targets_imo = set(), set()
    targets_name = {}  # mmsi_str ou imo_str -> nom canonique (targets.csv), pour les lignes 'id'
    if os.path.exists(a.targets):
        tg = pd.read_csv(a.targets)
        mmsi_num = pd.to_numeric(tg["mmsi"], errors="coerce")
        imo_num = pd.to_numeric(tg["imo"], errors="coerce")
        targets_mmsi = set(mmsi_num.dropna().astype(int).astype(str))
        targets_imo = set(imo_num.dropna().astype(int).astype(str))
        for mmsi_v, imo_v, name_v in zip(mmsi_num, imo_num, tg["name"]):
            if pd.notna(mmsi_v):
                targets_name[str(int(mmsi_v))] = name_v
            if pd.notna(imo_v):
                targets_name[str(int(imo_v))] = name_v
        print(f"cibles chargées : {len(targets_mmsi)} MMSI / {len(targets_imo)} IMO (confiance 'id').")
    os.makedirs(a.out, exist_ok=True)

    # Cache persistant MMSI -> IMO (cf. docstring de la commande 'stream' en haut du
    # fichier pour le pourquoi). Survit d'une session à l'autre.
    mmsi_imo_cache = _load_mmsi_imo_cache(a.cache)
    print(f"Cache MMSI->IMO : {len(mmsi_imo_cache)} entrée(s) chargée(s) depuis {a.cache}.")

    # aisstream limite FiltersShipMMSI à 50 valeurs : au-delà, on ne filtre pas côté serveur et on
    # filtre localement (comme pour le mode 'generic').
    mmsi_filter = sorted(targets_mmsi)[:50] if 0 < len(targets_mmsi) <= 50 else None

    sub = {
        "APIKey": a.key,
        "BoundingBoxes": [[[BBOX["lat_min"], BBOX["lon_min"]],
                           [BBOX["lat_max"], BBOX["lon_max"]]]],
        "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
    }
    if mmsi_filter:
        sub["FiltersShipMMSI"] = mmsi_filter
        print(f"Filtre serveur actif sur {len(mmsi_filter)} MMSI (sur {len(targets_mmsi)} cibles).")
    else:
        print(f"{len(targets_mmsi)} MMSI cibles > 50 ou =0 : pas de filtre serveur, "
              "filtrage local sur MMSI + IMO + gabarit.")

    async def run():
        static = {}   # mmsi -> (name, loa, draught, type, imo)
        n = 0       # positions coaster écrites dans les .jsonl
        n_raw = 0   # messages AIS reçus, tous types confondus (preuve que la clé API/l'abonnement fonctionnent)
        loop = asyncio.get_event_loop()
        last_save = loop.time()
        last_status = loop.time()
        while True:
            try:
                async with websockets.connect("wss://stream.aisstream.io/v0/stream",
                                              ping_interval=20) as ws:
                    await ws.send(json.dumps(sub))
                    print("[connexion] websocket ouverte, abonnement envoyé à aisstream.io.")
                    connected_at = loop.time()
                    async for raw in ws:
                        n_raw += 1
                        now = loop.time()
                        if now - last_status > 60:
                            print(f"[status] connecté depuis {int(now - connected_at)}s | "
                                  f"{n_raw:,} messages AIS reçus (preuve que la clé API est acceptée) | "
                                  f"{n:,} positions coaster écrites dans {a.out} | "
                                  f"{len(static):,} navires vus | cache MMSI->IMO : {len(mmsi_imo_cache):,}")
                            last_status = now
                        m = json.loads(raw)
                        md = m.get("MetaData", {})
                        mmsi = md.get("MMSI")
                        mmsi_str = str(mmsi)
                        msg = m.get("Message") or {}
                        if m.get("MessageType") == "ShipStaticData":
                            s = msg.get("ShipStaticData")
                            if s is None:
                                continue
                            dim = s.get("Dimension", {})
                            loa = (dim.get("A", 0) or 0) + (dim.get("B", 0) or 0)
                            imo = s.get("ImoNumber")
                            name = s.get("Name", "").strip()
                            static[mmsi] = (name, loa, s.get("MaximumStaticDraught"), s.get("Type"), imo)
                            # ImoNumber=0 : navire <300 GT, pas d'IMO attribué (normal, pas une
                            # erreur) -> on ne pollue pas le cache avec des "0".
                            if imo and str(imo) != "0":
                                prev_imo = mmsi_imo_cache.get(mmsi_str)
                                if prev_imo != str(imo):
                                    if prev_imo:
                                        print(f"[cache] MMSI {mmsi_str} : IMO {prev_imo} -> {imo} "
                                              f"(changement de pavillon probable, '{name}')")
                                    mmsi_imo_cache[mmsi_str] = str(imo)
                            continue
                        # Autres MessageType que PositionReport/ShipStaticData ignorés (ex. types
                        # non couverts par FilterMessageTypes mais quand même reçus côté serveur) :
                        # on ignore silencieusement plutôt que de planter le collecteur entier.
                        p = msg.get("PositionReport")
                        if p is None:
                            continue
                        nm, loa, dr, ty, imo_live = static.get(mmsi, ("", None, None, None, None))
                        # IMO connu pour ce MMSI : priorité au ShipStaticData reçu cette
                        # session, sinon on retombe sur le cache persistant (sessions
                        # précédentes) — potentiellement périmé si reflagging entre-temps,
                        # mais reste bien plus fiable qu'un MMSI figé dans targets.csv.
                        imo_known = imo_live if (imo_live and str(imo_live) != "0") else mmsi_imo_cache.get(mmsi_str)
                        # Tanker (type AIS 80-89) exclu du périmètre, quel que soit le mode de
                        # match (id/name/generic) — cf. fiche "OBJECTIF". Si le type n'est pas
                        # encore connu (static pas encore reçu), on ne peut pas trancher : on
                        # garde par défaut (mieux vaut filtrer a posteriori sur 'shiptype' dans
                        # le jsonl qu'exclure à tort faute de donnée).
                        is_tanker = ty is not None and 80 <= ty <= 89
                        if is_tanker:
                            continue
                        is_id = (mmsi_str in targets_mmsi
                                 or (imo_known is not None and str(imo_known) in targets_imo))
                        is_generic = (ty is not None and 70 <= ty <= 79
                                      and loa is not None and COASTER_LOA[0] <= loa <= COASTER_LOA[1]
                                      and dr is not None and COASTER_DRAUGHT[0] <= dr <= COASTER_DRAUGHT[1])
                        if not (is_id or is_generic):
                            continue
                        match = "id" if is_id else "generic"
                        # Pour 'id', le nom canonique vient de targets.csv (fiable, connu dès
                        # le départ) plutôt que du ShipStaticData live (nm) : ce dernier peut
                        # ne jamais arriver pour ce MMSI précis dans la session en cours, alors
                        # que l'identité du navire est déjà certaine via MMSI/IMO.
                        name = (targets_name.get(mmsi_str) or targets_name.get(str(imo_known))
                                or nm) if is_id else nm
                        rec = dict(ts=md.get("time_utc"), mmsi=mmsi, name=name, imo=imo_known,
                                   lat=p["Latitude"], lon=p["Longitude"], sog=p.get("Sog"),
                                   loa=loa, draught=dr, shiptype=ty, match=match)
                        day = (rec["ts"] or "")[:10] or datetime.utcnow().date().isoformat()
                        with open(os.path.join(a.out, f"ais-{day}.jsonl"), "a") as f:
                            f.write(json.dumps(rec) + "\n")
                        n += 1

                        if now - last_save > 60:
                            _save_mmsi_imo_cache(a.cache, mmsi_imo_cache)
                            last_save = now
            except Exception as e:
                detail = f"{type(e).__name__}: {e}"
                print(f"[reconnexion après erreur] {detail}")
                if any(tok in detail for tok in ("401", "403", "Unauthorized", "Forbidden", "InvalidStatus")):
                    print("[indice] ça ressemble à un refus d'authentification par aisstream.io "
                          "-> vérifier la clé API (--key / AISSTREAM_API_KEY).")
                _save_mmsi_imo_cache(a.cache, mmsi_imo_cache)
                await asyncio.sleep(10)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    finally:
        _save_mmsi_imo_cache(a.cache, mmsi_imo_cache)
        print(f"\nCache MMSI->IMO sauvegardé ({len(mmsi_imo_cache)} entrées) -> {a.cache}")


# --------------------------------------------------------------------------------------

def main():
    """Point d'entrée CLI : une sous-commande par étape du pipeline (targets / stream /
    stream-map / map), cf. docstring du module pour le détail de chacune.
    """
    # Config locale optionnelle (aismap.local.env) : ne fait que préremplir les défauts
    # ci-dessous pour la génération de carte, un flag CLI explicite prime toujours dessus.
    config = _load_local_config()
    map_defaults = _map_output_defaults(config)

    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("targets", help="construit targets.csv depuis le CSV Nom/IMO/MMSI d'entrée")
    s.set_defaults(f=cmd_targets)
    s.add_argument("--input", default="coasters_multi_flottes_imo_mmsi.csv",
                   help="CSV d'entrée Nom;IMO;MMSI[;Armateur] (seule source, matching par ID uniquement)")
    s.add_argument("--out", default="targets.csv", help="fichier targets.csv de sortie")

    s = sub.add_parser("map", help="trace la heatmap à partir d'une grille .npz déjà construite")
    s.set_defaults(f=cmd_map)
    s.add_argument("--grid", default=map_defaults["grid"],
                   help="grille à tracer (produite par 'stream-map --grid-out', défaut horodaté du jour)")
    s.add_argument("--png", default=map_defaults["png"], help="image PNG de sortie (défaut horodaté du jour)")
    s.add_argument("--html", default=map_defaults["html"],
                   help="carte HTML interactive de sortie, ignorée si folium absent (défaut horodaté du jour)")
    s.add_argument("--title", default="Présence des coasters (cargo, LOA 80-140 m, "
                                      "tirant d'eau 3-6 m ; + flottes identifiées "
                                      "par MMSI/IMO) — aisstream.io",
                   help="titre du graphique")
    s.add_argument("--caption", default="Source : aisstream.io (temps réel). "
                                        "Pondéré en heures de présence par cellule.",
                   help="légende en bas de figure")

    s = sub.add_parser("stream-map",
                       help="carte directement depuis les .jsonl de 'stream'")
    s.set_defaults(f=cmd_stream_map)
    s.add_argument("--dir", default=config.get("AISMAP_DRIVE_DIR", "stream"),
                   help="dossier contenant les ais-*.jsonl (config : AISMAP_DRIVE_DIR)")
    s.add_argument("--mode", choices=["id", "generic", "all"],
                   default=_config_choice(config, "AISMAP_MODE", ["id", "generic", "all"], "all"),
                   help="filtre sur le champ 'match' (défaut : all, config : AISMAP_MODE)")
    s.add_argument("--cruising-status", dest="cruising_status", choices=["cruising", "harbour"],
                   default=_config_choice(config, "AISMAP_CRUISING_STATUS", ["cruising", "harbour"], None),
                   help="filtre sur le SOG : 'cruising' garde SOG>0, 'harbour' garde SOG==0 ; "
                        "non renseigné = les deux (défaut, config : AISMAP_CRUISING_STATUS)")
    s.add_argument("--step-min", type=int, default=config.get("AISMAP_STEP_MIN", "5"),
                   help="pas de rééchantillonnage en min (config : AISMAP_STEP_MIN)")
    s.add_argument("--cell-deg", type=float, default=config.get("AISMAP_CELL_DEG", "0.01"),
                   help="taille de cellule en ° de latitude, défaut ~1,1 km (config : AISMAP_CELL_DEG)")
    s.add_argument("--grid-out", default=None,
                   help="si fourni, sauvegarde aussi la grille en .npz (réutilisable avec 'map --grid')")
    s.add_argument("--png", default=map_defaults["png"], help="image PNG de sortie (défaut horodaté du jour)")
    s.add_argument("--html", default=map_defaults["html"],
                   help="carte HTML interactive de sortie (défaut horodaté du jour)")
    s.add_argument("--title", default="Présence des coasters — flux aisstream.io en direct",
                   help="titre du graphique")
    s.add_argument("--caption", default="Source : aisstream.io (temps réel). "
                                        "Pondéré en heures de présence par cellule (rééchantillonnage "
                                        "par navire), pas en nombre de messages bruts.",
                   help="légende en bas de figure")

    s = sub.add_parser("stream", help="collecteur temps réel aisstream.io (boucle infinie)")
    s.set_defaults(f=cmd_stream)
    s.add_argument("--key", required=True, help="clé API aisstream.io")
    s.add_argument("--targets", default="targets.csv", help="liste des navires cibles pour le matching")
    s.add_argument("--out", default="stream", help="dossier de sortie des .jsonl")
    s.add_argument("--cache", default="mmsi_imo_cache.json",
                   help="cache persistant MMSI->IMO, réutilisé d'une session à l'autre")

    a = p.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
