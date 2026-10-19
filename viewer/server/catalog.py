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
with ``role="OTHER"`` and ``scene_key`` equal to the stem, so arbitrary
NetCDF files remain openable.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime as _dt
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Union

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

OTHER_ROLE = "OTHER"
ADHOC_LABEL = "ad hoc"
SKIP_DIR_NAMES = ("__pycache__",)


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
    role: str                 # see KNOWN_ROLES, or "OTHER"
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
    if os.name == "nt":
        key = key.lower()
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _stem(name: str) -> str:
    return name[:-3] if name.lower().endswith(".nc") else name


def parse_product_name(name: str) -> dict:
    """Parse a product filename into ``platform, sensor, datetime, date, tile,
    role, scene_key``. Unparseable names give ``role="OTHER"``, the other
    fields ``None`` and ``scene_key`` = the stem (name without ``.nc``)."""
    name = Path(name).name
    m = PRODUCT_NAME_RE.match(name)
    if m is None:
        return {
            "platform": None, "sensor": None, "datetime": None, "date": None,
            "tile": None, "role": OTHER_ROLE, "scene_key": _stem(name),
        }
    g = m.groupdict()
    date = f"{g['year']}-{g['month']}-{g['day']}"
    stamp = f"{date}T{g['hour']}:{g['minute']}:{g['second']}"
    stem = _stem(name)
    scene_key = stem[: -(len(g["role"]) + 1)]
    return {
        "platform": g["platform"], "sensor": g["sensor"], "datetime": stamp,
        "date": date, "tile": g["tile"], "role": g["role"], "scene_key": scene_key,
    }


def _iso_local(timestamp: float) -> str:
    return _dt.fromtimestamp(timestamp).isoformat(timespec="seconds")


def _run_below(path: Path, root: Optional[RootSpec]) -> str:
    if root is None:
        return ""
    try:
        rel = path.relative_to(Path(root.path).expanduser().resolve())
    except ValueError:
        return ""
    return rel.parts[0] if len(rel.parts) > 1 else ""


def make_entry(path: PathLike, root: Optional[RootSpec] = None) -> FileEntry:
    """Build a ``FileEntry`` for ``path`` (stat + name parse). ``run`` is the
    first path component below ``root.path`` ("" when the file sits directly in
    the root or no root is given). ``root_label`` is ``root.label`` or
    ``"ad hoc"``."""
    p = Path(path).expanduser().resolve()
    st = p.stat()
    parsed = parse_product_name(p.name)
    return FileEntry(
        id=file_id(p),
        path=str(p),
        name=p.name,
        root_label=root.label if root is not None else ADHOC_LABEL,
        run=_run_below(p, root),
        platform=parsed["platform"],
        sensor=parsed["sensor"],
        datetime=parsed["datetime"],
        date=parsed["date"],
        tile=parsed["tile"],
        role=parsed["role"],
        scene_key=parsed["scene_key"],
        size_bytes=int(st.st_size),
        mtime=_iso_local(st.st_mtime),
    )


def _skip_dir(name: str) -> bool:
    return name.startswith(".") or name in SKIP_DIR_NAMES


def _walk_nc(directory: Path, depth: int, max_depth: int) -> Iterator[Path]:
    """Yield ``*.nc`` files under ``directory`` (which sits at ``depth``),
    descending only while files would land at or below ``max_depth``."""
    try:
        with os.scandir(directory) as it:
            entries = list(it)
    except OSError:
        return
    subdirs: List[Path] = []
    for entry in entries:
        try:
            if entry.is_file(follow_symlinks=False):
                if entry.name.lower().endswith(".nc"):
                    yield Path(entry.path)
            elif entry.is_dir(follow_symlinks=False) and not _skip_dir(entry.name):
                subdirs.append(Path(entry.path))
        except OSError:
            continue
    if depth + 1 < max_depth:
        for sub in sorted(subdirs):
            yield from _walk_nc(sub, depth + 1, max_depth)


def _sorted_entries(entries: List[FileEntry]) -> List[FileEntry]:
    """(run, tile, datetime desc, role) via successive stable sorts."""
    out = sorted(entries, key=lambda e: (e.role, e.name))
    out.sort(key=lambda e: e.datetime or "", reverse=True)
    out.sort(key=lambda e: (e.run, e.tile or ""))
    return out


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
        found: Dict[str, FileEntry] = {}
        counts: Dict[str, int] = {}
        max_depth = max(1, int(self.config.max_depth))
        for root in self.config.roots:
            n = 0
            root_dir = Path(root.path).expanduser()
            if root_dir.is_dir():
                for p in _walk_nc(root_dir.resolve(), 0, max_depth):
                    try:
                        entry = make_entry(p, root)
                    except OSError:
                        continue
                    if entry.id not in found:
                        found[entry.id] = entry
                        n += 1
            counts[root.path] = n
        self._entries = found
        self._root_counts = counts
        for key in list(self._adhoc):
            if key in found:
                del self._adhoc[key]
        self.scanned_at = _dt.now().isoformat(timespec="seconds")

    def entries(self) -> List[FileEntry]:
        """All entries (scanned + ad hoc), sorted by (run, tile, datetime desc, role)."""
        merged = dict(self._adhoc)
        merged.update(self._entries)
        return _sorted_entries(list(merged.values()))

    def get(self, entry_id: str) -> FileEntry:
        """Lookup by id; ``KeyError`` when unknown."""
        entry = self._entries.get(entry_id) or self._adhoc.get(entry_id)
        if entry is None:
            raise KeyError(entry_id)
        return entry

    def register_path(self, path: PathLike) -> FileEntry:
        """Register an arbitrary ``.nc`` file (``FileNotFoundError`` if it does
        not exist or is not a file). Returns the existing entry when the path
        is already catalogued."""
        p = Path(path).expanduser()
        if not p.is_file():
            raise FileNotFoundError(f"not a file: {p}")
        if p.suffix.lower() != ".nc":
            raise ValueError(f"not a NetCDF (.nc) file: {p.name}")
        key = file_id(p)
        existing = self._entries.get(key) or self._adhoc.get(key)
        if existing is not None:
            return existing
        entry = make_entry(p, None)
        entry.run = p.resolve().parent.name
        self._adhoc[key] = entry
        return entry

    def roots_summary(self) -> List[dict]:
        """``[{label, path, exists, n_files}]`` in configuration order."""
        return [
            {
                "label": r.label,
                "path": r.path,
                "exists": r.exists(),
                "n_files": int(self._root_counts.get(r.path, 0)),
            }
            for r in self.config.roots
        ]
