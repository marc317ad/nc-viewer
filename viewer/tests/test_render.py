"""WP2: rendering and colormaps. Pure numpy, no netCDF fixtures."""

from __future__ import annotations

import io
import time

import numpy as np
import pytest
from PIL import Image

from viewer.server import colormaps
from viewer.server import render
from viewer.server.dataset import Grid

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def pixel_grid(ny: int, nx: int) -> Grid:
    return Grid.pixel_space(ny, nx)


def utm_grid(ny: int, nx: int, x0: float = 583860.0, y0: float = 3000000.0, step: float = 20.0) -> Grid:
    return Grid(kind="projected", crs="EPSG:32617", space="EPSG:32617",
                x0=x0, dx=step, y0=y0, dy=-step, units="m",
                bounds=(x0, y0 - ny * step, x0 + nx * step, y0), has_latlon=False, ny=ny, nx=nx)


def decode(png: bytes) -> np.ndarray:
    img = Image.open(io.BytesIO(png))
    assert img.mode == "RGBA"
    return np.asarray(img)


# --------------------------------------------------------------------------
# sample_to_grid
# --------------------------------------------------------------------------

def test_sample_identity_pixel_space():
    arr = np.arange(24, dtype=np.float32).reshape(4, 6)
    out = render.sample_to_grid(arr, pixel_grid(4, 6), (0, -4, 6, 0), 6, 4)
    assert out.dtype == np.float32
    np.testing.assert_array_equal(out, arr)          # row 0 is the top


def test_sample_projected_subblock_outside_and_border():
    ny, nx = 10, 8
    arr = np.arange(ny * nx, dtype=np.float32).reshape(ny, nx)
    g = utm_grid(ny, nx)
    # bottom-right quarter of the raster: cols 4..7, rows 5..9
    bbox = (g.x0 + 4 * 20, g.y0 - 10 * 20, g.x0 + 8 * 20, g.y0 - 5 * 20)
    out = render.sample_to_grid(arr, g, bbox, 4, 5)
    np.testing.assert_array_equal(out, arr[5:10, 4:8])
    # entirely outside (west of the raster)
    far = (g.x0 - 1000, g.y0 - 100, g.x0 - 800, g.y0)
    assert np.isnan(render.sample_to_grid(arr, g, far, 5, 5)).all()
    # one pixel beyond every edge -> NaN border, interior intact
    bbox2 = (g.x0 - 20, g.y0 - (ny + 1) * 20, g.x0 + (nx + 1) * 20, g.y0 + 20)
    out2 = render.sample_to_grid(arr, g, bbox2, nx + 2, ny + 2)
    assert np.isnan(out2[0, :]).all() and np.isnan(out2[-1, :]).all()
    assert np.isnan(out2[:, 0]).all() and np.isnan(out2[:, -1]).all()
    np.testing.assert_array_equal(out2[1:-1, 1:-1], arr)


def test_sample_zoom_in_repeats_and_zoom_out_subsamples():
    arr = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
    g = pixel_grid(2, 2)
    zoomed = render.sample_to_grid(arr, g, (0, -2, 2, 0), 4, 4)
    expected = np.array([[1, 1, 2, 2], [1, 1, 2, 2], [3, 3, 4, 4], [3, 3, 4, 4]], dtype=np.float32)
    np.testing.assert_array_equal(zoomed, expected)
    big = np.arange(100 * 100, dtype=np.float64).reshape(100, 100)
    small = render.sample_to_grid(big, pixel_grid(100, 100), (0, -100, 100, 0), 10, 10)
    assert small.shape == (10, 10) and small.dtype == np.float64
    assert np.isfinite(small).all()
    # centre of output pixel (0,0) is world (5, -5) -> row 5, col 5
    assert small[0, 0] == big[5, 5]
    # integer input keeps exact words through float64
    words = np.array([[2**32 - 1, 7]], dtype=np.uint32)
    out = render.sample_to_grid(words, pixel_grid(1, 2), (0, -1, 2, 0), 2, 1)
    assert out.dtype == np.float64 and out[0, 0] == 2**32 - 1


def test_sample_rejects_bad_sizes_and_bbox():
    arr = np.zeros((2, 2), dtype=np.float32)
    g = pixel_grid(2, 2)
    with pytest.raises(ValueError):
        render.sample_to_grid(arr, g, (0, -2, 2, 0), 0, 4)
    with pytest.raises(ValueError):
        render.sample_to_grid(arr, g, (0, -2, 2, 0), render.MAX_OUTPUT_PX + 1, 4)
    with pytest.raises(ValueError):
        render.sample_to_grid(arr, g, (2, -2, 0, 0), 4, 4)        # xmin > xmax


# --------------------------------------------------------------------------
# stretch / lut / png
# --------------------------------------------------------------------------

def test_stretch_linear_endpoints_and_degenerate():
    v = np.array([0.0, 10.0, 5.0, -1.0, 11.0, np.nan], dtype=np.float32)
    idx, valid = render.stretch_to_bytes(v, 0.0, 10.0)
    assert idx.dtype == np.uint8
    assert idx[0] == 0 and idx[1] == 255 and idx[2] in (127, 128)
    assert idx[3] == 0 and idx[4] == 255
    assert valid.tolist() == [True, True, True, True, True, False]
    idx2, valid2 = render.stretch_to_bytes(v, 5.0, 5.0)          # no division by zero
    assert valid2[:5].all() and not valid2[5]
    assert (idx2[:5] == 128).all()


def test_stretch_log_monotone_and_floor():
    v = np.array([1e-3, 1e-2, 1e-1, 1.0, 10.0, 0.0, -1.0], dtype=np.float64)
    idx, valid = render.stretch_to_bytes(v, 1e-3, 10.0, "log")
    assert idx[0] == 0 and idx[4] == 255
    assert np.all(np.diff(idx[:5].astype(int)) > 0)                # strictly increasing
    assert idx[2] in (127, 128)                                   # log-midpoint
    assert not valid[5] and not valid[6]                           # non-positive invalid
    idx2, _ = render.stretch_to_bytes(v, 0.0, 10.0, "log")         # vmin <= 0 gets a floor
    assert idx2[4] == 255 and np.all(np.diff(idx2[:5].astype(int)) >= 0)
    with pytest.raises(ValueError):
        render.stretch_to_bytes(v, 0.0, 1.0, "sqrt")


def test_apply_lut_alpha_zero_on_invalid():
    lut = colormaps.get_lut("gray")
    idx = np.array([[0, 255], [128, 7]], dtype=np.uint8)
    valid = np.array([[True, False], [True, True]])
    rgba = render.apply_lut(idx, valid, lut)
    assert rgba.shape == (2, 2, 4) and rgba.dtype == np.uint8
    assert tuple(rgba[0, 0]) == (0, 0, 0, 255)
    assert rgba[0, 1, 3] == 0
    assert tuple(rgba[1, 0, :3]) == (128, 128, 128) and rgba[1, 0, 3] == 255


def test_encode_png_roundtrip():
    rgba = np.zeros((5, 7, 4), dtype=np.uint8)
    rgba[..., 0] = 200
    rgba[..., 3] = 255
    rgba[2, 3, 3] = 0
    png = render.encode_png(rgba)
    assert isinstance(png, bytes) and png.startswith(PNG_SIG)
    back = decode(png)
    assert back.shape == (5, 7, 4)
    np.testing.assert_array_equal(back, rgba)


# --------------------------------------------------------------------------
# render_band
# --------------------------------------------------------------------------

def test_render_band_status_palette():
    arr = np.array([[1.0, 2.0], [np.nan, 8.0]], dtype=np.float64)
    png = render.render_band(arr, pixel_grid(2, 2), (0, -2, 2, 0), 2, 2, kind="status", cmap="ignored")
    img = decode(png)
    assert tuple(img[0, 0]) == (60, 180, 75, 255)
    assert tuple(img[0, 1]) == (70, 70, 70, 255)
    assert img[1, 0, 3] == 0
    assert tuple(img[1, 1]) == (240, 50, 230, 255)


def test_render_band_flags_bit_and_count_views():
    masks = [1, 2, 4, 8, 2**31]
    # pixel (0,0): bits 1 and 8 set -> count 2; (0,1): none; (0,2): top bit; (1,*): NaN / all five
    arr = np.array([[9.0, 0.0, float(2**31)], [np.nan, float(2**32 - 1), 8.0]], dtype=np.float64)
    g = pixel_grid(2, 3)
    bbox = (0, -2, 3, 0)
    count_lut = colormaps.count_palette()
    img = decode(render.render_band(arr, g, bbox, 3, 2, kind="flags", flag_masks=masks))
    assert tuple(img[0, 0]) == tuple(count_lut[2])
    assert tuple(img[0, 1]) == tuple(count_lut[0])
    assert tuple(img[0, 2]) == tuple(count_lut[1])
    assert img[1, 0, 3] == 0
    assert tuple(img[1, 1]) == tuple(count_lut[5])          # all five masks set, other bits ignored
    bit_lut = colormaps.bit_palette()
    img_b = decode(render.render_band(arr, g, bbox, 3, 2, kind="flags", bit=3, flag_masks=masks))
    assert tuple(img_b[0, 0]) == tuple(bit_lut[1]) == (255, 80, 80, 255)
    assert tuple(img_b[0, 1]) == tuple(bit_lut[0])
    assert tuple(img_b[1, 2]) == tuple(bit_lut[1])
    assert img_b[1, 0, 3] == 0
    img_top = decode(render.render_band(arr, g, bbox, 3, 2, kind="flags", bit=4, flag_masks=masks))
    assert tuple(img_top[0, 2]) == tuple(bit_lut[1]) and tuple(img_top[0, 0]) == tuple(bit_lut[0])
    with pytest.raises(ValueError):
        render.render_band(arr, g, bbox, 3, 2, kind="flags", bit=5, flag_masks=masks)
    # no flag_masks: bit is a plain position, count is the popcount of the word
    img_raw = decode(render.render_band(arr, g, bbox, 3, 2, kind="flags", bit=31))
    assert tuple(img_raw[0, 2]) == tuple(bit_lut[1])
    img_pc = decode(render.render_band(arr, g, bbox, 3, 2, kind="flags"))
    assert tuple(img_pc[1, 1]) == tuple(count_lut[32])
    with pytest.raises(ValueError):
        render.render_band(arr, g, bbox, 3, 2, kind="flags", bit=32)


def test_render_band_float_errors_and_default_cmap():
    arr = np.linspace(0, 1, 16, dtype=np.float32).reshape(4, 4)
    g = pixel_grid(4, 4)
    bbox = (0, -4, 4, 0)
    with pytest.raises(ValueError):
        render.render_band(arr, g, bbox, 4, 4, kind="float", vmin=0.0, vmax=1.0, cmap="nope")
    with pytest.raises(ValueError):
        render.render_band(arr, g, bbox, 4, 4, kind="float", vmin=None, vmax=1.0)
    with pytest.raises(ValueError):
        render.render_band(arr, g, bbox, 4, 4, kind="float", vmin=0.0, vmax=1.0, scale="cubic")
    with pytest.raises(ValueError):
        render.render_band(arr, g, bbox, 4, 4, kind="mystery", vmin=0.0, vmax=1.0)
    img = decode(render.render_band(arr, g, bbox, 4, 4, kind="float", vmin=0.0, vmax=1.0))
    assert tuple(img[0, 0]) == (0, 0, 0, 255) and tuple(img[3, 3]) == (255, 255, 255, 255)
    img_v = decode(render.render_band(arr, g, bbox, 4, 4, kind="latlon", vmin=0.0, vmax=1.0, cmap="viridis"))
    assert img_v.shape == (4, 4, 4) and (img_v[..., 3] == 255).all()


# --------------------------------------------------------------------------
# diff
# --------------------------------------------------------------------------

def test_render_diff_identical_and_shifted():
    rng = np.random.default_rng(1)
    a = rng.random((6, 8), dtype=np.float32)
    g = utm_grid(6, 8)
    bbox = g.bounds
    png, used = render.render_diff(a, g, a.copy(), g, bbox, 8, 6)
    assert used > 0
    img = decode(png)
    mid = colormaps.get_lut("RdBu")[128]
    assert (img.reshape(-1, 4) == mid).all()
    # B on a grid shifted east by one pixel: B's column c sits at A's column c+1
    g_b = utm_grid(6, 8, x0=g.x0 + 20)
    b = np.zeros_like(a)
    png2, used2 = render.render_diff(a, g, b, g_b, bbox, 8, 6, vmax=1.0)
    assert used2 == 1.0
    img2 = decode(png2)
    assert (img2[:, 0, 3] == 0).all()                 # B has no data west of its x0
    assert (img2[:, 1:, 3] == 255).all()
    # explicit estimate: diff a - 0 = a, so the colour follows a's own stretch
    expected_idx, _ = render.stretch_to_bytes(a[:, 1:], -1.0, 1.0)
    np.testing.assert_array_equal(img2[:, 1:, :3], colormaps.get_lut("RdBu")[expected_idx][..., :3])
    assert render.estimate_diff_vmax(np.full((3, 3), np.nan)) > 0
    assert render.estimate_diff_vmax(np.zeros((3, 3))) > 0
    with pytest.raises(ValueError):
        render.render_diff(a, g, b, g_b, bbox, 8, 6, vmax=0.0)
    with pytest.raises(ValueError):
        render.render_diff(a, g, b, g_b, bbox, 8, 6, cmap="nope")


# --------------------------------------------------------------------------
# performance smoke
# --------------------------------------------------------------------------

def test_render_rgb_channels_gamma_and_alpha():
    g = pixel_grid(2, 3)
    bbox = (0, -2, 3, 0)
    r = np.array([[0.0, 0.5, 1.0], [1.0, np.nan, 0.0]], dtype=np.float32)
    gg = np.array([[1.0, 0.5, 0.0], [0.0, 0.5, 1.0]], dtype=np.float32)
    b = np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]], dtype=np.float32)
    unit = [(0.0, 1.0)] * 3
    img = decode(render.render_rgb([r, gg, b], [g] * 3, bbox, 3, 2, ranges=unit))
    assert img.shape == (2, 3, 4)
    assert tuple(img[0, 0]) == (0, 255, 0, 255)
    assert tuple(img[0, 1]) == (128, 128, 0, 255)
    assert tuple(img[0, 2]) == (255, 0, 0, 255)
    assert tuple(img[1, 0]) == (255, 0, 255, 255)
    assert tuple(img[1, 1]) == (0, 0, 0, 0)            # NaN in one channel -> transparent
    assert tuple(img[1, 2]) == (0, 255, 255, 255)
    # per-channel ranges: halving the red range saturates red at 0.5
    img2 = decode(render.render_rgb([r, gg, b], [g] * 3, bbox, 3, 2, ranges=[(0.0, 0.5), (0.0, 1.0), (0.0, 1.0)]))
    assert img2[0, 1, 0] == 255 and img2[0, 1, 1] == 128
    # gamma 2 lifts the mid-tones (index 128 -> 255 * sqrt(128/255) = 181), endpoints unchanged
    img3 = decode(render.render_rgb([r, gg, b], [g] * 3, bbox, 3, 2, ranges=unit, gamma=2.0))
    assert img3[0, 1, 0] == 181 and img3[0, 1, 1] == 181
    assert tuple(img3[0, 0]) == (0, 255, 0, 255)
    # log scale: a non-positive channel makes the pixel transparent
    pos = np.full((2, 3), 0.5, dtype=np.float32)
    img4 = decode(render.render_rgb([pos, pos, r], [g] * 3, bbox, 3, 2, ranges=[(0.01, 1.0)] * 3, scale="log"))
    assert img4[0, 0, 3] == 0 and img4[0, 2, 3] == 255
    # errors
    with pytest.raises(ValueError):
        render.render_rgb([r, gg], [g] * 2, bbox, 3, 2, ranges=unit[:2])
    with pytest.raises(ValueError):
        render.render_rgb([r, gg, b], [g] * 3, bbox, 3, 2, ranges=unit, gamma=0)
    with pytest.raises(ValueError):
        render.render_rgb([r, gg, b], [g] * 3, bbox, 3, 2, ranges=unit, scale="sqrt")
    with pytest.raises(ValueError):
        render.render_rgb([r, gg, b], [g] * 3, bbox, 0, 2, ranges=unit)


def test_sample_perf_smoke():
    rng = np.random.default_rng(0)
    arr = rng.random((5500, 5500), dtype=np.float32)
    g = utm_grid(5500, 5500)
    bbox = g.bounds
    render.sample_to_grid(arr, g, bbox, 16, 10)       # warm up
    t0 = time.perf_counter()
    out = render.sample_to_grid(arr, g, bbox, 1600, 1000)
    dt = time.perf_counter() - t0
    assert out.shape == (1000, 1600)
    assert dt < 2.0, f"sampling took {dt:.3f} s"
    t1 = time.perf_counter()
    png = render.render_band(arr, g, bbox, 1600, 1000, kind="float", vmin=0.0, vmax=1.0, cmap="viridis")
    dt2 = time.perf_counter() - t1
    assert png.startswith(PNG_SIG)
    assert dt2 < 3.0, f"full render took {dt2:.3f} s"


# --------------------------------------------------------------------------
# colormaps
# --------------------------------------------------------------------------

def test_colormaps_generated_and_required_names(tmp_path):
    assert colormaps.COLORMAPS_JSON.exists()
    assert colormaps.COLORMAPS_JSON.stat().st_size < 60 * 1024
    assert b"\r" not in colormaps.COLORMAPS_JSON.read_bytes()
    names = colormaps.colormap_names()
    assert names[: len(colormaps.REQUIRED_NAMES)] == list(colormaps.REQUIRED_NAMES)
    for name in colormaps.REQUIRED_NAMES:
        lut = colormaps.get_lut(name)
        assert lut.shape == (256, 4) and lut.dtype == np.uint8
        assert (lut[:, 3] == 255).all()
        assert not lut.flags.writeable
    for name in colormaps.flag_palette_names():
        assert colormaps.get_lut(name).shape == (256, 4)
    with pytest.raises(KeyError):
        colormaps.get_lut("not-a-map")
    # regeneration is deterministic
    out = tmp_path / "cm.json"
    colormaps.generate_colormaps_json(out)
    assert out.read_bytes() == colormaps.COLORMAPS_JSON.read_bytes()
