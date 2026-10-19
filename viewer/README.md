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
**RGB** (false-colour composite, below), diff select (`vs pane N`), close.
The colorbar / legend sits under the image.

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

**RGB composite.** **RGB** in a pane header (or `r`) composes three bands of
the pane's file on red, green and blue. A third header row appears with a
preset select (true colour 665/560/492, false-colour NIR 865/665/560, SWIR
2202/865/665, red edge 704/665/560; presets resolve by nominal wavelength
within one spectral family, with 833 standing in for 865 and 740 for 704),
one band select per channel for any custom combination, **shared** and a
gamma select. Each channel is stretched p2-p98 on its own by default (so a
dark-water scene still shows colour); **shared** uses one range (the union)
for all three, which keeps the bands' relative brightness; gamma > 1
brightens the dark end. Min/max typed in the stretch row apply to every
channel; `log` works too. A pixel is drawn only where all three channels are
valid. The legend shows the three channel bands with their ranges, the
status bar shows the three values under the cursor, and the metadata drawer
follows the red channel. With **Link bands** on, the other panes follow
channel by channel (`Rrs_B04/B03/B02` -> `rrs_B4/B3/B2`) or flash when a
channel has no match. A composite pane cannot be a diff partner; picking a
diff or a single band leaves composite mode.

Keyboard: `1`-`4` layout, `s` sync, `l` link bands, `k` lock stretch, `r`
RGB composite, `c` catalog, `m` metadata, `F` fit all, `?` help; in a pane:
drag to pan, wheel to zoom at the cursor, double-click zoom 2x, `+`/`-` zoom,
`0` fit, arrows pan.

The URL hash holds layout, per-pane file:band (or `file:rgb(r,g,b)` for a
composite) and the toggles, so a reload (or a shared link) restores the
panes. Shared and gamma are pane-local and not kept in the hash.

## Tests

```
C:\Python313\python -m pytest viewer/tests -q
node --test viewer/tests/test_viewport.mjs viewer/tests/test_state.mjs
```

(List the `.mjs` files explicitly: node's default test patterns do not match
`test_*.mjs`.) The Python suite (51 tests) runs on synthetic files written by
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

### Large products, memory, E: roots, `--roots-file`

2026-10-19, branch `claude/brave-mahavira-991b1b` (`pytest viewer/tests`:
49 passed, 1 skipped; node: 8 passed), desktop app browser at 1600x1000.
Port 8767 was held by another viewer instance on this machine, so the
server ran on 8766/8768/8770. Files under
`L:\acolite_compressed_2025\production_dual_wqm`:
T17RNL = `S2B_MSI_2025_01_09_16_05_44_T17RNL` (DSFOPT 149 MB, C2RCCOPT
567 MB, 5455 x 2869 at 20 m) and T17RNM = `S2B_MSI_2025_01_12_16_15_24_T17RNM`
(DSFOPT 208 MB, C2RCCOPT 805 MB, 5490 x 2717). A float32 band of these is
57 MB; `pixel_status` and the flag bands are held as float64 (114 MB).
Browser wall times in ms for the Compare layout (two panes of 457 x 855),
each pair opened cold on a fresh server; "warm" means the band is in the
cache:

| Step                                            | T17RNL DSFOPT / C2RCCOPT | T17RNM DSFOPT / C2RCCOPT |
|-------------------------------------------------|--------------------------|--------------------------|
| open (`/bands`; C2RCCOPT has 62 variables)      | 406 / 1603               | 340 / 1387               |
| first band read, cold (`Rrs_B03` / `rrs_B3`)    | 282 / 352                | 331 / 371                |
| render after pan, warm                          | 20-23                    | 10-14                    |
| band switch, cold (`Rrs_B05`: stats + render / `rrs_B5`) | 306 + 302 / 511 | 327 + 297 / 615          |
| `pixel_status`, cold (stats reads it) + render  | 255 + 20 / 81            | 245 + 13 / 76            |
| `c2rcc_flags` count view, cold                  | - / 233                  | - / 178                  |
| diff `rrs_B5 - Rrs_B05`, warm (server X-Render-Ms) | 21-22 (18.7)          | 10-21 (17.6)             |
| stats, cold / warm (`Rrs_B06`)                  | 304 / 2-4                | 327 / 3                  |
| stats, cold (`c2rcc_flags`)                     | - / 795                  | - / 739                  |
| warm render, server X-Render-Ms                 | 15.6-20.1                | 14.9-18.3 (800x1000: 29.7) |

Cold reads never came near the 3 s threshold: `read_band` measured outside
the server takes 149-216 ms per float band, 52-69 ms for `pixel_status`
and 159-195 ms for a flag band (opens 385-470 ms DSFOPT, 1409-1496 ms
C2RCCOPT), so the read path itself was left alone. Memory was the problem.
With four panes (`layout=4`, both pairs, 174 x 388 each) opened cold from
the URL hash and every pane then switched through `Rrs_B05`/`rrs_B5`,
`pixel_status` and `l2_flags`/`c2rcc_flags`/`Rrs_B07`/`conc_chl`
(16 bands read), `Get-Process` on the server, in MB:

| Server build                                   | Working set | Peak WS | Private | Band cache | WS minus cache |
|------------------------------------------------|-------------|---------|---------|------------|----------------|
| before (netCDF4 default 64 MB chunk cache)     | 2358        | 2478    | 2962    | 1405       | 953            |
| 4 MB HDF5 chunk cache per variable             | 1454        | 1673    | 2045    | 1348       | 106            |
| + no second band copy in `_to_float_array`     | 1632        | 1818    | 2223    | 1456       | 176            |

Cause: netCDF4 gives every variable a 64 MB HDF5 chunk cache, and a
whole-band read leaves the decompressed chunks parked there for as long as
the file stays open, about 60 MB per touched variable (measured outside the
server on the T17RNM C2RCCOPT: 16 band reads cost 1004 MB on top of the band
cache at the default, 84 MB at a 1 MB chunk cache, with the same read time).
Fix: `_open_dataset()` in `viewer/server/dataset.py` sets a 4 MB chunk
cache before every open (covered by
`test_open_uses_small_hdf5_chunk_cache`), and `_to_float_array()` keeps
the netCDF4 buffer instead of copying it when the dtype already matches,
which removes one full-band transient (331 MB / 69 ms on an L2W band).
No function signature changed; `--cache-mb` stays at 2048. With the fix a
cold four-pane load shows its first image after ~2.5 s and all four after
~4.9 s (the two C2RCCOPT opens hold the NetCDF lock ~1.5 s each and the
other renders queue behind them); four concurrent cold band switches take
275 / 590 / 730 / 928 ms, serialised on that lock; after the E: files below
were added as well, the server sat at 1842 MB working set (peak 2589) with
1741 MB in the cache.

"rendering..." badge: visible for the whole cold read on a band switch
(338-856 ms) and for a 4.4 s file load, with the previous image still drawn
underneath. `setFile()` in `viewer/static/pane.js` now keeps the previous
image while the new file's first render is in flight when both files share
a space; a pixel-space file replacing a projected one still clears the pane,
since the old bbox means nothing there (CANON into pane 4: badge for
724-1045 ms over an empty pane, by design).

E: roots in the browser: `E:\acolite_compressed_2025\S2C_MSI_2026_08_17_16_05_42_T17RNL_C2RCCCANON_optimized.nc`
(C2RCCCANON, 97 MB, pixel space with 2-D lat/lon,
5066 x 2274, `Rrs_561`/`Rrs_866`/`c2rcc_flags`) and
`E:\acolite_output\sentinel2\S2A_MSI_2023_03_29_16_15_37_T17RNL_L2W.nc`
(L2W, 4637 MB, uncompressed and unchunked, EPSG:32617,
10153 x 8552, 14 bands) both open and display with the
Reflectance / Flags / Geolocation groups, and the synthetic `canon` fixture
already matches the CANON shape, so no new test was needed. A full-tile L2W
band is 331 MB and its first read takes 4.4-4.6 s: the E: drive delivers
~72 MB/s (the same band takes 0.15 s once the OS has it cached), so that
one is disk-bound. Its `l2_flags` caches as float64 (662 MB).

`--roots-file` with a copy of `viewer/roots.example.json` that lists only
`E:\acolite_compressed_2025`: `/api/config` reports that single root and the
catalog holds its 7 files and nothing else.

### RGB composite

2026-10-19 (later), on `feat/nc-viewer`, before the merge with the branch above:

- `pytest viewer/tests`: 50 passed, 1 skipped.
  `node --test viewer/tests/test_viewport.mjs viewer/tests/test_state.mjs`:
  15 passed.
- Browser (desktop app, 800x600) on
  `production_dual_wqm/S2A_MSI_2025_01_04_16_05_57_T17RNK` DSFOPT +
  C2RCCOPT with Link bands on: **RGB** switches pane 1 to true colour
  `Rrs_B04/B03/B02` and pane 2 follows as `rrs_B4/B3/B2` (hash shows both
  `rgb(...)` forms); NIR preset -> `Rrs_B8A/B04/B03` and `rrs_B8A/B4/B3`;
  gamma 2 and **shared** re-render (legend shows one range for the three
  channels); a typed max of 0.08 is sent for all three channels; the status
  bar lists R/G/B values per pane; the diff select is disabled while a pane
  is a composite; reload restores both composites from the hash; RGB off
  returns to `Rrs_B03`. 509x597 composite renders 8-18 ms server time. No
  console or server errors. (On that scene the C2RCCOPT `rrs_*` bands have a
  single value over all valid pixels, p2 = p98, so its composite is flat
  grey by construction, not a viewer fault.)

## Known limits

- The view position is not stored in the URL hash (layout, files, bands and
  toggles are).
- One band is read whole on first use (float32, ~120 MB for a full 5490 x 5490
  tile, 331 MB for a full-tile L2W from E:, which takes 4-5 s from that
  drive); the cache evicts least-recently-used bands past `--cache-mb`.
  Status and flag bands are held as float64 (`pixel_status` 114 MB on the
  production tiles, a full-tile L2W `l2_flags` 662 MB), so one of them can
  displace several float bands.
- All NetCDF reads share one lock: opening a 62-variable C2RCCOPT holds it
  for ~1.5 s, and renders for the other panes wait behind it.
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
