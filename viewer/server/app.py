"""FastAPI application: thin routes over catalog / dataset / stats / render.

Every route follows plan section 4 exactly. Routes parse parameters, call
the pinned module functions and serialise; no data logic lives here.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from . import colormaps as cmaps
from . import render as render_mod
from . import stats as stats_mod
from .cache import ArrayCache
from .catalog import Catalog
from .config import ViewerConfig
from .dataset import DatasetHandle, DatasetRegistry

log = logging.getLogger("viewer")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _parse_bbox(text: str):
    try:
        parts = [float(p) for p in text.split(",")]
    except ValueError:
        raise HTTPException(400, "bbox must be four comma-separated numbers")
    if len(parts) != 4:
        raise HTTPException(400, "bbox must be xmin,ymin,xmax,ymax")
    xmin, ymin, xmax, ymax = parts
    if not (xmax > xmin and ymax > ymin):
        raise HTTPException(400, "bbox must have xmax > xmin and ymax > ymin")
    return (xmin, ymin, xmax, ymax)


def _check_size(w: int, h: int) -> None:
    if w < 1 or h < 1 or w > render_mod.MAX_OUTPUT_PX or h > render_mod.MAX_OUTPUT_PX:
        raise HTTPException(400, f"w and h must be in 1..{render_mod.MAX_OUTPUT_PX}")


def create_app(config: ViewerConfig) -> FastAPI:
    app = FastAPI(
        title="NetCDF imagery viewer",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    cache = ArrayCache(config.cache_mb * 1024 * 1024)
    catalog = Catalog(config)
    registry = DatasetRegistry(catalog, cache)
    app.state.config = config
    app.state.cache = cache
    app.state.catalog = catalog
    app.state.registry = registry

    # -- helpers -------------------------------------------------------------
    def _handle(file_id: str) -> DatasetHandle:
        try:
            return registry.get(file_id)
        except KeyError:
            raise HTTPException(404, f"unknown file id {file_id!r}; refresh the catalog or use /api/open")
        except FileNotFoundError as exc:
            raise HTTPException(404, f"file is gone: {exc}")
        except Exception as exc:  # unreadable / not a NetCDF
            log.exception("cannot open %s", file_id)
            raise HTTPException(422, f"cannot open file: {exc}")

    def _band(handle: DatasetHandle, name: str):
        try:
            return handle.band(name)
        except KeyError:
            raise HTTPException(404, f"unknown band {name!r} in {Path(handle.path).name}")

    def _read(handle: DatasetHandle, name: str):
        try:
            return handle.read_band(name)
        except KeyError:
            raise HTTPException(404, f"unknown band {name!r}")
        except Exception as exc:
            log.exception("cannot read %s:%s", handle.path, name)
            raise HTTPException(422, f"cannot read band {name!r}: {exc}")

    # -- catalog -------------------------------------------------------------
    @app.get("/api/config")
    def api_config():
        return {
            "roots": [r.to_dict() for r in config.roots],
            "host": config.host,
            "port": config.port,
            "cache_mb": config.cache_mb,
            "version": __version__,
            "cache": cache.stats(),
        }

    @app.get("/api/catalog")
    def api_catalog(refresh: int = 0):
        if refresh or catalog.scanned_at is None:
            t0 = time.perf_counter()
            catalog.scan()
            log.info("catalog scan: %d files in %.2fs", len(catalog.entries()), time.perf_counter() - t0)
        return {
            "scanned_at": catalog.scanned_at,
            "roots": catalog.roots_summary(),
            "files": [e.to_dict() for e in catalog.entries()],
        }

    @app.get("/api/open")
    def api_open(path: str):
        try:
            entry = catalog.register_path(path)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return entry.to_dict()

    # -- files ---------------------------------------------------------------
    @app.get("/api/files/{file_id}/bands")
    def api_bands(file_id: str):
        handle = _handle(file_id)
        return handle.info_dict(file_id)

    @app.get("/api/files/{file_id}/meta")
    def api_meta(file_id: str):
        handle = _handle(file_id)
        try:
            return handle.metadata()
        except Exception as exc:
            log.exception("metadata failed for %s", file_id)
            raise HTTPException(422, f"cannot read metadata: {exc}")

    @app.get("/api/files/{file_id}/bands/{band}/stats")
    def api_stats(file_id: str, band: str):
        handle = _handle(file_id)
        _band(handle, band)
        try:
            return stats_mod.band_stats(handle, band)
        except Exception as exc:
            log.exception("stats failed for %s:%s", file_id, band)
            raise HTTPException(422, f"cannot compute stats: {exc}")

    @app.get("/api/files/{file_id}/value")
    def api_value(file_id: str, band: str, x: float, y: float):
        handle = _handle(file_id)
        _band(handle, band)
        try:
            return handle.value_at(band, x, y).to_dict()
        except Exception as exc:
            log.exception("value failed for %s:%s", file_id, band)
            raise HTTPException(422, f"cannot read value: {exc}")

    # -- rendering -----------------------------------------------------------
    @app.get("/api/colormaps")
    def api_colormaps():
        return {"names": cmaps.colormap_names(), "flags": cmaps.flag_palette_names()}

    @app.get("/api/render")
    def api_render(
        file: str,
        band: str,
        bbox: str,
        w: int,
        h: int,
        op: str = "band",
        file2: Optional[str] = None,
        band2: Optional[str] = None,
        vmin: Optional[float] = None,
        vmax: Optional[float] = None,
        cmap: Optional[str] = None,
        scale: str = Query("linear", pattern="^(linear|log)$"),
        bit: Optional[int] = None,
    ):
        box = _parse_bbox(bbox)
        _check_size(w, h)
        handle = _handle(file)
        binfo = _band(handle, band)
        arr = _read(handle, band)
        t0 = time.perf_counter()
        headers = {"Cache-Control": "no-store"}
        try:
            if op == "band":
                if binfo.kind in ("float", "latlon") and (vmin is None or vmax is None):
                    st = stats_mod.band_stats(handle, band)
                    if vmin is None:
                        vmin = st.get("p2") if st.get("p2") is not None else 0.0
                    if vmax is None:
                        vmax = st.get("p98") if st.get("p98") is not None else 1.0
                png = render_mod.render_band(
                    arr, handle.grid, box, w, h,
                    kind=binfo.kind, vmin=vmin, vmax=vmax, cmap=cmap, scale=scale,
                    bit=bit, flag_masks=binfo.flag_masks,
                )
                if vmin is not None:
                    headers["X-Vmin"] = repr(float(vmin))
                if vmax is not None:
                    headers["X-Vmax"] = repr(float(vmax))
            elif op == "diff":
                if not file2 or not band2:
                    raise HTTPException(400, "op=diff needs file2 and band2")
                handle2 = _handle(file2)
                _band(handle2, band2)
                if handle2.grid.space != handle.grid.space:
                    raise HTTPException(400, "diff needs both files in the same space (same CRS)")
                arr2 = _read(handle2, band2)
                png, used = render_mod.render_diff(
                    arr, handle.grid, arr2, handle2.grid, box, w, h,
                    vmax=vmax, cmap=cmap or "RdBu",
                )
                headers["X-Diff-Vmax"] = repr(float(used))
            else:
                raise HTTPException(400, f"unknown op {op!r}")
        except HTTPException:
            raise
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc))
        headers["X-Render-Ms"] = f"{(time.perf_counter() - t0) * 1000:.1f}"
        return Response(content=png, media_type="image/png", headers=headers)

    # -- frontend ------------------------------------------------------------
    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.exception_handler(Exception)
    async def _unhandled(request, exc):  # pragma: no cover - safety net
        log.exception("unhandled error on %s", request.url.path)
        return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})

    return app
