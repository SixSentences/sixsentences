import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import test from "node:test";

function sourceFiles(root) {
  return readdirSync(root, { withFileTypes: true }).flatMap((entry) => {
    const path = join(root, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    return /\.(?:tsx|jsx)$/.test(entry.name) ? [path] : [];
  });
}

test("every browser form fails safe to POST when JavaScript is unavailable", () => {
  const root = join(process.cwd(), "src");
  const missing = [];
  for (const file of sourceFiles(root)) {
    const source = readFileSync(file, "utf8");
    for (const match of source.matchAll(/<(?:motion\.)?form\b[^>]*>/gs)) {
      if (!/\bmethod=["']post["']/.test(match[0])) {
        const line = source.slice(0, match.index).split("\n").length;
        missing.push(`${relative(process.cwd(), file)}:${line}`);
      }
    }
  }
  assert.deepEqual(
    missing,
    [],
    `Forms without method="post":\n${missing.join("\n")}`,
  );
});
