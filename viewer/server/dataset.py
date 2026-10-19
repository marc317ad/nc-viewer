"""Dataset access: open a product, describe its grid and bands, read bands,
look up pixel values and lat/lon, expose metadata.

Contract: plan section 4 (``FileInfo``, ``Grid``, ``Band``, ``PixelValue``,
``VarMeta``) and section 3.4 (``match_key``), 3.5 (band kinds).

Thread safety: netCDF4/HDF5 is not thread-safe. Every call into netCDF4
that touches a file holds ``NETCDF_LOCK``. Pure numpy work on arrays
already in memory does not.

Implementation notes (WP1):

- Files are read with ``netCDF4`` directly (auto mask + scale on), not
  xarray: one open per handle, whole-variable reads, single-pixel reads for
  uncached lookups.
- ACOLITE's ``l2_flags`` carries no CF vocabulary on the variable; its bit
  names come from the global ``flag_exponent_<name>`` attributes when
  present, else ``bit0..bitN``.
"""

from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import asdict, dataclass
from datetime import datetime as _dt
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

from .cache import ArrayCache

PathLike = Union[str, Path]

#: Global lock around all netCDF/HDF5 I/O (plan section 7).
NETCDF_LOCK = threading.RLock()

#: HDF5 per-variable chunk cache for every file the viewer opens. netCDF4's
#: default is 64 MB per variable, and a whole-variable read leaves the
#: decompressed chunks parked in that cache for as long as the file is open:
#: measured on the 805 MB T17RNM C2RCCOPT, 16 band reads cost 1004 MB on top
#: of the viewer's own 1138 MB band cache; at 1 MB they cost 84 MB, at the
#: same read time (~200 ms per 57 MB band). 4 MB still holds a few chunks for
#: the single-pixel reads behind ``value_at``.
HDF5_CHUNK_CACHE_BYTES = 4 * 1024 * 1024
HDF5_CHUNK_CACHE_NELEMS = 521
HDF5_CHUNK_CACHE_PREEMPTION = 0.75


def _open_dataset(path: PathLike):
    """``netCDF4.Dataset(path)`` read-only, auto mask + scale on, with the
    viewer's chunk-cache setting applied first. Call under ``NETCDF_LOCK``."""
    import netCDF4
    netCDF4.set_chunk_cache(HDF5_CHUNK_CACHE_BYTES, HDF5_CHUNK_CACHE_NELEMS, HDF5_CHUNK_CACHE_PREEMPTION)
    ds = netCDF4.Dataset(path, mode="r")
    ds.set_auto_maskandscale(True)
    return ds

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

#: Display groups (band select is grouped by these; order = display order).
GROUP_REFLECTANCE = "Reflectance"
GROUP_PRODUCTS = "Products"
GROUP_FLAGS = "Flags"
GROUP_GEO = "Geolocation"
GROUP_OTHER = "Other"
GROUP_ORDER: Tuple[str, ...] = (GROUP_REFLECTANCE, GROUP_PRODUCTS, GROUP_FLAGS, GROUP_GEO, GROUP_OTHER)
REFLECTANCE_PREFIXES = ("rrs", "rhow", "rhown", "rhos", "rhot", "rhorc")
PRODUCT_PREFIXES = ("conc_", "iop_", "kd", "chl", "tsm", "spm", "tur", "unc_")
FAMILY_RANK = {"Rrs": 0, "rhow": 1, "rhos": 2, "rhot": 3, "rhorc": 4}

FLAG_VAR_NAMES = ("l2_flags", "c2rcc_flags")
WAVELENGTH_ATTRS = ("wavelength", "spectral_wavelength", "central_wavelength")
SPATIAL_DIM_PAIRS = (("y", "x"), ("lat", "lon"), ("latitude", "longitude"),
                     ("rows", "columns"), ("row", "col"), ("ny", "nx"))
MAX_ATTR_ARRAY = 64
DEFAULT_MATCH_KEY = "Rrs@560"

_BAND_CODE_RE = re.compile(r"_(B\d{1,2}A?)$", re.IGNORECASE)
_NM_SUFFIX_RE = re.compile(r"_(\d{3,4}(?:\.\d+)?)$")
_FLAG_EXPONENT_PREFIX = "flag_exponent_"


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


# ---------------------------------------------------------------------------
# Small value helpers
# ---------------------------------------------------------------------------

def _as_float(value: Any) -> Optional[float]:
    """First element of ``value`` as a finite float, else None."""
    try:
        arr = np.asarray(value).ravel()
        if arr.size == 0:
            return None
        f = float(arr[0])
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def _finite_or_none(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def _text(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    if isinstance(value, np.ndarray):
        flat = value.ravel()
        return str(flat[0]) if flat.size == 1 else " ".join(str(v) for v in flat)
    return str(value)


def _attr_text(value: Any) -> str:
    """Stringify an attribute value for ``/meta`` (arrays listed, long values
    truncated with `` ...[truncated, N chars]``)."""
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        text = value
    elif isinstance(value, (np.ndarray, list, tuple)):
        flat = np.asarray(value).ravel()
        items = [str(v) for v in flat[:MAX_ATTR_ARRAY]]
        if flat.size == 1:
            text = items[0]
        elif flat.size > MAX_ATTR_ARRAY:
            text = "[" + ", ".join(items) + f", ... ({flat.size} values)]"
        else:
            text = "[" + ", ".join(items) + "]"
    else:
        text = str(value)
    if len(text) > MAX_ATTR_CHARS:
        text = text[:MAX_ATTR_CHARS] + f" ...[truncated, {len(text)} chars]"
    return text


def _int_list(value: Any) -> List[int]:
    """``flag_masks``/``flag_values`` as ints; accepts numeric arrays and the
    legacy space-joined string form."""
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        tokens = value.replace(",", " ").split()
    else:
        tokens = list(np.asarray(value).ravel())
    out: List[int] = []
    for tok in tokens:
        try:
            out.append(int(tok))
        except (TypeError, ValueError):
            try:
                out.append(int(float(tok)))
            except (TypeError, ValueError):
                return []
    return out


def _str_list(value: Any) -> List[str]:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        return value.replace(",", " ").split()
    return [str(v) for v in np.asarray(value).ravel()]


def _bit_index(mask: int) -> int:
    return mask.bit_length() - 1 if mask > 0 else 0


# ---------------------------------------------------------------------------
# Band classification
# ---------------------------------------------------------------------------

def nominal_s2_wavelength(nm: float, tolerance_nm: float = 25.0) -> Optional[int]:
    """Nearest S2 band centre within ``tolerance_nm``; else None."""
    value = _as_float(nm)
    if value is None:
        return None
    best = min(S2_CENTRES_NM, key=lambda c: abs(c - value))
    return best if abs(best - value) <= tolerance_nm else None


def _spectral_family(name: str) -> Optional[str]:
    prefix = name.split("_", 1)[0].lower()
    return SPECTRAL_FAMILIES.get(prefix)


def _wavelength_attr(attrs: dict) -> Optional[float]:
    for key in WAVELENGTH_ATTRS:
        if key in attrs:
            wl = _as_float(attrs[key])
            if wl is not None and wl > 0:
                return wl
    return None


def _band_code(name: str) -> Optional[str]:
    m = _BAND_CODE_RE.search(name)
    if m is None:
        return None
    code = m.group(1).upper()
    return code if code in S2_NOMINAL_NM else None


def _wavelength_from_name(name: str) -> Optional[float]:
    m = _NM_SUFFIX_RE.search(name)
    if m is not None:
        return float(m.group(1))
    code = _band_code(name)
    return float(S2_NOMINAL_NM[code]) if code else None


def band_wavelength(name: str, attrs: dict) -> Optional[float]:
    """Wavelength (nm) from the attributes, else, for spectral families, from
    the name (``_560`` suffix or S2 band code); None otherwise."""
    wl = _wavelength_attr(attrs)
    if wl is None and _spectral_family(name) is not None:
        wl = _wavelength_from_name(name)
    return wl


def match_key_for(name: str, attrs: dict) -> str:
    """Plan section 3.4. Examples (all -> ``"Rrs@560"``): ``Rrs_B03`` with
    ``wavelength=559.8``; ``rrs_B3`` (band id, no wavelength attr);
    ``Rrs_560`` (wavelength in the name). ``rhown_B3`` -> ``"rhow@560"``.
    ``conc_chl`` -> ``"conc_chl"``. ``pixel_status`` -> ``"pixel_status"``."""
    family = _spectral_family(name)
    if family is None:
        return name
    wl = band_wavelength(name, attrs)
    if wl is None:
        return name
    nominal = nominal_s2_wavelength(wl)
    label = nominal if nominal is not None else int(round(wl))
    return f"{family}@{label}"


def _is_flags_name(name: str) -> bool:
    low = name.lower()
    return low.endswith("_flags") or low in FLAG_VAR_NAMES


def _is_latlon_name(name: str) -> bool:
    low = name.lower()
    return low in LATLON_NAMES or low.endswith(("_latitude", "_longitude"))


def classify_band(name: str, attrs: dict, dtype: np.dtype) -> str:
    """Return the band kind: ``"status"`` for ``pixel_status``; ``"flags"``
    when ``flag_masks``/``flag_meanings`` present or name ends with
    ``_flags``; ``"latlon"`` for ``LATLON_NAMES``; otherwise ``"float"``."""
    if name == "pixel_status":
        return "status"
    has_vocab = any(k in attrs for k in ("flag_masks", "flag_meanings", "flag_values"))
    if has_vocab or _is_flags_name(name):
        return "flags"
    if _is_latlon_name(name):
        return "latlon"
    return "float"


def band_group(name: str, kind: str) -> str:
    """Display group: ``"Flags"`` for flags/status kinds, ``"Geolocation"``
    for latlon, ``"Reflectance"`` for the Rrs/rhow/rhown/rhos/rhot families,
    ``"Products"`` for conc_/iop_/kd/chl/tsm/spm/tur/unc_ names, else
    ``"Other"``."""
    if kind in ("flags", "status"):
        return GROUP_FLAGS
    if kind == "latlon":
        return GROUP_GEO
    prefix = name.split("_", 1)[0].lower()
    if prefix in REFLECTANCE_PREFIXES:
        return GROUP_REFLECTANCE
    if name.lower().startswith(PRODUCT_PREFIXES):
        return GROUP_PRODUCTS
    return GROUP_OTHER


def _cf_vocabulary(attrs: dict) -> Tuple[List[str], List[int]]:
    meanings = _str_list(attrs["flag_meanings"]) if "flag_meanings" in attrs else []
    masks: List[int] = []
    for key in ("flag_masks", "flag_values"):
        if key in attrs:
            masks = _int_list(attrs[key])
            if masks:
                break
    if meanings and not masks:
        masks = [1 << i for i in range(len(meanings))]
    elif masks and not meanings:
        meanings = [f"bit{_bit_index(m)}" for m in masks]
    n = min(len(meanings), len(masks))
    return meanings[:n], masks[:n]


def _acolite_vocabulary(global_attrs: dict) -> Tuple[List[str], List[int]]:
    """ACOLITE records ``flag_exponent_<name>=N`` globally (ac_switch.py)."""
    pairs: List[Tuple[int, str]] = []
    for key, value in global_attrs.items():
        if not key.startswith(_FLAG_EXPONENT_PREFIX):
            continue
        exp = _as_float(value)
        if exp is not None and 0 <= exp < 64:
            pairs.append((int(exp), key[len(_FLAG_EXPONENT_PREFIX):]))
    pairs.sort()
    return [name for _, name in pairs], [1 << exp for exp, _ in pairs]


def _bit_vocabulary(dtype: np.dtype) -> Tuple[List[str], List[int]]:
    nbits = min(int(np.dtype(dtype).itemsize) * 8, 32) if np.dtype(dtype).kind in "iu" else 32
    return [f"bit{i}" for i in range(nbits)], [1 << i for i in range(nbits)]


def _flag_vocabulary(kind: str, attrs: dict, dtype: np.dtype,
                     global_attrs: dict) -> Tuple[Optional[List[str]], Optional[List[int]]]:
    if kind == "status":
        meanings, masks = _cf_vocabulary(attrs)
        if not meanings:
            return list(STATUS_MEANINGS.values()), list(STATUS_MEANINGS.keys())
        return meanings, masks
    if kind == "flags":
        meanings, masks = _cf_vocabulary(attrs)
        if not meanings:
            meanings, masks = _acolite_vocabulary(global_attrs)
        if not meanings:
            meanings, masks = _bit_vocabulary(dtype)
        return meanings, masks
    return None, None


def build_band_info(name: str, attrs: dict, dtype: np.dtype, global_attrs: Optional[dict] = None) -> BandInfo:
    """``BandInfo`` for a 2-D variable from its attributes and dtype."""
    global_attrs = global_attrs or {}
    kind = classify_band(name, attrs, dtype)
    meanings, masks = _flag_vocabulary(kind, attrs, dtype, global_attrs)
    return BandInfo(
        name=name,
        long_name=_text(attrs.get("long_name", "")),
        units=_text(attrs.get("units", "")),
        wavelength=band_wavelength(name, attrs),
        parameter=_text(attrs.get("parameter", "")),
        dtype=str(np.dtype(dtype)),
        kind=kind,
        group=band_group(name, kind),
        match_key=match_key_for(name, attrs),
        flag_meanings=meanings,
        flag_masks=masks,
    )


def _band_sort_key(info: BandInfo) -> tuple:
    gi = GROUP_ORDER.index(info.group) if info.group in GROUP_ORDER else len(GROUP_ORDER)
    if info.group == GROUP_REFLECTANCE:
        family = info.match_key.split("@", 1)[0]
        wl = info.wavelength if info.wavelength is not None else float("inf")
        return (gi, FAMILY_RANK.get(family, 9), wl, info.name)
    if info.group == GROUP_FLAGS:
        return (gi, 0 if info.kind == "status" else 1, 0.0, "")
    return (gi, 0, 0.0, "")   # file order within the group (stable sort)


def order_bands(bands: Sequence[BandInfo]) -> List[BandInfo]:
    """Reflectance by (family, wavelength), then Products, Flags (status
    first), Geolocation, Other; file order otherwise."""
    return sorted(bands, key=_band_sort_key)


def set_flag_names(word: int, meanings: Optional[Sequence[str]], masks: Optional[Sequence[int]]) -> List[str]:
    """Names of the flags set in ``word`` (bitwise against ``masks``)."""
    if not meanings or not masks:
        return []
    return [m for m, k in zip(meanings, masks) if word & int(k)]


def _pick_default(bands: Sequence[BandInfo]) -> Optional[str]:
    for b in bands:
        if b.match_key == DEFAULT_MATCH_KEY:
            return b.name
    for b in bands:
        if b.group == GROUP_REFLECTANCE and b.match_key.endswith("@560"):
            return b.name
    for b in bands:
        if b.kind == "float":
            return b.name
    return bands[0].name if bands else None


# ---------------------------------------------------------------------------
# CRS helpers
# ---------------------------------------------------------------------------

def _crs_strings(user_input: Any) -> Tuple[Optional[str], Optional[str]]:
    """``(crs, space)`` for a WKT / EPSG code / proj string; None, None when
    pyproj cannot parse it."""
    from pyproj import CRS
    try:
        crs = CRS.from_user_input(user_input)
    except Exception:
        return None, None
    try:
        epsg = crs.to_epsg()
    except Exception:
        epsg = None
    if epsg:
        code = f"EPSG:{int(epsg)}"
        return code, code
    wkt = crs.to_wkt()
    return wkt, "wkt:" + hashlib.sha1(wkt.encode("utf-8")).hexdigest()[:12]


def _grid_mapping_crs(var_records: Dict[str, dict], band_attrs: Dict[str, dict],
                      global_attrs: dict) -> Tuple[Optional[str], Optional[str]]:
    """CRS from the grid-mapping variable named by the bands (or any variable
    carrying ``crs_wkt``/``spatial_ref``), else the global
    ``analysis_window_epsg``."""
    candidates: List[str] = []
    for attrs in band_attrs.values():
        gm = attrs.get("grid_mapping")
        if isinstance(gm, str) and gm in var_records and gm not in candidates:
            candidates.append(gm)
    for name, rec in var_records.items():
        a = rec["attrs"]
        if ("crs_wkt" in a or "spatial_ref" in a or "grid_mapping_name" in a) and name not in candidates:
            candidates.append(name)
    for name in candidates:
        a = var_records[name]["attrs"]
        for key in ("crs_wkt", "spatial_ref", "esri_pe_string"):
            if isinstance(a.get(key), str) and a[key].strip():
                crs, space = _crs_strings(a[key])
                if crs:
                    return crs, space
        for key in ("epsg_code", "epsg", "EPSG"):
            code = _as_float(a.get(key)) if key in a else None
            if code:
                crs, space = _crs_strings(int(code))
                if crs:
                    return crs, space
    code = _as_float(global_attrs.get("analysis_window_epsg")) if "analysis_window_epsg" in global_attrs else None
    if code:
        return _crs_strings(int(code))
    return None, None


# ---------------------------------------------------------------------------
# Array helpers
# ---------------------------------------------------------------------------

def _to_float_array(raw: Any, kind: str) -> np.ndarray:
    """Masked/plain array -> float32 (float kinds) or float64 (flags, status,
    latlon) with NaN where masked. ``raw`` is consumed: a fresh netCDF4 read
    that already has the target dtype keeps its buffer instead of being
    copied a second time (a full-tile L2W band is 331 MB)."""
    target = np.float32 if kind == "float" else np.float64
    if np.ma.isMaskedArray(raw):
        out = np.asarray(np.ma.getdata(raw)).astype(target, copy=False)
        mask = np.ma.getmask(raw)
        if mask is not np.ma.nomask and np.any(mask):
            out[mask] = np.nan
    else:
        out = np.asarray(raw).astype(target, copy=False)
    return np.ascontiguousarray(out)


def _scalar_or_none(raw: Any) -> Optional[float]:
    if np.ma.is_masked(raw):
        return None
    return _finite_or_none(np.asarray(np.ma.getdata(raw)).ravel()[0])


def _is_numeric_dtype(dtype: Any) -> bool:
    try:
        return np.dtype(dtype).kind in "biuf"
    except TypeError:
        return False


# ---------------------------------------------------------------------------
# DatasetHandle
# ---------------------------------------------------------------------------

class DatasetHandle:
    """One opened product.

    Construction opens the file with netCDF4 under ``NETCDF_LOCK``, derives
    ``shape``, ``grid``, ``bands``, ``default_band``, ``status_band`` and
    ``flag_bands``, and keeps the dataset open for later reads. Bands are
    every numeric variable with dims exactly ``(y, x)`` (or the two spatial
    dims of the file when they are named differently; the first 2-D
    variable's dims define them). Scalar grid-mapping variables and 1-D
    coordinates are not bands.

    Grid detection: when 1-D coordinate variables for both spatial dims exist
    with ``standard_name projection_x/y_coordinate`` (or names ``x``/``y``),
    build a projected ``Grid`` from their first two values (edge = centre -
    step/2); CRS from the ``grid_mapping`` variable's ``crs_wkt`` (pyproj ->
    ``"EPSG:NNNN"`` when ``to_epsg()`` resolves, else the WKT), or the global
    ``analysis_window_epsg`` attribute. Otherwise ``Grid.pixel_space`` with
    ``has_latlon`` when 2-D lat/lon variables exist.

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
        self._global_attrs: Dict[str, Any] = {}
        self._dims: Dict[str, int] = {}
        self._var_records: Dict[str, dict] = {}
        self._lat_name: Optional[str] = None
        self._lon_name: Optional[str] = None
        self._transformer = None
        self._transformer_tried = False
        self._tr_lock = threading.Lock()
        self._open()

    # -- lifecycle -----------------------------------------------------------
    def _open(self) -> None:
        with NETCDF_LOCK:
            ds = _open_dataset(self.path)
            try:
                self._global_attrs = {k: ds.getncattr(k) for k in ds.ncattrs()}
                self._dims = {name: len(dim) for name, dim in ds.dimensions.items()}
                self._var_records = {name: self._record(var) for name, var in ds.variables.items()}
                self._describe(ds)
            except Exception:
                ds.close()
                raise
            self._ds = ds

    @staticmethod
    def _record(var) -> dict:
        attrs = {k: var.getncattr(k) for k in var.ncattrs()}
        try:
            filters = var.filters() or {}
        except Exception:
            filters = {}
        try:
            chunking = var.chunking()
        except Exception:
            chunking = None
        return {
            "name": var.name,
            "dtype": str(var.dtype) if _is_numeric_dtype(var.dtype) else getattr(var.dtype, "__name__", str(var.dtype)),
            "numeric": _is_numeric_dtype(var.dtype),
            "np_dtype": np.dtype(var.dtype) if _is_numeric_dtype(var.dtype) else None,
            "dims": list(var.dimensions),
            "shape": [int(s) for s in var.shape],
            "attrs": attrs,
            "zlib": bool(filters.get("zlib")) if filters else False,
            "chunksizes": [int(c) for c in chunking] if isinstance(chunking, (list, tuple)) else None,
        }

    def _spatial_dims(self) -> Optional[Tuple[str, str]]:
        dims = self._dims
        for ydim, xdim in SPATIAL_DIM_PAIRS:
            if ydim in dims and xdim in dims:
                return ydim, xdim
        for rec in self._var_records.values():
            if len(rec["dims"]) == 2 and rec["numeric"] and all(dims.get(d, 0) > 1 for d in rec["dims"]):
                return rec["dims"][0], rec["dims"][1]
        big = [n for n, s in dims.items() if s > 1]
        if len(big) == 2:
            return big[0], big[1]
        return None

    def _describe(self, ds) -> None:
        spatial = self._spatial_dims()
        if spatial is None:
            raise ValueError("no 2-D (y, x) variables found")
        ydim, xdim = spatial
        ny, nx = int(self._dims[ydim]), int(self._dims[xdim])
        self.shape = (ny, nx)

        band_attrs: Dict[str, dict] = {}
        infos: List[BandInfo] = []
        for name, rec in self._var_records.items():
            if rec["dims"] == [ydim, xdim] and rec["numeric"]:
                band_attrs[name] = rec["attrs"]
                infos.append(build_band_info(name, rec["attrs"], rec["np_dtype"], self._global_attrs))
        self.bands = order_bands(infos)
        self.default_band = _pick_default(self.bands)
        self.status_band = next((b.name for b in self.bands if b.kind == "status"), None)
        self.flag_bands = [b.name for b in self.bands if b.kind == "flags"]
        self._lat_name, self._lon_name = self._latlon_names()
        has_latlon = self._lat_name is not None and self._lon_name is not None

        grid = self._projected_grid(ds, ydim, xdim, ny, nx, has_latlon, band_attrs)
        self.grid = grid if grid is not None else Grid.pixel_space(ny, nx, has_latlon)

    def _latlon_names(self) -> Tuple[Optional[str], Optional[str]]:
        lat = lon = None
        for b in self.bands:
            if b.kind != "latlon":
                continue
            low = b.name.lower()
            if lat is None and ("lat" in low):
                lat = b.name
            elif lon is None and ("lon" in low):
                lon = b.name
        return lat, lon

    @staticmethod
    def _is_projection_coord(rec: Optional[dict], axis: str, dim: str) -> bool:
        if rec is None or rec["dims"] != [dim] or not rec["numeric"]:
            return False
        std = str(rec["attrs"].get("standard_name", ""))
        return std == f"projection_{axis}_coordinate" or dim == axis

    def _projected_grid(self, ds, ydim: str, xdim: str, ny: int, nx: int,
                        has_latlon: bool, band_attrs: Dict[str, dict]) -> Optional[Grid]:
        xrec = self._var_records.get(xdim)
        yrec = self._var_records.get(ydim)
        if nx < 2 or ny < 2:
            return None
        if not (self._is_projection_coord(xrec, "x", xdim) and self._is_projection_coord(yrec, "y", ydim)):
            return None
        x = np.asarray(np.ma.getdata(ds.variables[xdim][:2]), dtype=np.float64)
        y = np.asarray(np.ma.getdata(ds.variables[ydim][:2]), dtype=np.float64)
        if not (np.all(np.isfinite(x)) and np.all(np.isfinite(y))):
            return None
        dx = float(x[1] - x[0])
        dy = float(y[1] - y[0])
        if dx == 0.0 or dy == 0.0:
            return None
        x0 = float(x[0]) - dx / 2.0
        y0 = float(y[0]) - dy / 2.0
        crs, space = _grid_mapping_crs(self._var_records, band_attrs, self._global_attrs)
        xs = (x0, x0 + nx * dx)
        ys = (y0, y0 + ny * dy)
        return Grid(
            kind="projected", crs=crs, space=space or "projected-unknown",
            x0=x0, dx=dx, y0=y0, dy=dy, units="m",
            bounds=(min(xs), min(ys), max(xs), max(ys)),
            has_latlon=has_latlon, ny=ny, nx=nx,
        )

    def _dataset(self):
        """The open netCDF4 dataset (reopened after ``close``). Call under
        ``NETCDF_LOCK``."""
        if self._ds is None:
            self._ds = _open_dataset(self.path)
        return self._ds

    def close(self) -> None:
        with NETCDF_LOCK:
            ds, self._ds = self._ds, None
            if ds is not None:
                try:
                    ds.close()
                except Exception:
                    pass

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

    def _var_meta(self, rec: dict) -> dict:
        attrs = rec["attrs"]
        fill = attrs.get("_FillValue")
        return {
            "name": rec["name"],
            "dtype": rec["dtype"],
            "dims": list(rec["dims"]),
            "shape": list(rec["shape"]),
            "attrs": {k: _attr_text(v) for k, v in attrs.items()},
            "encoding": {
                "dtype": rec["dtype"],
                "_FillValue": _attr_text(fill) if fill is not None else None,
                "zlib": rec["zlib"],
                "chunksizes": rec["chunksizes"],
            },
        }

    def metadata(self) -> dict:
        """Plan section 4 ``/meta`` payload: ``file``, ``dims``,
        ``global_attrs``, ``coords`` (the non-band variables: 1-D
        coordinates, grid mapping, ...), ``variables`` (every variable in
        file order, bands included), plus ``path``, ``grid`` and ``global``
        (same object as ``global_attrs``). Attribute values are stringified
        and truncated at ``MAX_ATTR_CHARS`` with `` ...[truncated, N chars]``."""
        p = Path(self.path)
        try:
            st = p.stat()
            file_block = {"path": self.path, "name": p.name, "size_bytes": int(st.st_size),
                          "mtime": _dt.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")}
        except OSError:
            file_block = {"path": self.path, "name": p.name, "size_bytes": None, "mtime": None}
        band_names = [b.name for b in self.bands]
        global_attrs = {k: _attr_text(v) for k, v in self._global_attrs.items()}
        coords = [self._var_meta(rec) for name, rec in self._var_records.items() if name not in band_names]
        variables = [self._var_meta(rec) for rec in self._var_records.values()]
        return {
            "file": file_block,
            "path": self.path,
            "dims": dict(self._dims),
            "global_attrs": global_attrs,
            "global": global_attrs,
            "coords": coords,
            "variables": variables,
            "grid": self.grid.to_dict(),
        }

    # -- data ----------------------------------------------------------------
    def _cache_key(self, name: str) -> tuple:
        return ("band", self.path, name)

    def read_band(self, name: str) -> np.ndarray:
        """Full 2-D array for ``name``. Float kinds -> ``float32`` with NaN
        fill. ``flags``/``status`` kinds -> ``float64`` holding the integer
        word, NaN where the fill value was (exact for words < 2**53).
        Cached in ``self.cache`` under ``("band", self.path, name)``; the read
        itself holds ``NETCDF_LOCK``. ``KeyError`` for unknown bands."""
        info = self.band(name)
        key = self._cache_key(name)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        with NETCDF_LOCK:
            raw = self._dataset().variables[name][:]
        arr = _to_float_array(raw, info.kind)
        self.cache.put(key, arr)
        return arr

    def _pixel(self, name: str, row: int, col: int) -> Optional[float]:
        """Value at ``[row, col]`` from the cached band when present, else a
        single-element read under the lock; None for fill/NaN."""
        arr = self.cache.get(self._cache_key(name))
        if arr is not None:
            return _finite_or_none(arr[row, col])
        with NETCDF_LOCK:
            raw = self._dataset().variables[name][row, col]
        return _scalar_or_none(raw)

    def _status_text(self, row: int, col: int) -> Optional[str]:
        if self.status_band is None:
            return None
        word = self._pixel(self.status_band, row, col)
        if word is None:
            return "fill"
        info = self.band(self.status_band)
        names = set_flag_names(int(word), info.flag_meanings, info.flag_masks)
        return "|".join(names) if names else f"unknown({int(word)})"

    def _flags_at(self, info: BandInfo, value: Optional[float], row: int, col: int) -> Optional[List[str]]:
        if info.kind in ("flags", "status"):
            return set_flag_names(int(value), info.flag_meanings, info.flag_masks) if value is not None else []
        if not self.flag_bands:
            return None
        fb = self.band(self.flag_bands[0])
        word = self._pixel(fb.name, row, col)
        return set_flag_names(int(word), fb.flag_meanings, fb.flag_masks) if word is not None else []

    def value_at(self, name: str, x: float, y: float) -> PixelValue:
        """Sample ``name`` at world point ``(x, y)`` (pixel containing it).
        ``inside`` false (and value None) when off-grid. ``value`` is the
        float (or the integer word for flags/status kinds) or None when NaN.
        ``status`` is the decoded ``pixel_status`` meaning at that pixel when
        the file has one (``None`` otherwise). ``flags`` lists set flag names
        when ``name`` is a flags/status band, else when the file has a flags
        band (``c2rcc_flags``/``l2_flags``) its set names, else None.
        ``lon, lat`` from ``latlon_at``. Does not read the whole band when it
        is not already cached: reads the single pixel under the lock."""
        info = self.band(name)
        fname = Path(self.path).name
        row, col = self.grid.world_to_rowcol(x, y)
        if not self.grid.contains(row, col):
            return PixelValue(file=fname, band=name, x=float(x), y=float(y), row=row, col=col,
                              inside=False, value=None, status=None, flags=None, lon=None, lat=None)
        raw = self._pixel(name, row, col)
        value: Optional[float]
        if raw is None:
            value = None
        elif info.kind in ("flags", "status"):
            value = int(raw)
        else:
            value = float(raw)
        lon, lat = self.latlon_at(row, col)
        return PixelValue(
            file=fname, band=name, x=float(x), y=float(y), row=row, col=col, inside=True,
            value=value, status=self._status_text(row, col),
            flags=self._flags_at(info, raw, row, col), lon=lon, lat=lat,
        )

    def _get_transformer(self):
        if self._transformer_tried:
            return self._transformer
        self._transformer_tried = True
        if self.grid.kind == "projected" and self.grid.crs:
            try:
                from pyproj import Transformer
                self._transformer = Transformer.from_crs(self.grid.crs, "EPSG:4326", always_xy=True)
            except Exception:
                self._transformer = None
        return self._transformer

    def latlon_at(self, row: int, col: int) -> Tuple[Optional[float], Optional[float]]:
        """``(lon, lat)`` of the pixel centre: projected grids transform the
        centre with pyproj (``always_xy=True``) to EPSG:4326; otherwise the
        2-D lat/lon variables are read at ``[row, col]``; ``(None, None)``
        when neither is possible or off-grid."""
        if not self.grid.contains(row, col):
            return None, None
        tr = self._get_transformer()
        if tr is not None:
            x, y = self.grid.rowcol_to_world(row + 0.5, col + 0.5)
            with self._tr_lock:
                lon, lat = tr.transform(x, y)
            return _finite_or_none(lon), _finite_or_none(lat)
        if self._lat_name and self._lon_name:
            return self._pixel(self._lon_name, row, col), self._pixel(self._lat_name, row, col)
        return None, None


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
