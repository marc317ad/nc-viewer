// node --test viewer/tests/test_state.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  createState, serializeHash, parseHash,
  RGB_PRESETS, resolveRgbPreset, rgbPresetOf, defaultRgb, mapRgbByMatchKey, spectralFamilies,
} from "../static/state.js";

const band = (name, wl, fam = "Rrs", kind = "float") => ({
  name, kind, wavelength: wl, match_key: wl ? `${fam}@${wl}` : name,
  group: kind === "float" ? "Reflectance" : "Flags",
});
const dsf = { id: "a", bands: [
  band("Rrs_B02", 492), band("Rrs_B03", 560), band("Rrs_B04", 665), band("Rrs_B8A", 865), band("Rrs_B12", 2202),
  band("pixel_status", null, null, "status"),
] };
const c2r = { id: "b", bands: [
  band("rrs_B2", 492), band("rrs_B3", 560), band("rrs_B4", 665), band("rrs_B8", 833),
  band("rhown_B4", 665, "rhow"), band("rhown_B3", 560, "rhow"), band("rhown_B2", 492, "rhow"),
  band("conc_chl", null),
] };

test("presets resolve within one spectral family with fallbacks", () => {
  assert.deepEqual(resolveRgbPreset(dsf, "true"), ["Rrs_B04", "Rrs_B03", "Rrs_B02"]);
  assert.deepEqual(resolveRgbPreset(dsf, "nir"), ["Rrs_B8A", "Rrs_B04", "Rrs_B03"]);
  assert.deepEqual(resolveRgbPreset(dsf, "swir"), ["Rrs_B12", "Rrs_B8A", "Rrs_B04"]);
  assert.equal(resolveRgbPreset(dsf, "rededge"), null);
  assert.deepEqual(resolveRgbPreset(c2r, "nir"), ["rrs_B8", "rrs_B4", "rrs_B3"]);   // 833 fallback
  assert.equal(resolveRgbPreset(c2r, "swir"), null);
  assert.equal(resolveRgbPreset(dsf, "nope"), null);
  assert.equal(resolveRgbPreset(null, "true"), null);
});

test("the preferred band's family wins; families are listed in rank order", () => {
  assert.deepEqual(spectralFamilies(c2r, "rhown_B4").slice(0, 2), ["rhow", "Rrs"]);
  assert.deepEqual(resolveRgbPreset(c2r, "true", "rhown_B4"), ["rhown_B4", "rhown_B3", "rhown_B2"]);
  assert.deepEqual(resolveRgbPreset(c2r, "true"), ["rrs_B4", "rrs_B3", "rrs_B2"]);
});

test("rgbPresetOf recognises a preset's channels and reports custom otherwise", () => {
  assert.equal(rgbPresetOf(dsf, ["Rrs_B04", "Rrs_B03", "Rrs_B02"]), "true");
  assert.equal(rgbPresetOf(dsf, ["Rrs_B8A", "Rrs_B04", "Rrs_B03"]), "nir");
  assert.equal(rgbPresetOf(dsf, ["Rrs_B03", "Rrs_B04", "Rrs_B02"]), "custom");
  assert.equal(rgbPresetOf(dsf, null), "custom");
  assert.ok(RGB_PRESETS.every((p) => p.channels.length === 3));
});

test("defaultRgb takes the first preset that resolves, else three float bands, else null", () => {
  assert.deepEqual(defaultRgb(dsf), ["Rrs_B04", "Rrs_B03", "Rrs_B02"]);
  const odd = { bands: [band("a", null), band("b", null), band("c", null), band("f", null, null, "flags")] };
  assert.deepEqual(defaultRgb(odd), ["c", "b", "a"]);
  assert.equal(defaultRgb({ bands: [band("a", null), band("b", null)] }), null);
});

test("mapRgbByMatchKey carries a composite to another product by wavelength", () => {
  assert.deepEqual(mapRgbByMatchKey(dsf, ["Rrs_B04", "Rrs_B03", "Rrs_B02"], c2r), ["rrs_B4", "rrs_B3", "rrs_B2"]);
  assert.equal(mapRgbByMatchKey(dsf, ["Rrs_B12", "Rrs_B8A", "Rrs_B04"], c2r), null);
  assert.equal(mapRgbByMatchKey(dsf, ["Rrs_B04"], c2r), null);
});

test("hash round-trips single bands and rgb composites", () => {
  const s = createState();
  s.layout = 2;
  Object.assign(s.panes[0], { fileId: "abc123", band: "Rrs_B03" });
  Object.assign(s.panes[1], { fileId: "def456", band: "rrs_B3", rgb: { enabled: true, r: "rrs_B4", g: "rrs_B3", b: "rrs_B2", shared: false, gamma: 1 } });
  const h = serializeHash(s);
  const p = parseHash(h);
  assert.equal(p.layout, 2);
  assert.deepEqual(p.panes[1], { fileId: "abc123", band: "Rrs_B03" });
  assert.deepEqual(p.panes[2], { fileId: "def456", band: null, rgb: ["rrs_B4", "rrs_B3", "rrs_B2"] });
  // an rgb pane that is switched off serialises its single band again
  s.panes[1].rgb.enabled = false;
  assert.deepEqual(parseHash(serializeHash(s)).panes[2], { fileId: "def456", band: "rrs_B3" });
});

test("parseHash tolerates malformed rgb fields", () => {
  assert.deepEqual(parseHash("#p1=abc:rgb(a,b)").panes[1], { fileId: "abc", band: null });
  assert.deepEqual(parseHash("#p1=abc:rgb()").panes[1], { fileId: "abc", band: null });
  assert.deepEqual(parseHash("#p1=abc:rgb(a,,c)").panes[1], { fileId: "abc", band: null });
  assert.equal(parseHash("#p1=bad%20id:rgb(a,b,c)").panes[1], undefined);
  assert.deepEqual(parseHash("#p1=abc:plain").panes[1], { fileId: "abc", band: "plain" });
});
