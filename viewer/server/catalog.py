"""Catalog: scan the configured roots for ``*.nc`` files and parse their names.

Contract: plan section 4 (``FileEntry``). Scanning never opens a file; it
records names, sizes and mtimes only, so a few hundred files scan in well
under a second even on a network drive.

Filename convention (``processors/ac_orchestrator.py:245``)::

    {S2A|S2B|S2C}_MSI_{YYYY}_{MM}_{DD}_{hh}_{mm}_{ss}_{Txxxxx}_{ROLE}.nc

``ROLE`` is one of ``L2W``, ``C2RCC``, ``C2RCCCANON``, ``DSFOPT``,
``C2RCCOPT``, ``DSFALIGN``, ``ACSWITCH``, ``L2W_optimized``,
``C2RCCCANON_optimized`` (anything after the tile token, with its
``_optimized`` suffix kept). Names that do not match still produce an entry
with ``role="other"`` and ``scene_key`` equal to the stem, so arbitrary
NetCDF files remain openable.

WP1 implements every ``NotImplementedError`` below. Signatures are pinned;
``app.py`` calls them exactly as written.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Union

from .config import RootSpec, ViewerConfig

PathLike = Union[str, Path]

PRODUCT_NAME_RE = re.compile(
    r"^(?P<platform>S2[ABC]|S3[AB]|L[CO]0?[89]|[A-Z][A-Z0-9]{1,5})_"
    r"(?P<sensor>MSI|OLCI|OLI|OLI_TIRS|[A-Z]{2,6})_"
    r"(?P<year>\d{4})_(?P<month>\d{2})_(?P<day>\d{2})_"
    r"(?P<hour>\d{2})_(?P<minute>\d{2})_(?P<second>\d{2})_"
    r"(?P<tile>T\d{2}[A-Z]{3})_(?P<role>[A-Za-z0-9]+(?:_optimized)?)\.nc$"
)

KNOWN_ROLES = (
    "DSFOPT", "C2RCCOPT", "L2W", "C2RCC", "C2RCCCANON",
    "C2RCCCANON_optimized", "L2W_optimized", "DSFALIGN", "ACSWITCH",
)


@dataclass
class FileEntry:
    id: str
    path: str
    name: str
    root_label: str
    run: str
    platform: Optional[str]
    sensor: Optional[str]
    datetime: Optional[str]   # ISO 8601, e.g. "2025-01-09T16:05:44"
    date: Optional[str]       # "2025-01-09"
    tile: Optional[str]       # "T17RNL"
    role: str                 # see KNOWN_ROLES, or "other"
    scene_key: str            # stem without role, or full stem
    size_bytes: int
    mtime: str                # ISO 8601 local time

    def to_dict(self) -> dict:
        return asdict(self)


def file_id(path: PathLike) -> str:
    """Stable 12-hex id for a path: sha1 of the absolute, forward-slash,
    case-folded (Windows) path."""
    p = Path(path).expanduser().resolve()
    key = p.as_posix()
    import os
    if os.name == "nt":
        key = key.lower()
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def parse_product_name(name: str) -> dict:
    """Parse a product filename into ``platform, sensor, datetime, date, tile,
    role, scene_key``. Unparseable names give ``role="other"``, the other
    fields ``None`` and ``scene_key`` = the stem (name without ``.nc``)."""
    raise NotImplementedError("WP1")


def make_entry(path: PathLike, root: Optional[RootSpec] = None) -> FileEntry:
    """Build a ``FileEntry`` for ``path`` (stat + name parse). ``run`` is the
    first path component below ``root.path`` ("" when the file sits directly in
    the root or no root is given). ``root_label`` is ``root.label`` or
    ``"ad hoc"``."""
    raise NotImplementedError("WP1")


class Catalog:
    """In-memory registry of ``FileEntry`` objects keyed by id.

    ``scan()`` walks every configured root to ``config.max_depth`` levels
    (root itself is depth 0), collecting ``*.nc`` files, skipping hidden
    directories (name starting with ``.``) and never following symlinks.
    Ad hoc entries registered with ``register_path`` survive a rescan.
    """

    def __init__(self, config: ViewerConfig) -> None:
        self.config = config
        self._entries: Dict[str, FileEntry] = {}
        self._adhoc: Dict[str, FileEntry] = {}
        self._root_counts: Dict[str, int] = {}
        self.scanned_at: Optional[str] = None

    def scan(self) -> None:
        """Rescan all roots; replaces scanned entries, keeps ad hoc ones;
        sets ``scanned_at`` (ISO) and per-root counts."""
        raise NotImplementedError("WP1")

    def entries(self) -> List[FileEntry]:
        """All entries (scanned + ad hoc), sorted by (run, tile, datetime desc, role)."""
        raise NotImplementedError("WP1")

    def get(self, entry_id: str) -> FileEntry:
        """Lookup by id; ``KeyError`` when unknown."""
        raise NotImplementedError("WP1")

    def register_path(self, path: PathLike) -> FileEntry:
        """Register an arbitrary ``.nc`` file (``FileNotFoundError`` if it does
        not exist or is not a file). Returns the existing entry when the path
        is already catalogued."""
        raise NotImplementedError("WP1")

    def roots_summary(self) -> List[dict]:
        """``[{label, path, exists, n_files}]`` in configuration order."""
        raise NotImplementedError("WP1")
