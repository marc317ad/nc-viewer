"""Dataset access: open a product, describe its grid and bands, read bands,
look up pixel values and lat/lon, expose metadata.

Contract: plan section 4 (``FileInfo``, ``Grid``, ``Band``, ``PixelValue``,
``VarMeta``) and section 3.4 (``match_key``), 3.5 (band kinds).

Thread safety: netCDF4/HDF5 is not thread-safe. Every call into xarray or
netCDF4 that touches a file must hold ``NETCDF_LOCK``. Pure numpy work on
arrays already in memory must NOT hold it.

WP1 implements every ``NotImplementedError``. Signatures are pinned.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import numpy as np

from .cache import ArrayCache

PathLike = Union[str, Path]

#: Global lock around all netCDF/HDF5 I/O (plan section 7).
NETCDF_LOCK = threading.RLock()

#: Sentinel-2 nominal band centres (nm) used for ``match_key`` folding.
S2_NOMINAL_NM: Dict[str, int] = {
    "B1": 443, "B01": 443, "B2": 492, "B02": 492, "B3": 560, "B03": 560,
    "B4": 665, "B04": 665, "B5": 704, "B05": 704, "B6": 740, "B06": 740,
    "B7": 783, "B07": 783, "B8": 833, "B08": 833, "B8A": 865,
    "B9": 945, "B09": 945, "B10": 1375, "B11": 1614, "B12": 2202,
}
S2_CENTRES_NM: Tuple[int, ...] = (443, 492, 560, 665, 704, 740, 783, 833, 865, 945, 1375, 1614, 2202)

#: Spectral family folding for ``match_key`` (lower-cased prefix -> family).
SPECTRAL_FAMILIES: Dict[str, str] = {
    "rrs": "Rrs", "rhow": "rhow", "rhown": "rhow", "rhos": "rhos", "rhot": "rhot",
    "rhorc": "rhorc", "lt": "Lt", "ed": "Ed",
}

STATUS_MEANINGS: Dict[int, str] = {1: "value", 2: "outside_aoi", 4: "flagged", 8: "no_data"}
LATLON_NAMES = ("lat", "lon", "latitude", "longitude", "aux_latitude", "aux_longitude")
MAX_ATTR_CHARS = 4000


@dataclass
class Grid:
    """Affine description of the (y, x) raster. ``x0, y0`` are the OUTER EDGE
    of pixel (row 0, col 0); ``dx > 0``; ``dy < 0`` for north-up projected
    grids and ``-1`` for pixel space (so world ``Y = -row``).

    ``col = floor((X - x0) / dx)``, ``row = floor((Y - y0) / dy)``.
    """

    kind: str                 # "projected" | "pixel"
    crs: Optional[str]        # "EPSG:32617", or WKT when no EPSG resolves, or None
    space: str                # crs for projected, "pixel" otherwise (sync-group key)
    x0: float
    dx: float
    y0: float
    dy: float
    units: str                # "m" | "px"
    bounds: Tuple[float, float, float, float]   # xmin, ymin, xmax, ymax (world)
    has_latlon: bool
    ny: int = 0
    nx: int = 0

    def world_to_rowcol(self, x: float, y: float) -> Tuple[int, int]:
        """Integer (row, col) of the pixel containing world point (x, y);
        may be outside ``[0, ny) x [0, nx)``."""
        col = int(np.floor((x - self.x0) / self.dx))
        row = int(np.floor((y - self.y0) / self.dy))
        return row, col

    def rowcol_to_world(self, row: float, col: float) -> Tuple[float, float]:
        """World coordinate of a (fractional) pixel position; integer
        ``row, col`` plus 0.5 gives the pixel centre."""
        return self.x0 + col * self.dx, self.y0 + row * self.dy

    def contains(self, row: int, col: int) -> bool:
        return 0 <= row < self.ny and 0 <= col < self.nx

    def to_dict(self) -> dict:
        d = asdict(self)
        d["bounds"] = list(self.bounds)
        return d

    @classmethod
    def pixel_space(cls, ny: int, nx: int, has_latlon: bool = False) -> "Grid":
        return cls(kind="pixel", crs=None, space="pixel", x0=0.0, dx=1.0, y0=0.0, dy=-1.0,
                   units="px", bounds=(0.0, -float(ny), float(nx), 0.0),
                   has_latlon=has_latlon, ny=ny, nx=nx)


@dataclass
class BandInfo:
    name: str
    long_name: str
    units: str
    wavelength: Optional[float]
    parameter: str
    dtype: str
    kind: str                         # "float" | "flags" | "status" | "latlon"
    group: str
    match_key: str
    flag_meanings: Optional[List[str]] = None
    flag_masks: Optional[List[int]] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PixelValue:
    file: str
    band: str
    x: float
    y: float
    row: int
    col: int
    inside: bool
    value: Optional[float]
    status: Optional[str]
    flags: Optional[List[str]]
    lon: Optional[float]
    lat: Optional[float]

    def to_dict(self) -> dict:
        return asdict(self)


def nominal_s2_wavelength(nm: float, tolerance_nm: float = 25.0) -> Optional[int]:
    """Nearest S2 band centre within ``tolerance_nm``; else None."""
    raise NotImplementedError("WP1")


def match_key_for(name: str, attrs: dict) -> str:
    """Plan section 3.4. Examples (all -> ``"Rrs@560"``): ``Rrs_B03`` with
    ``wavelength=559.8``; ``rrs_B3`` (band id, no wavelength attr);
    ``Rrs_560`` (wavelength in the name). ``rhown_B3`` -> ``"rhow@560"``.
    ``conc_chl`` -> ``"conc_chl"``. ``pixel_status`` -> ``"pixel_status"``."""
    raise NotImplementedError("WP1")


def classify_band(name: str, attrs: dict, dtype: np.dtype) -> str:
    """Return the band kind: ``"status"`` for ``pixel_status``; ``"flags"``
    when ``flag_masks``/``flag_meanings`` present or name ends with
    ``_flags``; ``"latlon"`` for ``LATLON_NAMES``; otherwise ``"float"``."""
    raise NotImplementedError("WP1")


def band_group(name: str, kind: str) -> str:
    """Display group: ``"status"``/``"flags"`` by kind, ``"aux"`` for latlon,
    else the lower-cased prefix before the first ``_`` when the name has one
    (``Rrs_B03`` -> ``"rrs"``, ``conc_chl`` -> ``"conc"``), else ``"other"``."""
    raise NotImplementedError("WP1")


class DatasetHandle:
    """One opened product.

    Construction opens the file (``xarray.open_dataset(path, engine="netcdf4",
    decode_coords="all", mask_and_scale=True, cache=False)``) under
    ``NETCDF_LOCK``, derives ``shape``, ``grid``, ``bands``, ``default_band``,
    ``status_band`` and ``flag_bands``, and keeps the dataset open for later
    reads. Bands are every variable with dims exactly ``(y, x)`` (or the two
    spatial dims of the file when they are named differently; the first
    2-D variable's dims define them). Scalar grid-mapping variables and 1-D
    coordinates are not bands.

    Grid detection: when 1-D coordinate variables for both spatial dims exist
    with ``standard_name projection_x/y_coordinate`` (or names ``x``/``y`` in
    metres), build a projected ``Grid`` from their first two values (edge =
    centre - step/2); CRS from the ``grid_mapping`` variable's ``crs_wkt``
    (pyproj -> ``"EPSG:NNNN"`` when ``to_epsg()`` resolves, else the WKT), or
    the global ``analysis_window_epsg`` attribute. Otherwise ``Grid.pixel_space``
    with ``has_latlon`` when 2-D lat/lon variables exist.

    ``default_band``: the band whose ``match_key`` is ``"Rrs@560"`` if any,
    else the first ``"float"`` band, else the first band.
    """

    def __init__(self, path: PathLike, cache: ArrayCache) -> None:
        self.path = str(Path(path))
        self.cache = cache
        self.shape: Tuple[int, int] = (0, 0)
        self.grid: Grid = Grid.pixel_space(0, 0)
        self.bands: List[BandInfo] = []
        self.default_band: Optional[str] = None
        self.status_band: Optional[str] = None
        self.flag_bands: List[str] = []
        self._ds = None
        self._open()

    # -- lifecycle -----------------------------------------------------------
    def _open(self) -> None:
        raise NotImplementedError("WP1")

    def close(self) -> None:
        raise NotImplementedError("WP1")

    # -- description ---------------------------------------------------------
    def band(self, name: str) -> BandInfo:
        """``KeyError`` when ``name`` is not a band."""
        for b in self.bands:
            if b.name == name:
                return b
        raise KeyError(name)

    def info_dict(self, file_id: str = "") -> dict:
        """``FileInfo`` (plan section 4)."""
        return {
            "id": file_id,
            "path": self.path,
            "name": Path(self.path).name,
            "shape": list(self.shape),
            "grid": self.grid.to_dict(),
            "bands": [b.to_dict() for b in self.bands],
            "default_band": self.default_band,
            "status_band": self.status_band,
            "flag_bands": list(self.flag_bands),
        }

    def metadata(self) -> dict:
        """Plan section 4 ``/meta`` payload. Attribute values stringified and
        truncated at ``MAX_ATTR_CHARS`` with `` ...[truncated]``; numpy
        scalars/arrays rendered via ``str()``; encoding limited to
        ``dtype, _FillValue, zlib, chunksizes``."""
        raise NotImplementedError("WP1")

    # -- data ----------------------------------------------------------------
    def read_band(self, name: str) -> np.ndarray:
        """Full 2-D array for ``name``. Float kinds -> ``float32`` with NaN
        fill. ``flags``/``status`` kinds -> ``float64`` holding the integer
        word, NaN where the fill value was (exact for words < 2**53).
        Cached in ``self.cache`` under ``("band", self.path, name)``; the read
        itself holds ``NETCDF_LOCK``. ``KeyError`` for unknown bands."""
        raise NotImplementedError("WP1")

    def value_at(self, name: str, x: float, y: float) -> PixelValue:
        """Sample ``name`` at world point ``(x, y)`` (pixel containing it).
        ``inside`` false (and value None) when off-grid. ``value`` is the
        float (or integer word as float) or None when NaN. ``status`` is the
        decoded ``pixel_status`` meaning at that pixel when the file has one
        (``None`` otherwise). ``flags`` lists set flag names when ``name``
        is a flags/status band, else when the file has a flags band
        (``c2rcc_flags``/``l2_flags``) its set names, else None.
        ``lon, lat`` from ``latlon_at``. Must not read the whole band when it
        is not already cached: read the single pixel through xarray
        ``isel`` under the lock in that case."""
        raise NotImplementedError("WP1")

    def latlon_at(self, row: int, col: int) -> Tuple[Optional[float], Optional[float]]:
        """``(lon, lat)`` of the pixel centre: projected grids transform the
        centre with pyproj (``always_xy=True``) to EPSG:4326; pixel-space
        grids read the 2-D lat/lon variables at ``[row, col]``; ``(None,
        None)`` when neither is possible or off-grid."""
        raise NotImplementedError("WP1")


class DatasetRegistry:
    """Lazily opened ``DatasetHandle`` per catalog id, sharing one cache.
    ``get`` raises ``KeyError`` for unknown ids and lets open errors
    propagate (``FileNotFoundError``, ``OSError``, ``ValueError``)."""

    def __init__(self, catalog, cache: ArrayCache) -> None:
        self.catalog = catalog
        self.cache = cache
        self._handles: Dict[str, DatasetHandle] = {}
        self._lock = threading.Lock()

    def get(self, entry_id: str) -> DatasetHandle:
        entry = self.catalog.get(entry_id)  # KeyError propagates
        with self._lock:
            h = self._handles.get(entry_id)
            if h is None:
                h = DatasetHandle(entry.path, self.cache)
                self._handles[entry_id] = h
            return h

    def close_all(self) -> None:
        with self._lock:
            for h in self._handles.values():
                try:
                    h.close()
                except Exception:
                    pass
            self._handles.clear()
