import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const pagePath = new URL("../src/app/(app)/data/[id]/page.tsx", import.meta.url);

test("dataset workspace has one integrated information architecture", async () => {
  const source = await readFile(pagePath, "utf8");

  assert.doesNotMatch(source, /setView\(|view === "studio"|\["studio", BarChart3/);
  assert.doesNotMatch(source, /Dices|rollChart|Surprise me/);
  assert.match(source, /aria-labelledby="dataset-overview-heading"/);
  assert.match(source, /aria-labelledby="schema-heading"/);
  assert.match(source, /aria-labelledby="context-heading"/);
  assert.match(source, /aria-labelledby="versions-heading"/);
  assert.match(source, /aria-labelledby="chart-heading"/);
});

test("integrated workspace preserves real dataset operations", async () => {
  const source = await readFile(pagePath, "utf8");

  assert.match(source, /api\.datasetVersionAdd\(datasetId/);
  assert.match(source, /versionInputRef\.current\?\.click\(\)/);
  assert.match(source, /api\.datasetUpdate\(datasetId/);
  assert.match(source, /setProfileEditing\(false\)/);
  assert.match(source, /setEditDescription\(dataset\.description\)/);
  assert.match(source, /api\.datasetChart\(datasetId/);
  assert.match(source, /chart\.mutate\(\{/);
  assert.match(source, /chartX === chartY/);
  assert.match(source, /href=\{`\/figures\?dataset=\$\{dataset\.public_id\}`\}/);
  assert.match(source, /dataset\.preview\.slice\(0, 12\)/);
  assert.match(source, /dataset\.columns\.map/);
  assert.match(source, /datasetChatStream/);
});

test("dataset workspace is keyboard-labelled and bilingual", async () => {
  const source = await readFile(pagePath, "utf8");

  for (const control of [
    "dataset-description",
    "dataset-provenance",
    "dataset-license",
    "chart-kind",
    "chart-x",
    "chart-y",
    "chart-title",
  ]) {
    assert.match(source, new RegExp(`htmlFor="${control}"`));
    assert.match(source, new RegExp(`id="${control}"`));
  }
  assert.match(source, /<caption className="sr-only">/);
  assert.match(source, /<th scope="col"/);
  assert.match(source, /<th scope="row"/);
  assert.match(source, /href="\/data" aria-label=\{t\("Zurück zum Data Hub", "Back to Data Hub"\)\}/);
  assert.match(source, /const isGerman = me\?\.language === "de"/);
  assert.match(source, /const t = \(de: string, en: string\)/);
  assert.match(source, /"Schema und Datenqualität", "Schema and data quality"/);
  assert.match(source, /"Versionsverlauf", "Version history"/);
});

const dataPage = ts.transpileModule(
  await readFile(new URL("../src/app/(app)/data/page.tsx", import.meta.url), "utf8"),
  {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  },
).outputText;

function nodePath(node, predicate) {
  if (!node || typeof node !== "object") return null;
  if (predicate(node)) return [node];
  for (const child of [node.props?.children].flat(Infinity)) {
    const path = nodePath(child, predicate);
    if (path) return [node, ...path];
  }
  return null;
}

function findNode(tree, predicate) {
  const path = nodePath(tree, predicate);
  assert.ok(path, "expected control in the rendered component");
  return path.at(-1);
}

function nodeText(node) {
  if (typeof node === "string") return node;
  return [node?.props?.children].flat(Infinity).map((child) => (
    typeof child === "object" || typeof child === "string" ? nodeText(child) : ""
  )).join("");
}

// Run the real page and its handlers with synthetic files and in-memory hooks.
// React portal events follow the component ancestry, which dispatchDrag models.
function createDataPageHarness() {
  const slots = [];
  const mutationSlots = [];
  const calls = [];
  const errors = [];
  let cursor = 0;
  let mutationCursor = 0;
  const react = {
    useMemo: (callback) => callback(),
    useRef(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = { current: initial };
      return slots[index];
    },
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = initial;
      return [slots[index], (value) => {
        slots[index] = typeof value === "function" ? value(slots[index]) : value;
      }];
    },
  };
  const jsx = (type, props) => ({ type, props });
  const api = {
    async datasetCreate(filename, content, options) {
      calls.push({ kind: "create", filename, content, options });
      return { public_id: "created-fixture" };
    },
    async datasetVersionAdd(target, filename, content) {
      calls.push({ kind: "version", target, filename, content });
    },
    async datasetDelete(target) {
      calls.push({ kind: "delete", target });
    },
  };
  const imports = {
    react,
    "react/jsx-runtime": { jsx, jsxs: jsx },
    "next/navigation": { useRouter: () => ({ push() {} }) },
    "@tanstack/react-query": {
      useQuery: () => ({
        data: [{ public_id: "existing-fixture", name: "Synthetic dataset", row_count: 1, column_count: 1 }],
        isLoading: false,
      }),
      useQueryClient: () => ({ invalidateQueries() {}, setQueryData() {} }),
      useMutation(options) {
        const index = mutationCursor++;
        const state = mutationSlots[index] ??= { isPending: false };
        return {
          isPending: state.isPending,
          mutate(variables) {
            state.isPending = true;
            Promise.resolve().then(() => options.mutationFn(variables))
              .then(options.onSuccess, options.onError)
              .finally(() => { state.isPending = false; });
          },
        };
      },
    },
    "@/lib/api": {
      api,
      fileToBase64: async (file) => Buffer.from(await file.text()).toString("base64"),
    },
    "@/lib/project-context": { useActiveProject: () => ({ activeProjectId: 17 }) },
    "@/lib/utils": { cn: (...values) => values.filter(Boolean).join(" ") },
    "@/lib/analytics": { track() {} },
    sonner: { toast: { error: (message) => errors.push(message), success() {} } },
  };
  const exports = {};
  new Function("exports", "require", dataPage)(exports, (name) => {
    if (name in imports) return imports[name];
    if (name === "lucide-react" || name.startsWith("@/components/")) {
      return new Proxy({}, { get: (_target, key) => String(key) });
    }
    throw new Error(`Unexpected import: ${name}`);
  });
  const render = () => {
    cursor = 0;
    mutationCursor = 0;
    return exports.default();
  };
  const openImport = () => {
    findNode(render(), (node) => node.type === "Button" && nodeText(node) === " Import data").props.onClick();
    return render();
  };
  return { calls, errors, render, openImport, flush: () => new Promise(setImmediate) };
}

function importContent(tree) {
  return findNode(tree, (node) => node.type === "DialogContent" && nodeText(node).includes("Import data"));
}

function dropZone(tree) {
  return findNode(importContent(tree), (node) => node.type === "button" && Boolean(node.props.onDrop));
}

function dispatchDrag(tree, target, handler = "onDrop", files = [new File(["value\n42\n"], "synthetic.csv")]) {
  const event = {
    defaultPrevented: false,
    stopped: false,
    visited: [],
    dataTransfer: { files, types: ["Files"], dropEffect: "none" },
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() { this.stopped = true; },
  };
  const path = nodePath(tree, (node) => node === target);
  assert.ok(path);
  for (const node of path.toReversed()) {
    event.visited.push(node);
    node.props?.[handler]?.(event);
    if (event.stopped) break;
  }
  return event;
}

test("dropping into the import dialog only stages files until confirmation", async () => {
  const page = createDataPageHarness();
  const tree = page.openImport();
  dispatchDrag(tree, dropZone(tree));
  await page.flush();
  assert.deepEqual(page.calls, []);
  assert.ok(nodeText(importContent(page.render())).includes("synthetic.csv"));
  const confirm = findNode(importContent(page.render()), (node) => node.type === "Button");
  assert.equal(confirm.props.disabled, false);
  confirm.props.onClick();
  assert.equal(findNode(importContent(page.render()), (node) => node.type === "Button").props.disabled, true);
  await page.flush();
  assert.deepEqual(page.calls, [{
    kind: "create", filename: "synthetic.csv", content: Buffer.from("value\n42\n").toString("base64"),
    options: { name: "synthetic", project_id: 17 },
  }]);
});

test("all import-dialog drag events stay inside the modal, including its padding", async () => {
  for (const handler of ["onDragEnter", "onDragOver", "onDragLeave", "onDrop"]) {
    for (const targetOf of [importContent, dropZone]) {
      const page = createDataPageHarness();
      const tree = page.openImport();
      const event = dispatchDrag(tree, targetOf(tree), handler);
      await page.flush();
      assert.equal(event.visited.includes(tree), false, `${handler} reached the page`);
      assert.equal(event.defaultPrevented, true);
      assert.deepEqual(page.calls, []);
    }
  }
});

test("a drop outside modal content cannot trigger the page autoimport while a dialog is open", async () => {
  for (const title of [" Import data", " New profile"]) {
    const page = createDataPageHarness();
    findNode(page.render(), (node) => node.type === "Button" && nodeText(node) === title).props.onClick();
    const tree = page.render();
    dispatchDrag(tree, tree, "onDragEnter");
    assert.equal(nodeText(page.render()).includes("Drop to import"), false);
    dispatchDrag(tree, tree);
    await page.flush();
    assert.deepEqual(page.calls, []);
  }
});

test("confirming a staged drop respects the selected version target exactly once", async () => {
  const page = createDataPageHarness();
  let tree = page.openImport();
  findNode(importContent(tree), (node) => node.type === "Select").props.onValueChange("existing-fixture");
  tree = page.render();
  dispatchDrag(tree, dropZone(tree));
  await page.flush();
  assert.deepEqual(page.calls, []);
  findNode(importContent(page.render()), (node) => node.type === "Button").props.onClick();
  await page.flush();
  assert.deepEqual(page.calls.map(({ kind, target, filename }) => ({ kind, target, filename })), [
    { kind: "version", target: "existing-fixture", filename: "synthetic.csv" },
  ]);
});

test("the delete dialog blocks autoimport and retains explicit cancel and confirm actions", async () => {
  const page = createDataPageHarness();
  const openDelete = () => {
    findNode(page.render(), (node) => node.props?.["aria-label"] === "Delete Synthetic dataset").props.onClick();
    return page.render();
  };
  const tree = openDelete();
  const dialog = findNode(tree, (node) => node.type === "ConfirmDeleteDialog");
  assert.notEqual(dialog.props.target, null);
  dispatchDrag(tree, dialog, "onDragEnter");
  assert.equal(nodeText(page.render()).includes("Drop to import"), false);
  dispatchDrag(tree, dialog);
  await page.flush();
  assert.deepEqual(page.calls, []);
  dialog.props.onCancel();
  assert.equal(findNode(page.render(), (node) => node.type === "ConfirmDeleteDialog").props.target, null);
  assert.deepEqual(page.calls, []);
  findNode(openDelete(), (node) => node.type === "ConfirmDeleteDialog").props.onConfirm();
  await page.flush();
  assert.deepEqual(page.calls, [{ kind: "delete", target: "existing-fixture" }]);
  assert.equal(findNode(page.render(), (node) => node.type === "ConfirmDeleteDialog").props.target, null);
});

test("the import destination label names its select trigger", () => {
  const page = createDataPageHarness();
  const content = importContent(page.openImport());
  const label = findNode(content, (node) => node.type === "Label" && nodeText(node) === "Land in");
  const select = findNode(content, (node) => node.type === "SelectTrigger");
  assert.equal(typeof label.props.htmlFor, "string");
  assert.equal(label.props.htmlFor, select.props.id);
});

test("file picking stages without writes and cancellation discards the staged import", async () => {
  const page = createDataPageHarness();
  const tree = page.openImport();
  const input = findNode(importContent(tree), (node) => node.type === "input" && node.props.type === "file");
  input.props.onChange({ target: { files: [new File(["value\n42\n"], "synthetic.csv")], value: "synthetic.csv" } });
  await page.flush();
  assert.deepEqual(page.calls, []);
  assert.ok(nodeText(importContent(page.render())).includes("synthetic.csv"));
  findNode(page.render(), (node) => node.type === "Dialog" && nodeText(node).includes("Import data")).props.onOpenChange(false);
  assert.equal(nodeText(importContent(page.openImport())).includes("synthetic.csv"), false);
  assert.deepEqual(page.calls, []);
});

test("ordinary page and dataset-card drops still import directly", async () => {
  for (const onCard of [false, true]) {
    const page = createDataPageHarness();
    const tree = page.render();
    dispatchDrag(tree, onCard ? findNode(tree, (node) => node.type === "article") : tree);
    await page.flush();
    assert.equal(page.calls.length, 1);
    assert.equal(page.calls[0].kind, onCard ? "version" : "create");
    if (onCard) assert.equal(page.calls[0].target, "existing-fixture");
    assert.deepEqual(page.errors, []);
  }
});
