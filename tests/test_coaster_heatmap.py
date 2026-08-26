"""Tests légers pour tools/coaster_heatmap.py : filet de sécurité minimal sur la logique
qui tourne sans supervision sur le Pi (cf. README.md, item CI : le Pi applique
`git reset --hard` sur chaque push, une régression non testée casse la collecte en prod).
Ne couvre pas 'stream' (websocket temps réel) ni le rendu matplotlib/folium en détail.
"""
import argparse
import json
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import coaster_heatmap as ch  # noqa: E402


def test_cmd_targets_drops_rows_without_mmsi_or_imo(tmp_path):
    input_csv = tmp_path / "input.csv"
    input_csv.write_text(
        "Nom;IMO;MMSI;Armateur\n"
        "Avec les deux;9234317;304011029;Armateur A\n"
        "Avec IMO seul;9431587;;Armateur B\n"
        "Avec MMSI seul;;245070000;Armateur C\n"
        "Sans aucun;;;Armateur D\n",
        encoding="utf-8",
    )
    out_csv = tmp_path / "targets.csv"

    ch.cmd_targets(argparse.Namespace(input=str(input_csv), out=str(out_csv)))

    result = pd.read_csv(out_csv)
    assert len(result) == 3
    assert "Sans aucun" not in set(result["name"])
    assert set(result["name"]) == {"Avec les deux", "Avec IMO seul", "Avec MMSI seul"}


def test_cmd_targets_missing_input_exits(tmp_path):
    with pytest.raises(SystemExit):
        ch.cmd_targets(argparse.Namespace(input=str(tmp_path / "absent.csv"),
                                          out=str(tmp_path / "out.csv")))


def _write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


def _sample_records():
    # 2 positions 'id' (une en route, une à quai) + 2 'generic' (idem), toutes dans BBOX.
    base = dict(lat=56.0, lon=8.0, loa=90, draught=4, shiptype=71)
    return [
        dict(base, ts="2026-08-25 08:00:00.000000000 +0000 UTC", mmsi=111, name="A",
             imo=None, sog=5, match="id"),
        dict(base, ts="2026-08-25 09:00:00.000000000 +0000 UTC", mmsi=222, name="B",
             imo=None, sog=0, match="id"),
        dict(base, ts="2026-08-25 10:00:00.000000000 +0000 UTC", mmsi=333, name="C",
             imo=None, sog=5, match="generic"),
        dict(base, ts="2026-08-25 11:00:00.000000000 +0000 UTC", mmsi=444, name="D",
             imo=None, sog=0, match="generic"),
    ]


def _base_args(tmp_path, **overrides):
    args = dict(
        dir=str(tmp_path),
        mode="all",
        cruising_status=None,
        step_min=5,
        cell_deg=0.5,
        grid_out=None,
        png=str(tmp_path / "heatmap.png"),
        html=None,
        title="test",
        caption="test",
    )
    args.update(overrides)
    return argparse.Namespace(**args)


def test_cmd_stream_map_mode_filter(tmp_path, capsys):
    _write_jsonl(tmp_path / "ais-2026-08-25.jsonl", _sample_records())

    ch.cmd_stream_map(_base_args(tmp_path, mode="id"))

    out = capsys.readouterr().out
    assert "Après filtre match='id': 2 lignes." in out


def test_cmd_stream_map_cruising_status_filter(tmp_path, capsys):
    _write_jsonl(tmp_path / "ais-2026-08-25.jsonl", _sample_records())

    ch.cmd_stream_map(_base_args(tmp_path, cruising_status="cruising"))

    out = capsys.readouterr().out
    # 2 lignes à SOG>0 sur les 4 (mmsi 111 et 333) ; les 2 autres ont SOG==0 (pas inconnu).
    assert "2 lignes (0 lignes à SOG inconnu écartées)" in out


def test_cmd_stream_map_no_files_exits(tmp_path):
    with pytest.raises(SystemExit):
        ch.cmd_stream_map(_base_args(tmp_path))


def test_load_local_config_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert ch._load_local_config() == {}


def test_load_local_config_parses_key_value(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ch.LOCAL_CONFIG_FILE).write_text(
        "# commentaire\nAISMAP_DRIVE_DIR=/tmp/foo\n\nAISMAP_STEP_MIN=10\n",
        encoding="utf-8",
    )
    config = ch._load_local_config()
    assert config == {"AISMAP_DRIVE_DIR": "/tmp/foo", "AISMAP_STEP_MIN": "10"}


def test_config_choice_rejects_invalid_value(capsys):
    result = ch._config_choice({"AISMAP_MODE": "bogus"}, "AISMAP_MODE",
                               ["id", "generic", "all"], "all")
    assert result == "all"
    assert "invalide" in capsys.readouterr().out
