// state.js - application state (panes, layout, toggles, viewports per space)
// and the pure helpers over it: URL-hash (de)serialisation, band matching,
// space grouping. No DOM here so the functions can be unit-tested.

export const MAX_PANES = 4;

export function createPane(id) {
  return {
    id,                 // 1..4
    fileId: null,       // catalog FileEntry.id
    entry: null,        // FileEntry (for the header label), may be null for hash-restored ids
    fileInfo: null,     // /api/files/{id}/bands payload
    band: null,
    cmap: "viridis",
    stretch: { mode: "auto", vmin: null, vmax: null, scale: "linear" },
    bit: null,          // flags kind: null = count view, else 0-based index into flag_masks
    diff: { enabled: false, paneId: null, fileId: null, band: null, vmax: null },
  };
}

export function createState() {
  return {
    panes: Array.from({ length: MAX_PANES }, (_, i) => createPane(i + 1)),
    layout: 1,
    sync: true,
    linkBands: true,
    lockStretch: false,
    focusedPane: 1,
    viewports: {},       // space -> {cx, cy, mpp} (shared when sync is on)
    paneViews: {},       // paneId -> {cx, cy, mpp} (own view when not synced)
  };
}

export function visiblePanes(state) {
  return state.panes.slice(0, state.layout);
}

export function paneById(state, id) {
  return state.panes.find((p) => p.id === id) || null;
}

export function paneSpace(pane) {
  return pane && pane.fileInfo && pane.fileInfo.grid ? pane.fileInfo.grid.space : null;
}

/** Visible panes with a file in `space` (optionally excluding one pane id). */
export function panesInSpace(state, space, exceptId = null) {
  if (!space) return [];
  return visiblePanes(state).filter((p) => p.id !== exceptId && paneSpace(p) === space);
}

/** Find the band of `fileInfo` by exact name. */
export function bandByName(fileInfo, name) {
  if (!fileInfo || !name) return null;
  return fileInfo.bands.find((b) => b.name === name) || null;
}

/** Find the band of `fileInfo` with a given match_key (first match). */
export function bandByMatchKey(fileInfo, matchKey) {
  if (!fileInfo || !matchKey) return null;
  return fileInfo.bands.find((b) => b.match_key === matchKey) || null;
}

/** Band to show when a file is opened: requested name, else default_band, else first. */
export function initialBand(fileInfo, requested) {
  return (requested && bandByName(fileInfo, requested)) ||
    bandByName(fileInfo, fileInfo.default_band) ||
    (fileInfo.bands[0] || null);
}

/** Group bands by `group` preserving first-seen order -> [[group, [bands]], ...]. */
export function groupBands(bands) {
  const groups = new Map();
  for (const b of bands || []) {
    const g = b.group || "other";
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(b);
  }
  return [...groups.entries()];
}

/** Label for a band option: name, wavelength nm, units. */
export function bandLabel(b) {
  let s = b.name;
  if (b.wavelength != null && Number.isFinite(b.wavelength)) s += ` (${Math.round(b.wavelength)} nm)`;
  if (b.units && b.units !== "1" && b.units !== "") s += ` [${b.units}]`;
  return s;
}

// -- URL hash ----------------------------------------------------------------
// #layout=2&p1=<fileId>:<band>&p2=<fileId>:<band>&sync=1&link=1&lock=0

export function serializeHash(state) {
  const q = new URLSearchParams();
  q.set("layout", String(state.layout));
  for (const p of state.panes) {
    if (p.fileId) q.set(`p${p.id}`, p.band ? `${p.fileId}:${p.band}` : p.fileId);
  }
  q.set("sync", state.sync ? "1" : "0");
  q.set("link", state.linkBands ? "1" : "0");
  q.set("lock", state.lockStretch ? "1" : "0");
  return "#" + q.toString();
}

/** Parse a hash into {layout, sync, linkBands, lockStretch, panes:{1:{fileId, band}}}; tolerant of junk. */
export function parseHash(hash) {
  const out = { layout: null, sync: null, linkBands: null, lockStretch: null, panes: {} };
  const text = (hash || "").replace(/^#/, "");
  if (!text) return out;
  let q;
  try { q = new URLSearchParams(text); } catch { return out; }
  const layout = parseInt(q.get("layout") || "", 10);
  if (layout >= 1 && layout <= MAX_PANES) out.layout = layout;
  const flag = (k) => (q.has(k) ? q.get(k) !== "0" : null);
  out.sync = flag("sync");
  out.linkBands = flag("link");
  out.lockStretch = flag("lock");
  for (let i = 1; i <= MAX_PANES; i++) {
    const v = q.get(`p${i}`);
    if (!v) continue;
    const idx = v.indexOf(":");
    const fileId = idx < 0 ? v : v.slice(0, idx);
    const band = idx < 0 ? null : v.slice(idx + 1) || null;
    if (/^[A-Za-z0-9_-]{1,64}$/.test(fileId)) out.panes[i] = { fileId, band };
  }
  return out;
}

/** Apply the global flags parsed from a hash to a state (panes are opened by the app). */
export function applyHashFlags(state, parsed) {
  if (parsed.layout) state.layout = parsed.layout;
  if (parsed.sync != null) state.sync = parsed.sync;
  if (parsed.linkBands != null) state.linkBands = parsed.linkBands;
  if (parsed.lockStretch != null) state.lockStretch = parsed.lockStretch;
  return state;
}
