"""Band statistics and histograms (plan section 4, ``BandStats``).

The dict shape follows the plan (``count_total``, ``count_valid``,
``sampled``, ``min`` .. ``p99``, ``hist``, ``log_hist``, ``flag_counts``)
and additionally carries ``kind``, ``count``/``valid``/``nan_count`` aliases,
a ``flags`` list (``[{name, mask, count}]``) for flags/status kinds and a
``counts`` dict (meaning -> n, plus ``fill``) for the status kind. Every
number is a plain Python int/float and NaN is rendered as None.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from .dataset import STATUS_MEANINGS, DatasetHandle

MAX_SAMPLES = 4_000_000
HIST_BINS = 64
PERCENTILES = (1, 2, 5, 50, 95, 98, 99)
STATS_NBYTES = 8192   # nominal cache charge for one stats dict


def _f(value) -> Optional[float]:
    """JSON-safe float: None for NaN/inf/None."""
    if value is None:
        return None
    f = float(value)
    return f if math.isfinite(f) else None


def _empty(kind: str, total: int, valid: int) -> dict:
    return {
        "kind": kind,
        "count_total": int(total), "count_valid": int(valid),
        "count": int(total), "valid": int(valid), "nan_count": int(total - valid),
        "sampled": False,
        "min": None, "max": None, "mean": None, "std": None,
        "p1": None, "p2": None, "p5": None, "p50": None, "p95": None, "p98": None, "p99": None,
        "hist": None, "log_hist": None, "flag_counts": None,
    }


def _histogram(values: np.ndarray, lo: float, hi: float) -> Optional[dict]:
    if not (math.isfinite(lo) and math.isfinite(hi)) or values.size == 0:
        return None
    counts, edges = np.histogram(values, bins=HIST_BINS, range=(min(lo, hi), max(lo, hi)))
    return {"edges": [float(e) for e in edges], "counts": [int(c) for c in counts]}


def _sample(values: np.ndarray, max_samples: int) -> tuple:
    if max_samples > 0 and values.size > max_samples:
        stride = int(math.ceil(values.size / max_samples))
        return values[::stride], True
    return values, False


def _float_stats(arr: np.ndarray, kind: str, max_samples: int) -> dict:
    flat = np.asarray(arr).ravel()
    finite = np.isfinite(flat)
    total = int(flat.size)
    valid = int(np.count_nonzero(finite))
    out = _empty(kind, total, valid)
    if valid == 0:
        return out
    values = flat[finite] if valid < total else flat
    values = values.astype(np.float64, copy=False)
    vmin = float(values.min())
    vmax = float(values.max())
    sample, sampled = _sample(values, max_samples)
    pct = np.percentile(sample, PERCENTILES)
    p = {f"p{q}": _f(v) for q, v in zip(PERCENTILES, pct)}
    out.update(p)
    out.update({
        "sampled": bool(sampled),
        "min": _f(vmin), "max": _f(vmax),
        "mean": _f(sample.mean()), "std": _f(sample.std()),
        "hist": _histogram(sample, float(pct[0]), float(pct[-1])),
    })
    if vmin > 0:
        logs = np.log10(sample)
        out["log_hist"] = _histogram(logs, math.log10(float(pct[0])), math.log10(float(pct[-1])))
    return out


def _flag_stats(arr: np.ndarray, kind: str, flag_meanings: Optional[List[str]],
                flag_masks: Optional[List[int]]) -> dict:
    flat = np.asarray(arr).ravel()
    if flat.dtype.kind == "f":
        finite = np.isfinite(flat)
        total = int(flat.size)
        valid = int(np.count_nonzero(finite))
        words = (flat[finite] if valid < total else flat).astype(np.int64, copy=False)
    else:
        total = valid = int(flat.size)
        words = flat.astype(np.int64, copy=False)
    out = _empty(kind, total, valid)
    meanings = list(flag_meanings or [])
    masks = [int(m) for m in (flag_masks or [])]
    if not meanings or not masks:
        meanings, masks = list(STATUS_MEANINGS.values()), list(STATUS_MEANINGS.keys())
    n = min(len(meanings), len(masks))
    counts: Dict[str, int] = {}
    flags: List[dict] = []
    for name, mask in zip(meanings[:n], masks[:n]):
        c = int(np.count_nonzero(words & np.int64(mask))) if words.size else 0
        counts[name] = c
        flags.append({"name": name, "mask": int(mask), "count": c})
    out["flag_counts"] = counts
    out["flags"] = flags
    if words.size:
        out["min"] = int(words.min())
        out["max"] = int(words.max())
    if kind == "status":
        status_counts = dict(counts)
        status_counts["fill"] = int(total - valid)
        out["counts"] = status_counts
    return out


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
    if kind in ("flags", "status"):
        return _flag_stats(arr, kind, flag_meanings, flag_masks)
    return _float_stats(arr, kind, max_samples)


def band_stats(handle: DatasetHandle, band: str) -> dict:
    """``BandStats`` for ``band`` of ``handle`` (``KeyError`` for unknown
    band). Cached in ``handle.cache`` under ``("stats", handle.path, band)``."""
    info = handle.band(band)   # KeyError propagates
    key = ("stats", handle.path, band)
    cached = handle.cache.get(key)
    if cached is not None:
        return cached
    arr = handle.read_band(band)
    result = {"file": Path(handle.path).name, "band": band}
    result.update(compute_stats(arr, info.kind, info.flag_meanings, info.flag_masks))
    handle.cache.put(key, result, nbytes=STATS_NBYTES)
    return result
