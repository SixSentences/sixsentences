import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import ts from "typescript";

const read = (path) => readFileSync(join(process.cwd(), path), "utf8");

function load(source, dependencies = {}) {
  const compiled = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  const module = { exports: {} };
  new Function("require", "module", "exports", "window", compiled)(
    (name) => {
      assert.ok(name in dependencies, `Unexpected module: ${name}`);
      return dependencies[name];
    },
    module,
    module.exports,
    dependencies.window,
  );
  return module.exports;
}

const selection = load(read("src/lib/private-model-selection.ts"));
const { privateModelOptions, resolvePrivateModelId } = selection;

const flash = { id: "gemini-3.5-flash", provider: "gemini", default: true, locked: false };
const pro = { id: "gemini-3.1-pro-preview", provider: "gemini", default: false, locked: false };
const catalog = {
  scope: "full",
  routing_mode: "gemini_private",
  content_scope: "private",
  default_id: flash.id,
  models: [flash, pro],
};

test("private options require explicit server routing and content scope", () => {
  assert.deepEqual(privateModelOptions(catalog), [flash, pro]);
  assert.deepEqual(privateModelOptions({ ...catalog, routing_mode: undefined }), []);
  assert.deepEqual(privateModelOptions({ ...catalog, content_scope: "public" }), []);
  assert.deepEqual(privateModelOptions({ scope: "full", models: [flash, pro] }), []);
  assert.deepEqual(privateModelOptions(undefined), []);
});

test("a Gemini label or model ID never substitutes for the actual provider", () => {
  const mixed = { ...catalog, models: [flash, { ...pro, provider: "openrouter" }] };
  assert.deepEqual(privateModelOptions(mixed), [flash]);
  assert.equal(resolvePrivateModelId(pro.id, mixed), flash.id);
  assert.equal(resolvePrivateModelId(flash.id, {
    ...catalog, models: [{ ...flash, provider: undefined }],
  }), "auto");
});

test("old, missing and removed choices resolve to the declared server default", () => {
  for (const previous of [undefined, null, "", "auto", "sixsentences-router", "openai/gpt-5", "google/gemini-3.1-pro-preview"]) {
    assert.equal(resolvePrivateModelId(previous, catalog), flash.id);
  }
  assert.equal(resolvePrivateModelId(pro.id, catalog), pro.id);
  assert.equal(resolvePrivateModelId("old-model", { ...catalog, default_id: pro.id }), pro.id);
});

test("server-unavailable models cannot be restored from a stored preference", () => {
  const restricted = { ...catalog, scope: "flash_only", models: [flash, { ...pro, locked: true }] };
  assert.equal(resolvePrivateModelId(pro.id, restricted), flash.id);
  assert.equal(resolvePrivateModelId(pro.id, { ...restricted, default_id: pro.id }), flash.id);
  assert.equal(resolvePrivateModelId(flash.id, {
    ...catalog, models: [{ ...flash, locked: true }, { ...pro, locked: true }],
  }), "auto");
});

test("loading, stale catalogs and unavailable models use only the safe server alias", () => {
  for (const value of [undefined, null, { scope: "admin", models: [flash] }, { ...catalog, models: [] }]) {
    assert.equal(resolvePrivateModelId("openai/gpt-5", value), "auto");
  }
});

function preferenceHarness({ initialCatalog = catalog, stored = "", storageThrows = false } = {}) {
  let currentCatalog = initialCatalog;
  let cursor = 0;
  let dirty = false;
  let effects = [];
  const slots = [];
  const storage = new Map(stored ? [["six:test-model", stored]] : []);
  const react = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = initial;
      return [slots[index], (value) => {
        slots[index] = typeof value === "function" ? value(slots[index]) : value;
        dirty = true;
      }];
    },
    useEffect(effect, deps) {
      const index = cursor++;
      const previous = slots[index];
      if (!previous || deps.some((value, offset) => !Object.is(value, previous[offset]))) {
        effects.push(effect);
        slots[index] = deps;
      }
    },
    useCallback(callback) { return callback; },
  };
  const { usePrivateModelPreference } = load(read("src/hooks/use-private-model-preference.ts"), {
    react,
    "@/hooks/queries": { useModels: () => ({ data: currentCatalog }) },
    "@/lib/private-model-selection": selection,
    window: {
      localStorage: {
        getItem(key) {
          if (storageThrows) throw new Error("Storage blocked");
          return storage.get(key) ?? null;
        },
        setItem(key, value) {
          if (storageThrows) throw new Error("Storage blocked");
          storage.set(key, value);
        },
      },
    },
  });
  return {
    storage,
    setCatalog(value) { currentCatalog = value; },
    render() {
      let result;
      let renders = 0;
      do {
        assert.ok(renders++ < 10, "Preference effects must settle");
        cursor = 0;
        dirty = false;
        effects = [];
        result = usePrivateModelPreference("six:test-model");
        effects.forEach((effect) => effect());
      } while (dirty);
      return result;
    },
  };
}

test("stored OpenRouter preferences are migrated before a request and persist safely", () => {
  const harness = preferenceHarness({ stored: "openai/gpt-5" });
  assert.equal(harness.render()[0], flash.id);
  assert.equal(harness.storage.get("six:test-model"), flash.id);
});

test("a loading catalog does not destroy a valid preference before it arrives", () => {
  const harness = preferenceHarness({ initialCatalog: undefined, stored: pro.id });
  harness.setCatalog(undefined);
  assert.equal(harness.render()[0], "auto");
  assert.equal(harness.storage.get("six:test-model"), pro.id);
  harness.setCatalog(catalog);
  assert.equal(harness.render()[0], pro.id);
  assert.equal(harness.storage.get("six:test-model"), pro.id);
});

test("availability changes replace and persist an unavailable preference", () => {
  const harness = preferenceHarness({ stored: pro.id });
  assert.equal(harness.render()[0], pro.id);
  harness.setCatalog({ ...catalog, models: [flash, { ...pro, locked: true }] });
  assert.equal(harness.render()[0], flash.id);
  assert.equal(harness.storage.get("six:test-model"), flash.id);
  harness.setCatalog(catalog);
  assert.equal(harness.render()[0], flash.id, "A later unlock must not silently restore the retired preference");
});

test("disabled storage and invalid picker input cannot resurrect an old route", () => {
  const harness = preferenceHarness({ storageThrows: true });
  const [model, choose] = harness.render();
  assert.equal(model, flash.id);
  choose("openai/gpt-5");
  assert.equal(harness.render()[0], flash.id);
  choose(pro.id);
  assert.equal(harness.render()[0], pro.id);
});

test("every text specialist uses the shared validated preference", () => {
  for (const [path, key] of [
    ["writer/[id]", "writer"],
    ["data/[id]", "dataset"],
    ["interviews/[id]", "interview"],
    ["interviews/studies/[id]", "study"],
    ["surveys/[id]", "survey"],
  ]) {
    const source = read(`src/app/(app)/${path}/page.tsx`);
    assert.ok(source.includes(`usePrivateModelPreference("six:${key}-model")`), path);
    assert.ok(!source.includes(`localStorage.getItem("six:${key}-model")`), path);
  }
  assert.match(read("src/components/search/composer.tsx"), /usePrivateModelPreference\(\)/);
});

test("chat history, queued turns and retries are normalized at the send boundary", () => {
  const source = read("src/components/run/chat-panel.tsx");
  assert.match(source, /const model = resolvePrivateModelId\(requestedModel, modelCatalog\)/);
  assert.match(source, /selectedModel: resolvePrivateModelId\(turn\.model, modelCatalog\)/);
  assert.match(source, /model: next\.model/);
  assert.match(source, /model: retry\.model/);
  assert.doesNotMatch(source, /sixsentences-router/);
  assert.doesNotMatch(read("src/components/run/run-view.tsx"), /sixsentences-router/);
  const picker = read("src/components/search/model-picker.tsx");
  assert.match(picker, /privateModelOptions\(catalog\)/);
  assert.match(picker, /resolvePrivateModelId\(value, catalog\)/);
});

test("figure rendering identifies the configured Gemini provider without a pricing or ZDR claim", () => {
  const figures = read("src/app/(app)/figures/page.tsx");
  assert.match(figures, /Rendered through the configured Google Gemini provider/);
  assert.doesNotMatch(figures, /paid Google Gemini API/);
  assert.match(figures, /href=\{publicLegalUrl\("privacy"\)\}[^>]*>Data processing and retention/);
  assert.doesNotMatch(figures, /Zero Data Retention routes only|endpoint may\s+be selected dynamically/);
});
