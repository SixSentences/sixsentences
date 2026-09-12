"use client";

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { api, type RunRef } from "@/lib/api";
import type { WorkVerdictFilter } from "@/lib/types";

/** Central React Query hooks — one place for keys, polling and enablement. */

export function useRuns() {
  return useQuery({
    queryKey: ["runs"],
    queryFn: () => api.listRuns(),
    // safety net while anything is still moving; SSE invalidates eagerly
    refetchInterval: (query) =>
      query.state.data?.some((r) =>
        ["pending", "running"].includes(r.status),
      )
        ? 10_000
        : false,
  });
}

export function useProjects() {
  return useQuery({ queryKey: ["projects"], queryFn: api.listProjects });
}

export function useProjectWorkspace(id: number) {
  return useQuery({
    queryKey: ["project-workspace", id],
    queryFn: () => api.projectWorkspace(id),
    enabled: Number.isFinite(id) && id > 0,
  });
}

export function useRun(id: RunRef) {
  return useQuery({
    queryKey: ["run", String(id)],
    queryFn: () => api.run(id),
    enabled: Boolean(id),
    // SSE is the fast path, but a browser, proxy or laptop sleep can drop the
    // terminal frame. Keep a small REST safety net while the cached run is
    // moving so the header and composer cannot remain stuck on "Queued".
    refetchInterval: (query) =>
      query.state.data &&
      ["pending", "running"].includes(query.state.data.status)
        ? 2_000
        : false,
  });
}

export function useRunEvents(id: RunRef, enabled = true) {
  return useQuery({
    queryKey: ["run-events", String(id)],
    queryFn: () => api.runEvents(id),
    enabled: Boolean(id) && enabled,
  });
}

export function useWorks(
  id: RunRef,
  verdict: WorkVerdictFilter | boolean = "all",
  enabled = true,
  limit = 500,
  offset = 0,
) {
  const resolvedVerdict: WorkVerdictFilter =
    typeof verdict === "boolean" ? (verdict ? "include" : "all") : verdict;
  return useQuery({
    queryKey: ["works", String(id), resolvedVerdict, limit, offset],
    queryFn: () => api.runWorks(id, { verdict: resolvedVerdict, limit, offset }),
    enabled,
  });
}

export function useQueue(id: RunRef, enabled = true, limit = 25, offset = 0) {
  return useQuery({
    queryKey: ["queue", String(id), limit, offset],
    queryFn: () => api.screeningQueuePage(id, limit, offset),
    enabled,
  });
}

export function useDecisions(id: RunRef, enabled = true) {
  return useQuery({
    queryKey: ["decisions", String(id)],
    queryFn: () => api.decisions(id),
    enabled,
  });
}

export function useDocuments(id: RunRef, enabled = true) {
  return useQuery({
    queryKey: ["documents", String(id)],
    queryFn: () => api.runDocuments(id),
    enabled,
  });
}

export function useWebSources(id: RunRef, enabled = true) {
  return useQuery({
    queryKey: ["web-sources", String(id)],
    queryFn: () => api.webSources(id),
    enabled,
  });
}

export function useMethods(id: RunRef, enabled = true) {
  return useQuery({
    queryKey: ["methods", String(id)],
    queryFn: () => api.runMethods(id),
    enabled,
  });
}

export function useProtocol(id: RunRef, enabled = true) {
  return useQuery({
    queryKey: ["protocol", String(id)],
    queryFn: () => api.protocol(id),
    enabled,
    retry: false, // 404 until the protocol is synthesized
  });
}

export function useChatHistory(id: RunRef, enabled = true) {
  return useQuery({
    queryKey: ["chat", String(id)],
    queryFn: () => api.chatHistory(id),
    enabled,
  });
}

export function useLibrary(q: string) {
  return useInfiniteQuery({
    queryKey: ["library", q],
    queryFn: ({ pageParam }) => api.libraryDocuments(q, pageParam, 200),
    initialPageParam: 0,
    getNextPageParam: (lastPage, pages) =>
      lastPage.length === 200 ? pages.length * 200 : undefined,
  });
}

export function useLibraryWebSources(q: string) {
  return useInfiniteQuery({
    queryKey: ["library-web-sources", q],
    queryFn: ({ pageParam }) => api.libraryWebSources(q, pageParam, 200),
    initialPageParam: 0,
    getNextPageParam: (lastPage, pages) =>
      lastPage.length === 200 ? pages.length * 200 : undefined,
  });
}

export function useModels() {
  return useQuery({ queryKey: ["models"], queryFn: api.models, staleTime: 60_000 });
}

export function useApiKeys(enabled = true) {
  return useQuery({ queryKey: ["api-keys"], queryFn: api.listApiKeys, enabled, retry: false });
}

export function useApiKeyScopes(enabled = true) {
  return useQuery({
    queryKey: ["api-key-scopes"],
    queryFn: api.apiKeyScopes,
    enabled,
    staleTime: 300_000,
  });
}

export function useMembers(enabled = true) {
  return useQuery({ queryKey: ["members"], queryFn: api.listMembers, enabled });
}

export function useWebhooks(enabled = true) {
  return useQuery({ queryKey: ["webhooks"], queryFn: api.listWebhooks, enabled });
}

export function useFeatures() {
  return useQuery({ queryKey: ["features"], queryFn: api.features });
}
