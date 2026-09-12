import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

test("account export requires reauthentication and keeps secrets out of the URL", () => {
  const apiSource = readFileSync(join(process.cwd(), "src/lib/api.ts"), "utf8");
  const settingsSource = readFileSync(
    join(process.cwd(), "src/components/settings/account-settings.tsx"),
    "utf8",
  );
  const exportFunction = apiSource.slice(
    apiSource.indexOf("export async function downloadAccountExport"),
    apiSource.indexOf("/** Render the SVG", apiSource.indexOf("downloadAccountExport")),
  );

  assert.match(exportFunction, /\/auth\/account\/export/);
  assert.match(exportFunction, /method: "POST"/);
  assert.match(exportFunction, /body: JSON\.stringify\(\{ password \}\)/);
  assert.doesNotMatch(exportFunction, /\?[^\n]*password/);
  assert.match(exportFunction, /cache: "no-store"/);
  assert.match(settingsSource, /method="post"/);
  assert.match(settingsSource, /downloadAccountExport\(exportPassword\)/);
});
