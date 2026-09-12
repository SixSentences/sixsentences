"use client";

import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { EVENT_NAMES } from "@/components/run/stage-meta";
import { api, runStreamUrl, type RunRef } from "@/lib/api";
import { isTerminal } from "@/lib/status";
import type { RunEvent, RunStatus } from "@/lib/types";

/**
 * Live progress over SSE. Streamed events are merged into the
 * ["run-events", id] cache (deduplicated by id — the server replays the full
 * log on reconnect), and the run/runs queries are invalidated so status
 * changes propagate. On `done` every run-scoped query is refreshed once.
 */
export function useRunStream(runId: RunRef, status: RunStatus | undefined) {
  const key = String(runId); // query keys are keyed by the string form
  const queryClient = useQueryClient();
  const active = status !== undefined && !isTerminal(status);

  useEffect(() => {
    if (!active || !key) return;

    let source: EventSource | null = null;
    let statusInvalidation: ReturnType<typeof setTimeout> | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let stopped = false;
    let finished = false;

    const onEvent = (event: MessageEvent) => {
      try {
        const parsed = JSON.parse(event.data) as {
          id: number;
          stage: string;
          event: string;
          payload: Record<string, unknown>;
        };
        queryClient.setQueryData<RunEvent[]>(["run-events", key], (old = []) => {
          if (old.some((existing) => existing.id === parsed.id)) return old;
          return [
            ...old,
            { ...parsed, created_at: new Date().toISOString() },
          ];
        });
      } catch {
        // ignore malformed frames; the REST log remains the source of truth
      }
      // debounce status refetches — events can arrive in bursts
      if (statusInvalidation) clearTimeout(statusInvalidation);
      statusInvalidation = setTimeout(() => {
        void queryClient.invalidateQueries({ queryKey: ["run", key] });
        void queryClient.invalidateQueries({ queryKey: ["runs"] });
      }, 400);
    };

    const onDone = () => {
      finished = true;
      source?.close();
      // the run is terminal — refresh everything derived from it
      void queryClient.invalidateQueries({ queryKey: ["run", key] });
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      void queryClient.invalidateQueries({ queryKey: ["run-events", key] });
      void queryClient.invalidateQueries({ queryKey: ["works", key] });
      void queryClient.invalidateQueries({ queryKey: ["queue", key] });
      void queryClient.invalidateQueries({ queryKey: ["decisions", key] });
      void queryClient.invalidateQueries({ queryKey: ["documents", key] });
      void queryClient.invalidateQueries({ queryKey: ["web-sources", key] });
      void queryClient.invalidateQueries({ queryKey: ["methods", key] });
      void queryClient.invalidateQueries({ queryKey: ["protocol", key] });
      void queryClient.invalidateQueries({ queryKey: ["chat", key] });
    };

    const connect = async () => {
      try {
        const { ticket } = await api.runStreamTicket(runId);
        if (stopped || finished) return;
        const nextSource = new EventSource(runStreamUrl(runId, ticket));
        source = nextSource;
        for (const name of EVENT_NAMES) {
          nextSource.addEventListener(name, onEvent);
        }
        nextSource.addEventListener("done", onDone);
        nextSource.onerror = () => {
          nextSource.close();
          if (stopped || finished || reconnectTimer) return;
          // A ticket is intentionally one-use. A dropped connection mints a
          // fresh ticket instead of replaying a long-lived credential.
          reconnectTimer = setTimeout(() => {
            reconnectTimer = null;
            void connect();
          }, 1_500);
        };
      } catch {
        if (stopped || finished || reconnectTimer) return;
        reconnectTimer = setTimeout(() => {
          reconnectTimer = null;
          void connect();
        }, 2_500);
      }
    };
    void connect();

    return () => {
      stopped = true;
      if (statusInvalidation) clearTimeout(statusInvalidation);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      source?.close();
    };
  }, [active, runId, key, queryClient]);
}
