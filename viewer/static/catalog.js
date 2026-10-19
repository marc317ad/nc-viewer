// catalog.js - left drawer: roots summary, filter box, run -> tile -> scene ->
// file tree with role badges, per-scene Compare buttons, Refresh and
// "Open path". Expanded state lives in memory only.

import { el, clear, fmtBytes } from "./util.js";

const ROLE_ORDER = ["DSFOPT", "C2RCCOPT", "L2W", "C2RCC", "C2RCCCANON", "C2RCCCANON_optimized", "L2W_optimized", "DSFALIGN", "ACSWITCH"];
const roleRank = (r) => { const i = ROLE_ORDER.indexOf(r); return i < 0 ? 99 : i; };
export const roleClass = (r) => (ROLE_ORDER.includes(r) ? `role-${r.replace(/_optimized$/, "")}` : "role-other");

/** Group FileEntry[] into runs -> tiles -> scenes (pure; exported for reuse). */
export function buildTree(files) {
  const runs = new Map();
  for (const f of files) {
    const runKey = `${f.root_label || ""}/${f.run || ""}`;
    if (!runs.has(runKey)) runs.set(runKey, { key: runKey, run: f.run || "(root)", root: f.root_label || "", tiles: new Map() });
    const run = runs.get(runKey);
    const tileKey = f.tile || "(no tile)";
    if (!run.tiles.has(tileKey)) run.tiles.set(tileKey, { key: `${runKey}/${tileKey}`, tile: tileKey, scenes: new Map() });
    const tile = run.tiles.get(tileKey);
    const sk = f.scene_key || f.name;
    if (!tile.scenes.has(sk)) tile.scenes.set(sk, { key: `${tileKey}/${sk}`, scene: sk, datetime: f.datetime || "", date: f.date, platform: f.platform, files: [] });
    tile.scenes.get(sk).files.push(f);
  }
  const out = [...runs.values()].sort((a, b) => a.run.localeCompare(b.run) || a.root.localeCompare(b.root));
  for (const run of out) {
    run.tiles = [...run.tiles.values()].sort((a, b) => a.tile.localeCompare(b.tile));
    for (const tile of run.tiles) {
      tile.scenes = [...tile.scenes.values()].sort((a, b) => (b.datetime || "").localeCompare(a.datetime || "") || a.scene.localeCompare(b.scene));
      for (const s of tile.scenes) s.files.sort((a, b) => roleRank(a.role) - roleRank(b.role) || a.name.localeCompare(b.name));
    }
  }
  return out;
}

/** The two files Compare opens: DSFOPT + C2RCCOPT when both exist, else the first two. */
export function comparePair(files) {
  const a = files.find((f) => f.role === "DSFOPT"), b = files.find((f) => f.role === "C2RCCOPT");
  if (a && b) return [a, b];
  return files.slice(0, 2);
}

export class CatalogView {
  constructor(root, handlers) {
    this.root = root;
    this.h = handlers;          // {onOpen(entry, {shift}), onCompare(files), onRefresh(), onOpenPath(path)}
    this.data = { roots: [], files: [], scanned_at: null };
    this.expanded = new Set();
    this.filter = "";
    this.target = 1;
    this._build();
  }

  _build() {
    this.targetEl = el("span.cat-target", { title: "files open in this pane (click a pane to change)" }, "→ pane 1");
    this.refreshBtn = el("button", { title: "rescan the data roots", onclick: () => this.h.onRefresh() }, "Refresh");
    this.filterIn = el("input.cat-filter", { type: "search", placeholder: "filter: name, run, tile, role, date", oninput: () => { this.filter = this.filterIn.value.trim().toLowerCase(); this.renderTree(); } });
    this.pathIn = el("input.cat-path", { type: "text", placeholder: "open path… (absolute .nc path)", title: "open any NetCDF file by absolute path", onkeydown: (e) => { if (e.key === "Enter") this._openPath(); } });
    this.pathBtn = el("button", { title: "open the path above in the target pane", onclick: () => this._openPath() }, "Open");
    this.rootsEl = el("div.cat-roots");
    this.treeEl = el("div.cat-tree");
    this.statusEl = el("div.cat-status");
    this.root.append(
      el("div.cat-head", null, el("strong", null, "Files"), this.targetEl, this.refreshBtn),
      this.filterIn,
      el("div.cat-open", null, this.pathIn, this.pathBtn),
      this.rootsEl,
      this.treeEl,
      this.statusEl,
    );
  }

  _openPath() {
    const p = this.pathIn.value.trim();
    if (p) this.h.onOpenPath(p);
  }

  setTarget(paneId) { this.target = paneId; this.targetEl.textContent = `→ pane ${paneId}`; }
  setStatus(text, isError = false) { this.statusEl.textContent = text || ""; this.statusEl.classList.toggle("error", !!isError); }
  focusFilter() { this.filterIn.focus(); this.filterIn.select(); }

  setData(data) {
    this.data = { roots: data.roots || [], files: data.files || [], scanned_at: data.scanned_at || null };
    this.tree = buildTree(this.data.files);
    if (this.expanded.size === 0 && this.tree.length) {
      this.expanded.add(this.tree[0].key);
      if (this.tree[0].tiles.length) this.expanded.add(this.tree[0].tiles[0].key);
    }
    this.renderRoots();
    this.renderTree();
    const n = this.data.files.length;
    this.setStatus(n ? `${n} file${n === 1 ? "" : "s"}${this.data.scanned_at ? ", scanned " + String(this.data.scanned_at).replace("T", " ").slice(0, 19) : ""}` : "no NetCDF files found in the roots");
  }

  /** Add a single entry (from /api/open) without a rescan. */
  addEntry(entry) {
    if (!this.data.files.some((f) => f.id === entry.id)) this.data.files.push(entry);
    this.tree = buildTree(this.data.files);
    const run = this.tree.find((r) => r.root === (entry.root_label || "") && r.run === (entry.run || "(root)"));
    if (run) { this.expanded.add(run.key); const t = run.tiles.find((t) => t.tile === (entry.tile || "(no tile)")); if (t) { this.expanded.add(t.key); } }
    this.renderTree();
  }

  renderRoots() {
    clear(this.rootsEl);
    for (const r of this.data.roots) {
      const missing = r.exists === false;
      this.rootsEl.append(el("div.cat-root" + (missing ? ".missing" : ""), { title: r.path },
        el("span.cat-root-label", null, r.label || r.path),
        el("span.cat-root-n", null, missing ? "missing" : `${r.n_files != null ? r.n_files : "?"} files`)));
    }
  }

  _matches(f) {
    if (!this.filter) return true;
    const hay = `${f.name} ${f.run || ""} ${f.tile || ""} ${f.role || ""} ${f.date || ""} ${f.root_label || ""}`.toLowerCase();
    return this.filter.split(/\s+/).every((tok) => hay.includes(tok));
  }

  renderTree() {
    clear(this.treeEl);
    const filtering = !!this.filter;
    let shown = 0;
    for (const run of this.tree || []) {
      const tiles = [];
      for (const tile of run.tiles) {
        const scenes = [];
        for (const scene of tile.scenes) {
          const files = scene.files.filter((f) => this._matches(f));
          if (files.length) scenes.push({ scene, files });
        }
        if (scenes.length) tiles.push({ tile, scenes });
      }
      if (!tiles.length) continue;
      const runOpen = filtering || this.expanded.has(run.key);
      const runEl = el("div.node.node-run" + (runOpen ? ".open" : ""));
      runEl.append(this._toggleRow(run.key, runEl, el("span.node-label", null, run.run), el("span.node-sub", null, run.root), el("span.node-count", null, String(tiles.reduce((n, t) => n + t.scenes.length, 0)))));
      const runKids = el("div.kids");
      for (const { tile, scenes } of tiles) {
        const tileOpen = filtering || this.expanded.has(tile.key);
        const tileEl = el("div.node.node-tile" + (tileOpen ? ".open" : ""));
        tileEl.append(this._toggleRow(tile.key, tileEl, el("span.node-label", null, tile.tile), el("span.node-count", null, String(scenes.length))));
        const tileKids = el("div.kids");
        for (const { scene, files } of scenes) {
          const sceneOpen = filtering || this.expanded.has(scene.key) || tile.scenes.length === 1;
          const sceneEl = el("div.node.node-scene" + (sceneOpen ? ".open" : ""));
          const when = scene.datetime ? scene.datetime.replace("T", " ").slice(0, 16) : scene.scene;
          const row = this._toggleRow(scene.key, sceneEl, el("span.node-label", null, `${scene.platform ? scene.platform + " " : ""}${when}`));
          if (scene.files.length >= 2) {
            row.append(el("button.btn-compare", { title: "open DSFOPT in pane 1 and C2RCCOPT in pane 2 (or the first two files), layout 2, synced", onclick: (e) => { e.stopPropagation(); this.h.onCompare(comparePair(scene.files), scene); } }, "Compare"));
          }
          sceneEl.append(row);
          const fileKids = el("div.kids");
          for (const f of files) {
            shown++;
            fileKids.append(el("div.file-row", {
              title: `${f.path}\n${fmtBytes(f.size_bytes)}  ${f.mtime || ""}\nclick: open in pane ${this.target}; shift-click: next pane`,
              dataset: { id: f.id },
              onclick: (e) => this.h.onOpen(f, { shift: e.shiftKey }),
            }, el("span.role-badge." + roleClass(f.role), null, f.role), el("span.file-name", null, f.tile ? f.role === "other" ? f.name : "" : f.name), el("span.file-size", null, fmtBytes(f.size_bytes))));
          }
          sceneEl.append(fileKids);
          tileKids.append(sceneEl);
        }
        tileEl.append(tileKids);
        runKids.append(tileEl);
      }
      runEl.append(runKids);
      this.treeEl.append(runEl);
    }
    if (!shown) this.treeEl.append(el("div.cat-empty", null, filtering ? "no files match the filter" : "no files; check the roots above or use Open path"));
  }

  _toggleRow(key, node, ...content) {
    const row = el("div.node-row", {
      onclick: () => {
        if (this.filter) return; // everything is expanded while filtering
        if (this.expanded.has(key)) this.expanded.delete(key); else this.expanded.add(key);
        node.classList.toggle("open", this.expanded.has(key));
      },
    }, el("span.caret"), ...content);
    return row;
  }

  /** Highlight the files currently open (ids -> pane numbers). */
  markOpen(idsToPanes) {
    for (const row of this.treeEl.querySelectorAll(".file-row")) {
      const n = idsToPanes.get(row.dataset.id);
      row.classList.toggle("open-in-pane", !!n);
      row.dataset.panes = n ? n.join(",") : "";
    }
  }
}
