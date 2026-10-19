"""Catalog: filename parsing, root scanning, ad hoc registration."""

from __future__ import annotations

from pathlib import Path

import pytest

from viewer.server.catalog import Catalog, file_id, make_entry, parse_product_name
from viewer.server.config import RootSpec, ViewerConfig

STEM = "S2B_MSI_2025_01_09_16_05_58_T17RNK"


def test_parse_product_name_real_looking():
    p = parse_product_name(f"{STEM}_DSFOPT.nc")
    assert p == {
        "platform": "S2B", "sensor": "MSI", "datetime": "2025-01-09T16:05:58",
        "date": "2025-01-09", "tile": "T17RNK", "role": "DSFOPT", "scene_key": STEM,
    }
    opt = parse_product_name("S2A_MSI_2025_06_08_16_05_44_T17RNL_C2RCCCANON_optimized.nc")
    assert opt["role"] == "C2RCCCANON_optimized"
    assert opt["scene_key"] == "S2A_MSI_2025_06_08_16_05_44_T17RNL"
    assert parse_product_name(f"{STEM}_L2W.nc")["role"] == "L2W"


def test_parse_product_name_weird():
    p = parse_product_name("weird_name.nc")
    assert p["role"] == "OTHER"
    assert p["scene_key"] == "weird_name"
    assert p["platform"] is None and p["tile"] is None and p["datetime"] is None


def test_scan_synthetic_root(viewer_config, synthetic_root):
    cat = Catalog(viewer_config)
    assert cat.scanned_at is None
    cat.scan()
    assert cat.scanned_at
    entries = cat.entries()
    assert len(entries) == 6
    by_name = {e.name: e for e in entries}
    assert set(by_name) == {
        f"{STEM}_DSFOPT.nc", f"{STEM}_C2RCCOPT.nc", f"{STEM}_L2W.nc",
        f"{STEM}_C2RCC.nc", f"{STEM}_C2RCCCANON.nc", "weird_name.nc",
    }
    assert all(".hidden" not in e.path for e in entries)
    for name, role in ((f"{STEM}_DSFOPT.nc", "DSFOPT"), (f"{STEM}_L2W.nc", "L2W"),
                       (f"{STEM}_C2RCC.nc", "C2RCC"), (f"{STEM}_C2RCCCANON.nc", "C2RCCCANON")):
        e = by_name[name]
        assert e.run == "production_test"
        assert e.tile == "T17RNK"
        assert e.role == role
        assert e.root_label == "test root"
        assert e.size_bytes > 0 and "T" in e.mtime
    weird = by_name["weird_name.nc"]
    assert weird.run == "other" and weird.role == "OTHER" and weird.tile is None
    # sorted by (run, tile, datetime desc, role): "other" run precedes "production_test"
    assert [e.run for e in entries] == sorted(e.run for e in entries)
    summary = cat.roots_summary()
    assert summary == [{"label": "test root", "path": viewer_config.roots[0].path, "exists": True, "n_files": 6}]
    assert cat.get(by_name[f"{STEM}_DSFOPT.nc"].id).path == str(Path(synthetic_root["dsfopt"]).resolve())
    d = entries[0].to_dict()
    assert set(d) >= {"id", "path", "name", "root_label", "run", "platform", "sensor", "datetime",
                      "date", "tile", "role", "scene_key", "size_bytes", "mtime"}


def test_register_path_roundtrip(viewer_config, synthetic_root):
    cat = Catalog(viewer_config)
    cat.scan()
    hidden = synthetic_root["root"] / ".hidden" / f"{STEM}_DSFOPT.nc"
    entry = cat.register_path(hidden)
    assert entry.id == file_id(hidden)
    assert entry.root_label == "ad hoc"
    assert entry.run == ".hidden"
    assert entry.role == "DSFOPT"
    assert cat.register_path(str(hidden)).id == entry.id
    assert cat.get(entry.id) is entry
    assert len(cat.entries()) == 7
    cat.scan()                                  # ad hoc entries survive a rescan
    assert cat.get(entry.id) is entry
    # an already catalogued path returns the scanned entry, not a duplicate
    scanned = cat.register_path(synthetic_root["dsfopt"])
    assert scanned.root_label == "test root"
    with pytest.raises(ValueError):
        cat.register_path(synthetic_root["root"] / "other" / "notes.txt")
    with pytest.raises(FileNotFoundError):
        cat.register_path(synthetic_root["root"] / "missing.nc")


def test_get_unknown_and_missing_root(synthetic_root):
    cfg = ViewerConfig(roots=[RootSpec("Z:/definitely/not/here", "nope")], max_depth=4)
    cat = Catalog(cfg)
    cat.scan()
    assert cat.entries() == []
    assert cat.roots_summary() == [{"label": "nope", "path": cfg.roots[0].path, "exists": False, "n_files": 0}]
    with pytest.raises(KeyError):
        cat.get("000000000000")
    direct = make_entry(synthetic_root["weird"], RootSpec(str(synthetic_root["root"] / "other"), "o"))
    assert direct.run == ""                     # file sits directly in the root
