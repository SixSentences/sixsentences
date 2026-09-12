"use client";

import { X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

export type QueuedAgentTurn<T> = {
  id: string;
  text: string;
  payload: T;
};

export function useAgentTurnQueue<T>({
  working,
  run,
}: {
  working: boolean;
  run: (payload: T) => void;
}) {
  const [queue, setQueue] = useState<Array<QueuedAgentTurn<T>>>([]);
  const runRef = useRef(run);
  const wasWorkingRef = useRef(working);
  const generationRef = useRef(0);

  useEffect(() => {
    runRef.current = run;
  }, [run]);

  useEffect(() => {
    const finished = wasWorkingRef.current && !working;
    wasWorkingRef.current = working;
    if (!finished) return;
    setQueue((current) => {
      const [next, ...rest] = current;
      if (!next) return current;
      const generation = generationRef.current;
      queueMicrotask(() => {
        if (generationRef.current === generation) runRef.current(next.payload);
      });
      return rest;
    });
  }, [working]);

  const submit = useCallback(
    (text: string, payload: T) => {
      const normalized = text.trim();
      if (!normalized) return;
      if (!working) {
        runRef.current(payload);
        return;
      }
      setQueue((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          text: normalized,
          payload,
        },
      ]);
    },
    [working],
  );

  const remove = useCallback((id: string) => {
    setQueue((current) => current.filter((item) => item.id !== id));
  }, []);

  const clear = useCallback(() => {
    generationRef.current += 1;
    setQueue([]);
  }, []);

  return { queue, submit, remove, clear };
}

export function AgentTurnQueue<T>({
  items,
  onRemove,
}: {
  items: Array<QueuedAgentTurn<T>>;
  onRemove: (id: string) => void;
}) {
  if (items.length === 0) return null;
  return (
    <div className="mb-2 space-y-1.5" aria-label="Queued assistant messages">
      {items.map((item, index) => (
        <div
          key={item.id}
          className="flex items-center gap-2 rounded-xl border border-moss/25 bg-accent/45 px-3 py-2 text-[0.71875rem]"
        >
          <span className="shrink-0 font-mono text-[0.59375rem] uppercase tracking-[0.16em] text-moss">
            {index === 0 ? "Next" : `Next ${index + 1}`}
          </span>
          <span className="min-w-0 flex-1 truncate text-foreground">{item.text}</span>
          <button
            type="button"
            onClick={() => onRemove(item.id)}
            aria-label={`Remove queued message: ${item.text}`}
            className="grid size-6 shrink-0 cursor-pointer place-items-center rounded-full text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
          >
            <X className="size-3.5" />
          </button>
        </div>
      ))}
    </div>
  );
}
