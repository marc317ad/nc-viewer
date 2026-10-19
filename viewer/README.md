# NetCDF imagery viewer

A small local web application for the pipeline's NetCDF products: the
fixed-window `*_DSFOPT.nc` / `*_C2RCCOPT.nc` files and the raw branch outputs
(`*_L2W.nc`, `*_C2RCC.nc`, `*_C2RCCCANON.nc`). It scans the data roots into a
catalog, opens files in up to four panes that pan and zoom together, switches
bands (linked across products by wavelength), stretches, decodes
`pixel_status` / `l2_flags` / `c2rcc_flags`, reads pixel values with lon/lat,
shows metadata and band statistics, and renders `A - B` differences.

Specification and plan: `docs/plans/2026-10-19-nc-viewer-plan.md`.
Backend: FastAPI + numpy + netCDF4 + Pillow (Python 3.13). Frontend: plain
HTML/JS/CSS modules, no build step, no network access.

## Run

Double-click `viewer\run_viewer.bat`, or from the repo root:

```
C:\Python313\python -m pip install -r viewer\requirements.txt   # once
C:\Python313\python -m viewer
```

The browser opens http://127.0.0.1:8765/ automatically. Options:

| option | meaning |
|---|---|
| `--root DIR` | add a directory to scan (repeatable) |
| `--roots-file FILE` | JSON with `roots`, `port`, `cache_mb` ... (default `viewer/roots.json` if present) |
| `--port N`, `--host H` | bind address (default 127.0.0.1:8765) |
| `--cache-mb N` | in-memory band cache budget (default 2048) |
| `--max-depth N` | directory depth scanned below each root (default 4) |
| `--no-browser` | do not open a browser tab |
| `--scan-only` | print the catalog summary and exit |

Default roots: `L:\acolite_compressed_2025`, `L:\acolite_output\sentinel2`,
`E:\acolite_compressed_2025`, `E:\acolite_output\sentinel2`. Missing roots are
reported, not fatal. To change them permanently copy `viewer/roots.example.json`
to `viewer/roots.json` (gitignored) and edit it.

Use the conda `acolite` environment only for the pipeline; the viewer runs on
the system Python and never touches that environment.

## Using it

**Catalog (left drawer).** Files are grouped run -> tile -> scene -> role.
Type in the filter box to narrow by name, run, tile, role or date. Click a
file to open it in the *target pane* (shown next to the drawer title; click a
pane to make it the target). **Compare** on a scene row opens its DSFOPT in
pane 1 and C2RCCOPT in pane 2, side by side and synced. **Open path** opens
any absolute `.nc` path outside the roots. **Refresh** rescans the roots.

**Panes.** Header: band select (grouped Reflectance / Products / Flags /
Geolocation), colormap, stretch (auto p2-p98, or type min/max; `log` toggle),
flag bit select for flag bands (count of set bits, or one flag as a mask),
diff select (`vs pane N`), close. The colorbar / legend sits under the image.

**Topbar.** Layout 1-4, **Sync view** (panes in the same CRS share pan/zoom;
pixel-space files such as raw C2RCC get their own view and an
"unsynced: pixel space" badge), **Link bands** (switching a band in one pane
switches the others to the band with the same nominal wavelength, e.g.
`Rrs_B05` <-> `rrs_B5`), **Lock stretch** (manual min/max applies to every
pane showing the same band), **Fit**, **Refresh**, **Metadata** drawer, **?**
shortcuts.

**Status bar.** Cursor X/Y (metres, EPSG:32617), row/col, lon/lat, and for
each pane the value under the cursor with its `pixel_status` meaning and the
set flags. A crosshair marks the same world position in every synced pane.
Click to pin a position.

**Metadata drawer (right).** *Metadata*: file, dims, global attributes
(long values collapsed), variables with their attributes. *Band stats*:
count/valid/min/max/mean/std/percentiles and a 64-bin histogram with the
current stretch marked; **Use p2-p98** / **Use min-max** / **Use p5-p95** set
the stretch.

**Diff.** Pick `vs pane N` in a pane's diff select: it renders
`this pane - pane N` for the two panes' current bands with a symmetric
diverging palette (red = this pane higher, blue = lower). Both files must be
in the same CRS. The auto vmax is the 98th percentile of |diff|; type a value
to fix it.

Keyboard: `1`-`4` layout, `s` sync, `l` link bands, `k` lock stretch, `c`
catalog, `m` metadata, `F` fit all, `?` help; in a pane: drag to pan, wheel to
zoom at the cursor, double-click zoom 2x, `+`/`-` zoom, `0` fit, arrows pan.

The URL hash holds layout, per-pane file:band and the toggles, so a reload
(or a shared link) restores the panes.

## Tests

```
C:\Python313\python -m pytest viewer/tests -q
node --test viewer/tests/test_viewport.mjs
```

The Python suite (48 tests) runs on synthetic files written by
`viewer/tests/conftest.py`; it needs no data drives. Set
`VIEWER_REAL_NC=<path to a product>` to also run the one real-file check.
`viewer/tests/conftest.py` carries the same `%TEMP%` workaround as the main
suite (the default pytest temp root is unreadable on this machine).

## Verified

2026-10-19, branch `feat/nc-viewer`, Windows 11, Python 3.13, Node 22:

- `pytest viewer/tests`: 48 passed, 1 skipped (real-file check).
  `node --test viewer/tests/test_viewport.mjs`: 8 passed.
- `--scan-only` over the four default roots: 480 files in 2.2 s
  (DSFOPT 35, C2RCCOPT 35, L2W 156, C2RCC 157, C2RCCCANON 84, other 13).
- API against `L:\acolite_compressed_2025\production_dual_wqm`
  `S2B_MSI_2025_01_09_16_05_58_T17RNK` (grid 454 x 764 at 20 m):
  open DSFOPT 0.6 s, open C2RCCOPT (62 variables) 1.9 s, stats 20-40 ms,
  1600x1000 renders of a float band / `pixel_status` / `l2_flags` count /
  diff all 70-100 ms (server time), pixel value 3 ms.
- Browser smoke pass (plan section 10) in the desktop app's browser at
  1600x1000: catalog load with 480 files and no console errors; Compare opens
  DSFOPT `Rrs_B03` + C2RCCOPT `rrs_B3` synced; wheel zoom moves both panes and
  the crosshair follows the cursor in both; status bar shows X/Y, row/col,
  lon/lat and both values with status and flags; Link bands `Rrs_B05` ->
  `rrs_B5`; `pixel_status` palette + legend; `c2rcc_flags` count view and
  `Cloud_risk` bit mask; diff `rrs_B3 - Rrs_B03` with symmetric colorbar;
  Band stats histogram and Use p2-p98; raw `*_C2RCC.nc` in pane 3 shows
  "unsynced: pixel space" with its own fit; reload restores the three panes
  from the hash. No server errors logged.

Not yet exercised: the 150-800 MB T17RNL/T17RNM products (first read of a
band from those takes several seconds; later reads come from the cache),
the E: roots in the browser, and the `--roots-file` path.

## Known limits

- The view position is not stored in the URL hash (layout, files, bands and
  toggles are).
- One band is read whole on first use (float32, ~120 MB for a full 5490 x 5490
  tile); the cache evicts least-recently-used bands past `--cache-mb`.
- Diff requires the same CRS; raw C2RCC files (lat/lon only) cannot be
  diffed against projected products.
- Single user, loopback only; there is no authentication.

## Troubleshooting

- *Port in use*: `python -m viewer --port 8766`.
- *A root shows "missing"*: the drive is not mounted; the rest keeps working.
- *"cannot open file" (HTTP 422)*: the file is not a readable NetCDF or has
  no 2-D variables; raw `*_L1R.nc` and manifests are not supported.
- *Blank pane after a band switch*: check the status bar for the server's
  error text; the previous image is kept.
