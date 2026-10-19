"""Synthetic products for the viewer test suite.

Nothing here touches real data. ``make_synthetic_product`` writes tiny files
that mirror the shapes in plan section 2:

- ``dsfopt``:    x/y + transverse_mercator, pixel_status, Rrs_B03/B04/B8A, l2_flags
- ``c2rccopt``:  same grid, pixel_status, rrs_B3/B4/B8A, conc_chl, c2rcc_flags,
                 aux_latitude/aux_longitude
- ``l2w``:       like dsfopt but ACOLITE raw naming (Rrs_560 ...), plus 2-D lat/lon
- ``c2rcc_raw``: no x/y, 2-D lat/lon coordinates, rrs_B3, conc_chl, c2rcc_flags
- ``canon``:     no coordinates at all, lat/lon as plain vars, Rrs_560, Rrs_865, c2rcc_flags

Grid: 20 m EPSG:32617 lattice, pixel centres starting at (583870, 2999990)
(the T17RNK window), ``ny`` rows x ``nx`` cols.
"""

from __future__ import annotations

import getpass
import os
import tempfile
from pathlib import Path

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Same workaround as tests/conftest.py: on this Windows machine
# %TEMP%\pytest-of-<user> is unreadable, which kills pytest's tmp_path.
# ---------------------------------------------------------------------------

def _redirect_broken_pytest_temproot() -> None:
    if os.environ.get("PYTEST_DEBUG_TEMPROOT"):
        return
    try:
        user = getpass.getuser() or "unknown"
    except Exception:
        return
    temproot = Path(tempfile.gettempdir())
    default_root = temproot / f"pytest-of-{user}"
    if not default_root.exists():
        return
    try:
        os.listdir(default_root)
        return
    except PermissionError:
        pass
    except OSError:
        return
    fallback = temproot / f"pytest-temproot-{user}"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    os.environ["PYTEST_DEBUG_TEMPROOT"] = str(fallback)


_redirect_broken_pytest_temproot()

# ---------------------------------------------------------------------------
# Synthetic product writer
# ---------------------------------------------------------------------------

EPSG = 32617
X0_CENTRE = 583870.0
Y0_CENTRE = 2999990.0
STEP = 20.0

C2RCC_FLAG_MEANINGS = "Rtosa_OOS Rtosa_OOR Rhow_OOR Cloud_risk Iop_OOR Apig_at_max Valid_PE"
C2RCC_FLAG_MASKS = np.array([1, 2, 4, 8, 16, 32, 2 ** 31], dtype=np.uint32)


def _crs_wkt() -> str:
    import pyproj
    return pyproj.CRS.from_epsg(EPSG).to_wkt()


def grid_centres(ny: int, nx: int):
    x = X0_CENTRE + STEP * np.arange(nx, dtype=np.float64)
    y = Y0_CENTRE - STEP * np.arange(ny, dtype=np.float64)
    return x, y


def status_pattern(ny: int, nx: int) -> np.ndarray:
    st = np.ones((ny, nx), dtype=np.uint8)           # 1 value
    st[:, : nx // 4] = 2                              # left quarter outside_aoi
    st[ny // 3: ny // 3 + 5, nx // 2: nx // 2 + 8] = 4  # a flagged block
    st[0, nx - 1] = 8                                 # two no_data pixels
    st[1, nx - 1] = 8
    return st


def field_pattern(ny: int, nx: int, seed: int, base: float = 0.01, amp: float = 0.03) -> np.ndarray:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:ny, 0:nx]
    f = base + amp * xx / max(nx - 1, 1) + 0.005 * np.sin(yy / 5.0) + rng.normal(0, 0.0005, (ny, nx))
    return f.astype(np.float32)


def latlon_grid(ny: int, nx: int):
    import pyproj
    x, y = grid_centres(ny, nx)
    xx, yy = np.meshgrid(x, y)
    tr = pyproj.Transformer.from_crs(EPSG, 4326, always_xy=True)
    lon, lat = tr.transform(xx, yy)
    return np.asarray(lon), np.asarray(lat)


def _write_grid(ds, x, y):
    ds.createDimension("y", len(y))
    ds.createDimension("x", len(x))
    vx = ds.createVariable("x", "f8", ("x",))
    vx.standard_name = "projection_x_coordinate"
    vx.long_name = "x coordinate of projection"
    vx.units = "m"
    vx[:] = x
    vy = ds.createVariable("y", "f8", ("y",))
    vy.standard_name = "projection_y_coordinate"
    vy.long_name = "y coordinate of projection"
    vy.units = "m"
    vy[:] = y
    tm = ds.createVariable("transverse_mercator", "i4")
    tm.grid_mapping_name = "transverse_mercator"
    tm.crs_wkt = _crs_wkt()
    tm.longitude_of_central_meridian = -81.0
    tm.latitude_of_projection_origin = 0.0
    tm.false_easting = 500000.0
    tm.false_northing = 0.0
    tm.scale_factor_at_central_meridian = 0.9996
    tm.semi_major_axis = 6378137.0
    tm.inverse_flattening = 298.257223563
    tm[...] = 0


def _write_status(ds, st, grid_mapping=True):
    v = ds.createVariable("pixel_status", "u1", ("y", "x"), zlib=True, fill_value=np.uint8(16))
    v.long_name = "Per-pixel science value status"
    v.flag_masks = np.array([1, 2, 4, 8], dtype=np.uint8)
    v.flag_meanings = "value outside_aoi flagged no_data"
    if grid_mapping:
        v.grid_mapping = "transverse_mercator"
    v[:] = st


def _float_var(ds, name, data, *, units, long_name, grid_mapping=True, coordinates=None, **attrs):
    v = ds.createVariable(name, "f4", ("y", "x"), zlib=True, fill_value=np.float32(np.nan))
    v.long_name = long_name
    v.units = units
    for k, val in attrs.items():
        setattr(v, k, val)
    if grid_mapping:
        v.grid_mapping = "transverse_mercator"
    if coordinates:
        v.coordinates = coordinates
    v[:] = data
    return v


def _c2rcc_flags(ds, st, grid_mapping=True, coordinates=None):
    v = ds.createVariable("c2rcc_flags", "u4", ("y", "x"), zlib=True, fill_value=np.uint32(1048576))
    v.long_name = "C2RCC quality flags"
    v.flag_meanings = C2RCC_FLAG_MEANINGS
    v.flag_masks = C2RCC_FLAG_MASKS
    if grid_mapping:
        v.grid_mapping = "transverse_mercator"
    if coordinates:
        v.coordinates = coordinates
    word = np.zeros(st.shape, dtype=np.uint32)
    word[st == 1] |= np.uint32(2 ** 31)       # Valid_PE on good pixels
    word[st == 4] |= np.uint32(8)             # Cloud_risk on flagged pixels
    word[st == 4] |= np.uint32(1)             # plus Rtosa_OOS
    word[st == 8] = np.uint32(1048576)        # fill on no_data
    v[:] = word


def make_synthetic_product(path, kind: str = "dsfopt", ny: int = 60, nx: int = 80) -> Path:
    """Write one synthetic product of ``kind`` to ``path`` and return it."""
    from netCDF4 import Dataset

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    x, y = grid_centres(ny, nx)
    st = status_pattern(ny, nx)
    valid = st == 1

    def masked(f):
        out = f.copy()
        out[~valid] = np.nan
        return out

    with Dataset(path, "w", format="NETCDF4") as ds:
        if kind == "dsfopt":
            _write_grid(ds, x, y)
            _write_status(ds, st)
            for name, wl, par, seed in (("Rrs_B03", 559.8490554870011, "Rrs_560", 3),
                                        ("Rrs_B04", 664.6217529184931, "Rrs_665", 4),
                                        ("Rrs_B8A", 864.7107892436754, "Rrs_865", 8)):
                _float_var(ds, name, masked(field_pattern(ny, nx, seed)), units="1",
                           long_name="Surface reflectance", wavelength=wl, parameter=par,
                           standard_name="surface_bidirectional_reflectance", algorithm="")
            fl = ds.createVariable("l2_flags", "i4", ("y", "x"), zlib=True, fill_value=np.int32(-999))
            fl.grid_mapping = "transverse_mercator"
            word = np.zeros((ny, nx), dtype=np.int32)
            word[st == 4] = 16
            word[st == 2] = 1
            fl[:] = word
            ds.generated_by = "ACOLITE"
            ds.acolite_version = "Generic GitHub Clone c2026-09-04T12:13:33"
            ds.sensor = "S2B_MSI"
            ds.isodate = "2025-01-09T16:05:58.000000+00:00"
            ds.acolite_file_type = "L2W"
            ds.Conventions = "CF-1.7"
            ds.analysis_window_epsg = 32617
            ds.analysis_window_tile = "T17RNK"
            ds.ac_artifact_role = "dsf_raw"
            ds.geocoding = "<ComponentGeoCoding>" + "x" * 6000 + "</ComponentGeoCoding>"
            ds.sza = 53.46
        elif kind == "c2rccopt":
            _write_grid(ds, x, y)
            _write_status(ds, st)
            for name, seed in (("rrs_B3", 13), ("rrs_B4", 14), ("rrs_B8A", 18)):
                _float_var(ds, name, masked(field_pattern(ny, nx, seed) * 0.8), units="sr^-1",
                           long_name="Atmospherically corrected Angular dependent remote sensing reflectances")
            _float_var(ds, "rhown_B3", masked(field_pattern(ny, nx, 23) * 3.0), units="1",
                       long_name="Normalized water leaving reflectances")
            rng = np.random.default_rng(99)
            chl = np.exp(rng.normal(1.0, 0.6, (ny, nx))).astype(np.float32)
            _float_var(ds, "conc_chl", masked(chl), units="mg m^-3", long_name="Chlorophyll concentration")
            _c2rcc_flags(ds, st)
            lon, lat = latlon_grid(ny, nx)
            _float_var(ds, "aux_latitude", lat.astype(np.float32), units="degrees_north", long_name="")
            _float_var(ds, "aux_longitude", lon.astype(np.float32), units="degrees_east", long_name="")
            ds.Conventions = "CF-1.4"
            ds.ac_method = "c2rcc"
            ds.ac_artifact_role = "c2rcc_raw"
            ds.analysis_window_tile = "T17RNK"
            ds.analysis_window_epsg = 32617
            ds.analysis_window_resolution_m = 20.0
            ds.pixel_status_counts = "value=1 outside_aoi=2 flagged=3 no_data=4"
        elif kind == "l2w":
            _write_grid(ds, x, y)
            lon, lat = latlon_grid(ny, nx)
            _float_var(ds, "lon", lon.astype(np.float32), units="degree_east", long_name="longitude", parameter="lon")
            _float_var(ds, "lat", lat.astype(np.float32), units="degree_north", long_name="latitude", parameter="lat")
            for name, wl, seed in (("Rrs_560", 559.8490554870011, 3), ("Rrs_665", 664.6217529184931, 4),
                                   ("Rrs_865", 864.7107892436754, 8)):
                _float_var(ds, name, masked(field_pattern(ny, nx, seed)), units="1",
                           long_name="Surface reflectance", wavelength=wl, parameter=name)
            fl = ds.createVariable("l2_flags", "i4", ("y", "x"), zlib=True)
            fl.grid_mapping = "transverse_mercator"
            word = np.zeros((ny, nx), dtype=np.int32)
            word[st == 4] = 16
            fl[:] = word
            ds.generated_by = "ACOLITE"
            ds.sensor = "S2B_MSI"
            ds.acolite_file_type = "L2W"
        elif kind == "c2rcc_raw":
            ds.createDimension("y", ny)
            ds.createDimension("x", nx)
            lon, lat = latlon_grid(ny, nx)
            vlat = ds.createVariable("lat", "f8", ("y", "x"))
            vlat.units = "degrees_north"
            vlat[:] = lat
            vlon = ds.createVariable("lon", "f8", ("y", "x"))
            vlon.units = "degrees_east"
            vlon[:] = lon
            _float_var(ds, "rrs_B3", masked(field_pattern(ny, nx, 13) * 0.8), units="sr^-1",
                       long_name="Atmospherically corrected Angular dependent remote sensing reflectances",
                       grid_mapping=False, coordinates="lat lon")
            rng = np.random.default_rng(7)
            chl = np.exp(rng.normal(1.0, 0.6, (ny, nx))).astype(np.float32)
            _float_var(ds, "conc_chl", masked(chl), units="mg m^-3", long_name="Chlorophyll concentration",
                       grid_mapping=False, coordinates="lat lon")
            _c2rcc_flags(ds, st, grid_mapping=False, coordinates="lat lon")
            ds.Conventions = "CF-1.4"
            ds.TileSize = "610:610"
            ds.start_date = "09-JAN-2025 16:05:09.024000"
        elif kind == "canon":
            ds.createDimension("y", ny)
            ds.createDimension("x", nx)
            lon, lat = latlon_grid(ny, nx)
            vlat = ds.createVariable("lat", "f8", ("y", "x"))
            vlat.units = "degrees_north"
            vlat[:] = lat
            vlon = ds.createVariable("lon", "f8", ("y", "x"))
            vlon.units = "degrees_east"
            vlon[:] = lon
            _float_var(ds, "Rrs_560", masked(field_pattern(ny, nx, 3) * 0.8), units="sr^-1", long_name="",
                       grid_mapping=False)
            _float_var(ds, "Rrs_865", masked(field_pattern(ny, nx, 8) * 0.8), units="sr^-1", long_name="",
                       grid_mapping=False)
            _c2rcc_flags(ds, st, grid_mapping=False)
            ds.Conventions = "CF-1.6"
            ds.sensor = "S2B_MSI"
            ds.tile = "T17RNK"
            ds.ac_method = "c2rcc"
            ds.ac_artifact_role = "c2rcc_canonical"
        else:
            raise ValueError(f"unknown synthetic kind {kind!r}")
    return path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

STEM = "S2B_MSI_2025_01_09_16_05_58_T17RNK"


@pytest.fixture(scope="session")
def synthetic_root(tmp_path_factory) -> dict:
    """A root laid out like a real run directory. Returns a dict of paths."""
    root = tmp_path_factory.mktemp("viewer_root")
    run = root / "production_test"
    paths = {
        "root": root,
        "run": run,
        "dsfopt": make_synthetic_product(run / f"{STEM}_DSFOPT.nc", "dsfopt"),
        "c2rccopt": make_synthetic_product(run / f"{STEM}_C2RCCOPT.nc", "c2rccopt"),
        "l2w": make_synthetic_product(
            run / "dsf" / "S2B_MSIL1C_20250109T160509_N0511_R054_T17RNK_20250109T202050" / f"{STEM}_L2W.nc", "l2w"),
        "c2rcc_raw": make_synthetic_product(run / "c2rcc" / f"{STEM}_C2RCC.nc", "c2rcc_raw"),
        "canon": make_synthetic_product(run / "c2rcc" / f"{STEM}_C2RCCCANON.nc", "canon"),
        "weird": make_synthetic_product(root / "other" / "weird_name.nc", "dsfopt", ny=20, nx=30),
    }
    (root / "other" / "notes.txt").write_text("not a product\n", encoding="utf-8")
    (root / ".hidden").mkdir()
    make_synthetic_product(root / ".hidden" / f"{STEM}_DSFOPT.nc", "dsfopt", ny=10, nx=10)
    return paths


@pytest.fixture(scope="session")
def dsfopt_path(synthetic_root) -> Path:
    return synthetic_root["dsfopt"]


@pytest.fixture(scope="session")
def c2rccopt_path(synthetic_root) -> Path:
    return synthetic_root["c2rccopt"]


@pytest.fixture(scope="session")
def l2w_path(synthetic_root) -> Path:
    return synthetic_root["l2w"]


@pytest.fixture(scope="session")
def c2rcc_raw_path(synthetic_root) -> Path:
    return synthetic_root["c2rcc_raw"]


@pytest.fixture(scope="session")
def canon_path(synthetic_root) -> Path:
    return synthetic_root["canon"]


@pytest.fixture(scope="session")
def viewer_config(synthetic_root):
    from viewer.server.config import RootSpec, ViewerConfig
    return ViewerConfig(roots=[RootSpec(str(synthetic_root["root"]), "test root")],
                        cache_mb=64, open_browser=False, max_depth=4)


@pytest.fixture(scope="session")
def array_cache():
    from viewer.server.cache import ArrayCache
    return ArrayCache(64 * 1024 * 1024)


@pytest.fixture(scope="session")
def client(viewer_config):
    from fastapi.testclient import TestClient
    from viewer.server.app import create_app
    app = create_app(viewer_config)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def real_nc() -> Path:
    """A real product when ``VIEWER_REAL_NC`` is set; otherwise skip."""
    p = os.environ.get("VIEWER_REAL_NC")
    if not p or not Path(p).is_file():
        pytest.skip("VIEWER_REAL_NC not set")
    return Path(p)
