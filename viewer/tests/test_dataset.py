"""DatasetHandle on the five synthetic product shapes."""

from __future__ import annotations

import numpy as np
import pytest

from viewer.server.dataset import (
    DatasetHandle, band_group, classify_band, match_key_for, nominal_s2_wavelength,
)

# T17RNK synthetic window: centres start at (583870, 2999990), 20 m, 60 x 80.
X0_EDGE, Y0_EDGE, STEP = 583860.0, 3000000.0, 20.0


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


def test_nominal_wavelength_and_match_key():
    assert nominal_s2_wavelength(559.8) == 560
    assert nominal_s2_wavelength(1600) == 1614
    assert nominal_s2_wavelength(600) is None
    assert nominal_s2_wavelength("nope") is None
    assert match_key_for("Rrs_B03", {"wavelength": 559.849}) == "Rrs@560"
    assert match_key_for("rrs_B3", {}) == "Rrs@560"
    assert match_key_for("Rrs_560", {}) == "Rrs@560"
    assert match_key_for("Rrs_559", {}) == "Rrs@560"
    assert match_key_for("rhown_B3", {}) == "rhow@560"
    assert match_key_for("rhow_560", {}) == "rhow@560"
    assert match_key_for("rhos_1600", {}) == "rhos@1614"
    assert match_key_for("conc_chl", {}) == "conc_chl"
    assert match_key_for("chl_oc3", {}) == "chl_oc3"
    assert match_key_for("pixel_status", {}) == "pixel_status"
    assert classify_band("aux_latitude", {}, np.dtype("f4")) == "latlon"
    assert classify_band("l2_flags", {}, np.dtype("i4")) == "flags"
    assert classify_band("pixel_status", {}, np.dtype("u1")) == "status"
    assert band_group("chl_oc3", "float") == "Products"
    assert band_group("ozone", "float") == "Other"


def test_dsfopt_grid(dsfopt):
    g = dsfopt.grid
    assert dsfopt.shape == (60, 80)
    assert g.kind == "projected" and g.space == "EPSG:32617" and g.crs == "EPSG:32617"
    assert (g.x0, g.y0, g.dx, g.dy) == (X0_EDGE, Y0_EDGE, STEP, -STEP)
    assert g.units == "m" and g.ny == 60 and g.nx == 80
    assert list(g.bounds) == [X0_EDGE, Y0_EDGE - 60 * STEP, X0_EDGE + 80 * STEP, Y0_EDGE]
    assert g.has_latlon is False
    d = dsfopt.info_dict("abc")
    assert d["id"] == "abc" and d["shape"] == [60, 80] and d["grid"]["bounds"] == list(g.bounds)


def test_dsfopt_bands(dsfopt):
    names = [b.name for b in dsfopt.bands]
    assert names[:3] == ["Rrs_B03", "Rrs_B04", "Rrs_B8A"]      # reflectance first, by wavelength
    assert set(names) == {"Rrs_B03", "Rrs_B04", "Rrs_B8A", "pixel_status", "l2_flags"}
    b = dsfopt.band("Rrs_B03")
    assert (b.match_key, b.kind, b.group, b.dtype) == ("Rrs@560", "float", "Reflectance", "float32")
    assert b.wavelength == pytest.approx(559.849, abs=1e-3)
    assert b.parameter == "Rrs_560" and b.units == "1"
    st = dsfopt.band("pixel_status")
    assert st.kind == "status" and st.group == "Flags"
    assert st.flag_meanings == ["value", "outside_aoi", "flagged", "no_data"]
    assert st.flag_masks == [1, 2, 4, 8]
    fl = dsfopt.band("l2_flags")
    assert fl.kind == "flags" and fl.flag_meanings[:2] == ["bit0", "bit1"] and fl.flag_masks[4] == 16
    assert dsfopt.default_band == "Rrs_B03"
    assert dsfopt.status_band == "pixel_status"
    assert dsfopt.flag_bands == ["l2_flags"]
    with pytest.raises(KeyError):
        dsfopt.band("nope")


def test_c2rccopt_bands(c2rccopt):
    assert c2rccopt.grid.space == "EPSG:32617"
    assert c2rccopt.grid.to_dict()["x0"] == X0_EDGE
    rrs = c2rccopt.band("rrs_B3")
    assert rrs.match_key == "Rrs@560" and rrs.group == "Reflectance" and rrs.wavelength == 560.0
    assert c2rccopt.band("rhown_B3").match_key == "rhow@560"
    chl = c2rccopt.band("conc_chl")
    assert chl.group == "Products" and chl.kind == "float" and chl.match_key == "conc_chl"
    fl = c2rccopt.band("c2rcc_flags")
    assert fl.kind == "flags" and 2 ** 31 in fl.flag_masks and "Cloud_risk" in fl.flag_meanings
    assert fl.flag_masks[fl.flag_meanings.index("Valid_PE")] == 2 ** 31
    lat = c2rccopt.band("aux_latitude")
    assert lat.kind == "latlon" and lat.group == "Geolocation"
    assert c2rccopt.grid.has_latlon is True
    assert c2rccopt.default_band == "rrs_B3"
    groups = [b.group for b in c2rccopt.bands]
    assert groups == sorted(groups, key=["Reflectance", "Products", "Flags", "Geolocation", "Other"].index)


def test_l2w(l2w_path, array_cache):
    h = DatasetHandle(l2w_path, array_cache)
    try:
        assert h.grid.kind == "projected" and h.grid.space == "EPSG:32617"
        assert h.grid.has_latlon is True
        assert h.band("Rrs_560").match_key == "Rrs@560"
        assert h.band("Rrs_865").match_key == "Rrs@865"
        assert h.band("lat").kind == "latlon" and h.band("lon").kind == "latlon"
        assert h.band("l2_flags").kind == "flags"
        assert h.default_band == "Rrs_560" and h.status_band is None
    finally:
        h.close()


def test_c2rcc_raw_georeferenced_from_latlon(c2rcc_raw_path, array_cache):
    """No x/y and no grid mapping, but the 2-D lat/lon describe the same
    20 m EPSG:32617 lattice as DSFOPT: the grid is recovered from them, so
    the file shares a view with the optimized products and can be diffed."""
    h = DatasetHandle(c2rcc_raw_path, array_cache)
    try:
        g = h.grid
        # the coordinates are lat/lon (standard_name), not SNAP's aux_* bands,
        # whose geographic lattice would have made this an EPSG:4326 grid
        assert (h._lat_name, h._lon_name) == ("lat", "lon")
        assert g.kind == "projected" and g.space == "EPSG:32617" and g.crs == "EPSG:32617"
        assert g.units == "m" and g.has_latlon is True
        assert (g.x0, g.dx, g.y0, g.dy) == pytest.approx((X0_EDGE, STEP, Y0_EDGE, -STEP), abs=1e-3)
        assert list(g.bounds) == pytest.approx([X0_EDGE, Y0_EDGE - 60 * STEP, X0_EDGE + 80 * STEP, Y0_EDGE], abs=1e-3)
        lon, lat = h.latlon_at(0, 0)
        assert -81 < lon < -80 and 26 < lat < 28
        assert h.latlon_at(60, 0) == (None, None)
        assert h.band("rrs_B3").match_key == "Rrs@560"
        assert h.status_band is None and h.flag_bands == ["c2rcc_flags"]
        pv = h.value_at("rrs_B3", X0_EDGE + 40.5 * STEP, Y0_EDGE - 30.5 * STEP)   # row 30, col 40
        assert pv.inside and (pv.row, pv.col) == (30, 40) and isinstance(pv.value, float)
        assert pv.status is None and pv.flags == ["Valid_PE"]
        # lon/lat from the recovered grid agree with the arrays in the file
        assert pv.lon == pytest.approx(float(h.read_band("lon")[30, 40]), abs=1e-6)
        assert pv.lat == pytest.approx(float(h.read_band("lat")[30, 40]), abs=1e-6)
    finally:
        h.close()


def test_canon(canon_path, array_cache):
    h = DatasetHandle(canon_path, array_cache)
    try:
        assert h.grid.kind == "projected" and h.grid.space == "EPSG:32617" and h.grid.has_latlon is True
        assert h.band("Rrs_560").match_key == "Rrs@560"
        assert h.default_band == "Rrs_560"
        assert {b.name for b in h.bands} == {"Rrs_560", "Rrs_865", "c2rcc_flags", "lat", "lon"}
    finally:
        h.close()


def _write_plain(path, lon=None, lat=None, ny=12, nx=16):
    """A file with one float band and, when given, 2-D float64 lat/lon."""
    from netCDF4 import Dataset
    with Dataset(path, "w", format="NETCDF4") as ds:
        ds.createDimension("y", ny)
        ds.createDimension("x", nx)
        if lat is not None:
            v = ds.createVariable("lat", "f8", ("y", "x"))
            v.units = "degrees_north"
            v[:] = lat
            v = ds.createVariable("lon", "f8", ("y", "x"))
            v.units = "degrees_east"
            v[:] = lon
        v = ds.createVariable("chl", "f4", ("y", "x"))
        v.units = "mg m^-3"
        v[:] = np.random.default_rng(1).random((ny, nx), dtype=np.float32)
    return path


def test_bare_file_is_pixel_space(tmp_path, array_cache):
    h = DatasetHandle(_write_plain(tmp_path / "bare.nc"), array_cache)
    try:
        g = h.grid
        assert g.kind == "pixel" and g.space == "pixel" and g.crs is None and g.has_latlon is False
        assert (g.x0, g.dx, g.y0, g.dy, g.units) == (0.0, 1.0, 0.0, -1.0, "px")
        assert list(g.bounds) == [0.0, -12.0, 16.0, 0.0]
        assert h.latlon_at(0, 0) == (None, None)
    finally:
        h.close()


def test_curved_latlon_stays_pixel_space(tmp_path, array_cache):
    """Lat/lon that are not a lattice (a swath): no candidate CRS makes the
    sample axis-aligned, so the file keeps its pixel-space view and lon/lat
    under the cursor come from the arrays."""
    ny, nx = 12, 16
    rr, cc = np.mgrid[0:ny, 0:nx].astype(np.float64)
    lon = -80.5 + 0.002 * cc + 0.0004 * rr * cc          # column spacing grows down the rows
    lat = 27.2 - 0.002 * rr + 0.0003 * cc ** 2 / nx
    h = DatasetHandle(_write_plain(tmp_path / "swath.nc", lon, lat, ny, nx), array_cache)
    try:
        g = h.grid
        assert g.kind == "pixel" and g.space == "pixel" and g.crs is None and g.has_latlon is True
        assert (g.x0, g.dx, g.y0, g.dy, g.units) == (0.0, 1.0, 0.0, -1.0, "px")
        assert h.latlon_at(3, 5) == (pytest.approx(lon[3, 5]), pytest.approx(lat[3, 5]))
        pv = h.value_at("chl", 5.5, -3.5)                 # world Y = -row
        assert pv.inside and (pv.row, pv.col) == (3, 5)
    finally:
        h.close()


def test_regular_geographic_latlon_grid(tmp_path, array_cache):
    """Lat/lon that are themselves a regular lattice (a product reprojected
    to geographic coordinates) become an EPSG:4326 grid in degrees."""
    ny, nx = 12, 16
    lon = -80.5 + 0.002 * np.arange(nx, dtype=np.float64)[None, :] + np.zeros((ny, 1))
    lat = 27.2 - 0.0015 * np.arange(ny, dtype=np.float64)[:, None] + np.zeros((1, nx))
    h = DatasetHandle(_write_plain(tmp_path / "geo.nc", lon, lat, ny, nx), array_cache)
    try:
        g = h.grid
        assert g.kind == "projected" and g.space == "EPSG:4326" and g.crs == "EPSG:4326" and g.units == "deg"
        assert (g.x0, g.dx, g.y0, g.dy) == pytest.approx((-80.501, 0.002, 27.20075, -0.0015))
        assert h.latlon_at(0, 0) == (pytest.approx(-80.5), pytest.approx(27.2))
        pv = h.value_at("chl", -80.5 + 0.002 * 5, 27.2 - 0.0015 * 3)
        assert pv.inside and (pv.row, pv.col) == (3, 5)
    finally:
        h.close()


def test_value_at(dsfopt):
    cx, cy = X0_EDGE + 40.5 * STEP, Y0_EDGE - 30.5 * STEP      # row 30, col 40: inside the AOI
    pv = dsfopt.value_at("Rrs_B03", cx, cy)
    assert pv.inside and (pv.row, pv.col) == (30, 40)
    assert isinstance(pv.value, float) and 0.0 < pv.value < 0.1
    assert pv.status == "value" and pv.flags == []
    assert -81 < pv.lon < -80 and 26 < pv.lat < 28
    assert pv.file == dsfopt.path.replace("\\", "/").rsplit("/", 1)[-1] and pv.band == "Rrs_B03"
    left = dsfopt.value_at("Rrs_B03", X0_EDGE + 5.5 * STEP, cy)  # col 5: outside_aoi, NaN
    assert left.inside and left.value is None and left.status == "outside_aoi" and left.flags == ["bit0"]
    off = dsfopt.value_at("Rrs_B03", 0.0, 0.0)
    assert off.inside is False and off.value is None and off.status is None and off.lon is None
    above = dsfopt.value_at("Rrs_B03", cx, Y0_EDGE + 1.0)
    assert above.inside is False and above.row == -1
    st = dsfopt.value_at("pixel_status", X0_EDGE + 5.5 * STEP, cy)
    assert st.value == 2 and st.flags == ["outside_aoi"] and st.status == "outside_aoi"
    flagged = dsfopt.value_at("l2_flags", X0_EDGE + 42.5 * STEP, Y0_EDGE - 22.5 * STEP)
    assert flagged.value == 16 and flagged.flags == ["bit4"] and flagged.status == "flagged"
    d = pv.to_dict()
    assert set(d) == {"file", "band", "x", "y", "row", "col", "inside", "value", "status", "flags", "lon", "lat"}


def test_world_rowcol_roundtrip(dsfopt):
    g = dsfopt.grid
    for row, col in ((0, 0), (17, 33), (59, 79)):
        x, y = g.rowcol_to_world(row + 0.5, col + 0.5)
        assert g.world_to_rowcol(x, y) == (row, col)
    assert g.rowcol_to_world(0, 0) == (X0_EDGE, Y0_EDGE)
    assert g.world_to_rowcol(X0_EDGE + 80 * STEP, Y0_EDGE - 60 * STEP) == (60, 80)   # far edge is outside


def test_read_band_dtypes_and_cache(dsfopt):
    arr = dsfopt.read_band("Rrs_B03")
    assert arr.dtype == np.float32 and arr.shape == (60, 80)
    assert np.isnan(arr[:, :20]).all() and np.isfinite(arr[30, 40])
    assert dsfopt.read_band("Rrs_B03") is arr                      # served from the cache
    assert ("band", dsfopt.path, "Rrs_B03") in dsfopt.cache
    st = dsfopt.read_band("pixel_status")
    assert st.dtype == np.float64 and st[0, 0] == 2.0 and st[0, 79] == 8.0 and not np.isnan(st).any()
    fl = dsfopt.read_band("l2_flags")
    assert fl.dtype == np.float64 and fl[22, 42] == 16.0 and fl[0, 0] == 1.0
    with pytest.raises(KeyError):
        dsfopt.read_band("nope")


def test_metadata_truncates(dsfopt):
    m = dsfopt.metadata()
    assert m["dims"] == {"y": 60, "x": 80}
    assert m["file"]["name"] == m["path"].replace("\\", "/").rsplit("/", 1)[-1]
    geo = m["global_attrs"]["geocoding"]
    assert len(geo) < 4100 and "...[truncated" in geo
    assert m["global_attrs"]["analysis_window_epsg"] == "32617"
    assert m["global"] is m["global_attrs"]
    names = [v["name"] for v in m["variables"]]
    assert names == ["x", "y", "transverse_mercator", "pixel_status", "Rrs_B03", "Rrs_B04", "Rrs_B8A", "l2_flags"]
    rrs = next(v for v in m["variables"] if v["name"] == "Rrs_B03")
    assert rrs["dims"] == ["y", "x"] and rrs["shape"] == [60, 80] and rrs["dtype"] == "float32"
    assert rrs["encoding"]["zlib"] is True and rrs["encoding"]["chunksizes"] is not None
    assert rrs["attrs"]["parameter"] == "Rrs_560"
    coords = {v["name"] for v in m["coords"]}
    assert {"x", "y", "transverse_mercator"} <= coords
    tm = next(v for v in m["coords"] if v["name"] == "transverse_mercator")
    assert "crs_wkt" in tm["attrs"]
    assert m["grid"]["space"] == "EPSG:32617"


def test_real_product_is_projected(real_nc, array_cache):
    h = DatasetHandle(real_nc, array_cache)
    try:
        assert h.grid.kind == "projected"
        assert h.grid.space.startswith("EPSG:")
        assert h.default_band is not None
    finally:
        h.close()


def test_open_uses_small_hdf5_chunk_cache(dsfopt_path, array_cache):
    """Every variable is opened with the viewer's small HDF5 chunk cache, not
    netCDF4's 64 MB default, so a whole-band read does not leave a second
    copy of the band inside HDF5 for as long as the file stays open. Checked
    on the first open and on the reopen after close()."""
    from viewer.server.dataset import HDF5_CHUNK_CACHE_BYTES, NETCDF_LOCK

    assert HDF5_CHUNK_CACHE_BYTES <= 8 * 1024 * 1024
    h = DatasetHandle(dsfopt_path, array_cache)
    try:
        for _ in range(2):
            with NETCDF_LOCK:
                ds = h._dataset()
                sizes = {name: int(var.get_var_chunk_cache()[0]) for name, var in ds.variables.items() if var.ndim == 2}
            assert sizes and set(sizes.values()) == {HDF5_CHUNK_CACHE_BYTES}, sizes
            assert h.read_band(h.default_band).dtype == np.float32
            h.close()
    finally:
        h.close()
