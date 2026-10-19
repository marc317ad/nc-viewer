// app.js - bootstrap and coordination: loads config/colormaps/catalog, builds
// the topbar, pane grid, drawers and status bar, and implements the cross-pane
// semantics (shared viewport per space when Sync is on, Link bands by
// match_key, Lock stretch, diff pairing, crosshair + pixel values on hover,
// URL-hash persistence, keyboard shortcuts, error reporting).

import { el, clear, fmtNum, fmtCoord, debounce, inTextField } from "./util.js";
import { createState, visiblePanes, paneSpace, bandByName, bandByMatchKey, mapRgbByMatchKey, serializeHash, parseHash, applyHashFlags, MAX_PANES } from "./state.js";
import { rowColAt } from "./viewport.js";
import { getConfig, getColormaps, getCatalog, getBands, getValue, openPath } from "./api.js";
import { Pane, fileLabel } from "./pane.js";
import { CatalogView } from "./catalog.js";
import { MetaDrawer } from "./meta.js";
import { configurePalettes } from "./colorbar.js";

const FALLBACK_CMAPS = ["gray", "viridis", "magma", "inferno", "plasma", "turbo", "RdBu", "RdBu_r", "Spectral", "coolwarm"];
const $ = (id) => document.getElementById(id);

const state = createState();
const entries = new Map();          // fileId -> FileEntry
let panes = [];                     // Pane instances, index = id-1
let colormaps = FALLBACK_CMAPS;
let catalogView, metaDrawer;
let pinned = null;                  // {x, y, space} or null
let valueSeq = 0;

// -- status bar / errors ----------------------------------------------------------
const status = {
  cursor: (t) => { $("st-cursor").textContent = t || ""; },
  values: (nodes) => { const e = clear($("st-values")); if (nodes) e.append(...nodes); },
  msg: (t, isError = false) => { const e = $("st-msg"); e.textContent = t || ""; e.classList.toggle("error", !!isError); },
};
function showError(msg, { banner = false } = {}) {
  status.msg(msg, true);
  if (banner) { $("banner-text").textContent = msg; $("banner").classList.remove("hidden"); }
  console.warn("[viewer]", msg);
}

// -- viewport bookkeeping ----------------------------------------------------------
function viewFor(p) {
  const space = paneSpace(p);
  if (state.sync && space) return state.viewports[space] || null;
  return state.paneViews[p.id] || null;
}
/** A pane changed its view: store it and push it to every pane that shares it. */
function applyView(pane, view) {
  const space = pane.space;
  if (state.sync && space) {
    state.viewports[space] = view;
    for (const q of panes) if (q.hasFile && q.space === space && !q.el.classList.contains("hidden")) q.setView(view);
  } else {
    state.paneViews[pane.id] = view;
    pane.setView(view);
  }
  if (pane.id === state.focusedPane) metaDrawer.setRange(pane.effectiveRange());
}
/** Give a freshly opened/visible pane a view: adopt the group's or fit to bounds. */
function ensureView(pane, forceFit = false) {
  if (!pane.hasFile) return;
  const space = pane.space;
  const others = visiblePanes(state).filter((p) => p.id !== pane.id && paneSpace(p) === space);
  let view = null;
  if (!forceFit && state.sync && space && others.length && state.viewports[space]) view = state.viewports[space];
  if (!view) view = pane.fitView();
  if (!view) return; // not laid out yet; the pane's resize event calls us again
  applyView(pane, view);
}
function fitAll() {
  const done = new Set();
  for (const pane of panes) {
    const p = pane.p;
    if (!pane.hasFile || pane.el.classList.contains("hidden")) continue;
    const key = state.sync && paneSpace(p) ? paneSpace(p) : `pane${p.id}`;
    if (done.has(key)) continue;
    done.add(key);
    const v = pane.fitView(); if (v) applyView(pane, v);
  }
}
function updateSyncBadges() {
  // reference space = the most common one among visible panes with a file; ties go to the focused pane
  const counts = new Map();
  for (const p of visiblePanes(state)) { const s = paneSpace(p); if (s) counts.set(s, (counts.get(s) || 0) + 1); }
  const focus = panes[state.focusedPane - 1];
  let ref = focus && focus.hasFile ? focus.space : null, best = ref ? counts.get(ref) : 0;
  for (const [s, n] of counts) if (n > best) { ref = s; best = n; }
  for (const pane of panes) {
    if (!pane.hasFile) { pane.setUnsynced(null); continue; }
    pane.setUnsynced(state.sync && ref && pane.space !== ref ? `unsynced: ${pane.space === "pixel" ? "pixel space" : pane.space}` : null);
  }
}

// -- focus / layout / toggles -----------------------------------------------------------
function setFocus(id) {
  state.focusedPane = id;
  for (const pane of panes) pane.setFocused(pane.id === id);
  catalogView.setTarget(id);
  updateMeta();
  updateSyncBadges();
}
function updateMeta() {
  const pane = panes[state.focusedPane - 1];
  // in composite mode the drawer follows the red channel
  const bandName = pane && pane.hasFile ? (pane.isRgb ? pane.rgbNames[0] : pane.p.band) : null;
  const b = bandName ? bandByName(pane.p.fileInfo, bandName) : null;
  metaDrawer.show(pane && pane.hasFile ? pane.p.fileId : null, bandName, pane ? pane.effectiveRange() : null, b ? b.kind : null);
}
function setLayout(n) {
  n = Math.max(1, Math.min(MAX_PANES, n));
  state.layout = n;
  const grid = $("panes");
  grid.className = `layout-${n}`;
  for (const pane of panes) pane.setVisible(pane.id <= n);
  for (const b of document.querySelectorAll("#layout-btns button")) b.classList.toggle("on", Number(b.dataset.layout) === n);
  if (state.focusedPane > n) setFocus(n);
  for (const pane of panes) if (pane.id <= n && pane.hasFile && !viewFor(pane.p)) ensureView(pane);
  for (const pane of panes) pane.refreshDiffOptions();
  updateSyncBadges();
  writeHash();
}
function setSync(on) {
  state.sync = !!on;
  $("btn-sync").classList.toggle("on", state.sync);
  if (state.sync) {
    // seed each space's shared view from the focused pane (or the first pane in that space)
    const seeded = {};
    const order = [panes[state.focusedPane - 1], ...panes].filter(Boolean);
    for (const pane of order) {
      if (!pane.hasFile || pane.el.classList.contains("hidden")) continue;
      const space = pane.space;
      if (seeded[space]) continue;
      seeded[space] = pane.view || state.paneViews[pane.id] || pane.fitView();
      if (seeded[space]) applyView(pane, seeded[space]);
    }
  } else {
    for (const pane of panes) if (pane.hasFile && pane.view) state.paneViews[pane.id] = pane.view;
  }
  updateSyncBadges();
  writeHash();
}
function setLink(on) { state.linkBands = !!on; $("btn-link").classList.toggle("on", state.linkBands); writeHash(); }
function setLock(on) { state.lockStretch = !!on; $("btn-lock").classList.toggle("on", state.lockStretch); writeHash(); }
function toggleDrawer(id, force) {
  const d = $(id);
  const collapsed = force == null ? !d.classList.contains("collapsed") : !force;
  d.classList.toggle("collapsed", collapsed);
  $(id === "catalog" ? "btn-files" : "btn-meta").classList.toggle("on", !collapsed);
}

// -- hash -------------------------------------------------------------------------------
const writeHash = debounce(() => {
  const h = serializeHash(state);
  if (location.hash !== h) history.replaceState(null, "", h);
}, 200);

// -- opening files -------------------------------------------------------------------------
async function openFile(paneId, entry, { band = null, matchKey = null, fileId = null, rgb = null } = {}) {
  const pane = panes[paneId - 1];
  const id = fileId || (entry && entry.id);
  if (!id) return false;
  pane.fileBtn.textContent = `loading ${entry ? entry.name : id}…`;
  status.msg(`opening ${entry ? entry.name : id}…`);
  let info;
  try {
    info = await getBands(id);
  } catch (e) {
    showError(`cannot open ${entry ? entry.name : id}: ${e.message}`, { banner: true });
    pane._updateHeader();
    return false;
  }
  if (!info.id) info.id = id;
  const wanted = band || (matchKey && bandByMatchKey(info, matchKey) ? bandByMatchKey(info, matchKey).name : null);
  pane.setFile(entry || entries.get(id) || null, info, wanted, rgb);
  ensureView(pane); // adopt the space's shared view when another pane shows it, else fit
  for (const q of panes) q.refreshDiffOptions();
  for (const q of panes) if (q.p.diff.enabled && q.p.diff.paneId === paneId) { q._updateHeader(); q.refresh(); }
  if (paneId === state.focusedPane) updateMeta();
  updateSyncBadges();
  markOpenFiles();
  writeHash();
  status.msg(`opened ${info.name} (${info.shape[0]}×${info.shape[1]}, ${info.grid.space})`);
  return true;
}
function markOpenFiles() {
  const m = new Map();
  for (const p of visiblePanes(state)) if (p.fileId) m.set(p.fileId, [...(m.get(p.fileId) || []), p.id]);
  catalogView.markOpen(m);
}
async function compare(files) {
  if (!files.length) return;
  if (state.layout < 2 && files.length > 1) setLayout(2);
  setSync(true); setLink(true);
  const a = files[0], b = files[1];
  await openFile(1, a, { matchKey: "Rrs@560" });
  if (b) {
    await openFile(2, b, { matchKey: "Rrs@560" });
    // if pane 1 had no Rrs@560 but pane 2 does (or vice versa), align on pane 1's key
    const k1 = panes[0].band && panes[0].band.match_key;
    const m = k1 && bandByMatchKey(panes[1].p.fileInfo, k1);
    if (m) panes[1].setBand(m.name, true);
  }
  setFocus(1);
  fitAll();
}

// -- pane events -----------------------------------------------------------------------------
function onPaneEvent(type, pane, payload) {
  switch (type) {
    case "focus": if (state.focusedPane !== pane.id) setFocus(pane.id); break;
    case "open-catalog": setFocus(pane.id); toggleDrawer("catalog", true); catalogView.focusFilter(); break;
    case "view": applyView(pane, payload); break;
    case "resize": if (pane.hasFile && !pane.view) ensureView(pane); break;
    case "band": {
      if (state.linkBands) {
        if (pane.isRgb) {
          // composite: the other panes follow channel by channel (by wavelength)
          for (const q of panes) {
            if (q === pane || !q.hasFile) continue;
            const m = mapRgbByMatchKey(pane.p.fileInfo, pane.rgbNames, q.p.fileInfo);
            if (m) q.setRgb(m, true); else q.flash();
          }
        } else {
          const key = pane.band && pane.band.match_key;
          for (const q of panes) {
            if (q === pane || !q.hasFile) continue;
            const m = key && bandByMatchKey(q.p.fileInfo, key);
            if (m) q.setBand(m.name, true); else q.flash();
          }
        }
      }
      for (const q of panes) q.refreshDiffOptions(); // a composite pane cannot be a diff partner
      for (const q of panes) if (q.p.diff.enabled && q.p.diff.paneId === pane.id) { q._updateHeader(); q.refresh(); }
      updateMeta(); // the focused pane may have followed via Link bands
      if (pinned) fetchValues(pinned, pinned.space);
      writeHash();
      break;
    }
    case "stretch": {
      if (state.lockStretch && !pane.p.diff.enabled) {
        // same band (or same three channels) by match_key
        const keyOf = (q) => q.isRgb
          ? "rgb:" + q.rgbNames.map((n) => (bandByName(q.p.fileInfo, n) || {}).match_key).join(",")
          : (q.band && q.band.match_key);
        const key = keyOf(pane);
        for (const q of panes) {
          if (q === pane || !q.hasFile || q.p.diff.enabled) continue;
          if (key && keyOf(q) === key) q.setStretch({ ...pane.p.stretch });
        }
      }
      if (pane.id === state.focusedPane) metaDrawer.setRange(pane.effectiveRange());
      break;
    }
    case "cmap": break;
    case "diff": pane.refreshDiffOptions(); break;
    case "close":
      for (const q of panes) q.refreshDiffOptions();
      if (pane.id === state.focusedPane) updateMeta();
      updateSyncBadges(); markOpenFiles(); writeHash();
      break;
    case "rendered": {
      const h = payload || {};
      const ms = h.renderMs != null ? `render ${h.renderMs.toFixed(0)} ms` : "rendered";
      status.msg(`pane ${pane.id}: ${ms}`);
      if (pane.id === state.focusedPane) metaDrawer.setRange(pane.effectiveRange());
      break;
    }
    case "error": showError(`pane ${pane.id}: ${payload}`); break;
    case "hover": onHover(pane, payload); break;
    case "pin": onPin(pane, payload); break;
    default: break;
  }
}

// -- cursor, crosshair, values ----------------------------------------------------------------
function cursorText(pane, world) {
  const g = pane.p.fileInfo.grid;
  const { row, col } = rowColAt(g, world.x, world.y);
  const inside = row >= 0 && col >= 0 && row < (g.ny || Infinity) && col < (g.nx || Infinity);
  return `X=${fmtCoord(world.x, g.units)} Y=${fmtCoord(world.y, g.units)} ${g.units === "px" ? "" : g.units}  row=${row} col=${col}${inside ? "" : " (off grid)"}`;
}
function onHover(pane, world) {
  if (!world) {
    for (const q of panes) q.setCrosshair(null);
    if (!pinned) status.cursor("");
    else if (pinned.space !== pane.space) { status.cursor("◉ " + cursorText(panes.find((q) => q.hasFile && q.space === pinned.space) || pane, pinned)); fetchValues(pinned, pinned.space); }
    return;
  }
  const space = pane.space;
  for (const q of panes) q.setCrosshair(q.hasFile && q.space === space ? world : null);
  if (pinned && pinned.space === space) return; // statusbar stays on the pinned point
  status.cursor(cursorText(pane, world));
  fetchValuesSoon(world, space);
}
const fetchValuesSoon = debounce((world, space) => fetchValues(world, space), 120);
async function fetchValues(world, space) {
  const seq = ++valueSeq;
  // one request per band; a composite pane reads its three channels
  const bandsOf = (p) => (p.rgb && p.rgb.enabled && p.rgb.r && p.rgb.g && p.rgb.b ? [p.rgb.r, p.rgb.g, p.rgb.b] : p.band ? [p.band] : []);
  const targets = visiblePanes(state).filter((p) => p.fileInfo && paneSpace(p) === space && bandsOf(p).length);
  if (!targets.length) { status.values(null); return; }
  const results = await Promise.all(targets.map((p) => {
    const names = bandsOf(p);
    return Promise.allSettled(names.map((n) => getValue(p.fileId, n, world.x, world.y))).then((rs) => ({ names, rs }));
  }));
  if (seq !== valueSeq) return;
  const nodes = [];
  let lonlat = null;
  results.forEach(({ names, rs }, i) => {
    const p = targets[i];
    const span = el("span.pv", null, el("b", null, `p${p.id} `));
    rs.forEach((r, k) => {
      const prefix = names.length === 3 ? ["R ", "G ", "B "][k] : "";
      if (k) span.append(" ");
      if (r.status === "rejected") { span.append(prefix, el("span.error", null, r.reason && r.reason.message ? r.reason.message : "error")); return; }
      const v = r.value || {};
      if (v.lon != null && v.lat != null && !lonlat) lonlat = v;
      if (v.inside === false) { span.append(`${prefix}${names[k]}: off grid`); return; }
      span.append(`${prefix}${names[k]}=${v.value == null ? "null" : fmtNum(v.value, 5)}`);
      if (k === 0 && v.status) span.append(el("span.pv-status." + v.status, null, ` ${v.status}`));
      if (k === 0 && v.flags && v.flags.length) span.append(el("span.pv-flags", { title: v.flags.join(", ") }, ` [${v.flags.join(",")}]`));
    });
    nodes.push(span);
  });
  if (lonlat) nodes.unshift(el("span.pv.lonlat", null, `lon=${lonlat.lon.toFixed(5)} lat=${lonlat.lat.toFixed(5)}`));
  status.values(nodes);
}
function onPin(pane, world) {
  if (pinned && Math.abs(pinned.x - world.x) < 1e-9 && Math.abs(pinned.y - world.y) < 1e-9) { clearPin(); return; }
  pinned = { ...world, space: pane.space };
  for (const q of panes) q.setPin(q.hasFile && q.space === pinned.space ? world : null);
  status.cursor("◉ " + cursorText(pane, world));
  fetchValuesSoon.cancel();
  fetchValues(world, pane.space);
}
function clearPin() {
  pinned = null;
  valueSeq++; // drop value responses still in flight for the old point
  for (const q of panes) q.setPin(null);
  status.cursor(""); status.values(null);
}

// -- catalog handlers -------------------------------------------------------------------------
function nextPaneId() {
  const n = (state.focusedPane % MAX_PANES) + 1;
  if (n > state.layout) setLayout(n);
  return n;
}
async function refreshCatalog(refresh) {
  catalogView.setStatus(refresh ? "rescanning…" : "loading catalog…");
  try {
    const cat = await getCatalog(refresh);
    entries.clear();
    for (const f of cat.files || []) entries.set(f.id, f);
    catalogView.setData(cat);
    markOpenFiles();
    return cat;
  } catch (e) {
    catalogView.setStatus(e.message, true);
    showError(`catalog: ${e.message}`, { banner: true });
    return null;
  }
}
async function openByPath(path) {
  status.msg(`opening ${path}…`);
  try {
    const entry = await openPath(path);
    entries.set(entry.id, entry);
    catalogView.addEntry(entry);
    await openFile(state.focusedPane, entry);
  } catch (e) {
    showError(`open path: ${e.message}`, { banner: true });
  }
}

// -- keyboard / help -------------------------------------------------------------------------------
function onKey(e) {
  if (e.key === "Escape") { if (!$("help").classList.contains("hidden")) $("help").classList.add("hidden"); else clearPin(); return; }
  if (inTextField(e) || e.ctrlKey || e.metaKey || e.altKey) return;
  switch (e.key) {
    case "c": toggleDrawer("catalog"); break;
    case "m": toggleDrawer("meta"); break;
    case "?": $("help").classList.toggle("hidden"); break;
    case "s": setSync(!state.sync); break;
    case "l": setLink(!state.linkBands); break;
    case "k": setLock(!state.lockStretch); break;
    case "r": { const pane = panes[state.focusedPane - 1]; if (pane && pane.hasFile) pane.setRgbEnabled(!pane.p.rgb.enabled); break; }
    case "1": case "2": case "3": case "4": setLayout(Number(e.key)); break;
    case "Tab": { e.preventDefault(); const n = (state.focusedPane % state.layout) + 1; setFocus(n); panes[n - 1].el.focus(); break; }
    default: {
      // forward view keys to the focused pane when the focus is elsewhere (e.g. body)
      if (!e.target.closest || !e.target.closest(".pane")) {
        const pane = panes[state.focusedPane - 1];
        if (pane) pane._key(e);
      }
    }
  }
}

// -- boot ------------------------------------------------------------------------------------------------
async function boot() {
  window.addEventListener("unhandledrejection", (ev) => { showError(`unexpected: ${ev.reason && ev.reason.message ? ev.reason.message : ev.reason}`); ev.preventDefault(); });
  $("banner-close").onclick = () => $("banner").classList.add("hidden");
  $("help-close").onclick = () => $("help").classList.add("hidden");
  $("btn-help").onclick = () => $("help").classList.toggle("hidden");

  const [cfgR, cmR] = await Promise.allSettled([getConfig(), getColormaps()]);
  if (cfgR.status === "fulfilled") {
    const c = cfgR.value;
    $("app-version").textContent = c.version ? `v${c.version}` : "";
    $("app-version").title = (c.roots || []).map((r) => `${r.exists === false ? "MISSING " : ""}${r.label || ""} ${r.path}`).join("\n");
  } else showError(`config: ${cfgR.reason.message}`, { banner: true });
  if (cmR.status === "fulfilled" && Array.isArray(cmR.value.names) && cmR.value.names.length) { colormaps = cmR.value.names; configurePalettes(cmR.value); }
  else showError(`colormaps: ${cmR.status === "rejected" ? cmR.reason.message : "empty list"}; using built-in names`);

  // panes
  const paneOpts = {
    colormaps,
    emit: onPaneEvent,
    resolveDiff: (paneId) => {
      const q = panes[paneId - 1];
      if (!q || !q.hasFile || !q.p.band || q.isRgb) return null;
      return { fileId: q.p.fileId, band: q.p.band, name: q.p.fileInfo.name, label: fileLabel(q.p.entry, q.p.fileInfo), space: q.space };
    },
    otherPanes: (self) => visiblePanes(state).filter((p) => p.id !== self.id).map((p) => ({ id: p.id, hasFile: !!p.fileInfo, space: paneSpace(p), label: fileLabel(p.entry, p.fileInfo), rgb: !!(p.rgb && p.rgb.enabled && p.rgb.r && p.rgb.g && p.rgb.b) })),
  };
  panes = state.panes.map((p) => new Pane(p, paneOpts));
  const grid = $("panes");
  for (const pane of panes) grid.append(pane.el);

  // drawers
  catalogView = new CatalogView($("catalog"), {
    onOpen: (entry, { shift }) => openFile(shift ? nextPaneId() : state.focusedPane, entry),
    onCompare: (files) => compare(files),
    onRefresh: () => refreshCatalog(true),
    onOpenPath: (path) => openByPath(path),
  });
  metaDrawer = new MetaDrawer($("meta"), {
    onUseRange: (vmin, vmax) => { const pane = panes[state.focusedPane - 1]; if (pane && pane.hasFile) pane.setStretch({ vmin, vmax }, true); },
    onError: (m) => showError(`metadata: ${m}`),
  });

  // topbar
  for (const b of document.querySelectorAll("#layout-btns button")) b.onclick = () => setLayout(Number(b.dataset.layout));
  $("btn-sync").onclick = () => setSync(!state.sync);
  $("btn-link").onclick = () => setLink(!state.linkBands);
  $("btn-lock").onclick = () => setLock(!state.lockStretch);
  $("btn-refresh").onclick = () => refreshCatalog(true);
  $("btn-fit").onclick = () => fitAll();
  $("btn-files").onclick = () => toggleDrawer("catalog");
  $("btn-meta").onclick = () => toggleDrawer("meta");
  document.addEventListener("keydown", onKey);
  window.addEventListener("hashchange", () => restoreFromHash(false));

  // initial state from the hash, then the catalog
  const parsed = parseHash(location.hash);
  applyHashFlags(state, parsed);
  setLayout(state.layout); setSync(state.sync); setLink(state.linkBands); setLock(state.lockStretch);
  setFocus(1);
  toggleDrawer("catalog", true); toggleDrawer("meta", true);
  await refreshCatalog(false);
  await restoreFromHash(true, parsed);
  if (!$("st-msg").classList.contains("error")) status.msg("ready");
}

async function restoreFromHash(initial, parsed = parseHash(location.hash)) {
  const ids = Object.keys(parsed.panes).map(Number);
  if (!ids.length) return;
  if (!initial) applyHashFlags(state, parsed);
  const need = Math.max(state.layout, ...ids);
  if (need !== state.layout) setLayout(need);
  for (const id of ids) {
    const { fileId, band, rgb } = parsed.panes[id];
    const pane = panes[id - 1];
    if (pane.p.fileId === fileId) {
      if (rgb) { if (!pane.isRgb || pane.rgbNames.join() !== rgb.join()) pane.setRgb(rgb, true); }
      else if (band && (pane.p.band !== band || pane.isRgb)) pane.setBand(band, true);
      continue;
    }
    await openFile(id, entries.get(fileId) || null, { band, fileId, rgb });
  }
  fitAll();
  setFocus(1);
}

boot().catch((e) => showError(`startup failed: ${e.message}`, { banner: true }));
