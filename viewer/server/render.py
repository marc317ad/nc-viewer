"""Viewport rendering: sample a band onto an output grid, stretch, colour, PNG.

Contract: plan sections 3.3, 3.5 and the ``/api/render`` row of section 4.
Pure numpy + Pillow; never touches a file. Holds no locks.

WP2 implements every ``NotImplementedError``. Signatures pinned.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

import numpy as np

from .dataset import Grid

MAX_OUTPUT_PX = 4096


def sample_to_grid(arr: np.ndarray, grid: Grid, bbox: Sequence[float], w: int, h: int) -> np.ndarray:
    """Nearest-neighbour sample of ``arr`` (ny, nx) onto an ``(h, w)`` output
    covering world ``bbox = (xmin, ymin, xmax, ymax)``; output row 0 is the
    TOP (``ymax``) edge. Output pixel centres map to source indices via
    ``grid`` (floor); positions outside the source are NaN (float64 output
    when ``arr`` is float64, else float32). Never materialises more than the
    (h, w) output: use index vectors + ``np.take``/fancy indexing with
    clipping and a validity mask.
    """
    raise NotImplementedError("WP2")


def stretch_to_bytes(values: np.ndarray, vmin: float, vmax: float, scale: str = "linear") -> Tuple[np.ndarray, np.ndarray]:
    """Map ``values`` to ``uint8`` indices 0..255 (``vmin`` -> 0, ``vmax`` ->
    255, clipped) and return ``(indices, valid)`` where ``valid`` is the
    finite mask. ``scale="log"`` maps ``log10`` of the values between
    ``log10(vmin)`` and ``log10(vmax)``; non-positive values become invalid;
    when ``vmin <= 0`` it is replaced by the smallest positive of ``vmax/1e4``.
    ``vmax <= vmin`` degenerates gracefully (everything -> 0, no exception).
    """
    raise NotImplementedError("WP2")


def apply_lut(indices: np.ndarray, valid: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """``(h, w, 4) uint8`` RGBA: ``lut[indices]`` with alpha 0 where not valid."""
    raise NotImplementedError("WP2")


def encode_png(rgba: np.ndarray) -> bytes:
    """PNG bytes of an ``(h, w, 4) uint8`` array (Pillow, compress_level 1)."""
    raise NotImplementedError("WP2")


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
      caller (``app.py`` fills them from stats); ``cmap`` default ``viridis``.
    - ``kind="status"``: index = word, palette ``status``; stretch ignored.
    - ``kind="flags"``: with ``bit`` (0-based position into ``flag_masks``, or
      a raw mask when ``flag_masks`` is None) -> palette ``mask`` of
      ``(word & mask) != 0``; without ``bit`` -> palette ``count`` of the
      number of set bits (popcount of the integer word).
    ``ValueError`` for bad ``bbox``/``w``/``h`` (non-positive, over
    ``MAX_OUTPUT_PX``) or unknown ``cmap``.
    """
    raise NotImplementedError("WP2")


def estimate_diff_vmax(diff: np.ndarray) -> float:
    """Symmetric range for a difference image: the 98th percentile of
    ``|diff|`` over finite values (sampled when large); at least ``1e-6``;
    ``1.0`` when nothing is finite."""
    raise NotImplementedError("WP2")


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
    raise NotImplementedError("WP2")
