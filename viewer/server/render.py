"""Viewport rendering: sample a band onto an output grid, stretch, colour, PNG.

Contract: plan sections 3.3, 3.5 and the ``/api/render`` row of section 4.
Pure numpy + Pillow; never touches a file. Holds no locks.

Pipeline per request: ``sample_to_grid`` (nearest neighbour, one 2-D gather)
-> ``stretch_to_bytes`` or a flag-word lookup -> ``apply_lut`` -> ``encode_png``.
Bad arguments raise ``ValueError`` (``app.py`` maps it to 400); a blank image
is never returned silently.

Signatures pinned.
"""

from __future__ import annotations

import io
import zlib
from typing import Optional, Sequence, Tuple

import numpy as np
from PIL import Image

from . import colormaps
from .dataset import Grid

MAX_OUTPUT_PX = 4096

# Sub-sampling cap for ``estimate_diff_vmax`` (elements of the difference image).
_PERCENTILE_SAMPLE = 4_000_000
_LOG_FLOOR = 1e-6
_DEGENERATE_INDEX = 128


# --------------------------------------------------------------------------
# argument checks
# --------------------------------------------------------------------------

def _check_size(w: int, h: int) -> Tuple[int, int]:
    try:
        w_i, h_i = int(w), int(h)
    except (TypeError, ValueError):
        raise ValueError(f"w and h must be integers, got {w!r}, {h!r}") from None
    if w_i < 1 or h_i < 1 or w_i > MAX_OUTPUT_PX or h_i > MAX_OUTPUT_PX:
        raise ValueError(f"w and h must be in 1..{MAX_OUTPUT_PX}, got {w_i}x{h_i}")
    return w_i, h_i


def _check_bbox(bbox: Sequence[float]) -> Tuple[float, float, float, float]:
    try:
        xmin, ymin, xmax, ymax = (float(v) for v in bbox)
    except (TypeError, ValueError):
        raise ValueError(f"bbox must be four numbers xmin,ymin,xmax,ymax, got {bbox!r}") from None
    if not all(np.isfinite(v) for v in (xmin, ymin, xmax, ymax)):
        raise ValueError("bbox values must be finite")
    if not (xmax > xmin) or not (ymax > ymin):
        raise ValueError(f"bbox must satisfy xmin < xmax and ymin < ymax, got {bbox!r}")
    return xmin, ymin, xmax, ymax


def _check_grid(grid: Grid) -> None:
    if not (grid.dx > 0) or not (grid.dy < 0) or not np.isfinite(grid.dx) or not np.isfinite(grid.dy):
        raise ValueError(f"grid must have dx > 0 and dy < 0, got dx={grid.dx}, dy={grid.dy}")


def _output_dtype(arr: np.ndarray) -> np.dtype:
    """float32 for float32/float16 input; float64 for float64 and for
    integer/bool input (keeps 32-bit flag words exact)."""
    if arr.dtype == np.float32 or arr.dtype == np.float16:
        return np.dtype(np.float32)
    return np.dtype(np.float64)


# --------------------------------------------------------------------------
# sampling
# --------------------------------------------------------------------------

def sample_to_grid(arr: np.ndarray, grid: Grid, bbox: Sequence[float], w: int, h: int) -> np.ndarray:
    """Nearest-neighbour sample of ``arr`` (ny, nx) onto an ``(h, w)`` output
    covering world ``bbox = (xmin, ymin, xmax, ymax)``; output row 0 is the
    TOP (``ymax``) edge. Output pixel centres map to source indices via
    ``grid`` (floor); positions outside the source are NaN (float64 output
    when ``arr`` is float64 or integer, else float32). Never materialises
    more than the (h, w) output: 1-D index vectors + one 2-D gather.
    """
    arr = np.asarray(arr)
    if arr.ndim != 2:
        raise ValueError(f"arr must be 2-D (ny, nx), got shape {arr.shape}")
    w, h = _check_size(w, h)
    xmin, ymin, xmax, ymax = _check_bbox(bbox)
    _check_grid(grid)
    ny, nx = arr.shape
    out_dtype = _output_dtype(arr)

    # World centres of the output pixels (float64 for the index arithmetic).
    xs = xmin + (np.arange(w, dtype=np.float64) + 0.5) * ((xmax - xmin) / w)
    ys = ymax - (np.arange(h, dtype=np.float64) + 0.5) * ((ymax - ymin) / h)
    cols = np.floor((xs - grid.x0) / grid.dx).astype(np.int64)
    rows = np.floor((ys - grid.y0) / grid.dy).astype(np.int64)
    col_ok = (cols >= 0) & (cols < nx)
    row_ok = (rows >= 0) & (rows < ny)

    if ny == 0 or nx == 0 or not col_ok.any() or not row_ok.any():
        return np.full((h, w), np.nan, dtype=out_dtype)

    np.clip(cols, 0, nx - 1, out=cols)
    np.clip(rows, 0, ny - 1, out=rows)
    out = arr[np.ix_(rows, cols)]               # (h, w) gather, a fresh array
    if out.dtype != out_dtype:
        out = out.astype(out_dtype)
    if not row_ok.all():
        out[~row_ok, :] = np.nan
    if not col_ok.all():
        out[:, ~col_ok] = np.nan
    return out


# --------------------------------------------------------------------------
# stretch / LUT / PNG
# --------------------------------------------------------------------------

def stretch_to_bytes(values: np.ndarray, vmin: float, vmax: float, scale: str = "linear") -> Tuple[np.ndarray, np.ndarray]:
    """Map ``values`` to ``uint8`` indices 0..255 (``vmin`` -> 0, ``vmax`` ->
    255, clipped, rounded to nearest) and return ``(indices, valid)`` where
    ``valid`` is the finite mask. ``scale="log"`` maps ``log10`` of the
    values between ``log10(vmin)`` and ``log10(vmax)``; non-positive values
    become invalid; when ``vmin <= 0`` it is replaced by ``vmax / 1e4`` (or
    ``1e-6`` when ``vmax`` is not positive either). ``vmax <= vmin``
    degenerates gracefully: every valid pixel -> 128, no exception.
    """
    v = np.asarray(values)
    if v.dtype.kind != "f":
        v = v.astype(np.float64)
    vmin = float(vmin)
    vmax = float(vmax)
    if not (np.isfinite(vmin) and np.isfinite(vmax)):
        raise ValueError(f"vmin and vmax must be finite, got {vmin}, {vmax}")
    valid = np.isfinite(v)

    if scale == "linear":
        lo, hi = vmin, vmax
        src = v
    elif scale == "log":
        lo = vmin if vmin > 0 else (vmax / 1e4 if vmax > 0 else _LOG_FLOOR)
        hi = vmax if vmax > 0 else lo * 10.0
        positive = v > 0
        valid &= positive
        with np.errstate(invalid="ignore", divide="ignore"):
            src = np.log10(np.where(positive, v, 1.0))
        lo, hi = np.log10(lo), np.log10(hi)
    else:
        raise ValueError(f"scale must be 'linear' or 'log', got {scale!r}")

    if not (hi > lo):
        indices = np.full(v.shape, _DEGENERATE_INDEX, dtype=np.uint8)
        return indices, valid

    with np.errstate(invalid="ignore", over="ignore"):
        t = (src - lo) * (255.0 / (hi - lo)) + 0.5
    t = np.nan_to_num(t, copy=False, nan=0.0, posinf=255.0, neginf=0.0)
    np.clip(t, 0.0, 255.0, out=t)
    indices = t.astype(np.uint8)
    return indices, valid


def apply_lut(indices: np.ndarray, valid: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """``(h, w, 4) uint8`` RGBA: ``lut[indices]`` with alpha 0 where not valid."""
    lut = np.asarray(lut)
    if lut.ndim != 2 or lut.shape[1] != 4 or lut.dtype != np.uint8:
        raise ValueError(f"lut must be (N, 4) uint8, got {lut.shape} {lut.dtype}")
    idx = np.asarray(indices)
    if idx.dtype != np.uint8 and (idx.min(initial=0) < 0 or idx.max(initial=0) >= lut.shape[0]):
        raise ValueError("indices out of range for lut")
    rgba = np.take(lut, idx, axis=0)             # fresh (h, w, 4) array
    valid = np.asarray(valid, dtype=bool)
    if valid.shape != idx.shape:
        raise ValueError(f"valid mask shape {valid.shape} != indices shape {idx.shape}")
    if not valid.all():
        rgba[~valid, 3] = 0
    return rgba


def encode_png(rgba: np.ndarray) -> bytes:
    """PNG bytes of an ``(h, w, 4) uint8`` array (Pillow, compress_level 1).

    zlib strategy ``Z_RLE`` (Pillow's ``compress_type``): measured 2026-10-19
    at 1600x1000 it is faster than the default strategy on every image type
    (81 vs 112 ms on noise, 48 vs 58 ms half-transparent, 37 vs 38 ms on a
    smooth field) and the files are 20-30 % smaller, because transparent
    runs and filtered residuals compress as runs.
    """
    rgba = np.ascontiguousarray(rgba)
    if rgba.ndim != 3 or rgba.shape[2] != 4 or rgba.dtype != np.uint8:
        raise ValueError(f"rgba must be (h, w, 4) uint8, got {rgba.shape} {rgba.dtype}")
    img = Image.fromarray(rgba)                  # uint8 (h, w, 4) -> RGBA
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=1, compress_type=zlib.Z_RLE)
    return buf.getvalue()


# --------------------------------------------------------------------------
# flag words
# --------------------------------------------------------------------------

def _to_words(sampled: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Float samples -> ``uint64`` words (NaN -> 0). Negative values (signed
    32-bit flag words with the top bit set) are reinterpreted as their
    unsigned 32-bit pattern. Exact for words < 2**53."""
    with np.errstate(invalid="ignore"):
        clean = np.where(valid, sampled, 0.0)
    signed = np.rint(clean).astype(np.int64)
    neg = signed < 0
    words = signed.astype(np.uint64)
    if neg.any():
        words[neg] &= np.uint64(0xFFFFFFFF)
    return words


def _mask_to_uint64(mask: int) -> np.uint64:
    m = int(mask)
    if m < 0:
        m &= 0xFFFFFFFF                           # signed int32 mask -> unsigned pattern
    if m > 0xFFFFFFFFFFFFFFFF:
        raise ValueError(f"flag mask {mask!r} does not fit in 64 bits")
    return np.uint64(m)


def _count_set(words: np.ndarray, flag_masks: Optional[Sequence[int]]) -> np.ndarray:
    """Number of set flags per pixel: popcount over the union of
    ``flag_masks`` when every mask is a single bit, else the number of masks
    with any bit set; popcount of the whole word when ``flag_masks`` is None
    or empty."""
    if flag_masks is None or len(flag_masks) == 0:
        return np.bitwise_count(words)
    masks = [_mask_to_uint64(m) for m in flag_masks]
    nonzero = [m for m in masks if int(m) != 0]
    if all((int(m) & (int(m) - 1)) == 0 for m in nonzero):
        union = np.uint64(0)
        for m in nonzero:
            union |= m
        return np.bitwise_count(words & union)
    count = np.zeros(words.shape, dtype=np.uint8)
    for m in nonzero:
        count += ((words & m) != 0).astype(np.uint8)
    return count


# --------------------------------------------------------------------------
# public renders
# --------------------------------------------------------------------------

def _continuous_lut(name: str) -> np.ndarray:
    if name not in colormaps.colormap_names():
        raise ValueError(f"unknown colormap {name!r}; available: {', '.join(colormaps.colormap_names())}")
    return colormaps.get_lut(name)


def render_band(
    arr: np.ndarray,
    grid: Grid,
    bbox: Sequence[float],
    w: int,
    h: int,
    *,
    kind: str = "float",
    vmin: Optional[float] = None,
    vmax: Optional[float] = None,
    cmap: Optional[str] = None,
    scale: str = "linear",
    bit: Optional[int] = None,
    flag_masks: Optional[Sequence[int]] = None,
) -> bytes:
    """PNG for one band over ``bbox``.

    - ``kind="float"`` (and ``"latlon"``): ``vmin``/``vmax`` required by the
      caller (``app.py`` fills them from stats; ``ValueError`` when None);
      ``cmap`` default ``gray``; ``scale`` ``linear`` or ``log``.
    - ``kind="status"``: index = word, palette ``status``; stretch and
      ``cmap`` ignored; NaN transparent.
    - ``kind="flags"``: with ``bit`` -> palette ``mask`` of ``(word & mask)
      != 0`` where ``mask = flag_masks[bit]`` when ``flag_masks`` is given,
      else ``1 << bit`` (``bit`` in 0..31); without ``bit`` -> palette
      ``count`` of the number of set flags (popcount over ``flag_masks``, or
      over every bit of the word when ``flag_masks`` is None). NaN transparent.
    ``ValueError`` for bad ``bbox``/``w``/``h`` (non-positive, over
    ``MAX_OUTPUT_PX``), unknown ``cmap``/``scale``/``kind``, missing
    ``vmin``/``vmax`` or a ``bit`` out of range.
    """
    w, h = _check_size(w, h)
    kind = kind or "float"

    if kind in ("float", "latlon"):
        if vmin is None or vmax is None:
            raise ValueError(f"vmin and vmax are required for kind={kind!r}")
        if scale not in ("linear", "log"):
            raise ValueError(f"scale must be 'linear' or 'log', got {scale!r}")
        lut = _continuous_lut(cmap or "gray")
        sampled = sample_to_grid(arr, grid, bbox, w, h)
        indices, valid = stretch_to_bytes(sampled, vmin, vmax, scale)
        return encode_png(apply_lut(indices, valid, lut))

    if kind == "status":
        sampled = sample_to_grid(arr, grid, bbox, w, h)
        valid = np.isfinite(sampled)
        words = _to_words(sampled, valid)
        indices = np.minimum(words, np.uint64(255)).astype(np.uint8)
        return encode_png(apply_lut(indices, valid, colormaps.status_palette()))

    if kind == "flags":
        mask: Optional[np.uint64] = None
        if bit is not None:
            try:
                bit_i = int(bit)
            except (TypeError, ValueError):
                raise ValueError(f"bit must be an integer, got {bit!r}") from None
            if flag_masks is not None and len(flag_masks) > 0:
                if not 0 <= bit_i < len(flag_masks):
                    raise ValueError(f"bit {bit_i} out of range: this band has {len(flag_masks)} flags (0..{len(flag_masks) - 1})")
                mask = _mask_to_uint64(flag_masks[bit_i])
            else:
                if not 0 <= bit_i < 32:
                    raise ValueError(f"bit {bit_i} out of range 0..31")
                mask = np.uint64(1 << bit_i)
        sampled = sample_to_grid(arr, grid, bbox, w, h)
        valid = np.isfinite(sampled)
        words = _to_words(sampled, valid)
        if mask is not None:
            indices = ((words & mask) != 0).astype(np.uint8)
            lut = colormaps.bit_palette()
        else:
            indices = np.minimum(_count_set(words, flag_masks), 255).astype(np.uint8)
            lut = colormaps.count_palette()
        return encode_png(apply_lut(indices, valid, lut))

    raise ValueError(f"unknown kind {kind!r}; expected float, latlon, status or flags")


def estimate_diff_vmax(diff: np.ndarray) -> float:
    """Symmetric range for a difference image: the 98th percentile of
    ``|diff|`` over finite values (sub-sampled beyond 4 M elements); ``1e-6``
    when nothing is finite or the percentile is 0 (never returns 0)."""
    flat = np.asarray(diff).ravel()
    if flat.size > _PERCENTILE_SAMPLE:
        step = int(np.ceil(flat.size / _PERCENTILE_SAMPLE))
        flat = flat[::step]
    if flat.dtype.kind != "f":
        flat = flat.astype(np.float64)
    finite = flat[np.isfinite(flat)]
    if finite.size == 0:
        return _LOG_FLOOR
    v = float(np.percentile(np.abs(finite), 98))
    if not np.isfinite(v) or v <= 0:
        return _LOG_FLOOR
    return v


def render_diff(
    arr_a: np.ndarray,
    grid_a: Grid,
    arr_b: np.ndarray,
    grid_b: Grid,
    bbox: Sequence[float],
    w: int,
    h: int,
    *,
    vmax: Optional[float] = None,
    cmap: str = "RdBu",
) -> Tuple[bytes, float]:
    """``A - B`` sampled on the same output grid (each from its own ``grid``),
    rendered with a diverging map over ``[-vmax, vmax]`` (``vmax`` from
    ``estimate_diff_vmax`` when None). Returns ``(png, vmax_used)``."""
    w, h = _check_size(w, h)
    lut = _continuous_lut(cmap or "RdBu")
    a = sample_to_grid(arr_a, grid_a, bbox, w, h)
    b = sample_to_grid(arr_b, grid_b, bbox, w, h)
    diff = a - b                                 # NaN where either side is NaN
    if vmax is None:
        used = estimate_diff_vmax(diff)
    else:
        used = float(vmax)
        if not np.isfinite(used) or used <= 0:
            raise ValueError(f"vmax must be a positive number, got {vmax!r}")
    indices, valid = stretch_to_bytes(diff, -used, used, "linear")
    return encode_png(apply_lut(indices, valid, lut)), used
