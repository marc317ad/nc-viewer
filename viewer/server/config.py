"""Viewer configuration: data roots, host/port, cache size.

Resolution order (later wins): built-in defaults (no roots, 127.0.0.1:8765,
a 2 GB band cache) -> settings file (``viewer/roots.json`` when present, or
``--roots-file``) -> command-line ``--root`` entries (appended) and
``--port`` / ``--host`` / ``--cache-mb``.

Without a settings file or ``--root`` the catalog is empty; files can still
be opened by absolute path from the browser.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

VIEWER_DIR = Path(__file__).resolve().parent.parent
DEFAULT_ROOTS_FILE = VIEWER_DIR / "roots.json"


@dataclass
class RootSpec:
    """One directory to scan for ``*.nc`` products."""

    path: str
    label: str = ""

    def __post_init__(self) -> None:
        self.path = str(Path(self.path).expanduser())
        if not self.label:
            self.label = self.path

    def exists(self) -> bool:
        return Path(self.path).is_dir()

    def to_dict(self) -> dict:
        return {"path": self.path, "label": self.label, "exists": self.exists()}


#: No built-in roots: configure them in ``viewer/roots.json`` or with ``--root``.
DEFAULT_ROOTS: List[RootSpec] = []


@dataclass
class ViewerConfig:
    roots: List[RootSpec] = field(default_factory=lambda: list(DEFAULT_ROOTS))
    host: str = "127.0.0.1"
    port: int = 8765
    cache_mb: int = 2048
    max_depth: int = 4
    open_browser: bool = True

    def to_dict(self) -> dict:
        d = asdict(self)
        d["roots"] = [r.to_dict() for r in self.roots]
        return d


def _roots_from_json(data: dict) -> List[RootSpec]:
    roots: List[RootSpec] = []
    for item in data.get("roots", []):
        if isinstance(item, str):
            roots.append(RootSpec(item))
        elif isinstance(item, dict) and item.get("path"):
            roots.append(RootSpec(item["path"], item.get("label", "")))
    return roots


def load_config(
    roots_file: Optional[Path] = None,
    extra_roots: Iterable[str] = (),
    *,
    host: Optional[str] = None,
    port: Optional[int] = None,
    cache_mb: Optional[int] = None,
    max_depth: Optional[int] = None,
    open_browser: Optional[bool] = None,
) -> ViewerConfig:
    """Build the effective configuration (see module docstring for precedence)."""
    cfg = ViewerConfig()
    path = Path(roots_file) if roots_file else (DEFAULT_ROOTS_FILE if DEFAULT_ROOTS_FILE.exists() else None)
    if path is not None:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        file_roots = _roots_from_json(data)
        if file_roots:
            cfg.roots = file_roots
        for key in ("host", "port", "cache_mb", "max_depth", "open_browser"):
            if key in data:
                setattr(cfg, key, data[key])
    for r in extra_roots:
        cfg.roots.append(RootSpec(r))
    if host is not None:
        cfg.host = host
    if port is not None:
        cfg.port = int(port)
    if cache_mb is not None:
        cfg.cache_mb = int(cache_mb)
    if max_depth is not None:
        cfg.max_depth = int(max_depth)
    if open_browser is not None:
        cfg.open_browser = bool(open_browser)
    return cfg
