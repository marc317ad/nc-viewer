// meta.js - right drawer with two tabs for the focused pane: Metadata (file
// block, dims, filterable global attrs, coords/variables with attrs and
// encoding) and Band stats (numbers, 64-bin histogram with the current
// vmin/vmax drawn as lines, "Use p2-p98" / "Use min-max" buttons).

import { el, clear, fmtNum, fmtBytes } from "./util.js";
import { getMeta, getStats } from "./api.js";

const LONG = 120;

export class MetaDrawer {
  constructor(root, handlers) {
    this.root = root;
    this.h = handlers;              // {onUseRange(vmin, vmax), onError(msg)}
    this.metaCache = new Map();     // fileId -> meta
    this.statsCache = new Map();    // fileId:band -> stats
    this.cur = { fileId: null, band: null, range: null, kind: null };
    this.tab = "meta";
    this.filter = "";
    this._build();
  }

  _build() {
    this.tabMeta = el("button.tab.on", { onclick: () => this.setTab("meta") }, "Metadata");
    this.tabStats = el("button.tab", { onclick: () => this.setTab("stats") }, "Band stats");
    this.metaEl = el("div.meta-body");
    this.statsEl = el("div.meta-body.hidden");
    this.root.append(el("div.meta-tabs", null, this.tabMeta, this.tabStats), this.metaEl, this.statsEl);
    this.metaEl.append(el("div.meta-hint", null, "select a pane with a file"));
    this.statsEl.append(el("div.meta-hint", null, "select a pane with a file"));
  }

  setTab(name) {
    this.tab = name;
    this.tabMeta.classList.toggle("on", name === "meta");
    this.tabStats.classList.toggle("on", name === "stats");
    this.metaEl.classList.toggle("hidden", name !== "meta");
    this.statsEl.classList.toggle("hidden", name !== "stats");
    if (name === "stats") this._drawHist();
  }

  /** Show file + band of the focused pane; `range` = {vmin, vmax} or null for the histogram lines. */
  show(fileId, band, range, kind) {
    const fileChanged = fileId !== this.cur.fileId;
    const bandChanged = fileChanged || band !== this.cur.band;
    this.cur = { fileId, band, range, kind };
    if (!fileId) {
      clear(this.metaEl).append(el("div.meta-hint", null, "select a pane with a file"));
      clear(this.statsEl).append(el("div.meta-hint", null, "select a pane with a file"));
      return;
    }
    if (fileChanged) this._loadMeta(fileId);
    if (bandChanged) this._loadStats(fileId, band);
    else this._drawHist();
  }

  setRange(range) { this.cur.range = range; this._drawHist(); }

  // -- metadata -----------------------------------------------------------------
  async _loadMeta(fileId) {
    let meta = this.metaCache.get(fileId);
    if (!meta) {
      clear(this.metaEl).append(el("div.meta-hint", null, "loading metadata…"));
      try { meta = await getMeta(fileId); this.metaCache.set(fileId, meta); }
      catch (e) { if (this.cur.fileId === fileId) clear(this.metaEl).append(el("div.meta-hint.error", null, e.message)); this.h.onError && this.h.onError(e.message); return; }
    }
    if (this.cur.fileId === fileId) this._renderMeta(meta);
  }

  _renderMeta(meta) {
    clear(this.metaEl);
    const f = meta.file || {};
    const filterIn = el("input.meta-filter", { type: "search", placeholder: "filter attributes and variables", value: this.filter, oninput: () => { this.filter = filterIn.value.trim().toLowerCase(); this._renderMeta(meta); } });
    const q = this.filter;
    const hit = (k, v) => !q || `${k} ${v}`.toLowerCase().includes(q);
    this.metaEl.append(
      el("div.meta-file", null, el("div.meta-name", { title: f.path || "" }, f.name || ""),
        el("div.meta-sub", null, `${fmtBytes(f.size_bytes)}  ${f.mtime ? String(f.mtime).replace("T", " ").slice(0, 19) : ""}`),
        el("div.meta-sub.mono", { title: f.path || "" }, f.path || "")),
      el("div.meta-section", null, "dims: ", Object.entries(meta.dims || {}).map(([k, v]) => `${k}=${v}`).join(", ") || "—"),
      filterIn,
    );
    const attrs = Object.entries(meta.global_attrs || {}).filter(([k, v]) => hit(k, v));
    this.metaEl.append(el("div.meta-section", null, `global attributes (${attrs.length}${q ? " matching" : ""})`));
    this.metaEl.append(this._attrTable(attrs));
    for (const [title, list] of [["coordinates", meta.coords || []], ["variables", meta.variables || []]]) {
      const vars = list.filter((v) => !q || hit(v.name, Object.entries(v.attrs || {}).map(([k, x]) => `${k} ${x}`).join(" ")));
      this.metaEl.append(el("div.meta-section", null, `${title} (${vars.length})`));
      for (const v of vars) {
        const d = el("details.var", { open: !!q && vars.length <= 5 });
        d.append(el("summary", null, el("span.mono", null, v.name), el("span.meta-sub", null, ` ${v.dtype || ""} (${(v.dims || []).join(", ")}) ${(v.shape || []).join("×")}`)));
        d.append(this._attrTable(Object.entries(v.attrs || {})));
        const enc = Object.entries(v.encoding || {}).filter(([, x]) => x != null);
        if (enc.length) d.append(el("div.meta-sub", null, "encoding: " + enc.map(([k, x]) => `${k}=${Array.isArray(x) ? "[" + x.join(",") + "]" : x}`).join("  ")));
        this.metaEl.append(d);
      }
    }
  }

  _attrTable(entries) {
    const t = el("table.kv");
    for (const [k, v] of entries) {
      const s = v == null ? "" : String(v);
      const td = el("td.v");
      if (s.length > LONG) {
        const short = el("span", null, s.slice(0, LONG) + "… ");
        const more = el("a.more", { href: "#", onclick: (e) => { e.preventDefault(); clear(td).append(el("span.long", null, s)); } }, `more (${s.length})`);
        td.append(short, more);
      } else td.textContent = s;
      t.append(el("tr", null, el("td.k", { title: k }, k), td));
    }
    if (!entries.length) t.append(el("tr", null, el("td.k", null, "—"), el("td.v", null, "")));
    return t;
  }

  // -- band stats --------------------------------------------------------------------
  async _loadStats(fileId, band) {
    const key = `${fileId}:${band}`;
    let st = this.statsCache.get(key);
    if (!band) { clear(this.statsEl).append(el("div.meta-hint", null, "no band")); return; }
    if (!st) {
      clear(this.statsEl).append(el("div.meta-hint", null, `computing stats for ${band}…`));
      try { st = await getStats(fileId, band); this.statsCache.set(key, st); }
      catch (e) { if (this.cur.fileId === fileId && this.cur.band === band) clear(this.statsEl).append(el("div.meta-hint.error", null, e.message)); this.h.onError && this.h.onError(e.message); return; }
    }
    if (this.cur.fileId === fileId && this.cur.band === band) this._renderStats(st);
  }

  _renderStats(st) {
    clear(this.statsEl);
    this.stats = st;
    const kind = st.kind || this.cur.kind || "float";
    this.statsEl.append(el("div.meta-section", null, `${st.band || this.cur.band}  (${kind}${st.sampled ? ", sampled" : ""})`));
    const rows = [];
    const put = (k, v, f = fmtNum) => { if (v !== undefined && v !== null) rows.push([k, typeof v === "number" ? f(v) : String(v)]); };
    const int = (v) => (typeof v === "number" ? v.toLocaleString() : v);
    put("total", st.count_total != null ? st.count_total : st.count, int);
    put("valid", st.count_valid != null ? st.count_valid : st.valid, int);
    if (st.nan_count != null) put("nan", st.nan_count, int);
    else if (st.count_total != null && st.count_valid != null) put("invalid", st.count_total - st.count_valid, int);
    for (const k of ["min", "max", "mean", "std", "p1", "p2", "p5", "p50", "p95", "p98", "p99"]) put(k, st[k], (v) => fmtNum(v, 6));
    const t = el("table.kv");
    for (const [k, v] of rows) t.append(el("tr", null, el("td.k", null, k), el("td.v", null, v)));
    this.statsEl.append(t);
    // flags / status counts (any of the shapes the server may send)
    const fc = st.flag_counts || (st.counts && typeof st.counts === "object" ? st.counts : null) ||
      (Array.isArray(st.flags) ? Object.fromEntries(st.flags.map((f) => [f.name, f.count])) : null);
    if (fc) {
      const ft = el("table.kv");
      const tot = st.count_total || st.count || 0;
      for (const [k, v] of Object.entries(fc)) ft.append(el("tr", null, el("td.k", null, k), el("td.v", null, `${int(v)}${tot ? ` (${(100 * v / tot).toFixed(1)}%)` : ""}`)));
      this.statsEl.append(el("div.meta-section", null, "flag counts"), ft);
    }
    if (st.hist && st.hist.counts && st.hist.edges) {
      this.histCanvas = el("canvas.hist", { title: "64-bin histogram between p1 and p99; lines mark the current stretch" });
      this.statsEl.append(this.histCanvas, el("div.hist-axis", null, el("span", null, fmtNum(st.hist.edges[0])), el("span", null, fmtNum(st.hist.edges[st.hist.edges.length - 1]))));
      const btns = el("div.meta-btns");
      if (st.p2 != null && st.p98 != null) btns.append(el("button", { onclick: () => this.h.onUseRange(st.p2, st.p98) }, "Use p2–p98"));
      if (st.min != null && st.max != null) btns.append(el("button", { onclick: () => this.h.onUseRange(st.min, st.max) }, "Use min–max"));
      if (st.p5 != null && st.p95 != null) btns.append(el("button", { onclick: () => this.h.onUseRange(st.p5, st.p95) }, "Use p5–p95"));
      this.statsEl.append(btns);
      this._drawHist();
    } else {
      this.histCanvas = null;
    }
  }

  _drawHist() {
    const c = this.histCanvas, st = this.stats;
    if (!c || !st || !st.hist || !c.isConnected || this.tab !== "stats") return;
    const dpr = window.devicePixelRatio || 1;
    const W = Math.max(50, c.clientWidth), H = Math.max(40, c.clientHeight);
    c.width = Math.round(W * dpr); c.height = Math.round(H * dpr);
    const ctx = c.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const counts = st.hist.counts, edges = st.hist.edges;
    const n = counts.length, max = Math.max(1, ...counts);
    const x0 = edges[0], x1 = edges[edges.length - 1], span = x1 - x0 || 1;
    ctx.fillStyle = "#6aa0d8";
    for (let i = 0; i < n; i++) {
      const bx = ((edges[i] - x0) / span) * W, bw = Math.max(1, ((edges[i + 1] - edges[i]) / span) * W - 0.5);
      const bh = (counts[i] / max) * (H - 4);
      ctx.fillRect(bx, H - bh, bw, bh);
    }
    const r = this.cur.range;
    if (r) {
      for (const [v, col] of [[r.vmin, "#ffd54a"], [r.vmax, "#ff7a59"]]) {
        if (v == null || !Number.isFinite(v)) continue;
        const x = Math.min(W - 1, Math.max(0, ((v - x0) / span) * W));
        ctx.strokeStyle = col; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.moveTo(x + 0.5, 0); ctx.lineTo(x + 0.5, H); ctx.stroke();
      }
    }
  }
}
