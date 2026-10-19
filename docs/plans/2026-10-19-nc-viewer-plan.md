# NetCDF imagery viewer: specification and development plan

**Date:** 2026-10-19
**Branch:** `feat/nc-viewer` (from `wqm-aoi-adoption` at `51f2b6e`)
**Status:** specification frozen for the overnight build; implementation in progress.
**Deliverable:** a usable prototype at `viewer/` that opens this repo's NetCDF
products, shows them side by side with synchronized pan/zoom, switches bands,
decodes flags and shows metadata. Lightweight by design: no build step, no
database, no auth, a small pytest suite, no acceptance-test programme.

---

## 1. What the viewer is for

The pipeline's end products are per-scene NetCDF files on a fixed 20 m
EPSG:32617 window per tile (plan `2026-09-04-branch-output-coordination-plan.md`,
G0-G6 closed 2026-09-07). The people using them need to answer, quickly and
visually:

- *What does this scene look like in band X?* (open one file, switch bands)
- *How do DSF and C2RCC compare on the same scene?* (two files, same tile and
  date, pixel-aligned, same band family, same stretch; and their difference)
- *How does one tile change between dates?* (two files, same tile)
- *Why is this pixel missing?* (`pixel_status`, `l2_flags`, `c2rcc_flags`
  decoded under the cursor)
- *What are the processing parameters of this file?* (global attributes,
  variable attributes, encoding)

SNAP and QGIS can do all of this, slowly and with many clicks. The viewer trades
their generality for a two-second path from "file list" to "two aligned panes".

## 2. Products the viewer must open

Measured 2026-10-19 on `L:\acolite_compressed_2025\production_dual_wqm` and
`L:\acolite_output\sentinel2\production_dual_wqm`.

| product | role suffix | dims | coords | CRS | 2-D vars | notes |
|---|---|---|---|---|---|---|
| optimized DSF | `_DSFOPT.nc` | y,x up to 5455x2869 | `x`,`y` float64 centres, `transverse_mercator` scalar | `crs_wkt` (UTM 17N, EPSG:32617) | 13: `pixel_status`, `Rrs_B01..B12` (`wavelength`, `parameter=Rrs_443`), `l2_flags` | ~505 global attrs (ACOLITE); 8-220 MB |
| optimized C2RCC | `_C2RCCOPT.nc` | same window as DSFOPT of the same tile | `x`,`y`, `transverse_mercator` | same | 62: `pixel_status`, `rrs_B1..B8A`, `rhown_B1..B6`, `iop_*`, `conc_chl`, `conc_tsm`, `kd*`, `unc_*`, `c2rcc_flags` (uint32, `flag_masks`/`flag_meanings`), ECMWF/CAMS aux, `aux_latitude/longitude` | 33 global attrs; 24-844 MB |
| raw ACOLITE L2W | `_L2W.nc` (inside a per-scene directory) | tile-cropped | `x`,`y`, `transverse_mercator`, plus 2-D `lat`,`lon` | `crs_wkt` | `Rrs_443..Rrs_2202` (wavelength names), `l2_flags` int32 | |
| raw SNAP C2RCC | `_C2RCC.nc` | subset | 2-D `lat`,`lon` only | none | 61 (as above, no `pixel_status`) | pixel-space display |
| canonical C2RCC | `_C2RCCCANON.nc` | subset | none (lat/lon are plain vars) | none | `Rrs_560`, `Rrs_865`, `c2rcc_flags` | pixel-space display |
| optimized canonical | `_C2RCCCANON_optimized.nc` | | | | | E:\ phase8 only |

Common facts the design relies on:

- Every science variable is 2-D `(y, x)`, float32 with NaN fill (after
  `mask_and_scale`), or an integer flag word.
- `pixel_status` is uint8 on disk (`_FillValue` 16), decoded to float32 by
  xarray; `flag_masks = [1 2 4 8]`, `flag_meanings = value outside_aoi flagged no_data`.
- `x`/`y` are pixel **centres**; `dx = +20`, `dy = -20` (north-up).
- `transverse_mercator.crs_wkt` parses with pyproj to EPSG:32617.
- The same tile has an identical window in DSFOPT and C2RCCOPT, every date.
  Different tiles are on the same lattice at different offsets.
- Filename stem: `{S2A|S2B|S2C}_MSI_{YYYY}_{MM}_{DD}_{hh}_{mm}_{ss}_{Txxxxx}_{ROLE}.nc`.
  Roles seen: `L2W`, `C2RCC`, `C2RCCCANON`, `DSFOPT`, `C2RCCOPT`,
  `C2RCCCANON_optimized`; the orchestrator also defines `DSFALIGN`,
  `ACSWITCH`, `L2W_optimized` (`processors/ac_orchestrator.py:245`).

## 3. Design

### 3.1 Shape

A local web application:

- **Backend:** Python 3.13 (`C:\Python313\python`, the repo's Windows test
  interpreter, which already has xarray 2026.7, netCDF4 1.7.4, numpy 2.5,
  pyproj 3.8, Pillow 12). Added: `fastapi`, `uvicorn`, `httpx` (tests).
  Serves JSON and PNG on `http://127.0.0.1:8765`.
- **Frontend:** one HTML page, vanilla JS modules, CSS, `<canvas>` panes. No
  framework, no bundler, no CDN (works offline).
- **No persistence** beyond an in-memory LRU of decoded bands. Catalog is
  rescanned on demand.

Why web and not Qt: the orchestrating session can exercise it end to end in
the in-app browser tonight; nothing to install beyond two pip packages; it can
be opened from another machine on the LAN later by changing `host`.

### 3.2 Viewport model

Every pane has a **space**: the file's CRS string for projected grids
(`"EPSG:32617"`), or `"pixel"` for files without `x`/`y`. Panes in the same
space share one viewport `{cx, cy, mpp}` (centre in world units, world units
per screen pixel) when *Sync* is on. World coordinates are the file's native
units (metres for projected, pixels for pixel-space with `y` pointing up,
i.e. `Y = -row`). The frontend never resamples: it asks the server for a
render of exactly the world bbox the canvas shows, at canvas size, and draws
the returned PNG 1:1. During a drag or wheel it transforms the last PNG on the
canvas immediately and fetches a fresh render after a 120 ms debounce.

### 3.3 Rendering

Server-side, per request: nearest-neighbour sample of the cached float32 band
on the requested output grid (numpy fancy indexing; out-of-bounds and NaN are
transparent), stretch to 0..255 (`linear` or `log`), 256-entry RGBA LUT, PNG
via Pillow. A 1200x800 render of a 15 Mpx band takes well under 100 ms once
the band is cached. First touch of a band pays one zlib decode (0.5-3 s for
the biggest C2RCCOPT variables); the UI shows a spinner.

Difference view: `op=diff` samples two files at the same world coordinates
and renders `A - B` with a diverging LUT symmetric about zero.

### 3.4 Band matching across files

For *Link bands* (switch the band in pane A and pane B follows) the server
emits a `match_key` per band: spectral bands get `"{family}@{nominal_nm}"`
where family folds `Rrs`/`rrs` to `Rrs`, `rhown`/`rhow` to `rhow`, and the
nominal wavelength is the nearest S2 centre from the `wavelength` attribute or
the band id (`B1/B01` 443, `B2` 492, `B3` 560, `B4` 665, `B5` 704, `B6` 740,
`B7` 783, `B8` 833, `B8A` 865, `B9` 945, `B10` 1375, `B11` 1614, `B12` 2202).
Non-spectral bands use their own name. The frontend matches on the string.

### 3.5 Flags

- `kind = "status"`: `pixel_status`. Fixed palette: value green, outside_aoi
  dark grey, flagged orange, no_data magenta.
- `kind = "flags"`: any variable with `flag_masks` or `flag_meanings`, or a
  name ending in `_flags`. Rendered as "number of set bits" by default
  (categorical LUT) or one bit as a binary mask via `bit=N`. Under the cursor
  the set bit names are listed.
- `kind = "float"` everything else numeric; `kind = "latlon"` for
  `lat/lon/aux_latitude/aux_longitude` (listed under *aux*).

### 3.6 Out of scope for v1

Tiles/pyramids (viewport renders suffice at these sizes), RGB composites,
drawing/annotation, exporting PNGs (the browser can save the canvas),
time-series plots at a pixel, mosaicking across tiles (they align already;
open two panes), authentication, remote data.

## 4. API contract (frozen)

All endpoints are `GET`, JSON unless stated. File identity is the catalog
`id` (12 hex chars, sha1 of the absolute path). Errors: `404` unknown id/band,
`400` bad parameters, `422` unreadable file, always JSON `{"detail": str}`.

| endpoint | returns |
|---|---|
| `/api/config` | `{roots:[{label,path,exists}], host, port, version}` |
| `/api/catalog?refresh=0 or 1` | `{scanned_at, roots:[{label,path,exists,n_files}], files:[FileEntry]}` |
| `/api/open?path=<abs path>` | `FileEntry` for an arbitrary `.nc` (registered ad hoc; `404` if missing) |
| `/api/files/{id}/bands` | `FileInfo` (below) |
| `/api/files/{id}/meta` | `{file:{path,name,size_bytes,mtime}, dims:{}, global_attrs:{k:str}, coords:[VarMeta], variables:[VarMeta]}` |
| `/api/files/{id}/bands/{band}/stats` | `BandStats` |
| `/api/files/{id}/value?band=&x=&y=` | `PixelValue` |
| `/api/render?file=&band=&bbox=xmin,ymin,xmax,ymax&w=&h=[&vmin=&vmax=&cmap=viridis&scale=linear or log&bit=N]` | `image/png` RGBA `w`x`h` |
| `/api/render?op=diff&file=&band=&file2=&band2=&bbox=&w=&h=[&vmax=&cmap=RdBu]` | `image/png`, `A - B`, symmetric range `[-vmax, vmax]`; response header `X-Diff-Vmax` carries the value used |
| `/api/colormaps` | `{names:[...], flags:[...]}` |
| `/` and `/static/*` | the frontend |

```
FileEntry = {id, path, name, root_label, run, platform, sensor,
             datetime (ISO or null), date (YYYY-MM-DD or null), tile,
             role, scene_key, size_bytes, mtime (ISO)}
   run       = first path component below the root (e.g. "production_dual_wqm")
   scene_key = stem without the role (e.g. "S2B_MSI_2025_01_09_16_05_44_T17RNL"),
               or the full stem when the name does not parse
   role      = "L2W" | "C2RCC" | "C2RCCCANON" | "DSFOPT" | "C2RCCOPT" |
               "C2RCCCANON_optimized" | ... | "other"

FileInfo  = {id, path, name, shape:[ny,nx], grid:Grid, bands:[Band],
             default_band, status_band (name or null), flag_bands:[names]}
Grid      = {kind:"projected"|"pixel", crs:"EPSG:32617"|wkt|null, space:str,
             x0, dx, y0, dy, units:"m"|"px", bounds:[xmin,ymin,xmax,ymax],
             has_latlon:bool}
   x0,y0 are the OUTER EDGE of pixel (row 0, col 0); dx>0, dy<0.
   col = floor((X - x0)/dx), row = floor((Y - y0)/dy).
   pixel kind: x0=0,dx=1,y0=0,dy=-1  (so Y = -row, "up" is up).
   space = crs for projected, "pixel" otherwise.
Band      = {name, long_name, units, wavelength (float|null), parameter,
             dtype, kind:"float"|"flags"|"status"|"latlon", group,
             match_key, flag_meanings:[str]|null, flag_masks:[int]|null}
BandStats = {band, kind, count_total, count_valid, sampled:bool,
             min, max, mean, std, p1, p2, p5, p50, p95, p98, p99,
             hist:{edges:[65 floats], counts:[64 ints]},
             log_hist:{edges, counts}|null,          # only when min>0
             flag_counts:{name:int}|null}             # flags/status kinds
PixelValue= {file, band, x, y, row, col, inside:bool, value:float|null,
             status:str|null, flags:[str]|null, lon:float|null, lat:float|null}
VarMeta   = {name, dtype, dims:[...], shape:[...], attrs:{k:str},
             encoding:{dtype,_FillValue,zlib,chunksizes}}
```

Attribute values are stringified; strings longer than 4000 characters are
truncated with ` ...[truncated]`.

## 5. Package layout

```
viewer/
  README.md                 how to run, config, keyboard, troubleshooting
  requirements.txt          fastapi, uvicorn, httpx, numpy, xarray, netCDF4, pyproj, pillow
  roots.example.json        copy to viewer/roots.json (gitignored) to set data roots
  run_viewer.bat            double-click launcher (Windows)
  __init__.py               __version__
  __main__.py               CLI: python -m viewer [--roots-file F] [--root P ...] [--port N] [--no-browser]
  server/
    __init__.py
    config.py               ViewerConfig: roots, host, port, cache_mb, max_depth; load/merge
    catalog.py              scan roots, parse filenames, FileEntry registry (id <-> path)
    dataset.py              DatasetHandle: open/cached xarray dataset, Grid, bands, read_band,
                            value_at, latlon_at, metadata; global netCDF read lock
    stats.py                band statistics + histograms (cached)
    render.py               sample_to_grid, stretch, apply_lut, encode_png, render_band, render_diff
    colormaps.py            LUT registry (loads colormaps.json), flag palettes
    colormaps.json          256x4 uint8 tables: gray, viridis, magma, inferno, plasma, turbo, RdBu, Spectral, coolwarm
    cache.py                byte-bounded LRU for arrays and stats
    app.py                  FastAPI app factory, routes (thin: parse -> call -> serialize)
  static/
    index.html
    styles.css
    js/api.js               fetch wrappers for section 4
    js/viewport.js          pure math: world<->screen, fit, zoom-about-point (node-testable)
    js/pane.js              one pane: canvas, controls, render fetch/debounce, cursor
    js/catalog.js           sidebar tree, filters, compare buttons
    js/metadata.js          right drawer: attrs table with filter, band stats + histogram canvas
    js/state.js             app state, sync groups, link-bands, lock-stretch
    js/main.js              boot, layout switching, keyboard
  tests/
    conftest.py             make_synthetic_product(): tiny DSFOPT-like / C2RCCOPT-like / raw-C2RCC-like files
    test_catalog.py  test_dataset.py  test_stats.py  test_render.py  test_api.py
    test_viewport.mjs        node --test for viewport.js
```

Python interfaces are pinned in the skeleton files (docstrings + signatures);
agents fill bodies and may add private helpers, not change signatures.

## 6. Frontend specification

Layout (desktop, 1400 px wide and up; must still work at 1100):

```
+--------------------------------------------------------------------------+
| topbar: [Layout 1|2|3|4] [Sync view] [Link bands] [Lock stretch] [Refresh]|
+-----------+------------------------------------------------+-------------+
| catalog   |  pane 1                |  pane 2               | drawer      |
| filter [] |  header: date tile role [band v][cmap v][x]    | tabs:       |
| v run     |  stretch: [min][max][auto][log] [diff: - v]    |  Metadata   |
|  v tile   |  canvas                                        |  Band stats |
|   v date  |                                                |             |
|    file   |  footer: cursor value, status                  |             |
|    file   +------------------------------------------------+             |
|   [Compare]                                                |             |
+-----------+------------------------------------------------+-------------+
| statusbar: X= Y= (m)  lon= lat=   pane1: Rrs_B03=0.0123 value  pane2: ... |
+--------------------------------------------------------------------------+
```

Behaviour:

- Catalog groups `files` as run -> tile -> scene (date time) -> files (role),
  sorted newest first within a tile. A filter box narrows by substring on
  name, run, tile, role. Click a file: opens in the active pane (highlighted
  border); shift-click opens in the next pane. Each scene row with two or more
  files has a **Compare** button: opens the DSFOPT and C2RCCOPT (or the first
  two files) in panes 1 and 2, layout 2, sync on, link bands on, picks the
  `Rrs@560` band in both when available.
- A pane without a file shows "click a file in the catalog".
- Band select is grouped by `group`; the label shows `name` and, when
  present, `wavelength` nm and units. Changing the band keeps the viewport.
  With *Link bands* on, other panes in the same sync group switch to the band
  with the same `match_key` when they have one (otherwise unchanged).
- Stretch defaults to `p2..p98` from stats. Inputs accept manual min/max;
  *Auto* resets to percentiles; *Log* available when `min > 0`. With *Lock
  stretch* on, panes showing bands with the same `match_key` share min/max.
- Colormaps from `/api/colormaps`; `status` and `flags` kinds switch to their
  palettes automatically and hide the stretch inputs; a flags band shows a
  `bit` select (all / each flag meaning).
- Pan: drag. Zoom: wheel about the cursor, `+`/`-` keys, double-click to fit
  the file's bounds. `F` fits all panes. Panes in the same space and *Sync*
  on share the viewport; a pane in another space keeps its own.
- Cursor: moving over any pane draws a crosshair at the same world position
  on every synced pane and fills the statusbar with each pane's `PixelValue`
  (debounced 60 ms, one `/value` request per pane, stale responses dropped).
  Click pins the point (statusbar stays until the next click or Esc).
- Diff: a pane's *diff* select lists the other panes in the same space;
  choosing one turns this pane into `this - other` (render `op=diff`,
  `vmax` defaults to the server's estimate, cmap RdBu); the header shows
  `A - B` with both file names.
- Drawer *Metadata*: the active pane's `meta`: file block, dims, a filterable
  key/value table of global attrs, then variables (expandable: attrs and
  encoding). *Band stats*: the active pane's band stats with a 64-bin
  histogram on a canvas and the current min/max drawn as lines.
- State in the URL hash: `#p1=<id>:<band>&p2=<id>:<band>&layout=2` so a
  reload restores the panes (viewport need not be restored).
- Errors (server down, unreadable file) show in a dismissible banner, never a
  blank page.

Visual: dark neutral theme (canvas area near-black so NaN transparency reads
as "no data"), system font, no icon fonts. Keep CSS under 400 lines.

## 7. Performance budget

- Catalog scan of four roots (a few hundred files): < 2 s. No file is opened
  during the scan; only names and sizes.
- Open a band (first time): < 3 s for the largest C2RCCOPT variable; cached
  afterwards. LRU bounded by `cache_mb` (default 2048 MB).
- Viewport render: < 150 ms server time at 1600x1000 output from cache.
- Value lookup: < 20 ms.
- HDF5 is not thread-safe: all netCDF reads go through one `threading.Lock`
  in `dataset.py`. Pure numpy work (render, stats) runs outside the lock.

## 8. Testing policy

Small, fast, offline. The synthetic fixtures in `tests/conftest.py` are the
only data the suite needs; a few tests additionally run against a real
product when `VIEWER_REAL_NC` points at one (skipped otherwise). Target: about
30 tests, under 20 s. Verification of the UI is a manual smoke pass in the
browser (section 10), recorded in the README's "Verified" block.

Run:

```
C:\Python313\python -m pytest viewer/tests -q
node --test viewer/tests/test_viewport.mjs
```

## 9. Work packages

| WP | owner | files | done when |
|---|---|---|---|
| WP0 skeleton | orchestrator | everything in section 5 as stubs, `app.py` and `__main__.py` complete, `conftest.py` complete, requirements, launcher, this plan | `python -m viewer --help` works; tests collect |
| WP1 data layer | agent "data" | `server/config.py`, `catalog.py`, `dataset.py`, `stats.py`, `cache.py`; `tests/test_catalog.py`, `test_dataset.py`, `test_stats.py` | tests green on Windows; `DatasetHandle` opens all five product types in section 2 |
| WP2 rendering | agent "render" | `server/render.py`, `colormaps.py`, `colormaps.json`; `tests/test_render.py` | tests green; `render_band` on a 5455x2869 array at 1600x1000 in < 150 ms |
| WP3 frontend | agent "frontend" | everything under `static/`; `tests/test_viewport.mjs` | implements section 6 against section 4; `node --test` green; loads without console errors against the stub server |
| WP4 integration | orchestrator | `tests/test_api.py`, wiring fixes, README "Verified" block | browser smoke pass (section 10) on real products |

WP1, WP2 and WP3 run in parallel and touch disjoint files. `app.py` is written
in WP0 against the pinned signatures and is not edited by WP1-WP3; if a
signature must change, the agent reports it instead of changing it.

## 10. Smoke pass (manual, in the browser)

1. Start `run_viewer.bat`; the page opens; catalog lists `production_dual_wqm`
   with 10 scenes.
2. Compare on `S2B 2025-01-09 T17RNL`: two panes, DSFOPT `Rrs_B03` and
   C2RCCOPT `rrs_B3`, aligned (same coastline position when toggling layout
   1 <-> 2), synced pan/zoom.
3. Switch pane 1 to `Rrs_B05` (704 nm): pane 2 follows to `rrs_B5`. Switch
   to `Rrs_B11` (1614 nm): pane 2 has no match and stays.
4. Hover a lagoon pixel: statusbar shows both values, `status=value`,
   lon/lat around -80.6, 28.1. Hover land: `status=outside_aoi`, values null.
5. Pane 2 band `pixel_status`: four-colour map. Band `c2rcc_flags`: counts
   palette; bit select `Cloud_risk` shows a binary mask.
6. Pane 2 diff with pane 1 on `Rrs@560`: diverging map, header `A - B`.
7. Metadata tab: filter `acolite_version` finds the ACOLITE build string;
   variables list shows 13 entries with encoding.
8. Open path: paste a raw `_C2RCC.nc`; pixel-space display, lat/lon readout
   from the 2-D arrays, no sync with projected panes.
9. Reload the page: panes restore from the hash.

## 11. Risks and decisions

- **Big first read.** C2RCCOPT variables are 512x512 zlib chunks; a full-band
  read is one `ds[var].values`. Measured 0.6 s (DSFOPT T17RNK) to ~3 s
  (C2RCCOPT T17RNM, 844 MB file). Accepted for v1; the spinner covers it. If
  it hurts, read only the chunks that intersect the viewport (xarray lazy
  indexing) as a v1.1 change inside `read_band`.
- **Memory.** Four panes of the largest band: 4 x 62 MB plus stats, well
  under the 2 GB LRU. The LRU evicts by bytes.
- **Mixed spaces.** Raw C2RCC has no CRS; it is deliberately not placed on
  the projected lattice (guessing an affine from 2-D lat/lon is a v2 item).
- **`*config*.json` is gitignored** repo-wide; the viewer's settings file is
  therefore named `roots.json` with `roots.example.json` committed, and
  `viewer/roots.json` is added to `.gitignore`.
- **`lib/` is gitignored**; no directory in `viewer/` is named `lib`.
- **Thread safety** of netCDF4/HDF5: one global read lock (section 7).
