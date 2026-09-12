import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import { join, relative } from "node:path";
import test from "node:test";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const packageRoot = fileURLToPath(new URL("../", import.meta.url));
const sourceRoot = join(packageRoot, "src");
const readSource = (path) => readFile(join(sourceRoot, path), "utf8");

async function pathsBelow(directory, root = directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const paths = [];
  for (const entry of entries) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) paths.push(...await pathsBelow(path, root));
    else paths.push(relative(root, path));
  }
  return paths.sort();
}

function loadUmd(source, globalName) {
  const context = vm.createContext({ URL });
  vm.runInContext(source, context);
  return context[globalName];
}

test("capture policy auto-attaches only direct or same-origin eligible PDFs", async () => {
  const policy = loadUmd(await readSource("capture-policy.js"), "SixSentencesCapturePolicy");
  assert.deepEqual({ ...policy.captureRoute({
    sourceKind: "paper",
    url: "https://papers.example/paper",
    pdfUrl: "https://papers.example/paper.pdf",
  }) }, { route: "paper", uploadPdf: true, detectedPdf: true, unattachedPdf: false });
  assert.equal(policy.captureRoute({
    sourceKind: "paper",
    url: "https://papers.example/paper",
    pdfUrl: "https://files.example/paper.pdf",
  }).uploadPdf, false);
  assert.equal(policy.captureRoute({
    sourceKind: "web",
    url: "https://papers.example/page",
    pdfUrl: "https://papers.example/file.pdf",
  }).uploadPdf, false);
});

test("capture results accept only bounded known outcomes and warning codes", async () => {
  const result = loadUmd(await readSource("capture-result.js"), "SixSentencesCaptureResult");
  assert.equal(result.normalizeCaptureResult({ status: "duplicate" }).status, "already_saved");
  assert.throws(() => result.normalizeCaptureResult({ status: "synthetic_unknown" }), /unknown save result/);
  const copy = result.captureResultCopy({
    status: "created",
    warnings: ["synthetic private diagnostics"],
  }, "paper");
  assert.doesNotMatch(copy.detail, /private diagnostics/);
});

test("worker preserves pairing, capture, PDF, and revoke community contracts", async () => {
  const worker = await readSource("service-worker.js");
  for (const route of [
    "/browser-capture/pair/exchange",
    "/browser-capture/web",
    "/browser-capture/citations",
    "/browser-capture/papers",
    "/browser-capture/device/revoke",
  ]) assert.match(worker, new RegExp(route.replaceAll("/", "\\/")));
  assert.match(worker, /launchWebAuthFlow/);
  assert.match(worker, /code_challenge: challenge/);
  assert.match(worker, /returned\.searchParams\.get\("state"\) !== state/);
});

test("privileged credentials stay in trusted worker storage", async () => {
  const worker = await readSource("service-worker.js");
  const content = await readSource("content-script.js");
  const popup = await readSource("popup.js");
  assert.match(worker, /setAccessLevel\(\{ accessLevel: "TRUSTED_CONTEXTS" \}\)/);
  assert.match(worker, /!TRUSTED_CONTEXTS\.has\(sender\.id\) \|\| sender\.tab/);
  assert.match(worker, /chrome\.storage\.local\.set\(\{ apiKey: body\.api_key \}\)/);
  assert.doesNotMatch(`${content}\n${popup}`, /storage\.local|Authorization:\s*`Bearer/);
});

test("PDF transfer is bounded, verifies magic, and rejects redirects", async () => {
  const worker = await readSource("service-worker.js");
  const fetcher = await readSource("pdf-fetcher.js");
  assert.match(worker, /MAX_PDF_BYTES = 50 \* 1024 \* 1024/);
  assert.match(worker, /redirect: "error"/);
  assert.match(worker, /decode\(content\.subarray\(0, 5\)\) !== "%PDF-"/);
  assert.match(fetcher, /MAX_PDF_BYTES = 50 \* 1024 \* 1024/);
  assert.match(fetcher, /credentials: "same-origin"/);
  assert.match(fetcher, /redirect: "error"/);
});

test("manifest template is keyless and has no deployment permission", async () => {
  const manifest = JSON.parse(await readSource("manifest.template.json"));
  assert.equal(manifest.manifest_version, 3);
  assert.equal(Object.hasOwn(manifest, "key"), false);
  assert.deepEqual(manifest.host_permissions, []);
  assert.deepEqual(manifest.permissions.sort(), ["activeTab", "identity", "scripting", "storage"]);
  assert.equal(Object.hasOwn(manifest, "content_scripts"), false);
});

test("community source contains no hosted endpoint or commercial gating copy", async () => {
  const textFiles = (await pathsBelow(packageRoot)).filter((path) =>
    /\.(?:js|mjs|json|md|html|css)$/.test(path) || ["LICENSE", "NOTICE"].includes(path));
  const source = (await Promise.all(textFiles.map((path) => readFile(join(packageRoot, path), "utf8")))).join("\n");
  assert.doesNotMatch(source, /(?:app|api)\.sixsentences\.com/i);
  const commercialTerms = [
    `feature_not_${"in_plan"}`,
    `upgrade_${"required"}`,
    `bill${"ing"}`,
    `check${"out"}`,
    `subscr${"iption"}`,
    `pric${"ing"}`,
    `entitle${"ment"}`,
  ];
  assert.doesNotMatch(source, new RegExp(commercialTerms.join("|"), "i"));
  assert.doesNotMatch(source, /BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY/);
  assert.equal(source.includes(`<all_${"urls"}>`), false);
});

test("snapshot contains no store package, screenshots, archives, or build output", async () => {
  const paths = await pathsBelow(packageRoot);
  assert.equal(paths.some((path) => /(?:^|\/)store(?:\/|$)/i.test(path)), false);
  assert.equal(paths.some((path) => /screenshot|researchgate.*fixture|arxiv.*fixture/i.test(path)), false);
  assert.equal(paths.some((path) => /\.(?:zip|crx|pem|key)$/i.test(path)), false);
  assert.equal(paths.some((path) => /(?:^|\/)dist(?:\/|$)|(?:^|\/)build(?:\/|$)/i.test(path)), false);
});
