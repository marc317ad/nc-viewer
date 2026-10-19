# NetCDF imagery viewer

A small local web application for the pipeline's NetCDF products (`*_DSFOPT.nc`,
`*_C2RCCOPT.nc`, raw `*_L2W.nc` / `*_C2RCC.nc` / `*_C2RCCCANON.nc`): browse a
catalog, open files in up to four synchronized panes, switch bands, stretch,
decode flags, read pixel values and metadata, and view `A - B` differences.

Specification and plan: `docs/plans/2026-10-19-nc-viewer-plan.md`.

## Run

```
viewer\run_viewer.bat
```

or

```
C:\Python313\python -m pip install -r viewer\requirements.txt   # once
C:\Python313\python -m viewer
```

Then open http://127.0.0.1:8765/ (the launcher opens it for you).

Options: `--root DIR` (repeatable), `--roots-file FILE`, `--port N`,
`--no-browser`, `--scan-only`. Copy `viewer/roots.example.json` to
`viewer/roots.json` to change the default data roots permanently
(`roots.json` is gitignored).

## Tests

```
C:\Python313\python -m pytest viewer/tests -q
node --test viewer/tests/test_viewport.mjs
```

The suite uses synthetic files only. Set `VIEWER_REAL_NC=<path to a product>`
to also run the few real-data checks.

## Status

Skeleton (WP0). Sections on usage, keyboard shortcuts, troubleshooting and
the "Verified" block are filled in at integration (WP4).
