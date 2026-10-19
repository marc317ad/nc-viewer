"""Command line entry point: ``python -m viewer [options]``."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import webbrowser
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m viewer",
        description="Lightweight viewer for the pipeline's NetCDF products.",
    )
    p.add_argument("--roots-file", type=Path, default=None,
                   help="JSON with {roots:[{path,label}], port, ...}; default viewer/roots.json when present")
    p.add_argument("--root", action="append", default=[], metavar="DIR",
                   help="additional directory to scan (repeatable)")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--cache-mb", type=int, default=None, help="band cache budget in MB (default 2048)")
    p.add_argument("--max-depth", type=int, default=None, help="directory depth to scan below each root (default 4)")
    p.add_argument("--no-browser", action="store_true", help="do not open the browser on start")
    p.add_argument("--scan-only", action="store_true", help="scan the roots, print a summary and exit")
    p.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from .server.config import load_config

    config = load_config(
        args.roots_file, args.root,
        host=args.host, port=args.port, cache_mb=args.cache_mb,
        max_depth=args.max_depth,
        open_browser=False if args.no_browser else None,
    )

    if args.scan_only:
        from .server.catalog import Catalog
        cat = Catalog(config)
        cat.scan()
        for r in cat.roots_summary():
            print(f"{'ok ' if r['exists'] else 'MISSING'} {r['n_files']:5d}  {r['label']}  ({r['path']})")
        by_role: dict = {}
        for e in cat.entries():
            by_role[e.role] = by_role.get(e.role, 0) + 1
        print("roles:", ", ".join(f"{k}={v}" for k, v in sorted(by_role.items())))
        return 0

    import uvicorn
    from .server.app import create_app

    app = create_app(config)
    url = f"http://{config.host}:{config.port}/"
    print(f"NetCDF viewer on {url}  (Ctrl+C to stop)")
    for r in config.roots:
        print(f"  root {'ok     ' if r.exists() else 'MISSING'} {r.path}")
    if config.open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=config.host, port=config.port, log_level=args.log_level, access_log=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
