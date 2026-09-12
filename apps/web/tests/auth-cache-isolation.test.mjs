import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import ts from "typescript";

const read = (path) => readFileSync(join(process.cwd(), path), "utf8");
const auth = read("src/lib/auth.tsx");
const providers = read("src/app/providers.tsx");
const api = read("src/lib/api.ts");
const layout = read("src/app/(app)/layout.tsx");
const workspaceActions = read("src/components/agent/workspace-action-card.tsx");

test("authenticated React Query data is fenced across A logout and B login", () => {
  assert.match(providers, /<QueryClientProvider client=\{queryClient\}>[\s\S]*?<AuthProvider queryClient=\{queryClient\}>/);
  assert.match(auth, /acceptedIdentityRef = useRef<string \| null>\(null\)/);
  assert.match(auth, /const nextIdentity = `\$\{who\.org_id\}:\$\{who\.user_id\}`/);

  const identitySwap = auth.indexOf("acceptedIdentityRef.current !== nextIdentity");
  const swapClear = auth.indexOf("clearProtectedCache();", identitySwap);
  const acceptIdentity = auth.indexOf("acceptedIdentityRef.current = nextIdentity", identitySwap);
  const acceptMe = auth.indexOf("setMe(who)", acceptIdentity);
  assert.ok(identitySwap >= 0 && swapClear > identitySwap);
  assert.ok(acceptIdentity > swapClear && acceptMe > acceptIdentity);
  assert.match(auth, /const clearProtectedCache = useCallback\([\s\S]*?six:auth-boundary[\s\S]*?queryClient\.clear\(\)/);

  const signOutStart = auth.indexOf("const signOut = useCallback");
  const signOutEnd = auth.indexOf("const setLanguage", signOutStart);
  const signOut = auth.slice(signOutStart, signOutEnd);
  assert.match(signOut, /clearToken\(\);[\s\S]*?leaveAuthenticatedIdentity\(true\);[\s\S]*?setMe\(null\)/);

  const unauthorizedStart = auth.indexOf("const onUnauthorized");
  const unauthorizedEnd = auth.indexOf("window.addEventListener", unauthorizedStart);
  const unauthorized = auth.slice(unauthorizedStart, unauthorizedEnd);
  assert.match(unauthorized, /leaveAuthenticatedIdentity\(true\);[\s\S]*?setMe\(null\)/);
});

test("stale identity resolution cannot repopulate a cleared cache", () => {
  assert.match(auth, /const tokenAtStart = getToken\(\)/);
  assert.match(auth, /if \(getToken\(\) !== tokenAtStart\) return/);
  assert.match(auth, /setStatus\("loading"\);[\s\S]*?setToken\(token\);[\s\S]*?await resolve\(\)/);
  assert.match(auth, /if \(force \|\| acceptedIdentityRef\.current !== null\) clearProtectedCache\(\)/);
  assert.match(auth, /if \(!tokenAtStart\) \{[\s\S]*?leaveAuthenticatedIdentity\(false\)/);
});

test("authenticated document byte fetches preserve the global 401 boundary", () => {
  const start = api.indexOf("async function fetchAuthenticatedDocumentBytes");
  const end = api.indexOf("export async function fetchDocumentBytes", start);
  const byteFetch = api.slice(start, end);
  assert.ok(start >= 0 && end > start);
  assert.match(byteFetch, /catch \{[\s\S]*new ApiError\([\s\S]*0,/);
  assert.match(byteFetch, /res\.status === 401[\s\S]*clearToken\(\);[\s\S]*announce\("six:unauthorized"\)/);
  assert.match(byteFetch, /return await res\.arrayBuffer\(\)/);
});

test("auth boundaries purge content-bearing browser state but preserve UI preferences", () => {
  const clearStart = auth.indexOf("const clearProtectedCache");
  const clearEnd = auth.indexOf("const leaveAuthenticatedIdentity", clearStart);
  const clear = auth.slice(clearStart, clearEnd);
  for (const key of [
    "six:landing-question",
    "six:onboarding-seed",
    "six:refine",
  ]) {
    assert.match(auth, new RegExp(`PROTECTED_SESSION_STORAGE_KEYS[\\s\\S]*?"${key}"`));
  }
  for (const prefix of [
    "six:knowledge:",
    "six:brainstorming:",
    "six:repository-analysis:",
    "six:repository-manuscript:",
  ]) {
    assert.match(auth, new RegExp(`PROTECTED_SESSION_STORAGE_PREFIXES[\\s\\S]*?"${prefix}"`));
  }
  assert.match(auth, /PROTECTED_LOCAL_STORAGE_KEYS = new Set\(\["six:landing-question"\]\)/);
  assert.match(auth, /PROTECTED_LOCAL_STORAGE_PREFIXES = \[[\s\S]*?"six:workspace-action:"[\s\S]*?"six:writer-visual-auto-insert:"/);
  assert.match(clear, /clearMatchingStorageEntries\(\s*window\.sessionStorage/);
  assert.match(clear, /clearMatchingStorageEntries\(\s*window\.localStorage/);
  assert.doesNotMatch(clear, /sessionStorage\.clear\(\)/);
  assert.doesNotMatch(clear, /localStorage\.clear\(\)/);

  const sessionPurge = clear.indexOf("window.sessionStorage");
  const localPurge = clear.indexOf("window.localStorage");
  const event = clear.indexOf('window.dispatchEvent(new Event("six:auth-boundary"))');
  const queryClear = clear.indexOf("queryClient.clear()");
  assert.ok(sessionPurge >= 0 && localPurge > sessionPurge);
  assert.ok(event > localPurge && queryClear > event);

  for (const retainedPreference of [
    "six:sidebar-collapsed",
    "six:writer-model",
    "six:writer-chat-split",
  ]) {
    assert.doesNotMatch(auth, new RegExp(`PROTECTED_[A-Z_]+[\\s\\S]*?"${retainedPreference}"`));
  }
});

test("workspace action receipts are isolated by organization and user", () => {
  assert.match(
    workspaceActions,
    /`six:workspace-action:v2:\$\{me\.org_id\}:\$\{me\.user_id\}:\$\{action\.id\}`/,
  );
  assert.match(workspaceActions, /useEffect\(\(\) => \{\s*setCreated\(null\);\s*if \(!receiptKey\) \{\s*return;/);
  assert.match(workspaceActions, /if \(!controlAction && receiptKey\) \{/);
  assert.doesNotMatch(workspaceActions, /const receiptKey = `six:workspace-action:\$\{action\.id\}`/);
});

function compile(source) {
  return ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
  }).outputText.replace(/^export /gm, "");
}

// Run the actual resolver body with deterministic state and no network or real timers.
const retryStart = api.indexOf("export class ApiError");
const retryEnd = api.indexOf("export type ChatStreamFailureKind", retryStart);
assert.ok(retryStart >= 0 && retryEnd > retryStart);
const { ApiError, retryTransientApiQuery, transientApiRetryDelay } = new Function(
  compile(api.slice(retryStart, retryEnd))
  + "return { ApiError, retryTransientApiQuery, transientApiRetryDelay };",
)();
const resolverStart = auth.indexOf("  const resolve = useCallback(async () => {");
const resolverEnd = auth.indexOf("\n  useEffect(() => {", resolverStart);
assert.ok(resolverStart >= 0 && resolverEnd > resolverStart);
const buildResolver = new Function(
  "api", "getToken", "clearToken", "ApiError", "retryTransientApiQuery",
  "transientApiRetryDelay", "resolutionRef", "acceptedIdentityRef", "acceptedTokenRef",
  "leaveAuthenticatedIdentity", "clearProtectedCache", "setMe", "setStatus",
  "setIsResolving", "setTimeout", "useCallback",
  compile(auth.slice(resolverStart, resolverEnd)) + "return resolve;",
);

function resolverFixture(getMe, { me = null, schedule = (done) => { done(); return 0; } } = {}) {
  const state = { token: "fixture-token", me, status: me ? "signed-in" : "loading", resolving: false, clears: 0, calls: 0 };
  const resolutionRef = { current: 0 };
  const acceptedIdentityRef = { current: me ? `${me.org_id}:${me.user_id}` : null };
  const acceptedTokenRef = { current: me ? state.token : null };
  const clearCache = () => { state.clears += 1; };
  const resolve = buildResolver(
    { me: async () => { state.calls += 1; return getMe(state.calls); } },
    () => state.token,
    () => { state.token = null; },
    ApiError,
    retryTransientApiQuery,
    transientApiRetryDelay,
    resolutionRef,
    acceptedIdentityRef,
    acceptedTokenRef,
    (force) => {
      if (force || acceptedIdentityRef.current !== null) clearCache();
      acceptedIdentityRef.current = null;
      acceptedTokenRef.current = null;
    },
    clearCache,
    (value) => { state.me = value; },
    (value) => { state.status = value; },
    (value) => { state.resolving = value; },
    schedule,
    (callback) => callback,
  );
  return { state, resolve, resolutionRef };
}

const knownIdentity = { org_id: 42, user_id: 7, language: "en" };

for (const status of [0, 502, 503]) {
  test(`auth retries transient ${status} without clearing the verified workspace`, async () => {
    const fixture = resolverFixture((attempt) => {
      if (attempt === 1) throw new ApiError(status, "fixture failure");
      return knownIdentity;
    }, { me: knownIdentity });
    await fixture.resolve();
    assert.equal(fixture.state.calls, 2);
    assert.equal(fixture.state.status, "signed-in");
    assert.equal(fixture.state.me, knownIdentity);
    assert.equal(fixture.state.token, "fixture-token");
    assert.equal(fixture.state.clears, 0);
    assert.equal(fixture.state.resolving, false);
  });
}

test("exhausted auth retries preserve the verified identity and expose recovery", async () => {
  const fixture = resolverFixture(() => { throw new ApiError(0, "fixture failure"); }, { me: knownIdentity });
  await fixture.resolve();
  assert.equal(fixture.state.calls, 6);
  assert.equal(fixture.state.status, "unavailable");
  assert.equal(fixture.state.me, knownIdentity);
  assert.equal(fixture.state.clears, 0);
  assert.equal(fixture.state.token, "fixture-token");
});

test("an unavailable first session never invents an authenticated identity", async () => {
  const fixture = resolverFixture(() => { throw new ApiError(503, "fixture failure"); });
  await fixture.resolve();
  assert.equal(fixture.state.status, "unavailable");
  assert.equal(fixture.state.me, null);
  assert.equal(fixture.state.clears, 0);
  assert.match(layout, /if \(status === "unavailable" && !me\)/);
  assert.ok(layout.indexOf('status === "unavailable" && !me') < layout.indexOf("<ProjectProvider>"));
});

test("auth abort is not retried or interpreted as a logout", async () => {
  const aborted = new Error("fixture abort");
  aborted.name = "AbortError";
  const fixture = resolverFixture(() => { throw aborted; }, { me: knownIdentity });
  await fixture.resolve();
  assert.equal(fixture.state.calls, 1);
  assert.equal(fixture.state.status, "unavailable");
  assert.equal(fixture.state.me, knownIdentity);
  assert.equal(fixture.state.clears, 0);
});

test("a genuinely invalid auth session still clears identity and token immediately", async () => {
  const fixture = resolverFixture(() => { throw new ApiError(401, "fixture invalid session"); }, { me: knownIdentity });
  await fixture.resolve();
  assert.equal(fixture.state.calls, 1);
  assert.equal(fixture.state.status, "signed-out");
  assert.equal(fixture.state.me, null);
  assert.equal(fixture.state.token, null);
  assert.equal(fixture.state.clears, 1);
});

test("a replaced shared token cannot retain the prior workspace after a failed check", async () => {
  const fixture = resolverFixture(() => { throw new ApiError(503, "fixture failure"); }, { me: knownIdentity });
  fixture.state.token = "different-fixture-token";
  await fixture.resolve();
  assert.equal(fixture.state.status, "unavailable");
  assert.equal(fixture.state.me, null);
  assert.equal(fixture.state.clears, 1);
  assert.equal(fixture.state.token, "different-fixture-token");
});

test("a newer successful resolution fences an older transient retry", async () => {
  let releaseRetry;
  const fixture = resolverFixture((attempt) => {
    if (attempt === 1) throw new ApiError(0, "fixture failure");
    return knownIdentity;
  }, { schedule: (done) => { releaseRetry = done; return 0; } });
  const earlier = fixture.resolve();
  await Promise.resolve();
  await fixture.resolve();
  assert.equal(typeof releaseRetry, "function");
  releaseRetry();
  await earlier;
  assert.equal(fixture.state.calls, 2);
  assert.equal(fixture.state.status, "signed-in");
  assert.equal(fixture.state.me, knownIdentity);
  assert.equal(fixture.state.resolving, false);
});

test("auth recovery keeps the protected tree mounted only for a previously verified identity", () => {
  assert.match(layout, /status !== "signed-in" && !\(status === "unavailable" && me\)/);
  assert.match(layout, /disabled=\{isResolving\} onClick=\{\(\) => void refresh\(\)\}/);
  assert.match(layout, /if \(status === "signed-out"\)[\s\S]*?router\.replace/);
  assert.match(layout, /<AppShell>\{children\}<\/AppShell>[\s\S]*?\{connectionNotice &&/);
  assert.match(auth, /const onUnauthorized = \(\) => \{\s*resolutionRef\.current \+= 1/);
  assert.match(auth, /const signOut = useCallback\(\(\) => \{\s*resolutionRef\.current \+= 1/);
});

const requestStart = api.indexOf("async function request<T>(");
const requestEnd = api.indexOf("\n/**", requestStart);
assert.ok(requestStart >= 0 && requestEnd > requestStart);
const buildRequest = new Function(
  "API_URL", "getToken", "fetch", "toApiError", "clearToken", "announce",
  "readJsonResponse", "ApiError",
  compile(api.slice(requestStart, requestEnd)) + "return request;",
);

for (const tokenChanged of [false, true]) {
  test(`a late 401 ${tokenChanged ? "cannot sign out a newer token" : "still signs out its current token"}`, async () => {
    const state = { token: "token-a", clears: 0, events: [], header: null };
    let finishFetch;
    const request = buildRequest(
      "https://fixture.invalid",
      () => state.token,
      async (_url, init) => {
        state.header = init.headers.Authorization;
        return new Promise((resolve) => { finishFetch = resolve; });
      },
      async () => new ApiError(401, "fixture invalid session"),
      () => { state.token = null; state.clears += 1; },
      (event) => state.events.push(event),
      async () => { throw new Error("unexpected successful response"); },
      ApiError,
    );
    const pending = request("/fixture").catch((error) => error);
    assert.equal(state.header, "Bearer token-a");
    if (tokenChanged) state.token = "token-b";
    finishFetch({ ok: false, status: 401 });
    assert.equal((await pending).status, 401);
    assert.equal(state.token, tokenChanged ? "token-b" : null);
    assert.equal(state.clears, tokenChanged ? 0 : 1);
    assert.deepEqual(state.events, tokenChanged ? [] : ["six:unauthorized"]);
  });
}

test("every existing API unauthorized announcement is fenced to its sent token", () => {
  const announcements = [...api.matchAll(/clearToken\(\);\s*announce\("six:unauthorized"\)/g)];
  assert.ok(announcements.length >= 7);
  for (const announcement of announcements) {
    assert.match(api.slice(announcement.index - 100, announcement.index), /getToken\(\) === token\) \{\s*$/);
  }
});

const preferencesStart = auth.indexOf("  const setLanguage = useCallback");
const preferencesEnd = auth.indexOf("\n  const value = useMemo", preferencesStart);
assert.ok(preferencesStart >= 0 && preferencesEnd > preferencesStart);
const buildPreferences = new Function(
  "me", "getToken", "acceptedIdentityRef", "acceptedTokenRef", "resolutionRef",
  "api", "setMe", "useCallback",
  compile(auth.slice(preferencesStart, preferencesEnd)) + "return { setLanguage, setAssistantPreferences };",
);

for (const preference of ["language", "assistant_preferences"]) {
  for (const identityChanged of [false, true]) {
    test(`failed ${preference} update ${identityChanged ? "cannot restore a previous identity" : "rolls back only its field"}`, async () => {
      const previous = { ...knownIdentity, assistant_preferences: { verbosity: "short" }, legal_reaccept_required: false };
      const state = { token: "token-a", me: previous };
      const identityRef = { current: `${previous.org_id}:${previous.user_id}` };
      const tokenRef = { current: "token-a" };
      const resolutionRef = { current: 1 };
      let rejectUpdate;
      const setters = buildPreferences(
        previous, () => state.token, identityRef, tokenRef, resolutionRef,
        { updatePreferences: () => new Promise((_resolve, reject) => { rejectUpdate = reject; }) },
        (value) => { state.me = typeof value === "function" ? value(state.me) : value; },
        (callback) => callback,
      );
      const pending = (preference === "language"
        ? setters.setLanguage("de")
        : setters.setAssistantPreferences({ verbosity: "long" })
      ).catch((error) => error);
      if (identityChanged) {
        state.token = "token-b";
        tokenRef.current = "token-b";
        identityRef.current = "99:88";
        resolutionRef.current += 1;
        state.me = { ...previous, org_id: 99, user_id: 88, language: "de" };
      } else {
        state.me = { ...state.me, legal_reaccept_required: true };
      }
      const identityBeforeFailure = state.me;
      rejectUpdate(new Error("fixture failure"));
      await pending;
      if (identityChanged) {
        assert.equal(state.me, identityBeforeFailure);
        assert.equal(state.token, "token-b");
      } else {
        assert.equal(state.me[preference], previous[preference]);
        assert.equal(state.me.legal_reaccept_required, true);
      }
    });
  }
}
