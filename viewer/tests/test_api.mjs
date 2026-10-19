// node --test viewer/tests/test_api.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { ApiError, describeFailure } from "../static/api.js";

test("describeFailure: the server's detail text is shown verbatim", () => {
  const detail = "unknown file id 'x'; refresh the catalog or use /api/open";
  assert.equal(describeFailure(404, detail, "/api/render?file=x"), detail);
  assert.equal(describeFailure(422, "cannot open file: boom", "/api/files/x/bands"), "cannot open file: boom");
});

test("describeFailure: a bare 404 names the missing route and asks for a restart", () => {
  const url = "http://127.0.0.1:8765/api/render_rgb?file=a&r=1&g=2&b=3&bbox=0,0,1,1&w=2&h=2";
  assert.equal(describeFailure(404, "Not Found", url),
    "the server has no /api/render_rgb route: it is older than this page, restart the viewer");
  assert.match(describeFailure(404, "Not Found", "/api/nope?x=1"), /no \/api\/nope route/);
});

test("describeFailure: without a detail the status line is the message", () => {
  assert.equal(describeFailure(502, null, "/api/catalog", "Bad Gateway"), "502 Bad Gateway");
  assert.equal(describeFailure(500, null, "/api/catalog"), "500 error");
  const e = new ApiError("m", 404, "/u");
  assert.equal(e.status, 404);
  assert.equal(e.name, "ApiError");
});
