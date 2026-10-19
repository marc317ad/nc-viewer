// colorbar.js - colours for the colorbar strips and legends. At boot app.js
// calls configurePalettes() with the /api/colormaps payload so the strips use
// the server's exact 256-entry LUTs and flag palettes; the STOPS below are a
// fallback that only matters if that request fails. Images themselves are
// always coloured by the server.

const STOPS = {
  gray: [[0, 0, 0], [255, 255, 255]],
  viridis: [[68, 1, 84], [72, 40, 120], [62, 74, 137], [49, 104, 142], [38, 130, 142], [31, 158, 137], [53, 183, 121], [109, 205, 89], [180, 222, 44], [253, 231, 37]],
  magma: [[0, 0, 4], [28, 16, 68], [79, 18, 123], [129, 37, 129], [181, 54, 122], [229, 80, 100], [251, 135, 97], [254, 194, 135], [252, 253, 191]],
  inferno: [[0, 0, 4], [31, 12, 72], [85, 15, 109], [136, 34, 106], [186, 54, 85], [227, 89, 51], [249, 140, 10], [249, 201, 50], [252, 255, 164]],
  plasma: [[13, 8, 135], [75, 3, 161], [125, 3, 168], [168, 34, 150], [203, 70, 121], [229, 107, 93], [248, 148, 65], [253, 195, 40], [240, 249, 33]],
  turbo: [[48, 18, 59], [70, 107, 227], [40, 187, 209], [53, 237, 131], [164, 252, 59], [246, 210, 44], [250, 133, 20], [205, 54, 4], [122, 4, 3]],
  RdBu: [[103, 0, 31], [178, 24, 43], [214, 96, 77], [244, 165, 130], [253, 219, 199], [247, 247, 247], [209, 229, 240], [146, 197, 222], [67, 147, 195], [33, 102, 172], [5, 48, 97]],
  Spectral: [[158, 1, 66], [213, 62, 79], [244, 109, 67], [253, 174, 97], [254, 224, 139], [255, 255, 191], [230, 245, 152], [171, 221, 164], [102, 194, 165], [50, 136, 189], [94, 79, 162]],
  coolwarm: [[59, 76, 192], [98, 130, 234], [141, 176, 254], [184, 208, 249], [221, 221, 221], [245, 196, 173], [244, 154, 123], [222, 96, 77], [180, 4, 38]],
};
STOPS.RdBu_r = STOPS.RdBu.slice().reverse();

/** Exact server LUTs (name -> 256 x [r,g,b]) once configurePalettes() ran. */
const LUTS = {};

/** pixel_status palette (mirrors server colormaps.py STATUS_COLOURS). */
export const STATUS_CLASSES = [
  { value: 1, name: "value", rgb: [60, 180, 75] },
  { value: 2, name: "outside_aoi", rgb: [70, 70, 70] },
  { value: 4, name: "flagged", rgb: [245, 130, 48] },
  { value: 8, name: "no_data", rgb: [240, 50, 230] },
];
/** Bit-mask view: colour of a set bit (server BIT_SET). Mutated in place by configurePalettes. */
export const MASK_RGB = [255, 80, 80];
/** Count view: colour of 1..8 set bits (server COUNT_RAMP, clamped beyond). */
const COUNT_RAMP = [[81, 18, 124], [121, 34, 130], [163, 48, 126], [205, 64, 113], [240, 95, 94], [252, 142, 100], [254, 191, 132], [252, 240, 178]];

/** Adopt the server's palettes from the /api/colormaps payload (tolerates missing parts). */
export function configurePalettes(payload) {
  if (!payload || typeof payload !== "object") return;
  const luts = payload.luts || {};
  for (const [name, table] of Object.entries(luts)) {
    if (Array.isArray(table) && table.length === 256) LUTS[name] = table;
  }
  const pal = payload.palettes || {};
  if (Array.isArray(pal.status)) {
    for (const row of pal.status) {
      const c = STATUS_CLASSES.find((s) => s.value === row.value);
      if (c && Array.isArray(row.rgb)) { c.rgb = row.rgb.slice(0, 3); if (row.name) c.name = row.name; }
    }
  }
  if (pal.mask && Array.isArray(pal.mask.set)) for (let k = 0; k < 3; k++) MASK_RGB[k] = pal.mask.set[k];
  if (pal.count && Array.isArray(pal.count.ramp) && pal.count.ramp.length) {
    COUNT_RAMP.length = 0;
    for (const rgb of pal.count.ramp) COUNT_RAMP.push(rgb.slice(0, 3));
  }
}

/** RGB of colormap `name` at t in [0, 1]; unknown names fall back to gray. */
export function cmapRgb(name, t) {
  const tt = Math.min(1, Math.max(0, t));
  const lut = LUTS[name];
  if (lut) return lut[Math.round(tt * 255)].slice(0, 3);
  const stops = STOPS[name] || STOPS.gray;
  const x = tt * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(x));
  const f = x - i;
  const a = stops[i], b = stops[i + 1];
  return [0, 1, 2].map((k) => Math.round(a[k] + (b[k] - a[k]) * f));
}

/** Colour for n set bits in the "count" view (null for 0 = translucent grey on the server). */
export function countRgb(n) {
  if (n <= 0) return null;
  return COUNT_RAMP[Math.min(n, COUNT_RAMP.length) - 1];
}

export const css = (rgb) => `rgb(${rgb[0]},${rgb[1]},${rgb[2]})`;

/** Draw a horizontal gradient of `name` on `canvas` (sized by CSS; uses its client size). */
export function drawGradient(canvas, name) {
  const w = Math.max(2, canvas.clientWidth | 0), h = Math.max(2, canvas.clientHeight | 0);
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  const ctx = canvas.getContext("2d");
  const img = ctx.createImageData(w, 1);
  for (let x = 0; x < w; x++) {
    const [r, g, b] = cmapRgb(name, x / (w - 1));
    img.data.set([r, g, b, 255], x * 4);
  }
  for (let y = 0; y < h; y++) ctx.putImageData(img, 0, y);
}
