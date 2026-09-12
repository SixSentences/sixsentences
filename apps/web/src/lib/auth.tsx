"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useRouter } from "next/navigation";
import type { QueryClient } from "@tanstack/react-query";

import {
  ApiError,
  api,
  clearToken,
  getToken,
  retryTransientApiQuery,
  setToken,
  transientApiRetryDelay,
} from "@/lib/api";
import type { AssistantPreferences, Me } from "@/lib/types";

type AuthState = {
  /** Unavailable preserves only a previously verified identity, never a new login. */
  status: "loading" | "signed-in" | "signed-out" | "unavailable";
  isResolving: boolean;
  me: Me | null;
  signIn: (token: string) => Promise<void>;
  signOut: () => void;
  refresh: () => Promise<void>;
  setLanguage: (language: "en" | "de") => Promise<void>;
  setAssistantPreferences: (preferences: AssistantPreferences) => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

const PROTECTED_SESSION_STORAGE_KEYS = new Set([
  "six:landing-question",
  "six:onboarding-seed",
  "six:refine",
]);
const PROTECTED_SESSION_STORAGE_PREFIXES = [
  "six:knowledge:",
  "six:brainstorming:",
  "six:repository-analysis:",
  "six:repository-manuscript:",
];
const PROTECTED_LOCAL_STORAGE_KEYS = new Set(["six:landing-question"]);
const PROTECTED_LOCAL_STORAGE_PREFIXES = [
  "six:workspace-action:",
  "six:writer-visual-auto-insert:",
];

function clearMatchingStorageEntries(
  storage: Storage,
  exactKeys: ReadonlySet<string>,
  prefixes: readonly string[],
): void {
  for (let index = storage.length - 1; index >= 0; index -= 1) {
    const key = storage.key(index);
    if (
      key !== null
      && (exactKeys.has(key) || prefixes.some((prefix) => key.startsWith(prefix)))
    ) {
      storage.removeItem(key);
    }
  }
}

export function AuthProvider({
  children,
  queryClient,
}: {
  children: React.ReactNode;
  queryClient: QueryClient;
}) {
  const [status, setStatus] = useState<AuthState["status"]>("loading");
  const [isResolving, setIsResolving] = useState(false);
  const [me, setMe] = useState<Me | null>(null);
  const acceptedIdentityRef = useRef<string | null>(null);
  const acceptedTokenRef = useRef<string | null>(null);
  const resolutionRef = useRef(0);
  const router = useRouter();

  const clearProtectedCache = useCallback(() => {
    if (typeof window !== "undefined") {
      try {
        clearMatchingStorageEntries(
          window.sessionStorage,
          PROTECTED_SESSION_STORAGE_KEYS,
          PROTECTED_SESSION_STORAGE_PREFIXES,
        );
      } catch {
        // Some hardened browser contexts disable session storage. Continue with
        // the independent local-storage and in-memory boundaries below.
      }
      try {
        clearMatchingStorageEntries(
          window.localStorage,
          PROTECTED_LOCAL_STORAGE_KEYS,
          PROTECTED_LOCAL_STORAGE_PREFIXES,
        );
      } catch {
        // Some hardened browser contexts disable local storage; the Query
        // cache and mounted components are still cleared below.
      }
      window.dispatchEvent(new Event("six:auth-boundary"));
    }
    queryClient.clear();
  }, [queryClient]);

  const leaveAuthenticatedIdentity = useCallback((force: boolean) => {
    // Query keys are intentionally domain-oriented rather than user-oriented.
    // Clear the whole process-wide client at an auth boundary so no protected
    // query or mutation receipt can cross into the next identity.
    if (force || acceptedIdentityRef.current !== null) clearProtectedCache();
    acceptedIdentityRef.current = null;
    acceptedTokenRef.current = null;
  }, [clearProtectedCache]);

  const resolve = useCallback(async () => {
    const resolution = ++resolutionRef.current;
    const tokenAtStart = getToken();
    if (!tokenAtStart) {
      // Keep an anonymous-only cache warm on a first public visit, but clear
      // whenever this transition actually leaves an authenticated identity.
      leaveAuthenticatedIdentity(false);
      setMe(null);
      setStatus("signed-out");
      setIsResolving(false);
      return;
    }
    if (acceptedTokenRef.current !== null && acceptedTokenRef.current !== tokenAtStart) {
      // Another tab may have replaced the shared token. Never retain A's view
      // while resolving an unverified token that may belong to B.
      leaveAuthenticatedIdentity(true);
      setMe(null);
      setStatus("loading");
    }
    setIsResolving(true);
    try {
      for (let failureCount = 0; ; failureCount += 1) {
        if (getToken() !== tokenAtStart) return;
        if (resolutionRef.current !== resolution) return;
        try {
          const who = await api.me();
          // A logout, newer resolution or another login won while /me was in flight.
          if (getToken() !== tokenAtStart) return;
          if (resolutionRef.current !== resolution) return;
          const nextIdentity = `${who.org_id}:${who.user_id}`;
          if (
            acceptedIdentityRef.current !== null &&
            acceptedIdentityRef.current !== nextIdentity
          ) clearProtectedCache();
          acceptedIdentityRef.current = nextIdentity;
          acceptedTokenRef.current = tokenAtStart;
          setMe(who);
          setStatus("signed-in");
          return;
        } catch (error) {
          if (getToken() !== tokenAtStart) return;
          if (resolutionRef.current !== resolution) return;
          if (error instanceof ApiError && error.status === 401) {
            clearToken();
            leaveAuthenticatedIdentity(true);
            setMe(null);
            setStatus("signed-out");
            return;
          }
          if (!retryTransientApiQuery(failureCount, error)) {
            // An unreachable API does not invalidate a previously verified identity.
            // Preserve its cached workspace; an unverified first load stays blocked.
            setStatus("unavailable");
            return;
          }
          await new Promise<void>((done) =>
            setTimeout(done, transientApiRetryDelay(failureCount)),
          );
        }
      }
    } finally {
      if (resolutionRef.current === resolution) setIsResolving(false);
    }
  }, [clearProtectedCache, leaveAuthenticatedIdentity]);

  useEffect(() => {
    void resolve();
    return () => { resolutionRef.current += 1; };
  }, [resolve]);

  useEffect(() => {
    document.documentElement.lang = me?.language ?? "en";
  }, [me?.language]);

  // Any 401 from any request signs the session out app-wide.
  useEffect(() => {
    const onUnauthorized = () => {
      resolutionRef.current += 1;
      setIsResolving(false);
      leaveAuthenticatedIdentity(true);
      setMe(null);
      setStatus("signed-out");
      router.replace("/login");
    };
    window.addEventListener("six:unauthorized", onUnauthorized);
    return () => window.removeEventListener("six:unauthorized", onUnauthorized);
  }, [leaveAuthenticatedIdentity, router]);

  const signIn = useCallback(
    async (token: string) => {
      // Block authenticated surfaces while the new token is resolved. If it
      // belongs to another user, resolve() clears A before accepting B.
      setMe(null);
      setStatus("loading");
      setToken(token);
      await resolve();
    },
    [resolve],
  );

  const signOut = useCallback(() => {
    resolutionRef.current += 1;
    setIsResolving(false);
    clearToken();
    leaveAuthenticatedIdentity(true);
    setMe(null);
    setStatus("signed-out");
    router.replace("/login");
  }, [leaveAuthenticatedIdentity, router]);

  const setLanguage = useCallback(async (language: "en" | "de") => {
    const previous = me;
    const tokenAtStart = getToken();
    const identityAtStart = acceptedIdentityRef.current;
    const resolutionAtStart = resolutionRef.current;
    if (!previous || !tokenAtStart || acceptedTokenRef.current !== tokenAtStart
      || identityAtStart !== `${previous.org_id}:${previous.user_id}`) return;
    setMe((current) =>
      getToken() === tokenAtStart
        && current?.org_id === previous.org_id && current?.user_id === previous.user_id
        ? { ...current, language }
        : current,
    );
    try {
      await api.updatePreferences({ language });
    } catch (error) {
      setMe((current) =>
        getToken() === tokenAtStart
          && acceptedIdentityRef.current === identityAtStart
          && resolutionRef.current === resolutionAtStart
          && current?.org_id === previous.org_id && current?.user_id === previous.user_id
          ? { ...current, language: previous.language }
          : current,
      );
      throw error;
    }
  }, [me]);

  const setAssistantPreferences = useCallback(async (
    assistantPreferences: AssistantPreferences,
  ) => {
    const previous = me;
    const tokenAtStart = getToken();
    const identityAtStart = acceptedIdentityRef.current;
    const resolutionAtStart = resolutionRef.current;
    if (!previous || !tokenAtStart || acceptedTokenRef.current !== tokenAtStart
      || identityAtStart !== `${previous.org_id}:${previous.user_id}`) return;
    setMe((current) =>
      getToken() === tokenAtStart
        && current?.org_id === previous.org_id && current?.user_id === previous.user_id
        ? { ...current, assistant_preferences: assistantPreferences }
        : current,
    );
    try {
      await api.updatePreferences({
        assistant_preferences: assistantPreferences,
      });
    } catch (error) {
      setMe((current) =>
        getToken() === tokenAtStart
          && acceptedIdentityRef.current === identityAtStart
          && resolutionRef.current === resolutionAtStart
          && current?.org_id === previous.org_id && current?.user_id === previous.user_id
          ? { ...current, assistant_preferences: previous.assistant_preferences }
          : current,
      );
      throw error;
    }
  }, [me]);

  const value = useMemo(
    () => ({
      status,
      isResolving,
      me,
      signIn,
      signOut,
      refresh: resolve,
      setLanguage,
      setAssistantPreferences,
    }),
    [
      status,
      isResolving,
      me,
      signIn,
      signOut,
      resolve,
      setLanguage,
      setAssistantPreferences,
    ],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
