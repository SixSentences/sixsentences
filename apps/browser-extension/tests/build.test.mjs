import assert from "node:assert/strict";
import { createHash, generateKeyPairSync } from "node:crypto";
import {
  mkdtemp,
  readFile,
  readdir,
  stat,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, relative } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

const packageRoot = fileURLToPath(new URL("../", import.meta.url));
const buildScript = join(packageRoot, "scripts/build.mjs");

async function filesBelow(root, directory = root) {
  const entries = await readdir(directory, { withFileTypes: true });
  const paths = [];
  for (const entry of entries) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) paths.push(...await filesBelow(root, path));
    else paths.push(relative(root, path));
  }
  return paths.sort();
}

async function treeDigest(root) {
  const hash = createHash("sha256");
  for (const path of await filesBelow(root)) {
    hash.update(path).update("\0").update(await readFile(join(root, path))).update("\0");
  }
  return hash.digest("hex");
}

function build(output, overrides = {}) {
  return spawnSync(process.execPath, [buildScript, "--out", output], {
    cwd: packageRoot,
    encoding: "utf8",
    env: {
      ...process.env,
      APP_ORIGIN: "https://research.example.org",
      API_ORIGIN: "https://research.example.org/api",
      SIX_EXTENSION_ALLOW_INSECURE_LOOPBACK: "false",
      SIX_EXTENSION_PUBLIC_KEY: "",
      ...overrides,
    },
  });
}

test("two clean builds are byte-for-byte deterministic", async () => {
  const parent = await mkdtemp(join(tmpdir(), "sixsentences-extension-build-"));
  const first = join(parent, "first");
  const second = join(parent, "second");
  assert.equal(build(first).status, 0);
  assert.equal(build(second).status, 0);
  assert.equal(await treeDigest(first), await treeDigest(second));
  assert.deepEqual(await filesBelow(first), await filesBelow(second));
});

test("generated manifest is MV3 and grants only its configured API host", async () => {
  const parent = await mkdtemp(join(tmpdir(), "sixsentences-extension-manifest-"));
  const output = join(parent, "unpacked");
  assert.equal(build(output).status, 0);
  const manifest = JSON.parse(await readFile(join(output, "manifest.json"), "utf8"));
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual(manifest.permissions.sort(), ["activeTab", "identity", "scripting", "storage"]);
  assert.deepEqual(manifest.host_permissions, ["https://research.example.org/*"]);
  assert.equal(Object.hasOwn(manifest, "key"), false);
  assert.equal(JSON.stringify(manifest).includes(`<all_${"urls"}>`), false);
  assert.equal(manifest.background.type, "module");
  assert.equal(manifest.content_security_policy.extension_pages, "script-src 'self'; object-src 'none'");
});

test("generated runtime binds the worker to exact deployment base URLs", async () => {
  const parent = await mkdtemp(join(tmpdir(), "sixsentences-extension-config-"));
  const output = join(parent, "unpacked");
  assert.equal(build(output).status, 0);
  const config = await readFile(join(output, "config.js"), "utf8");
  const worker = await readFile(join(output, "service-worker.js"), "utf8");
  assert.equal(config.includes('export const APP_ORIGIN = "https://research.example.org";'), true);
  assert.equal(config.includes('export const API_ORIGIN = "https://research.example.org/api";'), true);
  assert.match(worker, /^import \{ API_ORIGIN, APP_ORIGIN \} from "\.\/config\.js";/);
  const runtime = `${config}\n${worker}`.toLowerCase();
  assert.equal(runtime.includes(["app", "sixsentences", "com"].join(".")), false);
  assert.equal(runtime.includes(["api", "sixsentences", "com"].join(".")), false);
});

test("optional operator public key is present only when explicitly supplied", async () => {
  const { publicKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
  const encoded = publicKey.export({ type: "spki", format: "der" }).toString("base64");
  const parent = await mkdtemp(join(tmpdir(), "sixsentences-extension-key-"));
  const output = join(parent, "unpacked");
  assert.equal(build(output, { SIX_EXTENSION_PUBLIC_KEY: encoded }).status, 0);
  const manifest = JSON.parse(await readFile(join(output, "manifest.json"), "utf8"));
  assert.equal(manifest.key, encoded);
});

test("build refuses invalid configuration before creating output", async () => {
  const parent = await mkdtemp(join(tmpdir(), "sixsentences-extension-invalid-"));
  const output = join(parent, "unpacked");
  const credentialedUrl = [
    "https://",
    "synthetic-user",
    ":",
    "synthetic-pass",
    "@research.example.org/api",
  ].join("");
  const result = build(output, { API_ORIGIN: credentialedUrl });
  assert.notEqual(result.status, 0);
  await assert.rejects(stat(output), { code: "ENOENT" });
});

test("build never overwrites an existing output directory", async () => {
  const parent = await mkdtemp(join(tmpdir(), "sixsentences-extension-existing-"));
  const output = join(parent, "unpacked");
  await stat(parent);
  await writeFile(output, "sentinel", "utf8");
  const result = build(output);
  assert.notEqual(result.status, 0);
  assert.equal(await readFile(output, "utf8"), "sentinel");
});

test("build output contains reviewed runtime plus license and notice only", async () => {
  const parent = await mkdtemp(join(tmpdir(), "sixsentences-extension-inventory-"));
  const output = join(parent, "unpacked");
  assert.equal(build(output).status, 0);
  assert.deepEqual(await filesBelow(output), [
    "BUILD-INFO.json",
    "LICENSE",
    "NOTICE",
    "capture-policy.js",
    "capture-result.js",
    "config.js",
    "content-script.js",
    "icons/icon-128.png",
    "icons/icon-16.png",
    "icons/icon-32.png",
    "icons/icon-48.png",
    "manifest.json",
    "pdf-fetcher.js",
    "popup.css",
    "popup.html",
    "popup.js",
    "service-worker.js",
  ]);
  const inventory = JSON.parse(await readFile(join(output, "BUILD-INFO.json"), "utf8"));
  assert.equal(inventory.schema_version, 1);
  assert.equal(inventory.stable_public_key_supplied, false);
  assert.equal(inventory.reviewed_sources.length, 14);
});
