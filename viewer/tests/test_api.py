"""End-to-end API tests over the synthetic root (plan section 8, WP4)."""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

# Synthetic dsfopt grid: 80 x 60 pixels, outer edges below.
XMIN, YMIN, XMAX, YMAX = 583860.0, 2998800.0, 585460.0, 3000000.0
FULL_BBOX = f"{XMIN},{YMIN},{XMAX},{YMAX}"


@pytest.fixture(scope="module")
def catalog(client):
    r = client.get("/api/catalog")
    assert r.status_code == 200, r.text
    return r.json()


def _by_role(catalog, role):
    hits = [f for f in catalog["files"] if f["role"] == role]
    assert hits, f"no {role} in catalog"
    return hits[0]


@pytest.fixture(scope="module")
def dsf(catalog):
    return _by_role(catalog, "DSFOPT")


@pytest.fixture(scope="module")
def c2r(catalog):
    return _by_role(catalog, "C2RCCOPT")


@pytest.fixture(scope="module")
def raw(catalog):
    return _by_role(catalog, "C2RCC")


def _png(resp):
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "image/png"
    img = Image.open(io.BytesIO(resp.content))
    assert img.mode == "RGBA"
    return img


# -- catalog / config ---------------------------------------------------------

def test_config_and_index(client):
    cfg = client.get("/api/config").json()
    assert cfg["roots"] and "version" in cfg
    html = client.get("/")
    assert html.status_code == 200 and "<html" in html.text.lower()
    names = client.get("/api/colormaps").json()
    assert "gray" in names["names"] and "status" in names["flags"]


def test_catalog_contents(catalog):
    roles = sorted(f["role"] for f in catalog["files"])
    assert roles == ["C2RCC", "C2RCCCANON", "C2RCCOPT", "DSFOPT", "L2W", "OTHER"]
    dsf = _by_role(catalog, "DSFOPT")
    assert dsf["run"] == "production_test"
    assert dsf["tile"] == "T17RNK"
    assert dsf["date"] == "2025-01-09"
    assert dsf["scene_key"] == "S2B_MSI_2025_01_09_16_05_58_T17RNK"
    l2w = _by_role(catalog, "L2W")
    assert l2w["run"] == "production_test"
    assert catalog["roots"][0]["exists"] is True
    assert catalog["roots"][0]["n_files"] == 6


def test_catalog_refresh(client):
    r = client.get("/api/catalog?refresh=1")
    assert r.status_code == 200 and len(r.json()["files"]) == 6


def test_open_path(client, dsf, synthetic_root):
    r = client.get("/api/open", params={"path": dsf["path"]})
    assert r.status_code == 200 and r.json()["id"] == dsf["id"]
    assert client.get("/api/open", params={"path": str(synthetic_root["root"] / "nope.nc")}).status_code == 404
    assert client.get("/api/open", params={"path": str(synthetic_root["root"] / "other" / "notes.txt")}).status_code == 400


# -- file info / meta / stats / value -----------------------------------------

def test_bands_info(client, dsf, c2r, raw):
    info = client.get(f"/api/files/{dsf['id']}/bands").json()
    assert info["grid"]["kind"] == "projected"
    assert info["grid"]["space"] == "EPSG:32617"
    assert info["grid"]["bounds"] == pytest.approx([XMIN, YMIN, XMAX, YMAX])
    assert (info["grid"]["ny"], info["grid"]["nx"]) == (60, 80)
    names = {b["name"]: b for b in info["bands"]}
    assert names["Rrs_B03"]["match_key"] == "Rrs@560"
    assert names["pixel_status"]["kind"] == "status"
    assert names["l2_flags"]["kind"] == "flags"
    assert info["default_band"] == "Rrs_B03"
    assert info["status_band"] == "pixel_status"

    info2 = client.get(f"/api/files/{c2r['id']}/bands").json()
    names2 = {b["name"]: b for b in info2["bands"]}
    assert names2["rrs_B3"]["match_key"] == "Rrs@560"
    assert "Cloud_risk" in names2["c2rcc_flags"]["flag_meanings"]
    assert info2["grid"]["space"] == info["grid"]["space"]

    info3 = client.get(f"/api/files/{raw['id']}/bands").json()
    assert info3["grid"]["kind"] == "pixel" and info3["grid"]["space"] == "pixel"
    assert info3["grid"]["has_latlon"] is True

    assert client.get("/api/files/doesnotexist/bands").status_code == 404


def test_meta(client, dsf):
    meta = client.get(f"/api/files/{dsf['id']}/meta").json()
    assert meta["dims"] == {"y": 60, "x": 80}
    assert meta["global"]["sensor"] == "S2B_MSI"
    assert len(meta["global"]["geocoding"]) < 4200
    assert "truncated" in meta["global"]["geocoding"]
    var_names = {v["name"] for v in meta["variables"]}
    assert {"Rrs_B03", "pixel_status", "transverse_mercator", "x", "y"} <= var_names


def test_stats(client, dsf, c2r):
    st = client.get(f"/api/files/{dsf['id']}/bands/Rrs_B03/stats").json()
    assert st["kind"] == "float"
    assert st["valid"] < st["count"] == 4800
    assert st["p2"] <= st["p98"]
    assert len(st["hist"]["counts"]) == 64
    ss = client.get(f"/api/files/{dsf['id']}/bands/pixel_status/stats").json()
    assert ss["kind"] == "status" and ss["counts"]["outside_aoi"] == 1200
    fs = client.get(f"/api/files/{c2r['id']}/bands/c2rcc_flags/stats").json()
    assert fs["kind"] == "flags"
    assert {f["name"] for f in fs["flags"]} >= {"Cloud_risk", "Valid_PE"}
    assert client.get(f"/api/files/{dsf['id']}/bands/nope/stats").status_code == 404


def test_value(client, dsf, raw):
    # col 50, row 10 -> inside the AOI
    r = client.get(f"/api/files/{dsf['id']}/value", params={"band": "Rrs_B03", "x": 584870, "y": 2999790}).json()
    assert r["inside"] is True and (r["row"], r["col"]) == (10, 50)
    assert isinstance(r["value"], float) and r["status"] == "value"
    assert -81.5 < r["lon"] < -80.0 and 26.5 < r["lat"] < 27.5
    # col 2 -> outside_aoi quarter: NaN value, status says why
    r = client.get(f"/api/files/{dsf['id']}/value", params={"band": "Rrs_B03", "x": 583910, "y": 2999790}).json()
    assert r["inside"] is True and r["value"] is None and r["status"] == "outside_aoi"
    # off the grid
    r = client.get(f"/api/files/{dsf['id']}/value", params={"band": "Rrs_B03", "x": 0, "y": 0}).json()
    assert r["inside"] is False and r["value"] is None
    # pixel-space file: world Y = -row
    r = client.get(f"/api/files/{raw['id']}/value", params={"band": "rrs_B3", "x": 50.5, "y": -10.5}).json()
    assert (r["row"], r["col"]) == (10, 50) and r["lat"] is not None


# -- render -------------------------------------------------------------------

def test_render_band_auto_stretch(client, dsf):
    resp = client.get("/api/render", params={"file": dsf["id"], "band": "Rrs_B03", "bbox": FULL_BBOX, "w": 160, "h": 120})
    img = _png(resp)
    assert img.size == (160, 120)
    assert float(resp.headers["X-Vmin"]) < float(resp.headers["X-Vmax"])
    assert float(resp.headers["X-Render-Ms"]) >= 0
    a = np.asarray(img)
    # left quarter is outside_aoi -> transparent; right side has data
    assert a[60, 5, 3] == 0
    assert a[60, 150, 3] == 255


def test_render_band_manual_and_log(client, dsf):
    p = {"file": dsf["id"], "band": "Rrs_B03", "bbox": FULL_BBOX, "w": 80, "h": 60, "vmin": 0.0, "vmax": 0.05, "cmap": "viridis"}
    resp = client.get("/api/render", params=p)
    _png(resp)
    assert resp.headers["X-Vmin"] == "0.0"
    p["scale"] = "log"
    _png(client.get("/api/render", params=p))
    p["scale"] = "sqrt"
    assert client.get("/api/render", params=p).status_code == 422  # pattern validation


def test_render_status_and_flags(client, dsf, c2r):
    resp = client.get("/api/render", params={"file": dsf["id"], "band": "pixel_status", "bbox": FULL_BBOX, "w": 80, "h": 60})
    a = np.asarray(_png(resp))
    assert tuple(a[30, 5]) == (70, 70, 70, 255)      # outside_aoi grey
    assert tuple(a[30, 60]) == (60, 180, 75, 255)    # value green
    resp = client.get("/api/render", params={"file": c2r["id"], "band": "c2rcc_flags", "bbox": FULL_BBOX, "w": 80, "h": 60})
    _png(resp)
    resp = client.get("/api/render", params={"file": c2r["id"], "band": "c2rcc_flags", "bbox": FULL_BBOX, "w": 80, "h": 60, "bit": 3})
    _png(resp)
    assert client.get("/api/render", params={"file": c2r["id"], "band": "c2rcc_flags", "bbox": FULL_BBOX, "w": 80, "h": 60, "bit": 99}).status_code == 400


def test_render_errors(client, dsf):
    base = {"file": dsf["id"], "band": "Rrs_B03", "bbox": FULL_BBOX, "w": 10, "h": 10}
    assert client.get("/api/render", params={**base, "bbox": "1,2,3"}).status_code == 400
    assert client.get("/api/render", params={**base, "bbox": "5,0,1,1"}).status_code == 400
    assert client.get("/api/render", params={**base, "w": 0}).status_code == 400
    assert client.get("/api/render", params={**base, "w": 5000}).status_code == 400
    assert client.get("/api/render", params={**base, "cmap": "nope"}).status_code == 400
    assert client.get("/api/render", params={**base, "band": "nope"}).status_code == 404
    assert client.get("/api/render", params={**base, "file": "nope"}).status_code == 404
    assert client.get("/api/render", params={**base, "op": "wat"}).status_code == 400


def test_render_diff(client, dsf, c2r, raw):
    p = {"op": "diff", "file": dsf["id"], "band": "Rrs_B03", "file2": c2r["id"], "band2": "rrs_B3",
         "bbox": FULL_BBOX, "w": 80, "h": 60}
    resp = client.get("/api/render", params=p)
    img = _png(resp)
    assert img.size == (80, 60)
    assert float(resp.headers["X-Diff-Vmax"]) > 0
    resp = client.get("/api/render", params={**p, "vmax": 0.01})
    assert resp.headers["X-Diff-Vmax"] == "0.01"
    # pixel-space file cannot be diffed against a projected one
    assert client.get("/api/render", params={**p, "file2": raw["id"], "band2": "rrs_B3"}).status_code == 400
    assert client.get("/api/render", params={k: v for k, v in p.items() if k != "file2"}).status_code == 400


def test_render_rgb(client, dsf, c2r):
    p = {"file": dsf["id"], "r": "Rrs_B8A", "g": "Rrs_B04", "b": "Rrs_B03", "bbox": FULL_BBOX, "w": 80, "h": 60}
    resp = client.get("/api/render_rgb", params=p)
    img = _png(resp)
    assert img.size == (80, 60)
    a = np.asarray(img)
    assert a[30, 5, 3] == 0 and a[30, 60, 3] == 255     # outside_aoi transparent, data opaque
    rng = {k: [float(v) for v in resp.headers[f"X-Range-{k}"].split(",")] for k in "RGB"}
    assert all(lo < hi for lo, hi in rng.values())
    assert float(resp.headers["X-Render-Ms"]) >= 0
    # shared=1: one range for all channels, the union of the three
    resp2 = client.get("/api/render_rgb", params={**p, "shared": 1})
    _png(resp2)
    assert resp2.headers["X-Range-R"] == resp2.headers["X-Range-G"] == resp2.headers["X-Range-B"]
    shared = [float(v) for v in resp2.headers["X-Range-R"].split(",")]
    assert shared[0] == min(v[0] for v in rng.values()) and shared[1] == max(v[1] for v in rng.values())
    # manual bounds are echoed; a missing bound falls back to the percentile
    resp3 = client.get("/api/render_rgb", params={**p, "rmin": 0, "rmax": 0.05, "gmin": 0.001})
    _png(resp3)
    assert resp3.headers["X-Range-R"] == "0.0,0.05"
    assert resp3.headers["X-Range-G"].startswith("0.001,")
    assert resp3.headers["X-Range-B"] == resp.headers["X-Range-B"]
    # gamma > 1 brightens the opaque pixels
    bright = np.asarray(_png(client.get("/api/render_rgb", params={**p, "gamma": 2})))
    opaque = a[..., 3] == 255
    assert bright[opaque][:, :3].mean() > a[opaque][:, :3].mean()
    # log scale accepted; the C2RCC product works with its own band names
    _png(client.get("/api/render_rgb", params={**p, "scale": "log"}))
    _png(client.get("/api/render_rgb", params={**p, "file": c2r["id"], "r": "rrs_B8A", "g": "rrs_B4", "b": "rrs_B3"}))
    # errors
    assert client.get("/api/render_rgb", params={**p, "b": "pixel_status"}).status_code == 400
    assert client.get("/api/render_rgb", params={**p, "g": "nope"}).status_code == 404
    assert client.get("/api/render_rgb", params={**p, "file": "nope"}).status_code == 404
    assert client.get("/api/render_rgb", params={**p, "gamma": 0}).status_code == 400
    assert client.get("/api/render_rgb", params={**p, "w": 0}).status_code == 400
    assert client.get("/api/render_rgb", params={**p, "scale": "sqrt"}).status_code == 422
    assert client.get("/api/render_rgb", params={k: v for k, v in p.items() if k != "b"}).status_code == 422


def test_render_subwindow_alignment(client, dsf):
    # A 1-pixel bbox at col 50,row 10 renders a uniform colour equal to that pixel's stretch
    x0 = XMIN + 50 * 20
    y1 = YMAX - 10 * 20
    bbox = f"{x0},{y1 - 20},{x0 + 20},{y1}"
    resp = client.get("/api/render", params={"file": dsf["id"], "band": "Rrs_B03", "bbox": bbox, "w": 8, "h": 8,
                                             "vmin": 0, "vmax": 1, "cmap": "gray"})
    a = np.asarray(_png(resp))
    assert a[..., 3].min() == 255
    assert np.all(a == a[0, 0])
