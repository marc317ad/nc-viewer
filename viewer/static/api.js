// api.js - fetch wrappers for every endpoint of plan section 4. Every failure
// becomes an ApiError whose .message is the server's {"detail"} text (or a
// network message), so callers can show it verbatim.

export class ApiError extends Error {
  constructor(message, status, url) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.url = url;
  }
}

async function detailOf(resp) {
  let detail = null;
  try {
    const j = await resp.json();
    if (j && typeof j.detail === "string") detail = j.detail;
    else if (j && j.detail) detail = JSON.stringify(j.detail);
  } catch { /* not JSON */ }
  return describeFailure(resp.status, detail, resp.url, resp.statusText);
}

/**
 * Message for a failed response: the server's detail text when it sent one.
 * A bare 404 "Not Found" is FastAPI's answer for a route that does not exist,
 * i.e. the running server predates this page (the code changed while it ran);
 * say so, because the status bar is the only place the user will see it.
 */
export function describeFailure(status, detail, url, statusText = "") {
  if (status === 404 && detail === "Not Found") {
    let route = url || "";
    try { route = new URL(url, "http://localhost").pathname; } catch { /* keep the url */ }
    return `the server has no ${route} route: it is older than this page, restart the viewer`;
  }
  if (detail) return detail;
  return `${status} ${statusText || "error"}`;
}

async function getJson(url, signal) {
  let resp;
  try {
    resp = await fetch(url, { signal, headers: { Accept: "application/json" } });
  } catch (e) {
    if (e && e.name === "AbortError") throw e;
    throw new ApiError(`server unreachable (${e && e.message ? e.message : e})`, 0, url);
  }
  if (!resp.ok) throw new ApiError(await detailOf(resp), resp.status, url);
  try {
    return await resp.json();
  } catch (e) {
    throw new ApiError(`bad JSON from ${url}: ${e.message}`, resp.status, url);
  }
}

const q = (params) => {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    s.set(k, Array.isArray(v) ? v.map(String).join(",") : String(v));
  }
  return s.toString();
};

export const getConfig = (signal) => getJson("/api/config", signal);
export const getColormaps = (signal) => getJson("/api/colormaps", signal);
export const getCatalog = (refresh = false, signal) => getJson(`/api/catalog?refresh=${refresh ? 1 : 0}`, signal);
export const openPath = (path, signal) => getJson(`/api/open?${q({ path })}`, signal);
export const getBands = (id, signal) => getJson(`/api/files/${encodeURIComponent(id)}/bands`, signal);
export const getMeta = (id, signal) => getJson(`/api/files/${encodeURIComponent(id)}/meta`, signal);
export const getStats = (id, band, signal) =>
  getJson(`/api/files/${encodeURIComponent(id)}/bands/${encodeURIComponent(band)}/stats`, signal);
export const getValue = (id, band, x, y, signal) =>
  getJson(`/api/files/${encodeURIComponent(id)}/value?${q({ band, x, y })}`, signal);

/**
 * Build a /api/render URL. `bbox` is [xmin, ymin, xmax, ymax] and is written
 * with full double precision (String(n) is the shortest round-trip form);
 * undefined/null params are omitted.
 */
export function renderUrl({ file, band, bbox, w, h, op, file2, band2, vmin, vmax, cmap, scale, bit }) {
  return "/api/render?" + q({
    op: op && op !== "band" ? op : undefined,
    file, band,
    file2, band2,
    bbox: bbox.map((v) => String(v)),
    w: Math.round(w), h: Math.round(h),
    vmin, vmax, cmap,
    scale: scale && scale !== "linear" ? scale : undefined,
    bit,
  });
}

/**
 * Build a /api/render_rgb URL (false-colour composite of three bands of one
 * file). Per-channel bounds (rmin..bmax) are omitted when null (server uses
 * p2..p98); `shared` sends 1; `gamma` is omitted when 1.
 */
export function renderRgbUrl({ file, r, g, b, bbox, w, h, rmin, rmax, gmin, gmax, bmin, bmax, shared, scale, gamma }) {
  return "/api/render_rgb?" + q({
    file, r, g, b,
    bbox: bbox.map((v) => String(v)),
    w: Math.round(w), h: Math.round(h),
    rmin, rmax, gmin, gmax, bmin, bmax,
    shared: shared ? 1 : undefined,
    scale: scale && scale !== "linear" ? scale : undefined,
    gamma: gamma != null && Number(gamma) !== 1 ? gamma : undefined,
  });
}

/**
 * Fetch a render PNG -> {img: ImageBitmap, headers: {vmin, vmax, diffVmax, renderMs, ranges}}.
 * `ranges` is {r: [lo, hi], g, b} for a composite (X-Range-* headers), else null.
 * Throws ApiError with the server detail on non-2xx; rethrows AbortError.
 */
export async function loadImage(url, signal) {
  let resp;
  try {
    resp = await fetch(url, { signal });
  } catch (e) {
    if (e && e.name === "AbortError") throw e;
    throw new ApiError(`server unreachable (${e && e.message ? e.message : e})`, 0, url);
  }
  if (!resp.ok) throw new ApiError(await detailOf(resp), resp.status, url);
  const blob = await resp.blob();
  const num = (k) => { const v = resp.headers.get(k); return v == null || v === "" ? null : Number(v); };
  const pair = (k) => {
    const v = resp.headers.get(k);
    if (!v) return null;
    const a = v.split(",").map(Number);
    return a.length === 2 && a.every(Number.isFinite) ? a : null;
  };
  const headers = {
    vmin: num("X-Vmin"), vmax: num("X-Vmax"), diffVmax: num("X-Diff-Vmax"), renderMs: num("X-Render-Ms"),
    ranges: pair("X-Range-R") ? { r: pair("X-Range-R"), g: pair("X-Range-G"), b: pair("X-Range-B") } : null,
  };
  let img;
  try {
    img = await createImageBitmap(blob);
  } catch (e) {
    throw new ApiError(`render returned an undecodable image: ${e.message}`, resp.status, url);
  }
  return { img, headers };
}
