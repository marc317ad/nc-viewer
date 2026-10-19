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
    // false-colour composite: three float bands of this file on R, G, B.
    // `shared` = one stretch range for all channels; `gamma` > 1 brightens.
    rgb: { enabled: false, r: null, g: null, b: null, shared: false, gamma: 1 },
  };
}

// -- RGB composites ------------------------------------------------------------
// Presets are nominal Sentinel-2 wavelengths per channel, first choice first
// (865 = B8A at 20 m, 833 = B08 when only the 10 m band is present).

export const RGB_PRESETS = [
  { id: "true", label: "True colour (665/560/492)", channels: [[665], [560], [492]] },
  { id: "nir", label: "False colour NIR (865/665/560)", channels: [[865, 833], [665], [560]] },
  { id: "swir", label: "SWIR (2202/865/665)", channels: [[2202, 1614], [865, 833], [665]] },
  { id: "rededge", label: "Red edge (704/665/560)", channels: [[704, 740], [665], [560]] },
];

const FAMILY_ORDER = ["Rrs", "rhow", "rhos", "rhot", "rhorc"];
const familyOf = (b) => (b && typeof b.match_key === "string" && b.match_key.includes("@") ? b.match_key.split("@")[0] : null);

/** Spectral families present in `fileInfo`, the family of `preferBand` first, then Rrs, rhow, ... */
export function spectralFamilies(fileInfo, preferBand = null) {
  const fams = new Set();
  const pb = preferBand ? bandByName(fileInfo, preferBand) : null;
  if (familyOf(pb)) fams.add(familyOf(pb));
  for (const f of FAMILY_ORDER) fams.add(f);
  for (const b of (fileInfo && fileInfo.bands) || []) if (b.kind === "float" && familyOf(b)) fams.add(familyOf(b));
  return [...fams];
}

/** Band names [r, g, b] for a preset (id or object) in `fileInfo`, all from one family; null when a channel is missing. */
export function resolveRgbPreset(fileInfo, preset, preferBand = null) {
  const pr = typeof preset === "string" ? RGB_PRESETS.find((p) => p.id === preset) : preset;
  if (!fileInfo || !pr) return null;
  for (const fam of spectralFamilies(fileInfo, preferBand)) {
    const names = pr.channels.map((cands) => {
      for (const wl of cands) {
        const b = bandByMatchKey(fileInfo, `${fam}@${wl}`);
        if (b && b.kind === "float") return b.name;
      }
      return null;
    });
    if (names.every(Boolean)) return names;
  }
  return null;
}

/** Preset id that resolves to exactly `names` for this file, else "custom". */
export function rgbPresetOf(fileInfo, names, preferBand = null) {
  if (!names || names.length !== 3) return "custom";
  for (const pr of RGB_PRESETS) {
    const r = resolveRgbPreset(fileInfo, pr, preferBand);
    if (r && r.every((n, i) => n === names[i])) return pr.id;
  }
  return "custom";
}

/** Composite to start with: the first preset that resolves, else three float bands (longest wavelength on R), else null. */
export function defaultRgb(fileInfo, preferBand = null) {
  for (const pr of RGB_PRESETS) {
    const r = resolveRgbPreset(fileInfo, pr, preferBand);
    if (r) return r;
  }
  const floats = ((fileInfo && fileInfo.bands) || []).filter((b) => b.kind === "float");
  if (floats.length < 3) return null;
  return [floats[2].name, floats[1].name, floats[0].name];
}

/** Map a composite of `srcInfo` onto `dstInfo` by match_key; null unless all three channels resolve. */
export function mapRgbByMatchKey(srcInfo, names, dstInfo) {
  if (!names || names.length !== 3) return null;
  const out = names.map((n) => {
    const b = bandByName(srcInfo, n);
    const m = b && bandByMatchKey(dstInfo, b.match_key);
    return m && m.kind === "float" ? m.name : null;
  });
  return out.every(Boolean) ? out : null;
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
// #layout=2&p1=<fileId>:<band>&p2=<fileId>:rgb(<r>,<g>,<b>)&sync=1&link=1&lock=0

export function serializeHash(state) {
  const q = new URLSearchParams();
  q.set("layout", String(state.layout));
  for (const p of state.panes) {
    if (!p.fileId) continue;
    const rgb = p.rgb && p.rgb.enabled && p.rgb.r && p.rgb.g && p.rgb.b ? `rgb(${p.rgb.r},${p.rgb.g},${p.rgb.b})` : null;
    q.set(`p${p.id}`, rgb ? `${p.fileId}:${rgb}` : p.band ? `${p.fileId}:${p.band}` : p.fileId);
  }
  q.set("sync", state.sync ? "1" : "0");
  q.set("link", state.linkBands ? "1" : "0");
  q.set("lock", state.lockStretch ? "1" : "0");
  return "#" + q.toString();
}

/** Parse a hash into {layout, sync, linkBands, lockStretch, panes:{1:{fileId, band, rgb}}}; tolerant of junk.
 *  `rgb` is [r, g, b] band names for a `rgb(r,g,b)` band field (then `band` is null), else undefined. */
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
    let band = idx < 0 ? null : v.slice(idx + 1) || null;
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(fileId)) continue;
    const m = band && /^rgb\((.*)\)$/.exec(band);
    if (m) {
      const parts = m[1].split(",").map((s) => s.trim());
      band = null;
      if (parts.length === 3 && parts.every(Boolean)) { out.panes[i] = { fileId, band, rgb: parts }; continue; }
    }
    out.panes[i] = { fileId, band };
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
