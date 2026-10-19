// viewport.js - pure viewport math, no DOM (unit-tested by tests/test_viewport.mjs).
//
// A view is {cx, cy, mpp}: world centre and world units per CSS pixel. World
// y points UP (north-up projected grids; pixel space uses Y = -row), screen y
// points down. Screen coordinates (px, py) are CSS pixels from the canvas
// top-left; (w, h) is the canvas size in CSS pixels.

/** World bbox [xmin, ymin, xmax, ymax] shown by a w x h canvas. */
export function bboxFor(view, w, h) {
  const hw = (w * view.mpp) / 2;
  const hh = (h * view.mpp) / 2;
  return [view.cx - hw, view.cy - hh, view.cx + hw, view.cy + hh];
}

/** View that shows all of `bounds` in a w x h canvas with `pad` (fraction) margin. */
export function fitTo(bounds, w, h, pad = 0.02) {
  const [xmin, ymin, xmax, ymax] = bounds;
  const bw = Math.max(xmax - xmin, 1e-9);
  const bh = Math.max(ymax - ymin, 1e-9);
  const mpp = Math.max(bw / Math.max(w, 1), bh / Math.max(h, 1)) * (1 + 2 * pad);
  return { cx: (xmin + xmax) / 2, cy: (ymin + ymax) / 2, mpp };
}

/** World point under screen pixel (px, py). */
export function screenToWorld(view, w, h, px, py) {
  return {
    x: view.cx + (px - w / 2) * view.mpp,
    y: view.cy - (py - h / 2) * view.mpp,
  };
}

/** Screen pixel of world point (X, Y). */
export function worldToScreen(view, w, h, X, Y) {
  return {
    px: w / 2 + (X - view.cx) / view.mpp,
    py: h / 2 - (Y - view.cy) / view.mpp,
  };
}

/** Zoom by `factor` (> 1 zooms in) keeping the world point under (px, py) fixed. */
export function zoomAt(view, w, h, px, py, factor) {
  const anchor = screenToWorld(view, w, h, px, py);
  const mpp = view.mpp / factor;
  return {
    cx: anchor.x - (px - w / 2) * mpp,
    cy: anchor.y + (py - h / 2) * mpp,
    mpp,
  };
}

/**
 * Pan by a pointer drag of (dxPx, dyPx) CSS pixels: the content follows the
 * pointer, so the centre moves the opposite way (dragging right shows what
 * was to the left).
 */
export function panBy(view, dxPx, dyPx) {
  return { cx: view.cx - dxPx * view.mpp, cy: view.cy + dyPx * view.mpp, mpp: view.mpp };
}

/** Integer (row, col) of the grid pixel containing world point (X, Y). */
export function rowColAt(grid, X, Y) {
  return {
    col: Math.floor((X - grid.x0) / grid.dx),
    row: Math.floor((Y - grid.y0) / grid.dy),
  };
}

/** World bbox of grid pixel (row, col) as [xmin, ymin, xmax, ymax]. */
export function pixelBbox(grid, row, col) {
  const x0 = grid.x0 + col * grid.dx;
  const y0 = grid.y0 + row * grid.dy; // top edge (dy < 0)
  const y1 = y0 + grid.dy;
  return [x0, Math.min(y0, y1), x0 + grid.dx, Math.max(y0, y1)];
}

/** True when two views are equal within floating tolerance. */
export function sameView(a, b, eps = 1e-9) {
  if (!a || !b) return a === b;
  return Math.abs(a.cx - b.cx) <= eps * Math.max(1, Math.abs(a.cx)) &&
    Math.abs(a.cy - b.cy) <= eps * Math.max(1, Math.abs(a.cy)) &&
    Math.abs(a.mpp - b.mpp) <= eps * Math.max(1, Math.abs(a.mpp));
}
