import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

// Execute the real component with controlled query states, without a server.
const source = readFileSync("src/app/(app)/writer/[id]/page.tsx", "utf8");
const start = source.indexOf("function FigureList(");
const end = source.indexOf("/* ---------- version history", start);
assert.ok(start >= 0 && end > start);
const compiled = ts.transpileModule(
  `${source.slice(start, end)}\nexports.FigureList = FigureList;`,
  { compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
    jsx: ts.JsxEmit.ReactJSX,
  } },
).outputText;

function render(queryState) {
  const queries = [];
  const inserted = [];
  let retries = 0;
  const jsx = (type, props) => ({ type, props });
  const context = {
    exports: {},
    require: (name) => {
      assert.equal(name, "react/jsx-runtime");
      return { jsx, jsxs: jsx };
    },
    useQueryClient: () => ({}),
    useRef: (current) => ({ current }),
    useState: (initial) => [initial, () => {}],
    useEffect: () => {},
    useMutation: () => ({ isPending: false }),
    useQuery: (options) => {
      queries.push(options);
      return {
        isPending: false, isFetching: false, error: null,
        refetch: () => { retries += 1; },
        ...queryState,
      };
    },
    api: {},
    Button: "Button",
    Loader2: "Loader2",
    Shapes: "Shapes",
    AssetThumb: "AssetThumb",
    ImageIcon: "ImageIcon",
    Trash2: "Trash2",
    ConfirmDeleteDialog: "ConfirmDeleteDialog",
    IMAGE_ASSET: /\.(png|jpe?g|webp|gif)$/i,
  };
  vm.runInNewContext(compiled, context);
  const tree = context.exports.FigureList({
    docId: "synthetic-writer", onInsert: (name) => inserted.push(name),
  });
  return { tree, queries, inserted, retries: () => retries };
}

function nodes(tree) {
  if (Array.isArray(tree)) return tree.flatMap(nodes);
  if (!tree || typeof tree !== "object") return [];
  return [tree, ...nodes(tree.props?.children)];
}

function text(tree) {
  if (Array.isArray(tree)) return tree.map(text).join("");
  if (tree === null || tree === undefined || typeof tree === "boolean") return "";
  if (typeof tree !== "object") return String(tree);
  return text(tree.props?.children);
}

test("initial figure loading is a status, not an empty asset list", () => {
  const { tree } = render({ isPending: true, isFetching: true });
  assert.match(text(tree), /Loading figures/);
  assert.ok(nodes(tree).some((node) => node.props.role === "status"));
  assert.doesNotMatch(text(tree), /Uploaded figures join/);
});

test("failed figure loading offers a document-scoped retry without claiming emptiness", () => {
  const view = render({ error: new Error("private provider response") });
  assert.ok(nodes(view.tree).some((node) => node.props.role === "alert"));
  assert.match(text(view.tree), /Figures could not be loaded/);
  assert.doesNotMatch(text(view.tree), /Uploaded figures join|private provider response/);
  assert.deepEqual(Array.from(view.queries[0].queryKey), ["writer-assets", "synthetic-writer"]);
  const retry = nodes(view.tree).find((node) => text(node) === "Try again");
  assert.equal(retry.props.disabled, false);
  retry.props.onClick();
  assert.equal(view.retries(), 1);
});

test("a retry in flight disables its action and never presents cached empty data as success", () => {
  const { tree } = render({ data: [], error: new Error("unavailable"), isFetching: true });
  const retry = nodes(tree).find((node) => text(node) === "Retrying…");
  assert.equal(retry.props.disabled, true);
  assert.doesNotMatch(text(tree), /Uploaded figures join/);
});

test("a failed refresh preserves loaded figures and their insert action", () => {
  const view = render({
    data: [{ id: 42, filename: "synthetic.png", byte_size: 42 }],
    error: new Error("unavailable"),
  });
  assert.match(text(view.tree), /Figures could not be loaded/);
  assert.match(text(view.tree), /synthetic\.png/);
  const insert = nodes(view.tree).find((node) => node.type === "button" && text(node) === "insert");
  insert.props.onClick();
  assert.deepEqual(view.inserted, ["synthetic.png"]);
});

test("only a successful empty query displays the empty-state guidance", () => {
  const { tree } = render({ data: [] });
  assert.match(text(tree), /Uploaded figures join every compile/);
  assert.ok(!nodes(tree).some((node) => ["status", "alert"].includes(node.props.role)));
});
