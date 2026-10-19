"""Colour lookup tables (256 x RGBA uint8) and flag palettes.

Continuous maps are loaded from ``colormaps.json`` next to this file
(generated once from matplotlib by ``python -m viewer.server.colormaps
--generate``, committed; matplotlib is NOT a runtime dependency). Required
names: ``gray, viridis, magma, inferno, plasma, turbo, RdBu, Spectral,
coolwarm``; every LUT is fully opaque (alpha 255).

Flag palettes (``flag_palette_names()``):
- ``status``: index = ``pixel_status`` word; 1 value -> green (60,180,75),
  2 outside_aoi -> dark grey (70,70,70), 4 flagged -> orange (245,130,48),
  8 no_data -> magenta (240,50,230); other indices transparent.
- ``count``: index = number of set bits (0..N); 0 -> transparent, 1..12 a
  categorical ramp (yellow -> red -> purple), beyond -> white.
- ``mask``: index 0 -> transparent, 1 -> opaque yellow (255,220,0).

WP2 implements. Signatures pinned.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import numpy as np

COLORMAPS_JSON = Path(__file__).with_name("colormaps.json")
REQUIRED_NAMES = ("gray", "viridis", "magma", "inferno", "plasma", "turbo", "RdBu", "Spectral", "coolwarm")
FLAG_PALETTES = ("status", "count", "mask")


def colormap_names() -> List[str]:
    """Continuous map names in display order (``REQUIRED_NAMES`` first)."""
    raise NotImplementedError("WP2")


def flag_palette_names() -> List[str]:
    return list(FLAG_PALETTES)


def get_lut(name: str) -> np.ndarray:
    """``(256, 4) uint8`` RGBA for a continuous map or a flag palette.
    ``KeyError`` for unknown names. Returned arrays are read-only."""
    raise NotImplementedError("WP2")


def generate_colormaps_json(path: Path = COLORMAPS_JSON) -> None:
    """Developer utility: write ``colormaps.json`` from matplotlib."""
    raise NotImplementedError("WP2")


if __name__ == "__main__":  # pragma: no cover
    import sys
    if "--generate" in sys.argv:
        generate_colormaps_json()
        print(f"wrote {COLORMAPS_JSON}")
