// util.js - tiny DOM and formatting helpers shared by the other modules.

/** Create an element: el('button.cls#id', {title: 'x', onclick: fn}, 'text', childEl, ...). */
export function el(spec, props, ...children) {
  const m = /^([a-z0-9]+)?((?:[.#][\w-]+)*)$/i.exec(spec) || [];
  const node = document.createElement(m[1] || "div");
  for (const tok of (m[2] || "").match(/[.#][\w-]+/g) || []) {
    if (tok[0] === ".") node.classList.add(tok.slice(1));
    else node.id = tok.slice(1);
  }
  if (props && typeof props === "object" && !(props instanceof Node)) {
    for (const [k, v] of Object.entries(props)) {
      if (v == null) continue;
      if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
      else if (k === "dataset") Object.assign(node.dataset, v);
      else if (k === "style" && typeof v === "object") Object.assign(node.style, v);
      else if (k in node && k !== "list") { try { node[k] = v; } catch { node.setAttribute(k, v); } }
      else node.setAttribute(k, v);
    }
  } else if (props != null) {
    children.unshift(props);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

/** Remove all children. */
export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

/** Compact number formatting for labels (4 significant digits, no trailing zeros). */
export function fmtNum(v, sig = 4) {
  if (v == null || Number.isNaN(v)) return "—";
  if (typeof v !== "number") return String(v);
  if (v === 0) return "0";
  const a = Math.abs(v);
  if (a >= 1e9 || a < 1e-4) return v.toExponential(sig - 1).replace(/\.?0+e/, "e");
  if (a >= 10 ** sig) return v.toFixed(0);
  const s = v.toPrecision(sig);
  return s.includes(".") ? s.replace(/\.?0+$/, "") : s;
}

/** Coordinates: metres to 1 decimal, pixel units to 2 decimals. */
export function fmtCoord(v, units) {
  if (v == null || Number.isNaN(v)) return "—";
  return units === "px" ? v.toFixed(2) : units === "deg" ? v.toFixed(5) : v.toFixed(1);
}

export function fmtBytes(n) {
  if (n == null) return "";
  if (n >= 1 << 30) return (n / (1 << 30)).toFixed(2) + " GB";
  if (n >= 1 << 20) return (n / (1 << 20)).toFixed(1) + " MB";
  if (n >= 1024) return (n / 1024).toFixed(0) + " kB";
  return n + " B";
}

/** Trailing debounce. */
export function debounce(fn, ms) {
  let t = null;
  const d = (...args) => { clearTimeout(t); t = setTimeout(() => { t = null; fn(...args); }, ms); };
  d.cancel = () => { clearTimeout(t); t = null; };
  return d;
}

/** Leading+trailing throttle at most once per `ms`. */
export function throttle(fn, ms) {
  let last = 0, t = null, pending = null;
  return (...args) => {
    const now = performance.now();
    pending = args;
    if (now - last >= ms) {
      last = now; pending = null; fn(...args);
    } else if (!t) {
      t = setTimeout(() => { t = null; last = performance.now(); const a = pending; pending = null; if (a) fn(...a); }, ms - (now - last));
    }
  };
}

/** Parse a number from an input; null when empty or invalid. */
export function numOrNull(s) {
  if (s == null) return null;
  const t = String(s).trim();
  if (t === "") return null;
  const v = Number(t);
  return Number.isFinite(v) ? v : null;
}

/** Is the keyboard event aimed at a text field (so global shortcuts must not fire)? */
export function inTextField(e) {
  const t = e.target;
  return t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
}
