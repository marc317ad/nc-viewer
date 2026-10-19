"""Band statistics and histograms (plan section 4, ``BandStats``).

WP1 implements. Signatures pinned.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np

from .dataset import DatasetHandle

MAX_SAMPLES = 4_000_000
HIST_BINS = 64


def compute_stats(
    arr: np.ndarray,
    kind: str,
    flag_meanings: Optional[List[str]] = None,
    flag_masks: Optional[List[int]] = None,
    max_samples: int = MAX_SAMPLES,
) -> dict:
    """Pure function: ``BandStats`` minus the ``band`` key.

    - ``count_total`` = ``arr.size``; ``count_valid`` = finite count.
    - Float kinds: when ``count_valid > max_samples`` compute percentiles,
      mean, std and histograms on an evenly strided subsample of the finite
      values (``sampled=True``); min/max always exact. Percentile keys
      ``p1 p2 p5 p50 p95 p98 p99``. ``hist`` has 64 bins between ``p1`` and
      ``p99`` (edges list of 65, counts list of 64 ints over the sample);
      ``log_hist`` likewise over ``log10`` when ``min > 0``, else None.
      With no finite values every numeric field is None and hist is None.
    - ``flags``/``status`` kinds: ``flag_counts`` maps each meaning to the
      number of pixels with that bit set (``status`` uses the four standard
      meanings when attrs are missing); ``min``/``max`` of the word; the
      percentile and histogram fields are None.
    All numbers are plain Python floats/ints (JSON serialisable).
    """
    raise NotImplementedError("WP1")


def band_stats(handle: DatasetHandle, band: str) -> dict:
    """``BandStats`` for ``band`` of ``handle`` (``KeyError`` for unknown
    band). Cached in ``handle.cache`` under ``("stats", handle.path, band)``."""
    raise NotImplementedError("WP1")
