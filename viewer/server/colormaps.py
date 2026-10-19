"""Colour lookup tables (256 x RGBA uint8) and flag palettes.

Continuous maps are loaded from ``colormaps.json`` next to this file
(generated once from matplotlib by ``python -m viewer.server.colormaps
--generate``, committed; matplotlib is NOT a runtime dependency). Required
names: ``gray, viridis, magma, inferno, plasma, turbo, RdBu, RdBu_r, Spectral,
coolwarm``; every LUT is fully opaque (alpha 255). When the json is missing
the module logs a warning and serves built-in ``gray`` and ``viridis`` ramps
so the server still runs.

Flag palettes (``flag_palette_names()``; ``render.py`` is the consumer, NaN
pixels are made transparent there through the validity mask):
- ``status``: index = ``pixel_status`` word; 1 value -> green (60,180,75),
  2 outside_aoi -> dark grey (70,70,70), 4 flagged -> orange (245,130,48),
  8 no_data -> magenta (240,50,230); any other word -> yellow (255,255,0)
  so an unexpected value is visible rather than hidden.
- ``mask`` (one bit): index 0 (bit clear) -> dark grey (40,40,40) alpha 120,
  index 1 (bit set) -> opaque (255,80,80).
- ``count``: index = number of set bits; 0 -> dark grey (40,40,40) alpha
  120, 1..8 an opaque magma-like ramp (purple -> red -> orange -> pale
  yellow), beyond 8 the last ramp colour.

Signatures pinned.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import numpy as np

log = logging.getLogger(__name__)

COLORMAPS_JSON = Path(__file__).with_name("colormaps.json")
REQUIRED_NAMES = ("gray", "viridis", "magma", "inferno", "plasma", "turbo", "RdBu", "RdBu_r", "Spectral", "coolwarm")
FLAG_PALETTES = ("status", "count", "mask")

LUT_SIZE = 256

# Fixed colours (RGB) of the flag palettes.
STATUS_COLOURS: Dict[int, Tuple[int, int, int]] = {
    1: (60, 180, 75),      # value
    2: (70, 70, 70),       # outside_aoi
    4: (245, 130, 48),     # flagged
    8: (240, 50, 230),     # no_data
}
STATUS_OTHER = (255, 255, 0)
BIT_CLEAR = (40, 40, 40, 120)
BIT_SET = (255, 80, 80, 255)
COUNT_ZERO = (40, 40, 40, 120)
# magma sampled at 8 points in [0.25, 0.97]; index 1..8 of the count palette.
COUNT_RAMP: Sequence[Tuple[int, int, int]] = (
    (81, 18, 124), (121, 34, 130), (163, 48, 126), (205, 64, 113),
    (240, 95, 94), (252, 142, 100), (254, 191, 132), (252, 240, 178),
)

# Anchors for the built-in fallback ramps (used only when colormaps.json is absent).
_VIRIDIS_ANCHORS: Sequence[Tuple[int, int, int]] = (
    (68, 1, 84), (71, 45, 123), (59, 82, 139), (44, 114, 142), (33, 145, 140),
    (40, 174, 128), (94, 201, 98), (173, 220, 48), (253, 231, 37),
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _readonly(a: np.ndarray) -> np.ndarray:
    a = np.ascontiguousarray(a, dtype=np.uint8)
    a.flags.writeable = False
    return a


def _rgb_to_lut(rgb: np.ndarray) -> np.ndarray:
    """``(256, 3)`` -> opaque ``(256, 4)`` uint8, read-only."""
    rgb = np.asarray(rgb)
    if rgb.shape != (LUT_SIZE, 3):
        raise ValueError(f"colormap table must be ({LUT_SIZE}, 3), got {rgb.shape}")
    lut = np.empty((LUT_SIZE, 4), dtype=np.uint8)
    lut[:, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    lut[:, 3] = 255
    return _readonly(lut)


def _ramp_from_anchors(anchors: Sequence[Tuple[int, int, int]]) -> np.ndarray:
    """Piecewise-linear interpolation of RGB anchors to 256 entries."""
    pts = np.asarray(anchors, dtype=np.float64)
    xp = np.linspace(0.0, 1.0, len(pts))
    xs = np.linspace(0.0, 1.0, LUT_SIZE)
    rgb = np.stack([np.interp(xs, xp, pts[:, c]) for c in range(3)], axis=1)
    return _rgb_to_lut(np.rint(rgb))


def _builtin_gray() -> np.ndarray:
    ramp = np.arange(LUT_SIZE, dtype=np.uint8)
    return _rgb_to_lut(np.stack([ramp, ramp, ramp], axis=1))


def _builtin_viridis() -> np.ndarray:
    return _ramp_from_anchors(_VIRIDIS_ANCHORS)


def _build_status_palette() -> np.ndarray:
    lut = np.empty((LUT_SIZE, 4), dtype=np.uint8)
    lut[:, :3] = STATUS_OTHER
    lut[:, 3] = 255
    for word, rgb in STATUS_COLOURS.items():
        lut[word, :3] = rgb
    return _readonly(lut)


def _build_bit_palette() -> np.ndarray:
    lut = np.empty((LUT_SIZE, 4), dtype=np.uint8)
    lut[:] = BIT_SET
    lut[0] = BIT_CLEAR
    return _readonly(lut)


def _build_count_palette() -> np.ndarray:
    lut = np.empty((LUT_SIZE, 4), dtype=np.uint8)
    lut[0] = COUNT_ZERO
    for i, rgb in enumerate(COUNT_RAMP, start=1):
        lut[i, :3] = rgb
        lut[i, 3] = 255
    last = len(COUNT_RAMP)
    lut[last + 1:, :3] = COUNT_RAMP[-1]
    lut[last + 1:, 3] = 255
    return _readonly(lut)


_FLAG_LUTS: Dict[str, np.ndarray] = {
    "status": _build_status_palette(),
    "count": _build_count_palette(),
    "mask": _build_bit_palette(),
}


# --------------------------------------------------------------------------
# continuous maps
# --------------------------------------------------------------------------

_LUTS: Dict[str, np.ndarray] = {}
_LOADED_FROM: Path | None = None


def _load_json(path: Path) -> Dict[str, np.ndarray]:
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, dict):
        raise ValueError("colormaps.json must be an object of name -> table")
    out: Dict[str, np.ndarray] = {}
    for name, table in raw.items():
        try:
            out[str(name)] = _rgb_to_lut(np.asarray(table, dtype=np.int64))
        except (ValueError, TypeError) as exc:
            log.warning("colormaps.json: skipping %r (%s)", name, exc)
    return out


def _load(path: Path = COLORMAPS_JSON) -> None:
    global _LOADED_FROM
    _LUTS.clear()
    _LOADED_FROM = None
    if path.exists():
        try:
            _LUTS.update(_load_json(path))
            _LOADED_FROM = path
        except (OSError, ValueError) as exc:
            log.warning("could not read %s (%s); using built-in colormaps", path, exc)
    else:
        log.warning("%s not found; using built-in gray/viridis only "
                    "(run: python -m viewer.server.colormaps --generate)", path)
    missing = [n for n in REQUIRED_NAMES if n not in _LUTS]
    if missing and _LOADED_FROM is not None:
        log.warning("%s lacks required colormaps: %s", path, ", ".join(missing))
    _LUTS.setdefault("gray", _builtin_gray())
    _LUTS.setdefault("viridis", _builtin_viridis())


def reload(path: Path = COLORMAPS_JSON) -> None:
    """Re-read the json (after ``generate_colormaps_json``)."""
    _load(path)


def colormap_names() -> List[str]:
    """Continuous map names in display order (``REQUIRED_NAMES`` first)."""
    names = [n for n in REQUIRED_NAMES if n in _LUTS]
    names.extend(sorted(n for n in _LUTS if n not in REQUIRED_NAMES))
    return names


def flag_palette_names() -> List[str]:
    return list(FLAG_PALETTES)


def get_lut(name: str) -> np.ndarray:
    """``(256, 4) uint8`` RGBA for a continuous map or a flag palette.
    ``KeyError`` for unknown names. Returned arrays are read-only."""
    if name in _LUTS:
        return _LUTS[name]
    if name in _FLAG_LUTS:
        return _FLAG_LUTS[name]
    raise KeyError(name)


def status_palette() -> np.ndarray:
    """``(256, 4)``: index = ``pixel_status`` word (see module docstring)."""
    return _FLAG_LUTS["status"]


def bit_palette() -> np.ndarray:
    """``(256, 4)``: index 0 = bit clear, 1 (and above) = bit set."""
    return _FLAG_LUTS["mask"]


def count_palette() -> np.ndarray:
    """``(256, 4)``: index = number of set bits."""
    return _FLAG_LUTS["count"]


# --------------------------------------------------------------------------
# generation (developer utility; needs matplotlib)
# --------------------------------------------------------------------------

def generate_colormaps_json(path: Path = COLORMAPS_JSON) -> None:
    """Developer utility: write ``colormaps.json`` from matplotlib.

    One colormap per line, ``[r, g, b]`` ints, LF newlines, deterministic.
    """
    import matplotlib  # local import: not a runtime dependency

    xs = np.linspace(0.0, 1.0, LUT_SIZE)
    lines = []
    for i, name in enumerate(REQUIRED_NAMES):
        cmap = matplotlib.colormaps[name]
        rgb = np.rint(np.asarray(cmap(xs))[:, :3] * 255.0).astype(int)
        rgb = np.clip(rgb, 0, 255)
        table = json.dumps(rgb.tolist(), separators=(",", ":"))
        comma = "," if i < len(REQUIRED_NAMES) - 1 else ""
        lines.append(f'  "{name}": {table}{comma}')
    text = "{\n" + "\n".join(lines) + "\n}\n"
    path = Path(path)
    path.write_text(text, encoding="utf-8", newline="\n")
    if path == COLORMAPS_JSON:
        _load(path)


_load()


if __name__ == "__main__":  # pragma: no cover
    import sys
    if "--generate" in sys.argv:
        generate_colormaps_json()
        print(f"wrote {COLORMAPS_JSON}")
