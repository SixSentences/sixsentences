import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const configUrl = new URL("../next.config.ts", import.meta.url);

test("the app owns browser headers without duplicating proxy HSTS", async () => {
  const source = await readFile(configUrl, "utf8");

  assert.match(source, /poweredByHeader:\s*false/);
  assert.match(source, /X-Content-Type-Options/);
  assert.match(source, /X-Frame-Options/);
  assert.match(source, /Content-Security-Policy/);
  assert.match(source, /Permissions-Policy/);
  assert.doesNotMatch(source, /Strict-Transport-Security/);
});
