"""Band statistics on the synthetic products.

Synthetic status pattern (60 x 80): left quarter (60 x 20 = 1200) outside_aoi,
a 5 x 8 flagged block (40), two no_data pixels, the remaining 3558 = value.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from viewer.server.dataset import DatasetHandle
from viewer.server.stats import band_stats, compute_stats

N_VALUE, N_OUTSIDE, N_FLAGGED, N_NODATA = 3558, 1200, 40, 2


@pytest.fixture(scope="module")
def dsfopt(dsfopt_path, array_cache):
    h = DatasetHandle(dsfopt_path, array_cache)
    yield h
    h.close()


@pytest.fixture(scope="module")
def c2rccopt(c2rccopt_path, array_cache):
    h = DatasetHandle(c2rccopt_path, array_cache)
    yield h
    h.close()


def test_float_stats_dsfopt(dsfopt):
    st = band_stats(dsfopt, "Rrs_B03")
    assert st["band"] == "Rrs_B03" and st["file"] == dsfopt.path.replace("\\", "/").rsplit("/", 1)[-1]
    assert st["kind"] == "float" and st["sampled"] is False
    assert st["count_total"] == 4800 and st["count_valid"] == N_VALUE
    assert st["count"] == 4800 and st["valid"] == N_VALUE and st["nan_count"] == 4800 - N_VALUE
    assert st["min"] <= st["p1"] <= st["p2"] <= st["p50"] <= st["p98"] <= st["p99"] <= st["max"]
    assert st["mean"] is not None and st["std"] > 0
    assert len(st["hist"]["edges"]) == 65 and len(st["hist"]["counts"]) == 64
    assert st["hist"]["edges"][0] == pytest.approx(st["p1"]) and st["hist"]["edges"][-1] == pytest.approx(st["p99"])
    assert st["log_hist"] is not None and len(st["log_hist"]["counts"]) == 64   # all values > 0
    assert st["flag_counts"] is None
    json.dumps(st)                                                # JSON-safe
    assert band_stats(dsfopt, "Rrs_B03") is st                    # cached
    assert ("stats", dsfopt.path, "Rrs_B03") in dsfopt.cache
    with pytest.raises(KeyError):
        band_stats(dsfopt, "nope")


def test_status_counts(dsfopt):
    st = band_stats(dsfopt, "pixel_status")
    assert st["kind"] == "status" and st["count_total"] == 4800 and st["count_valid"] == 4800
    assert st["flag_counts"] == {"value": N_VALUE, "outside_aoi": N_OUTSIDE, "flagged": N_FLAGGED, "no_data": N_NODATA}
    assert st["counts"] == {"value": N_VALUE, "outside_aoi": N_OUTSIDE, "flagged": N_FLAGGED,
                            "no_data": N_NODATA, "fill": 0}
    assert st["min"] == 1 and st["max"] == 8
    assert st["p50"] is None and st["hist"] is None
    json.dumps(st)


def test_flags_counts(c2rccopt):
    st = band_stats(c2rccopt, "c2rcc_flags")
    status = band_stats(c2rccopt, "pixel_status")
    assert st["kind"] == "flags"
    assert st["count_total"] == 4800 and st["count_valid"] == 4800 - N_NODATA   # fill word -> NaN
    assert st["flag_counts"]["Valid_PE"] == status["flag_counts"]["value"] == N_VALUE
    assert st["flag_counts"]["Cloud_risk"] == N_FLAGGED and st["flag_counts"]["Rtosa_OOS"] == N_FLAGGED
    assert st["flag_counts"]["Rhow_OOR"] == 0
    by_name = {f["name"]: f for f in st["flags"]}
    assert by_name["Valid_PE"] == {"name": "Valid_PE", "mask": 2 ** 31, "count": N_VALUE}
    assert st["max"] == 2 ** 31 and st["min"] == 0
    assert "counts" not in st
    json.dumps(st)


def test_sampling_path(dsfopt):
    arr = dsfopt.read_band("Rrs_B03")
    full = compute_stats(arr, "float")
    small = compute_stats(arr, "float", max_samples=100)
    assert small["sampled"] is True and full["sampled"] is False
    assert small["min"] == full["min"] and small["max"] == full["max"]      # exact even when sampled
    assert small["count_valid"] == full["count_valid"] == N_VALUE
    assert full["min"] <= small["p2"] <= small["p98"] <= full["max"]
    assert sum(small["hist"]["counts"]) <= 100
    assert len(small["hist"]["counts"]) == 64
    empty = compute_stats(np.full((4, 4), np.nan, dtype=np.float32), "float")
    assert empty["count_valid"] == 0 and empty["min"] is None and empty["hist"] is None
    assert empty["p2"] is None and empty["sampled"] is False
    json.dumps(empty)
    status = compute_stats(np.array([[1, 2], [4, np.nan]], dtype=np.float64), "status")
    assert status["counts"] == {"value": 1, "outside_aoi": 1, "flagged": 1, "no_data": 0, "fill": 1}
