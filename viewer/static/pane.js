// pane.js - one viewer pane: header controls (file, band, colormap, stretch,
// flag bit, diff pairing), a DPR-aware canvas that shows the server render of
// exactly the visible bbox, instant pan/zoom on the stale image while the
// next render loads, cursor/crosshair/pin overlays and a colorbar strip.
// The pane mutates its own state record `p` (see state.js) and reports
// everything else through opts.emit(type, pane, payload).

import { el, clear, fmtNum, numOrNull } from "./util.js";
import { bboxFor, fitTo, zoomAt, panBy, screenToWorld, worldToScreen } from "./viewport.js";
import { renderUrl, renderRgbUrl, loadImage } from "./api.js";
import { groupBands, bandLabel, bandByName, initialBand, RGB_PRESETS, resolveRgbPreset, rgbPresetOf, defaultRgb } from "./state.js";
import { drawGradient, STATUS_CLASSES, countRgb, css, MASK_RGB } from "./colorbar.js";

const MAX_PX = 4096;
const RENDER_DEBOUNCE_MS = 60;
const ZOOM_STEP = 1.25;

export function fileLabel(entry, fileInfo) {
  if (entry && entry.tile) {
    const t = entry.datetime ? entry.datetime.replace("T", " ").slice(0, 16) : entry.date || "";
    return [entry.platform, t, entry.tile, entry.role].filter(Boolean).join(" ");
  }
  return (entry && entry.name) || (fileInfo && fileInfo.name) || "";
}

export class Pane {
  constructor(p, opts) {
    this.p = p;
    this.opts = opts;               // {colormaps, emit, resolveDiff, otherPanes}
    this.view = null;               // {cx, cy, mpp} currently displayed (shared object when synced)
    this.w = 0; this.h = 0;         // CSS size of the canvas
    this.img = null; this.imgBbox = null;
    this.autoRange = { vmin: null, vmax: null, diffVmax: null, ranges: null };
    this.crosshair = null; this.pin = null;
    this._inflight = null; this._lastUrl = null; this._timer = null;
    this._build();
  }

  // -- DOM -------------------------------------------------------------------
  _build() {
    const p = this.p, o = this.opts;
    this.el = el("div.pane", { tabindex: 0, dataset: { pane: String(p.id) } });
    this.fileBtn = el("button.pane-file", { title: "choose a file for this pane", onclick: () => o.emit("open-catalog", this) }, "click a file in the catalog");
    this.bandSel = el("select.pane-band", { title: "band", onchange: () => this._onBand(this.bandSel.value) });
    this.cmapSel = el("select.pane-cmap", { title: "colormap", onchange: () => { p.cmap = this.cmapSel.value; this.refresh(); o.emit("cmap", this); } });
    for (const n of o.colormaps) this.cmapSel.append(el("option", { value: n }, n));
    this.cmapSel.value = p.cmap;
    this.closeBtn = el("button.pane-close", { title: "clear this pane", onclick: () => this.clearFile() }, "×");
    this.numEl = el("span.pane-num", { title: "pane number (target for the catalog)" }, String(p.id));

    this.vminIn = el("input.pane-vmin", { type: "text", inputmode: "decimal", placeholder: "min", title: "stretch minimum (empty = auto p2)", size: 7, onchange: () => this._onStretchInput() });
    this.vmaxIn = el("input.pane-vmax", { type: "text", inputmode: "decimal", placeholder: "max", title: "stretch maximum (empty = auto p98)", size: 7, onchange: () => this._onStretchInput() });
    this.autoBtn = el("button.pane-auto", { title: "reset the stretch to p2..p98", onclick: () => this.setStretch({ mode: "auto", vmin: null, vmax: null }, true) }, "auto");
    this.logBtn = el("button.pane-log.toggle", { title: "logarithmic stretch", onclick: () => this.setStretch({ scale: p.stretch.scale === "log" ? "linear" : "log" }, true) }, "log");
    this.bitSel = el("select.pane-bit", { title: "flag bit: count of set bits or one flag as a mask", onchange: () => { const v = this.bitSel.value; p.bit = v === "" ? null : Number(v); this.refresh(); } });
    this.diffSel = el("select.pane-diff", { title: "difference: this pane minus another pane (same space)", onchange: () => this._onDiff(this.diffSel.value) });
    this.diffLabel = el("label.pane-diff-label", { title: "difference against another pane" }, "−", this.diffSel);
    // RGB composite controls (row 3, shown in composite mode)
    this.rgbBtn = el("button.pane-rgb-btn.toggle", { title: "false-colour composite: three bands on R, G, B (r)", onclick: () => this.setRgbEnabled(!p.rgb.enabled) }, "RGB");
    this.presetSel = el("select.pane-preset", { title: "composite preset (nominal wavelengths R/G/B)", onchange: () => this._onPreset(this.presetSel.value) });
    this.chSel = {};
    for (const ch of ["r", "g", "b"]) this.chSel[ch] = el("select.pane-ch", { title: `${{ r: "red", g: "green", b: "blue" }[ch]} channel`, onchange: () => this._onChannel() });
    this.sharedBtn = el("button.pane-shared.toggle", { title: "one stretch range for all three channels (keeps the bands' relative brightness)", onclick: () => this.setRgbOptions({ shared: !p.rgb.shared }) }, "shared");
    this.gammaSel = el("select.pane-gamma", { title: "gamma: > 1 brightens the dark end", onchange: () => this.setRgbOptions({ gamma: Number(this.gammaSel.value) }) });
    for (const g of [0.5, 0.7, 1, 1.5, 2, 3]) this.gammaSel.append(el("option", { value: String(g) }, `γ ${g.toFixed(1)}`));
    this.gammaSel.value = String(p.rgb.gamma);

    this.row1 = el("div.ph-row", null, this.numEl, this.fileBtn, this.bandSel, this.rgbBtn, this.cmapSel, this.closeBtn);
    this.row2 = el("div.ph-row.ph-stretch", null, this.vminIn, this.vmaxIn, this.autoBtn, this.logBtn, this.bitSel, this.diffLabel);
    this.row3 = el("div.ph-row.ph-rgb.hidden", null, this.presetSel,
      el("span.ch-label.r", null, "R"), this.chSel.r, el("span.ch-label.g", null, "G"), this.chSel.g, el("span.ch-label.b", null, "B"), this.chSel.b,
      this.sharedBtn, this.gammaSel);
    this.header = el("div.pane-header", null, this.row1, this.row2, this.row3);

    this.canvas = el("canvas.pane-canvas");
    this.ctx = this.canvas.getContext("2d");
    this.emptyEl = el("div.pane-empty", null, "click a file in the catalog");
    this.badgeRender = el("span.badge.badge-render", null, "rendering…");
    this.badgeSync = el("span.badge.badge-unsynced");
    this.body = el("div.pane-body", null, this.canvas, this.emptyEl, el("div.pane-badges", null, this.badgeRender, this.badgeSync));

    this.cbMin = el("span.cb-label.cb-min");
    this.cbMax = el("span.cb-label.cb-max");
    this.cbGrad = el("canvas.cb-grad");
    this.cbLegend = el("div.cb-legend");
    this.colorbar = el("div.pane-colorbar", null, this.cbMin, this.cbGrad, this.cbMax, this.cbLegend);

    this.el.append(this.header, this.body, this.colorbar);
    this._wireEvents();
    this._ro = new ResizeObserver(() => this._onResize());
    this._ro.observe(this.body);
    this.refreshDiffOptions();
    this._updateControls();
  }

  _wireEvents() {
    const c = this.canvas;
    this.el.addEventListener("pointerdown", () => this.opts.emit("focus", this), true);
    c.addEventListener("pointerdown", (e) => this._pointerDown(e));
    c.addEventListener("mousedown", (e) => { if (e.button === 1) e.preventDefault(); }); // no autoscroll on the middle button
    c.addEventListener("pointermove", (e) => this._pointerMove(e));
    c.addEventListener("pointerup", (e) => this._pointerUp(e));
    c.addEventListener("pointercancel", () => { this._drag = null; });
    c.addEventListener("pointerleave", () => { if (!this._drag) this.opts.emit("hover", this, null); });
    c.addEventListener("wheel", (e) => this._wheel(e), { passive: false });
    c.addEventListener("dblclick", (e) => {
      clearTimeout(this._pinTimer); // a double click zooms; it must not pin/unpin
      if (!this.view) return;
      const r = c.getBoundingClientRect();
      this._emitView(zoomAt(this.view, this.w, this.h, e.clientX - r.left, e.clientY - r.top, 2));
    });
    this.el.addEventListener("keydown", (e) => this._key(e));
    this._hoverThrottle = 0;
  }

  // -- public API -------------------------------------------------------------
  get id() { return this.p.id; }
  get space() { return this.p.fileInfo ? this.p.fileInfo.grid.space : null; }
  get band() { return bandByName(this.p.fileInfo, this.p.band); }
  get hasFile() { return !!this.p.fileInfo; }

  setVisible(on) { this.el.classList.toggle("hidden", !on); if (on) this._onResize(); }
  setFocused(on) { this.el.classList.toggle("focused", on); }
  setUnsynced(text) { this.badgeSync.textContent = text || ""; this.badgeSync.classList.toggle("show", !!text); }
  flash() { this.header.classList.remove("flash"); void this.header.offsetWidth; this.header.classList.add("flash"); }

  /** Place a file in the pane (band: requested name or null; rgb: [r, g, b] names to open as a composite). Does not set the view. */
  setFile(entry, fileInfo, band = null, rgb = null) {
    const p = this.p;
    // Keep the previous image on the canvas while the new file's first render
    // is in flight (its bbox is still meaningful when both files share a
    // space), so the pane does not go blank behind the "rendering…" badge.
    const keepStale = !!(p.fileInfo && fileInfo.grid && p.fileInfo.grid.space === fileInfo.grid.space);
    p.entry = entry || null; p.fileId = fileInfo.id || (entry && entry.id); p.fileInfo = fileInfo;
    const b = initialBand(fileInfo, band);
    p.band = b ? b.name : null;
    p.bit = null;
    p.stretch = { mode: "auto", vmin: null, vmax: null, scale: p.stretch.scale };
    p.rgb = { enabled: false, r: null, g: null, b: null, shared: p.rgb.shared, gamma: p.rgb.gamma };
    const isFloat = (n) => { const x = bandByName(fileInfo, n); return x && (x.kind === "float" || x.kind === "latlon"); };
    if (rgb && rgb.length === 3 && rgb.every(isFloat)) { p.rgb.enabled = true; [p.rgb.r, p.rgb.g, p.rgb.b] = rgb; }
    if (!keepStale) this._dropImage(); this._lastUrl = null;
    this.autoRange = { vmin: null, vmax: null, diffVmax: null, ranges: null };
    this._fillBands(); this._fillChannels();
    this.emptyEl.classList.add("hidden");
    this._updateHeader();
    this._updateControls();
    this.refresh();
  }

  clearFile() {
    const p = this.p;
    if (this._inflight) this._inflight.abort();
    clearTimeout(this._timer);
    p.entry = null; p.fileId = null; p.fileInfo = null; p.band = null; p.bit = null;
    p.diff = { enabled: false, paneId: null, fileId: null, band: null, vmax: null };
    p.stretch = { mode: "auto", vmin: null, vmax: null, scale: "linear" };
    p.rgb = { enabled: false, r: null, g: null, b: null, shared: p.rgb.shared, gamma: p.rgb.gamma };
    this.view = null;
    this._dropImage(); this._lastUrl = null;
    clear(this.bandSel); this._fillChannels();
    this.emptyEl.classList.remove("hidden");
    this.badgeRender.classList.remove("show");
    this._updateHeader(); this._updateControls(); this.draw(); this._updateColorbar();
    this.opts.emit("close", this);
  }

  /** Switch the band (silent = do not emit, used by Link bands). Leaves composite mode. */
  setBand(name, silent = false) {
    const p = this.p;
    if (!bandByName(p.fileInfo, name) || (p.band === name && !p.rgb.enabled)) return false;
    p.band = name; p.bit = null; p.rgb.enabled = false;
    p.stretch = { ...p.stretch, mode: "auto", vmin: null, vmax: null };
    this.autoRange = { vmin: null, vmax: null, diffVmax: null, ranges: null };
    this._lastUrl = null;
    this.bandSel.value = name;
    this._updateControls(); this.refresh();
    if (!silent) this.opts.emit("band", this);
    return true;
  }

  /** Merge stretch fields; emit when the user did it (for Lock stretch). */
  setStretch(partial, emit = false) {
    const s = this.p.stretch;
    Object.assign(s, partial);
    if (s.vmin == null && s.vmax == null) s.mode = "auto";
    else s.mode = "manual";
    this._updateControls(); this.refresh();
    if (emit) this.opts.emit("stretch", this);
  }

  setCmap(name) { if (this.opts.colormaps.includes(name)) { this.p.cmap = name; this.cmapSel.value = name; this.refresh(); } }

  // -- RGB composite ------------------------------------------------------------
  /** True when the pane shows a composite (enabled with three channels). */
  get isRgb() { const r = this.p.rgb; return !!(r && r.enabled && r.r && r.g && r.b); }
  get rgbNames() { const r = this.p.rgb; return r && r.r && r.g && r.b ? [r.r, r.g, r.b] : null; }

  /** Enter / leave composite mode; entering with no channels set picks the first preset that resolves. */
  setRgbEnabled(on, silent = false) {
    const p = this.p;
    if (!this.hasFile) return false;
    if (!!on === !!p.rgb.enabled && (!on || this.rgbNames)) return false;
    if (on) {
      if (p.diff.enabled) this.setDiff(null, true);
      if (!this.rgbNames) {
        const d = defaultRgb(p.fileInfo, p.band);
        if (!d) { this.flash(); this.opts.emit("error", this, "this file has no three float bands to compose"); return false; }
        [p.rgb.r, p.rgb.g, p.rgb.b] = d;
      }
    }
    p.rgb.enabled = !!on;
    this._afterCompositeChange(silent);
    return true;
  }

  /** Set the channel bands [r, g, b] (float bands of this file) and enter composite mode. */
  setRgb(names, silent = false) {
    const p = this.p;
    if (!this.hasFile || !names || names.length !== 3) return false;
    const isFloat = (n) => { const x = bandByName(p.fileInfo, n); return x && (x.kind === "float" || x.kind === "latlon"); };
    if (!names.every(isFloat)) return false;
    if (p.rgb.enabled && this.rgbNames && this.rgbNames.every((n, i) => n === names[i])) return false;
    if (p.diff.enabled) this.setDiff(null, true);
    [p.rgb.r, p.rgb.g, p.rgb.b] = names;
    p.rgb.enabled = true;
    this._afterCompositeChange(silent);
    return true;
  }

  /** Composite options {shared, gamma}; pane-local, not propagated. */
  setRgbOptions(partial) {
    Object.assign(this.p.rgb, partial);
    this._lastUrl = null;
    this._updateControls(); this.refresh();
  }

  _afterCompositeChange(silent) {
    const p = this.p;
    p.bit = null;
    p.stretch = { ...p.stretch, mode: "auto", vmin: null, vmax: null };
    this.autoRange = { vmin: null, vmax: null, diffVmax: null, ranges: null };
    this._lastUrl = null;
    this._fillChannels(); this._updateControls(); this.refresh();
    if (!silent) this.opts.emit("band", this);
  }

  /** Rebuild the preset and channel selects for the current file / composite. */
  _fillChannels() {
    const p = this.p, info = p.fileInfo;
    clear(this.presetSel);
    for (const ch of ["r", "g", "b"]) clear(this.chSel[ch]);
    if (!info) return;
    for (const pr of RGB_PRESETS) {
      const names = resolveRgbPreset(info, pr, p.band);
      this.presetSel.append(el("option", { value: pr.id, disabled: !names, title: names ? names.join(" / ") : "bands not available in this file" }, pr.label));
    }
    this.presetSel.append(el("option", { value: "custom" }, "custom"));
    this.presetSel.value = rgbPresetOf(info, this.rgbNames, p.band);
    const floats = info.bands.filter((b) => b.kind === "float" || b.kind === "latlon");
    for (const ch of ["r", "g", "b"]) {
      for (const [g, bands] of groupBands(floats)) {
        const og = el("optgroup", { label: g });
        for (const b of bands) og.append(el("option", { value: b.name, title: b.long_name || b.name }, bandLabel(b)));
        this.chSel[ch].append(og);
      }
      if (p.rgb[ch]) this.chSel[ch].value = p.rgb[ch];
    }
  }

  _onPreset(id) {
    if (id === "custom") return;
    const names = resolveRgbPreset(this.p.fileInfo, id, this.p.band);
    if (names) this.setRgb(names);
    else this.presetSel.value = rgbPresetOf(this.p.fileInfo, this.rgbNames, this.p.band);
  }

  _onChannel() { this.setRgb(["r", "g", "b"].map((ch) => this.chSel[ch].value)); }

  /** Pair (or unpair, paneId null) this pane as `this - other`. Leaves composite mode. */
  setDiff(paneId, silent = false) {
    const p = this.p;
    const other = paneId ? this.opts.resolveDiff(paneId) : null;
    if (paneId && !other) paneId = null;
    if (paneId && p.rgb.enabled) { p.rgb.enabled = false; this._lastUrl = null; }
    const was = p.diff.enabled;
    p.diff = { enabled: !!paneId, paneId: paneId || null, fileId: other ? other.fileId : null, band: other ? other.band : null, vmax: p.diff.vmax };
    // diverging map while in diff mode; restore the previous map when leaving it
    if (paneId && !was) { this._cmapBeforeDiff = p.cmap; const dc = this.opts.colormaps.includes("RdBu_r") ? "RdBu_r" : "RdBu"; if (this.opts.colormaps.includes(dc)) { p.cmap = dc; this.cmapSel.value = dc; } }
    if (!paneId && was && this._cmapBeforeDiff && this.opts.colormaps.includes(this._cmapBeforeDiff)) { p.cmap = this._cmapBeforeDiff; this.cmapSel.value = p.cmap; }
    this._lastUrl = null;
    this.diffSel.value = paneId ? String(paneId) : "";
    this._updateHeader(); this._updateControls(); this.refresh();
    if (!silent) this.opts.emit("diff", this);
  }

  /** Rebuild the diff <select> from the other panes ({id, hasFile, space, label}). */
  refreshDiffOptions() {
    const others = this.opts.otherPanes ? this.opts.otherPanes(this) : [];
    const cur = this.p.diff.paneId;
    clear(this.diffSel);
    this.diffSel.append(el("option", { value: "" }, "—"));
    let curOk = false;
    for (const q of others) {
      const ok = q.hasFile && this.space && q.space === this.space && !q.rgb;
      const why = !q.hasFile ? "pane is empty" : q.space !== this.space ? `different space (${q.space})` : q.rgb ? "pane shows an RGB composite" : `${q.label}`;
      this.diffSel.append(el("option", { value: String(q.id), disabled: !ok, title: why }, `pane ${q.id}`));
      if (ok && q.id === cur) curOk = true;
    }
    this._diffBaseDisabled = !this.hasFile || others.every((q) => !(q.hasFile && q.space === this.space && !q.rgb));
    this.diffSel.disabled = this._diffBaseDisabled || this.isRgb;
    this.diffSel.title = this._diffBaseDisabled ? "difference needs another pane with a single band in the same space" : this.isRgb ? "no difference while this pane shows a composite" : "difference: this pane minus another pane";
    if (cur && !curOk) this.setDiff(null, true);
    else { this.diffSel.value = cur ? String(cur) : ""; if (cur) { const o = this.opts.resolveDiff(cur); if (o) { this.p.diff.fileId = o.fileId; this.p.diff.band = o.band; } this._updateHeader(); } }
  }

  /** Set the displayed view (shared object when synced), draw at once, re-render soon. */
  setView(view) {
    this.view = view;
    this.draw();
    this.refresh();
  }

  fitView() {
    if (!this.p.fileInfo || !this.w || !this.h) return null;
    return fitTo(this.p.fileInfo.grid.bounds, this.w, this.h);
  }

  setCrosshair(world) { this.crosshair = world; this.draw(); }
  setPin(world) { this.pin = world; this.draw(); }

  /** Debounced re-render of the visible bbox. */
  refresh() {
    clearTimeout(this._timer);
    this._timer = setTimeout(() => this._render(), RENDER_DEBOUNCE_MS);
    this._updateColorbar();
  }

  // -- rendering ---------------------------------------------------------------
  _renderParams() {
    const p = this.p;
    if (!p.fileInfo || !p.band || !this.view || this.w < 2 || this.h < 2) return null;
    const dpr = window.devicePixelRatio || 1;
    const w = Math.min(MAX_PX, Math.max(1, Math.round(this.w * dpr)));
    const h = Math.min(MAX_PX, Math.max(1, Math.round(this.h * dpr)));
    const bbox = bboxFor(this.view, this.w, this.h);
    const band = this.band;
    if (p.diff.enabled) {
      const other = this.opts.resolveDiff(p.diff.paneId);
      if (!other) return null;
      p.diff.fileId = other.fileId; p.diff.band = other.band;
      return { op: "diff", file: p.fileId, band: p.band, file2: other.fileId, band2: other.band, bbox, w, h, vmax: p.diff.vmax, cmap: p.cmap };
    }
    if (this.isRgb) {
      const [r, g, b] = this.rgbNames;
      const out = { op: "rgb", file: p.fileId, r, g, b, bbox, w, h, shared: p.rgb.shared, gamma: p.rgb.gamma, scale: p.stretch.scale };
      if (p.stretch.mode === "manual") for (const ch of ["r", "g", "b"]) { out[ch + "min"] = p.stretch.vmin; out[ch + "max"] = p.stretch.vmax; }
      return out;
    }
    const kind = band ? band.kind : "float";
    const out = { file: p.fileId, band: p.band, bbox, w, h };
    if (kind === "float" || kind === "latlon") {
      out.cmap = p.cmap;
      if (p.stretch.mode === "manual") { out.vmin = p.stretch.vmin; out.vmax = p.stretch.vmax; }
      if (p.stretch.scale === "log") out.scale = "log";
    } else if (kind === "flags" && p.bit != null) {
      out.bit = p.bit;
    }
    return out;
  }

  async _render() {
    const params = this._renderParams();
    if (!params) return;
    const url = params.op === "rgb" ? renderRgbUrl(params) : renderUrl(params);
    if (url === this._lastUrl && this.img) return;
    if (this._inflight) this._inflight.abort();
    const ctl = (this._inflight = new AbortController());
    this.badgeRender.classList.add("show");
    try {
      const { img, headers } = await loadImage(url, ctl.signal);
      if (ctl !== this._inflight) { img.close(); return; }
      this._dropImage();
      this.img = img; this.imgBbox = params.bbox; this._lastUrl = url;
      if (params.op === "diff") this.autoRange.diffVmax = headers.diffVmax;
      else if (params.op === "rgb") {
        const rg = headers.ranges;
        this.autoRange.ranges = rg;
        if (rg && rg.r && rg.g && rg.b) { this.autoRange.vmin = Math.min(rg.r[0], rg.g[0], rg.b[0]); this.autoRange.vmax = Math.max(rg.r[1], rg.g[1], rg.b[1]); }
      } else { if (headers.vmin != null) this.autoRange.vmin = headers.vmin; if (headers.vmax != null) this.autoRange.vmax = headers.vmax; }
      this._updatePlaceholders(); this._updateColorbar();
      this.draw();
      this.opts.emit("rendered", this, headers);
    } catch (e) {
      if (e && e.name === "AbortError") return;
      this._lastUrl = null;
      this.opts.emit("error", this, e.message || String(e));
    } finally {
      if (ctl === this._inflight) { this._inflight = null; this.badgeRender.classList.remove("show"); }
    }
  }

  _dropImage() { if (this.img && this.img.close) this.img.close(); this.img = null; this.imgBbox = null; }

  /** Draw the last image transformed to the current view, plus overlays. */
  draw() {
    const c = this.canvas, ctx = this.ctx;
    if (!c.width || !c.height) return;
    const dpr = c.width / Math.max(this.w, 1);
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = "#202020";
    ctx.fillRect(0, 0, c.width, c.height);
    if (this.img && this.view && this.imgBbox) {
      const [xmin, ymin, xmax, ymax] = this.imgBbox;
      const tl = worldToScreen(this.view, this.w, this.h, xmin, ymax);
      const br = worldToScreen(this.view, this.w, this.h, xmax, ymin);
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(this.img, tl.px * dpr, tl.py * dpr, (br.px - tl.px) * dpr, (br.py - tl.py) * dpr);
    }
    if (this.view && this.pin) this._drawMarker(this.pin, dpr, true);
    if (this.view && this.crosshair) this._drawMarker(this.crosshair, dpr, false);
  }

  _drawMarker(world, dpr, pinned) {
    const ctx = this.ctx, { px, py } = worldToScreen(this.view, this.w, this.h, world.x, world.y);
    const x = Math.round(px * dpr) + 0.5, y = Math.round(py * dpr) + 0.5;
    ctx.lineWidth = 1;
    if (pinned) {
      ctx.strokeStyle = "rgba(0,0,0,0.8)"; ctx.beginPath(); ctx.arc(x, y, 7 * dpr, 0, Math.PI * 2); ctx.stroke();
      ctx.strokeStyle = "rgba(255,230,0,0.95)"; ctx.beginPath(); ctx.arc(x, y, 6 * dpr, 0, Math.PI * 2); ctx.stroke();
      return;
    }
    const gap = 6 * dpr, W = this.canvas.width, H = this.canvas.height;
    for (const [col, lw] of [["rgba(0,0,0,0.7)", 3], ["rgba(255,255,255,0.8)", 1]]) {
      ctx.strokeStyle = col; ctx.lineWidth = lw; ctx.beginPath();
      ctx.moveTo(0, y); ctx.lineTo(x - gap, y); ctx.moveTo(x + gap, y); ctx.lineTo(W, y);
      ctx.moveTo(x, 0); ctx.lineTo(x, y - gap); ctx.moveTo(x, y + gap); ctx.lineTo(x, H);
      ctx.stroke();
    }
  }

  _onResize() {
    const r = this.body.getBoundingClientRect();
    const w = Math.round(r.width), h = Math.round(r.height);
    if (w < 1 || h < 1) return;
    const dpr = window.devicePixelRatio || 1;
    const pw = Math.min(MAX_PX, Math.round(w * dpr)), ph = Math.min(MAX_PX, Math.round(h * dpr));
    const changed = w !== this.w || h !== this.h || this.canvas.width !== pw || this.canvas.height !== ph;
    this.w = w; this.h = h;
    if (this.canvas.width !== pw) this.canvas.width = pw;
    if (this.canvas.height !== ph) this.canvas.height = ph;
    if (changed) {
      if (this.cbGrad.clientWidth) this._updateColorbar();
      this.draw();
      if (this.view) this.refresh();
      this.opts.emit("resize", this);
    }
  }

  // -- header / controls ---------------------------------------------------------
  _fillBands() {
    clear(this.bandSel);
    for (const [g, bands] of groupBands(this.p.fileInfo.bands)) {
      const og = el("optgroup", { label: g });
      for (const b of bands) og.append(el("option", { value: b.name, title: b.long_name || b.name }, bandLabel(b)));
      this.bandSel.append(og);
    }
    if (this.p.band) this.bandSel.value = this.p.band;
  }

  _updateHeader() {
    const p = this.p;
    if (!p.fileInfo) { this.fileBtn.textContent = "click a file in the catalog"; this.fileBtn.title = "choose a file for this pane"; this.el.classList.remove("has-file"); return; }
    this.el.classList.add("has-file");
    const me = fileLabel(p.entry, p.fileInfo);
    if (p.diff.enabled) {
      const o = this.opts.resolveDiff(p.diff.paneId);
      this.fileBtn.textContent = `${me} − pane ${p.diff.paneId}`;
      this.fileBtn.title = `A − B\nA: ${p.fileInfo.name} : ${p.band}\nB: ${o ? o.name + " : " + o.band : "?"}`;
    } else {
      this.fileBtn.textContent = me;
      this.fileBtn.title = (p.fileInfo.path || p.fileInfo.name) + (this.isRgb ? `\nRGB: ${this.rgbNames.join(" / ")}` : "");
    }
  }

  _updateControls() {
    const p = this.p, band = this.band, kind = band ? band.kind : null;
    const diff = p.diff.enabled, rgb = this.isRgb;
    const isFloat = kind === "float" || kind === "latlon";
    const show = (node, on) => node.classList.toggle("hidden", !on);
    show(this.bandSel, this.hasFile && !rgb);
    show(this.rgbBtn, this.hasFile && !diff);
    this.rgbBtn.classList.toggle("on", rgb);
    show(this.cmapSel, this.hasFile && (isFloat || diff) && !rgb);
    show(this.vminIn, this.hasFile && ((isFloat && !diff) || rgb));
    show(this.vmaxIn, this.hasFile && (isFloat || diff || rgb));
    show(this.autoBtn, this.hasFile && (isFloat || diff || rgb));
    show(this.logBtn, this.hasFile && ((isFloat && !diff) || rgb));
    show(this.bitSel, this.hasFile && kind === "flags" && !diff && !rgb);
    show(this.diffLabel, this.hasFile);
    show(this.closeBtn, this.hasFile);
    show(this.row3, this.hasFile && rgb);
    this.sharedBtn.classList.toggle("on", !!p.rgb.shared);
    this.gammaSel.value = String(p.rgb.gamma);
    this.diffSel.disabled = !!this._diffBaseDisabled || rgb;
    this.logBtn.classList.toggle("on", p.stretch.scale === "log");
    this.vminIn.title = rgb ? "stretch minimum for all three channels (empty = each channel's p2)" : "stretch minimum (empty = auto p2)";
    this.vmaxIn.title = diff ? "symmetric range |A − B| <= vmax (empty = server estimate)" : rgb ? "stretch maximum for all three channels (empty = each channel's p98)" : "stretch maximum (empty = auto p98)";
    if (kind === "flags") {
      clear(this.bitSel);
      this.bitSel.append(el("option", { value: "" }, "count of set bits"));
      const names = band.flag_meanings || [];
      const n = Math.max(names.length, (band.flag_masks || []).length);
      for (let i = 0; i < n; i++) this.bitSel.append(el("option", { value: String(i) }, names[i] || `bit ${i}`));
      this.bitSel.value = p.bit == null ? "" : String(p.bit);
      if (this.bitSel.value !== (p.bit == null ? "" : String(p.bit))) { p.bit = null; this.bitSel.value = ""; }
    }
    this._updatePlaceholders();
  }

  _updatePlaceholders() {
    const p = this.p, a = this.autoRange;
    if (p.diff.enabled) {
      this.vmaxIn.value = p.diff.vmax == null ? "" : String(p.diff.vmax);
      this.vmaxIn.placeholder = a.diffVmax == null ? "auto" : fmtNum(a.diffVmax, 5);
      return;
    }
    this.vminIn.value = p.stretch.vmin == null ? "" : String(p.stretch.vmin);
    this.vmaxIn.value = p.stretch.vmax == null ? "" : String(p.stretch.vmax);
    this.vminIn.placeholder = a.vmin == null ? "min" : fmtNum(a.vmin, 5);
    this.vmaxIn.placeholder = a.vmax == null ? "max" : fmtNum(a.vmax, 5);
  }

  _onBand(name) { this.setBand(name, false); }

  _onStretchInput() {
    const p = this.p;
    if (p.diff.enabled) {
      const v = numOrNull(this.vmaxIn.value);
      p.diff.vmax = v != null && v > 0 ? v : null;
      this._updatePlaceholders(); this.refresh(); this.opts.emit("stretch", this);
      return;
    }
    let vmin = numOrNull(this.vminIn.value), vmax = numOrNull(this.vmaxIn.value);
    if (vmin != null && vmax == null) vmax = this.autoRange.vmax;
    if (vmax != null && vmin == null) vmin = this.autoRange.vmin;
    if (vmin != null && vmax != null && vmax <= vmin) { this.vminIn.classList.add("bad"); setTimeout(() => this.vminIn.classList.remove("bad"), 800); this._updatePlaceholders(); return; }
    this.setStretch({ vmin, vmax }, true);
  }

  _onDiff(v) { this.setDiff(v ? Number(v) : null); }

  /** Current effective range for the colorbar/histogram: manual, else last auto. */
  effectiveRange() {
    const p = this.p;
    if (p.diff.enabled) { const v = p.diff.vmax != null ? p.diff.vmax : this.autoRange.diffVmax; return v == null ? null : { vmin: -v, vmax: v }; }
    const vmin = p.stretch.vmin != null ? p.stretch.vmin : this.autoRange.vmin;
    const vmax = p.stretch.vmax != null ? p.stretch.vmax : this.autoRange.vmax;
    return vmin == null || vmax == null ? null : { vmin, vmax };
  }

  _updateColorbar() {
    const p = this.p, band = this.band, kind = band ? band.kind : null;
    const chips = (items) => { clear(this.cbLegend); for (const it of items) this.cbLegend.append(el("span.chip", { title: it.title || it.label }, el("i", { style: { background: it.color || "transparent" } }), it.label)); };
    const gradient = (name, lo, hi) => { this.cbGrad.classList.remove("hidden"); this.cbLegend.classList.add("hidden"); drawGradient(this.cbGrad, name); this.cbMin.textContent = lo; this.cbMax.textContent = hi; };
    const legend = (items) => { this.cbGrad.classList.add("hidden"); this.cbLegend.classList.remove("hidden"); this.cbMin.textContent = ""; this.cbMax.textContent = ""; chips(items); };
    if (!this.hasFile) { legend([]); return; }
    const r = this.effectiveRange();
    if (p.diff.enabled) { gradient(p.cmap, r ? "−" + fmtNum(r.vmax) : "−vmax", r ? "+" + fmtNum(r.vmax) : "+vmax"); return; }
    if (this.isRgb) {
      const rg = this.autoRange.ranges, keys = ["r", "g", "b"], cols = ["#ff6b6b", "#6fd08a", "#6ab0ff"];
      legend(this.rgbNames.map((n, i) => {
        const rr = rg && rg[keys[i]];
        return { label: rr ? `${n} ${fmtNum(rr[0])}…${fmtNum(rr[1])}` : n, color: cols[i], title: `${keys[i].toUpperCase()}: ${n}${p.rgb.shared ? " (shared range)" : ""}` };
      }));
      return;
    }
    if (kind === "status") { legend(STATUS_CLASSES.map((c) => ({ label: c.name, color: css(c.rgb), title: `pixel_status = ${c.value}` }))); return; }
    if (kind === "flags") {
      if (p.bit != null) { const nm = (band.flag_meanings || [])[p.bit] || `bit ${p.bit}`; legend([{ label: nm + " set", color: css(MASK_RGB) }, { label: "clear", color: "transparent" }]); return; }
      const n = Math.min(12, Math.max(1, (band.flag_masks || band.flag_meanings || []).length || 8));
      const items = [{ label: "0", color: "transparent", title: "no flag set" }];
      for (let i = 1; i <= n; i++) items.push({ label: String(i), color: css(countRgb(i)), title: `${i} flag${i > 1 ? "s" : ""} set` });
      legend(items); return;
    }
    gradient(p.cmap, r ? fmtNum(r.vmin) : (p.stretch.scale === "log" ? "log" : "p2"), r ? fmtNum(r.vmax) : "p98");
  }

  // -- interaction ----------------------------------------------------------------
  _local(e) { const r = this.canvas.getBoundingClientRect(); return { px: e.clientX - r.left, py: e.clientY - r.top }; }

  _emitView(view) { this.opts.emit("view", this, view); }

  _pointerDown(e) {
    // left button: drag to pan, click to pin; middle (wheel) button: drag to pan only
    if ((e.button !== 0 && e.button !== 1) || !this.view) return;
    this.canvas.setPointerCapture(e.pointerId);
    this._drag = { id: e.pointerId, button: e.button, mask: e.button === 1 ? 4 : 1,
                   x: e.clientX, y: e.clientY, sx: e.clientX, sy: e.clientY, moved: false };
    e.preventDefault();
  }

  _pointerMove(e) {
    if (!this.view) return;
    const d = this._drag;
    if (d && d.id === e.pointerId && (e.buttons & d.mask)) {
      const dx = e.clientX - d.x, dy = e.clientY - d.y;
      d.x = e.clientX; d.y = e.clientY;
      if (Math.abs(e.clientX - d.sx) + Math.abs(e.clientY - d.sy) > 3) d.moved = true;
      if (dx || dy) this._emitView(panBy(this.view, dx, dy));
      return;
    }
    const now = performance.now();
    if (now - this._hoverThrottle < 66) { this._hoverPending = e; if (!this._hoverTimer) this._hoverTimer = setTimeout(() => { this._hoverTimer = null; const ev = this._hoverPending; this._hoverPending = null; if (ev) this._emitHover(ev); }, 66 - (now - this._hoverThrottle)); return; }
    this._emitHover(e);
  }

  _emitHover(e) {
    this._hoverThrottle = performance.now();
    if (!this.view) return;
    const { px, py } = this._local(e);
    this.opts.emit("hover", this, screenToWorld(this.view, this.w, this.h, px, py));
  }

  _pointerUp(e) {
    const d = this._drag;
    if (!d || d.id !== e.pointerId) return;
    this._drag = null;
    try { this.canvas.releasePointerCapture(e.pointerId); } catch { /* ignore */ }
    if (d.button === 0 && !d.moved && this.view) {
      const { px, py } = this._local(e);
      const world = screenToWorld(this.view, this.w, this.h, px, py);
      clearTimeout(this._pinTimer);
      this._pinTimer = setTimeout(() => this.opts.emit("pin", this, world), 250);
    }
  }

  _wheel(e) {
    if (!this.view) return;
    e.preventDefault();
    const { px, py } = this._local(e);
    const notches = e.deltaMode === 1 ? e.deltaY / 3 : e.deltaMode === 2 ? e.deltaY : e.deltaY / 100;
    const factor = Math.pow(ZOOM_STEP, -Math.max(-3, Math.min(3, notches || (e.deltaY < 0 ? -1 : 1))));
    this._emitView(zoomAt(this.view, this.w, this.h, px, py, factor));
  }

  _key(e) {
    if (!this.view || e.ctrlKey || e.metaKey || e.altKey) return;
    const t = e.target; if (t && (t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA")) return;
    const v = this.view, cx = this.w / 2, cy = this.h / 2;
    const map = {
      "+": () => zoomAt(v, this.w, this.h, cx, cy, ZOOM_STEP), "=": () => zoomAt(v, this.w, this.h, cx, cy, ZOOM_STEP),
      "-": () => zoomAt(v, this.w, this.h, cx, cy, 1 / ZOOM_STEP), "_": () => zoomAt(v, this.w, this.h, cx, cy, 1 / ZOOM_STEP),
      "0": () => this.fitView(), "f": () => this.fitView(), "F": () => this.fitView(),
      ArrowLeft: () => panBy(v, this.w * 0.1, 0), ArrowRight: () => panBy(v, -this.w * 0.1, 0),
      ArrowUp: () => panBy(v, 0, this.h * 0.1), ArrowDown: () => panBy(v, 0, -this.h * 0.1),
    };
    const fn = map[e.key];
    if (!fn) return;
    const nv = fn();
    if (nv) { e.preventDefault(); this._emitView(nv); }
  }
}
