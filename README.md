# nc-viewer

A small local web application for looking at NetCDF imagery products side
by side: open a file, a scene or a pair of products, pan and zoom them
together, switch bands, stretch, decode the flag words under the cursor,
read metadata and band statistics, render differences and false-colour
composites.

It was written for Sentinel-2 water-quality products from ACOLITE (DSF) and
SNAP C2RCC, which is where its band matching, flag decoding and catalog
grouping come from, but any NetCDF file with 2-D variables opens.

- Up to four panes with synchronised pan and zoom in world coordinates;
  files that only carry 2-D lat/lon (SNAP C2RCC output) are georeferenced
  from them, so they line up with the projected products.
- Band switching linked across products by nominal wavelength (`Rrs_B05`
  in one pane, `rrs_B5` in the other).
- Auto or manual stretch, log scale, several colormaps, and a lock that
  applies one stretch to every pane showing the same band.
- `pixel_status`, `l2_flags`, `c2rcc_flags` and any `flag_masks` variable
  decoded: bit-count view, single-bit masks, the set flags under the cursor.
- `A - B` differences with a symmetric diverging palette.
- RGB false-colour composites from any three bands, with presets.
- A metadata drawer with global attributes, variables, and band statistics
  with a histogram.
- A catalog built from file names (run, tile, scene, role) with a filter,
  and a URL hash that restores the panes on reload or from a shared link.

The server is FastAPI + numpy + netCDF4 + Pillow; the frontend is plain
HTML, CSS and JavaScript modules drawing on a canvas. No build step, no
database, no network access beyond the loopback interface.

## Requirements

- Python 3.11 or newer (developed and tested on 3.13).
- Windows, Linux or macOS. Developed on Windows 11; the commands below use
  Windows paths where the platforms differ.
- A current browser (Chrome, Edge, Firefox or Safari).
- Node.js 18 or newer, only to run the frontend unit tests.

## Installation

```
git clone https://github.com/marc317ad/nc-viewer.git
cd nc-viewer
python -m venv .venv
.venv\Scripts\activate          # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

The package runs from the clone; there is nothing to build or install
beyond the dependencies. `pytest` and `httpx` in `requirements.txt` are only
needed for the tests.

## Running

```
python -m viewer --root D:\data\products
```

The server starts on http://127.0.0.1:8765/ and opens a browser tab. Every
`--root` directory is scanned (four levels deep by default) for `*.nc`
files; the start-up line reports each root and whether it exists.

On Windows, `run_viewer.bat` does the same from a double-click. It uses
`.venv\Scripts\python.exe` next to it when present, otherwise `python` on
the PATH, or the interpreter named in a `VIEWER_PYTHON` environment
variable. Extra arguments pass through: `run_viewer.bat --root D:\other
--port 9000`.

| option | meaning |
|---|---|
| `--root DIR` | add a directory to scan (repeatable) |
| `--roots-file FILE` | settings file (below); default `viewer/roots.json` when present |
| `--port N`, `--host H` | bind address (default 127.0.0.1:8765) |
| `--cache-mb N` | in-memory band cache budget in MB (default 2048) |
| `--max-depth N` | directory depth scanned below each root (default 4) |
| `--no-browser` | do not open a browser tab |
| `--scan-only` | print the catalog summary and exit |
| `--log-level L` | `debug`, `info` (default), `warning` or `error` |

### Settings file

To keep the roots permanently, copy `viewer/roots.example.json` to
`viewer/roots.json` (ignored by git) and edit it:

```json
{
  "roots": [
    {"path": "D:/data/products", "label": "optimized products"},
    "D:/data/acolite_output"
  ],
  "host": "127.0.0.1",
  "port": 8765,
  "cache_mb": 2048,
  "max_depth": 4,
  "open_browser": true
}
```

An entry is a path or `{path, label}`; forward slashes work on Windows.
Command-line options override the file. A root that does not exist is
reported as missing at start-up and in the catalog, not treated as an
error. Without any root the catalog is empty and files are opened by
absolute path (**Open path** in the catalog drawer).

## What it opens

Any NetCDF file with at least one 2-D variable. How a file is displayed
depends on what it carries:

- **Projected grid**: 1-D `x`/`y` coordinates and a `grid_mapping`
  variable with `crs_wkt`, `spatial_ref` or an EPSG code. Panes showing
  files in the same CRS pan and zoom together in world coordinates, the
  status bar shows X/Y and lon/lat, and such files can be diffed.
- **Lat/lon only**: 2-D `lat`/`lon` variables and no grid mapping (SNAP
  C2RCC output). A sample of the lat/lon is projected into the file's grid
  mapping if it has one, the UTM zone of the scene and its neighbours, and
  plain lon/lat; when it lies on a regular lattice in one of them (within
  0.05 pixel) the grid is recovered and the file behaves like a projected
  one in that CRS. Otherwise it is displayed in pixel space with its own
  view (an "unsynced: pixel space" badge); lon/lat under the cursor still
  come from the arrays.
- **Flag words**: variables with `flag_masks` / `flag_meanings`, or a name
  ending in `_flags`. ACOLITE's `l2_flags` takes its bit names from the
  global `flag_exponent_*` attributes.
- **`pixel_status`**: a fixed palette (value, outside AOI, flagged, no
  data) and the meaning under the cursor.
- **Spectral bands**: names like `Rrs_B04`, `rrs_B4`, `Rrs_665`, `rhow_*`
  or `rhown_*`, or any variable with a `wavelength` attribute, get a match
  key `{family}@{nominal nm}` so that **Link bands** and the RGB presets can
  pair bands across products.

The catalog groups files by the naming convention
`{PLATFORM}_{SENSOR}_{YYYY}_{MM}_{DD}_{hh}_{mm}_{ss}_{TILE}_{ROLE}.nc`, for
example `S2B_MSI_2025_01_09_16_05_44_T17RNL_DSFOPT.nc`, and **Compare** on
a scene row opens its `DSFOPT` and `C2RCCOPT` products side by side. Files
with other names are listed under their own name and open normally.

Product families exercised so far: ACOLITE DSF (`*_L2W.nc` and the
fixed-window `*_DSFOPT.nc`), SNAP C2RCC (`*_C2RCC.nc`, `*_C2RCCCANON.nc`,
`*_C2RCCOPT.nc`), from 8 MB to 4.6 GB per file.

## Using it

**Catalog (left drawer).** Files are grouped run -> tile -> scene -> role.
Type in the filter box to narrow by name, run, tile, role or date. Click a
file to open it in the *target pane* (shown next to the drawer title; click
a pane to make it the target). **Compare** on a scene row opens its DSFOPT
in pane 1 and C2RCCOPT in pane 2, side by side and synced. **Open path**
opens any absolute `.nc` path outside the roots. **Refresh** rescans the
roots.

**Panes.** Header: band select (grouped Reflectance / Products / Flags /
Geolocation), colormap, stretch (auto p2-p98, or type min/max; `log`
toggle), flag bit select for flag bands (count of set bits, or one flag as
a mask), **RGB** (false-colour composite, below), diff select (`vs pane
N`), close. The colorbar or legend sits under the image.

**Topbar.** Layout 1-4, **Sync view** (panes in the same CRS share pan and
zoom, raw C2RCC next to DSFOPT included; a file in another CRS or in pixel
space gets its own view and an "unsynced" badge), **Link bands** (switching
a band in one pane switches the others to
the band with the same nominal wavelength, e.g. `Rrs_B05` <-> `rrs_B5`),
**Lock stretch** (manual min/max applies to every pane showing the same
band), **Fit**, **Refresh**, **Metadata** drawer, **?** shortcuts.

**Status bar.** Cursor X/Y in the file's projected units, row/col, lon/lat,
and for each pane the value under the cursor with its `pixel_status`
meaning and the set flags. A crosshair marks the same world position in
every synced pane. Click to pin a position.

**Metadata drawer (right).** *Metadata*: file, dims, global attributes
(long values collapsed), variables with their attributes. *Band stats*:
count/valid/min/max/mean/std/percentiles and a 64-bin histogram with the
current stretch marked; **Use p2-p98** / **Use min-max** / **Use p5-p95**
set the stretch.

**Diff.** Pick `vs pane N` in a pane's diff select: it renders `this pane -
pane N` for the two panes' current bands with a symmetric diverging palette
(red = this pane higher, blue = lower). Both files must be in the same CRS.
The auto vmax is the 98th percentile of |diff|; type a value to fix it.

**RGB composite.** **RGB** in a pane header (or `r`) composes three bands
of the pane's file on red, green and blue. A third header row appears with
a preset select (true colour 665/560/492, false-colour NIR 865/665/560,
SWIR 2202/865/665, red edge 704/665/560; presets resolve by nominal
wavelength within one spectral family, with 833 standing in for 865 and
740 for 704), one band select per channel for any custom combination,
**shared** and a gamma select. Each channel is stretched p2-p98 on its own
by default (so a dark-water scene still shows colour); **shared** uses one
range (the union) for all three, which keeps the bands' relative
brightness; gamma > 1 brightens the dark end. Min/max typed in the stretch
row apply to every channel; `log` works too. A pixel is drawn only where
all three channels are valid. The legend shows the three channel bands
with their ranges, the status bar shows the three values under the cursor,
and the metadata drawer follows the red channel. With **Link bands** on,
the other panes follow channel by channel (`Rrs_B04/B03/B02` ->
`rrs_B4/B3/B2`) or flash when a channel has no match. A composite pane
cannot be a diff partner; picking a diff or a single band leaves composite
mode.

**Keyboard.** `1`-`4` layout, `s` sync, `l` link bands, `k` lock stretch,
`r` RGB composite, `c` catalog, `m` metadata, `F` fit all, `?` help; in a
pane: drag with the left or middle (wheel) button to pan, wheel to zoom at
the cursor, double-click zoom 2x, `+`/`-` zoom, `0` fit, arrows pan.

**URL hash.** The hash holds the layout, each pane's file and band (or
`file:rgb(r,g,b)` for a composite) and the toggles, so a reload or a shared
link restores the panes. Shared and gamma are pane-local and not kept in
the hash.

## Tests

```
python -m pytest -q
node --test viewer/tests/test_viewport.mjs viewer/tests/test_state.mjs viewer/tests/test_api.mjs
```

The Python suite (57 tests) writes small synthetic products to a temporary
directory and needs no data. Two tests skip unless their extras are
present: set `VIEWER_REAL_NC=<path to a product>` to run the real-file
check, and install `matplotlib` to run the colormap regeneration check
(the viewer itself never imports it). List the `.mjs` files explicitly:
node's default test patterns do not match `test_*.mjs`.

## How it works

```
viewer/
  __main__.py        command line; starts uvicorn
  server/
    config.py        roots, host, port, cache size
    catalog.py       scans the roots and parses file names
    dataset.py       opens files, detects grid and CRS, reads bands, pixel lookups
    cache.py         LRU band cache (--cache-mb)
    stats.py         statistics and histograms
    render.py        viewport sampling, stretch, colormaps, diff, RGB
    colormaps.py     colormap tables (colormaps.json)
    app.py           FastAPI routes and the static files
  static/            index.html, app.js, pane.js, viewport.js, ... (ES modules)
  tests/             pytest suite and node tests
```

| endpoint | returns |
|---|---|
| `GET /api/config` | roots, host, port, cache budget and usage |
| `GET /api/catalog?refresh=1` | the scanned files |
| `GET /api/open?path=` | registers a file outside the roots |
| `GET /api/files/{id}/bands` | grid, CRS and band list of a file |
| `GET /api/files/{id}/meta` | global and variable attributes |
| `GET /api/files/{id}/bands/{band}/stats` | statistics and histogram |
| `GET /api/files/{id}/value?band=&x=&y=` | the value, status, flags and lon/lat at a pixel |
| `GET /api/colormaps` | colormap names |
| `GET /api/render?file=&band=&bbox=&w=&h=` | PNG of a band for a viewport (`op=diff` with `file2`, `band2`) |
| `GET /api/render_rgb?file=&r=&g=&b=&bbox=&w=&h=` | PNG composite of three bands |

A band is read whole on first use and kept in the cache (float32; status
and flag bands as float64); renders sample the cached array for the
requested viewport, so panning and zooming cost tens of milliseconds while
the first read of a band is bounded by the disk. All netCDF reads share one
lock. Each file is opened with a 4 MB HDF5 chunk cache, which keeps the
server's memory close to the band cache budget on large products.

## Limits

- The view position is not stored in the URL hash (layout, files, bands and
  toggles are).
- A band is read whole: about 120 MB for a 5500 x 5500 float32 tile, 331 MB
  for a full Sentinel-2 tile at 10 m, with status and flag bands held as
  float64 at twice that. The cache evicts least-recently-used bands past
  `--cache-mb`.
- Opening a file with many variables holds the netCDF lock for a second or
  two, and renders for the other panes wait behind it.
- Diff requires the same CRS; lat/lon-only files cannot be diffed against
  projected products.
- Single user, loopback only, no authentication.

## Troubleshooting

- *Port in use*: `python -m viewer --port 8766`.
- *A root shows "missing"*: the path does not exist or the drive is not
  mounted; the rest keeps working.
- *"cannot open file" (HTTP 422)*: the file is not a readable NetCDF or has
  no 2-D variables; Level-1 radiance files and manifests are not supported.
- *Blank pane after a band switch*: check the status bar for the server's
  error text; the previous image is kept.
- *A button does nothing and the status bar says "the server has no ... route"*:
  the running server is older than the page (the code changed while it ran).
  Stop it, start it again from this folder, then reload the page.

## Documents

- [docs/plans/2026-10-19-nc-viewer-plan.md](docs/plans/2026-10-19-nc-viewer-plan.md):
  the specification and development plan.
- [docs/verification-2026-10-19.md](docs/verification-2026-10-19.md):
  measured timings, memory figures and the browser smoke passes.

The viewer was developed inside the
[imagery_downloader](https://github.com/marc317ad/imagery_downloader)
pipeline repository and split into this repository on 2026-10-19 with its
history.

## License

MIT, see [LICENSE](LICENSE).
