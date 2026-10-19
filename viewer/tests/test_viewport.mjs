// node --test viewer/tests/test_viewport.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  bboxFor, fitTo, zoomAt, panBy, screenToWorld, worldToScreen, rowColAt, pixelBbox, sameView,
} from "../static/viewport.js";

const close = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) <= eps, `${a} != ${b}`);

test("bboxFor is symmetric around the centre, y up", () => {
  const v = { cx: 600000, cy: 3000000, mpp: 20 };
  const [xmin, ymin, xmax, ymax] = bboxFor(v, 800, 600);
  close(xmin, 600000 - 8000);
  close(xmax, 600000 + 8000);
  close(ymin, 3000000 - 6000);
  close(ymax, 3000000 + 6000);
  close(xmax - xmin, 800 * 20);
  close(ymax - ymin, 600 * 20);
});

test("fitTo covers the bounds with padding and centres them", () => {
  const bounds = [583860, 2890000, 641240, 2999990];
  const w = 700, h = 500;
  const v = fitTo(bounds, w, h, 0.02);
  const [xmin, ymin, xmax, ymax] = bboxFor(v, w, h);
  assert.ok(xmin <= bounds[0] && xmax >= bounds[2], "x covered");
  assert.ok(ymin <= bounds[1] && ymax >= bounds[3], "y covered");
  close(v.cx, (bounds[0] + bounds[2]) / 2);
  close(v.cy, (bounds[1] + bounds[3]) / 2);
  // limiting axis gets exactly the padding
  const bw = bounds[2] - bounds[0], bh = bounds[3] - bounds[1];
  const limiting = Math.max(bw / w, bh / h);
  close(v.mpp, limiting * 1.04, 1e-9);
});

test("zoomAt keeps the world point under the cursor fixed", () => {
  const v = { cx: 100, cy: 200, mpp: 2 };
  const w = 400, h = 300, px = 50, py = 280;
  const before = screenToWorld(v, w, h, px, py);
  const z = zoomAt(v, w, h, px, py, 1.25);
  const after = screenToWorld(z, w, h, px, py);
  close(after.x, before.x);
  close(after.y, before.y);
  close(z.mpp, 2 / 1.25);
  // zooming out again with the inverse factor restores the view
  const back = zoomAt(z, w, h, px, py, 1 / 1.25);
  assert.ok(sameView(back, v, 1e-9));
});

test("zoomAt around the canvas centre keeps the centre", () => {
  const v = { cx: 10, cy: -20, mpp: 5 };
  const z = zoomAt(v, 200, 100, 100, 50, 2);
  close(z.cx, 10);
  close(z.cy, -20);
  close(z.mpp, 2.5);
});

test("panBy moves the centre opposite to the drag by d*mpp", () => {
  const v = { cx: 0, cy: 0, mpp: 3 };
  const p = panBy(v, 10, -4);
  close(p.cx, -30);   // dragged right -> centre moves left
  close(p.cy, -12);   // dragged up on screen (dy<0) -> centre moves down in world
  close(p.mpp, 3);
});

test("screenToWorld and worldToScreen round-trip", () => {
  const v = { cx: 583870.5, cy: 2999990.25, mpp: 13.7 };
  const w = 1013, h = 777;
  for (const [px, py] of [[0, 0], [w, h], [333.3, 12.5], [w / 2, h / 2]]) {
    const { x, y } = screenToWorld(v, w, h, px, py);
    const s = worldToScreen(v, w, h, x, y);
    close(s.px, px, 1e-7);
    close(s.py, py, 1e-7);
  }
  // screen top-left is world (xmin, ymax)
  const [xmin, , , ymax] = bboxFor(v, w, h);
  const tl = screenToWorld(v, w, h, 0, 0);
  close(tl.x, xmin);
  close(tl.y, ymax);
});

test("pixel space convention: world Y = -row, row 0 at the top", () => {
  const grid = { x0: 0, dx: 1, y0: 0, dy: -1, ny: 10, nx: 20, bounds: [0, -10, 20, 0] };
  assert.deepEqual(rowColAt(grid, 0.5, -0.5), { row: 0, col: 0 });
  assert.deepEqual(rowColAt(grid, 3.2, -1), { row: 1, col: 3 });    // -1 is in (-2, -1]
  assert.deepEqual(rowColAt(grid, 19.9, -9.9), { row: 9, col: 19 });
  assert.deepEqual(rowColAt(grid, -0.1, 0.1), { row: -1, col: -1 }); // above/left of the grid
  // fitting the bounds puts row 0 at the top of the screen
  const v = fitTo(grid.bounds, 200, 100, 0);
  const top = screenToWorld(v, 200, 100, 100, 0);
  assert.ok(top.y >= -1e-9, "top of screen is world Y >= 0 (row 0)");
  assert.deepEqual(pixelBbox(grid, 2, 5), [5, -3, 6, -2]);
});

test("projected grid: col/row from outer edges with dy < 0", () => {
  const grid = { x0: 583860, dx: 20, y0: 3000000, dy: -20, ny: 100, nx: 100 };
  assert.deepEqual(rowColAt(grid, 583870, 2999990), { row: 0, col: 0 });  // first centre
  assert.deepEqual(rowColAt(grid, 583860 + 20 * 7 + 1, 3000000 - 20 * 3 - 1), { row: 3, col: 7 });
  const bb = pixelBbox(grid, 3, 7);
  close(bb[0], 583860 + 140); close(bb[2], 583860 + 160);
  close(bb[1], 3000000 - 80); close(bb[3], 3000000 - 60);
});
